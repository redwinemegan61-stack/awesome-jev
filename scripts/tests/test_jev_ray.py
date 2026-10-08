"""Offline tests for scripts/jev-ray.py: no network, no API key.

A local HTTP server stands in for both the GitHub API and the Jev endpoint, so
the whole path — digest, request body, WAF fallback, gate, patch — runs end to end.

Run: python3 -m unittest discover -s scripts/tests
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "jev-ray.py"
spec = importlib.util.spec_from_file_location("jev_ray", SCRIPT)
ray = importlib.util.module_from_spec(spec)
sys.modules["jev_ray"] = ray  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(ray)


def answers(agent="none", agent_conf=1.0, kind="none", kind_conf=1.0, evidence=0.0, multi=0.0):
    return {
        "agent": {"type": "choice", "choice": agent, "confidence": agent_conf},
        "type": {"type": "choice", "choice": kind, "confidence": kind_conf},
        "agent_evidence": {"type": "noul", "noul": evidence},
        "multi_agent": {"type": "noul", "noul": multi},
    }


def entry(name="x", url="https://github.com/o/x", tags=None, desc="Tools: does a thing."):
    return ray.Entry("f.md", 3, name, url, dict(tags or {}), desc)


SUPPORT = ray._load_audit_support()
TH = ray.Thresholds()


class Vocabulary(unittest.TestCase):
    def test_options_come_from_tags_json_plus_none(self):
        vocab = ray.load_vocabulary()
        questions = ray.build_questions(vocab)
        self.assertEqual(set(questions["agent"]["criteria"]), set(vocab["agent"]) | {"none"})
        self.assertEqual(set(questions["type"]["criteria"]), set(vocab["type"]) | {"none"})
        # The adversarial definitions are the model: they must reach the request.
        self.assertIn("Not the Claude models", questions["agent"]["criteria"]["claude-code"])
        self.assertEqual(questions["agent_evidence"]["type"], "noul")

    def test_annotation_keys_are_dropped(self):
        vocab = ray.load_vocabulary()
        self.assertFalse(any(k.startswith("$") for axis in vocab.values() for k in axis))


class Decide(unittest.TestCase):
    def test_no_agent_without_evidence(self):
        # "Never infer an agent from the fact that a project uses Jev."
        self.assertEqual(ray.decide(answers("pi", 1.0, evidence=0.3), TH), {})

    def test_single_agent(self):
        self.assertEqual(ray.decide(answers("pi", 0.9, evidence=0.9), TH), {"agent": "pi"})

    def test_multi_wins_over_single_choice(self):
        self.assertEqual(ray.decide(answers("pi", 0.9, evidence=0.9, multi=0.8), TH), {"agent": "multi"})

    def test_choice_multi_needs_the_multi_noul(self):
        self.assertEqual(ray.decide(answers("multi", 1.0, evidence=0.9, multi=0.2), TH), {})

    def test_low_choice_confidence_abstains(self):
        self.assertEqual(ray.decide(answers("codex", 0.4, evidence=0.9), TH), {})

    def test_type_gate(self):
        self.assertEqual(ray.decide(answers(kind="cli", kind_conf=0.9), TH), {"type": "cli"})
        self.assertEqual(ray.decide(answers(kind="cli", kind_conf=0.3), TH), {})
        self.assertEqual(ray.decide(answers(kind="none", kind_conf=1.0), TH), {})

    def test_confidence_derived_when_missing(self):
        a = answers("pi", evidence=0.9)
        a["agent"] = {"type": "choice", "choice": "pi", "probabilities": {"pi": 0.9, "none": 0.1}}
        self.assertAlmostEqual(ray._confidence(a["agent"]), 0.8)
        self.assertEqual(ray.decide(a, TH), {"agent": "pi"})


class Classify(unittest.TestCase):
    def test_hand_tag_wins_and_disagreement_is_reported(self):
        r = ray.classify(entry(tags={"agent": "claude-code"}, desc="Claude Code router."),
                         answers("codex", 1.0, evidence=0.9), TH, SUPPORT)
        self.assertEqual(r.status, "disagree")
        self.assertIn("hand=claude-code scan=codex", r.note)

    def test_agree(self):
        r = ray.classify(entry(tags={"agent": "pi", "type": "plugin"}),
                         answers("pi", 1.0, "plugin", 1.0, evidence=0.9), TH, SUPPORT)
        self.assertEqual(r.status, "agree")

    def test_abstain_is_not_agreement(self):
        r = ray.classify(entry(tags={"agent": "pi"}), answers("pi", 1.0, evidence=0.2), TH, SUPPORT)
        self.assertEqual(r.status, "abstain")

    def test_agent_held_back_when_sentence_does_not_name_it(self):
        # audit-tags.py fails a tag the entry text does not support, so the patch must not add it.
        r = ray.classify(entry(desc="Routing: picks a model per request."),
                         answers("claude-code", 1.0, "cli", 1.0, evidence=0.9), TH, SUPPORT)
        self.assertEqual(r.status, "hold")
        self.assertEqual(r.proposed, {"agent": "claude-code", "type": "cli"})

    def test_agent_proposed_when_sentence_names_it(self):
        r = ray.classify(entry(desc="Coding agents: a Claude Code plugin."),
                         answers("claude-code", 1.0, "plugin", 1.0, evidence=0.9), TH, SUPPORT)
        self.assertEqual((r.status, r.proposed), ("propose", {"agent": "claude-code", "type": "plugin"}))

    def test_multi_has_no_regex_so_is_never_held(self):
        r = ray.classify(entry(), answers("multi", 1.0, evidence=0.9, multi=0.9), TH, SUPPORT)
        self.assertEqual(r.proposed, {"agent": "multi"})

    def test_type_only_follows_the_agent_index_by_default(self):
        a = answers(kind="library", kind_conf=1.0)
        self.assertEqual(ray.classify(entry(), a, TH, SUPPORT).status, "none")
        self.assertEqual(ray.classify(entry(), a, TH, SUPPORT, all_types=True).proposed, {"type": "library"})
        # An entry that already has an agent gets its missing type.
        r = ray.classify(entry(tags={"agent": "pi"}), answers("pi", 1.0, "plugin", 1.0, evidence=0.9), TH, SUPPORT)
        self.assertEqual(r.proposed, {"type": "plugin"})


class Lines(unittest.TestCase):
    LINE = "- [pi-x](https://github.com/o/pi-x) - Coding agents: a Pi plugin - with a dash."

    def test_add_block(self):
        self.assertEqual(
            ray.retag_line(self.LINE, {"type": "plugin", "agent": "pi"}),
            "- [pi-x](https://github.com/o/pi-x) `{agent: pi, type: plugin}` - Coding agents: a Pi plugin - with a dash.",
        )

    def test_replace_block_and_round_trip_through_build_readme_grammar(self):
        tagged = ray.retag_line(self.LINE, {"agent": "pi"})
        again = ray.retag_line(tagged, {"agent": "pi", "type": "cli"})
        m = ray.ENTRY_RE.match(again)
        self.assertEqual(ray.parse_tags(m.group(3)), {"agent": "pi", "type": "cli"})
        self.assertEqual(m.group(4), "Coding agents: a Pi plugin - with a dash.")

    def test_template_line_is_not_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "c.md").write_text(
                "# C\n\n## Entries\n\n- [Name](URL) - Industry: one-sentence description.\n" + self.LINE + "\n",
                encoding="utf-8",
            )
            entries = ray.read_entries(Path(tmp))
        self.assertEqual([(e.name, e.line_no) for e in entries], [("pi-x", 6)])

    def test_readme_cleanup(self):
        text = "[![ci](b.svg)](x) ![logo](l.png)\n<p align=center>Hi</p>\n\n\n\n<!-- c -->Body"
        self.assertEqual(ray.clean_readme(text), "Hi\n\nBody")

    def test_strip_code(self):
        self.assertEqual(ray.strip_code("a\n```sh\ncurl -X POST http://x\n```\nrun `python -m x`"),
                         "a\n[code block omitted]\nrun [code]")


# --------------------------------------------------------------------------- model identity


class ModelIdentity(unittest.TestCase):
    """A cache holds a specific model's answers, so the model belongs in the key."""

    def test_another_model_does_not_read_the_first_models_answers(self):
        questions = ray.build_questions(ray.load_vocabulary())
        first = ray.Cache.key("https://github.com/o/pi-x", "state", questions, "jev-latest")
        self.assertEqual(first, ray.Cache.key("https://github.com/o/pi-x", "state", questions, "jev-latest"))
        self.assertNotEqual(first, ray.Cache.key("https://github.com/o/pi-x", "state", questions, "jev-2"))

    def test_model_resolves_without_an_api_key(self):
        # --offline keys the cache too, so resolving the model must not need Jev.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ray.resolve_model("typesafe"), "jev-latest")
            self.assertEqual(ray.resolve_model("openrouter"), "~typesafe/jev-latest")
        with mock.patch.dict(os.environ, {"JEV_MODEL": "jev-2"}, clear=True):
            self.assertEqual(ray.resolve_model("typesafe"), "jev-2")


# --------------------------------------------------------------------------- end to end


def _b64(text: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class FakeServer(BaseHTTPRequestHandler):
    """GitHub under /gh, Jev under /jev. Jev refuses any state containing `curl -X`."""

    requests: list = []

    def log_message(self, *args):
        pass

    def _send(self, status: int, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        routes = {
            "/gh/repos/o/pi-x": {"full_name": "o/pi-x", "description": "A Pi extension", "topics": ["pi"]},
            "/gh/repos/o/pi-x/contents/": [{"name": "package.json", "type": "file"}, {"name": "src", "type": "dir"}],
            "/gh/repos/o/pi-x/readme": _b64("# pi-x\nInstall: ```\ncurl -X POST http://x\n```\nWorks in Pi."),
            "/gh/repos/o/pi-x/contents/package.json": _b64('{"name": "pi-x", "pi": {"extensions": ["x"]}}'),
        }
        if self.path in routes:
            self._send(200, routes[self.path])
        else:
            self._send(404, {"message": "Not Found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeServer.requests.append((self.headers.get("Authorization"), body))
        if "curl -X" in body["state"]:
            self._send(403, b"<!DOCTYPE html><html>blocked</html>", "text/html")
            return
        self._send(200, {"model": "jev-test", "answers": answers("pi", 0.99, "plugin", 0.97, evidence=0.95, multi=0.1),
                         "usage": {"input_tokens": 123}})


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.env = {"GITHUB_API": f"{base}/gh", "JEV_ENDPOINT": f"{base}/jev",
                   "TYPESAFE_API_KEY": "test-key", "GITHUB_TOKEN": "gh-test"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeServer.requests.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.cats = Path(self.tmp.name)
        self.line = "- [pi-x](https://github.com/o/pi-x) - Coding agents: a Pi extension that asks Jev."
        (self.cats / "agent-decisions.md").write_text(
            "# Agent Decisions\n\n## Entries\n\n- [Name](URL) - Industry: template.\n"
            f"{self.line}\n- [a thread](https://x.com/a/1) - Talk: about Jev.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, **env_extra):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, **env_extra}), mock.patch.object(ray, "CATEGORIES_DIR", self.cats), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = ray.main(list(argv) + ["--cache", str(self.cats / "cache.json")])
        return code, out.getvalue()

    def test_scan_sends_typed_questions_and_falls_back_past_the_firewall(self):
        code, out = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("propose  pi-x", out)
        self.assertIn("skip     a thread", out)  # not a GitHub repository
        first, second = FakeServer.requests
        self.assertEqual(first[0], "Bearer test-key")
        self.assertIn("curl -X", first[1]["state"])
        self.assertNotIn("curl -X", second[1]["state"])
        self.assertEqual(second[1]["questions"]["agent"]["type"], "choice")
        self.assertIn("package.json", second[1]["state"])

    def test_second_run_is_served_from_cache(self):
        self.run_cli("scan")
        FakeServer.requests.clear()
        self.run_cli("scan")
        self.assertEqual(FakeServer.requests, [])  # digest unchanged, answers cached
        code, out = self.run_cli("scan", "--offline")
        self.assertIn("propose  pi-x", out)

    def test_each_model_is_asked_separately_and_kept_separately(self):
        # The answers are that model's judgment: reusing them for another model is
        # how `evaluate` would report one model's precision under another's name.
        self.run_cli("scan")
        self.assertNotEqual(FakeServer.requests, [])

        FakeServer.requests.clear()
        self.run_cli("scan")
        self.assertEqual(FakeServer.requests, [])  # the same model is served from cache

        FakeServer.requests.clear()
        code, _ = self.run_cli("scan", JEV_MODEL="jev-2")
        self.assertEqual(code, 0)
        self.assertNotEqual(FakeServer.requests, [],
                            "jev-2 was never asked: jev-latest's answers were served as its own")

        FakeServer.requests.clear()
        self.run_cli("scan")  # back to the first model
        self.assertEqual(FakeServer.requests, [], "each model's answers should stay cached side by side")

    def test_patch_dry_run_then_write(self):
        code, diff = self.run_cli("patch", "--dry-run")
        self.assertIn("+- [pi-x](https://github.com/o/pi-x) `{agent: pi, type: plugin}` - Coding agents", diff)
        self.assertEqual((self.cats / "agent-decisions.md").read_text().count("`{"), 0)
        self.run_cli("patch")
        text = (self.cats / "agent-decisions.md").read_text()
        self.assertIn("`{agent: pi, type: plugin}`", text)
        self.assertIn("- [Name](URL) - Industry: template.", text)  # untouched

    def test_check_warns_in_ci_and_strict_fails_on_disagreement(self):
        path = self.cats / "agent-decisions.md"
        path.write_text(path.read_text().replace(self.line, ray.retag_line(self.line, {"agent": "codex"})))
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            code, out = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("::warning file=categories/agent-decisions.md,line=6::pi-x", out)
        code, _ = self.run_cli("check", "--strict", "--offline")
        self.assertEqual(code, 1)

    def test_missing_key_is_a_clear_error(self):
        env = {k: v for k, v in self.env.items() if k != "TYPESAFE_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(ray, "CATEGORIES_DIR", self.cats):
            with self.assertRaises(SystemExit) as ctx:
                ray.main(["scan", "--no-cache"])
        self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
