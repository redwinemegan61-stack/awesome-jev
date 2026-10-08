"""Offline tests for scripts/audit-tags.py: no network, no fixture files in the repo.

Most of what is here guards one failure mode: the audit quietly covering less
than tags.json allows. check_support_coverage's docstring spells out what fails
open when a value arrives without an evidence rule.

Run: python3 -m unittest discover -s scripts/tests
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit-tags.py"
TAGS_PATH = ROOT / "tags.json"
spec = importlib.util.spec_from_file_location("audit_tags", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

REAL_VOCABULARY = json.loads(TAGS_PATH.read_text(encoding="utf-8"))


def write_tags(directory: Path, extra_agent: dict | None = None) -> Path:
    """A copy of the real tags.json, optionally carrying one more agent value."""
    tags = copy.deepcopy(REAL_VOCABULARY)
    if extra_agent:
        tags["axes"]["agent"]["values"].update(extra_agent)
    path = directory / "tags.json"
    path.write_text(json.dumps(tags), encoding="utf-8")
    return path


def run(root: Path, tags_path: Path) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = audit.main(["audit-tags.py", str(root), str(tags_path)])
    return code, out.getvalue()


class SupportCoverage(unittest.TestCase):
    def test_real_vocabulary_is_fully_covered(self):
        # The one that has to keep passing: adding an agent value to tags.json
        # without an evidence rule here is supposed to break CI, not silently
        # narrow the audit.
        missing = audit.check_support_coverage(audit.load_agent_values(TAGS_PATH))
        self.assertEqual(missing, [])

    def test_multi_is_a_recorded_answer_not_a_gap(self):
        # `multi` is deliberately None — breadth is a judgement no single name
        # confirms. Coverage is compared by key, so it counts as answered.
        self.assertIsNone(audit.SUPPORT["multi"])
        self.assertNotIn("multi", audit.check_support_coverage(["multi"]))

    def test_annotation_keys_are_not_vocabulary(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tags.json"
            path.write_text(
                json.dumps({"axes": {"agent": {"values": {"$comment": "note", "pi": {}}}}}),
                encoding="utf-8",
            )
            self.assertEqual(audit.load_agent_values(path), ["pi"])

    def test_a_value_without_a_rule_fails_the_audit(self):
        # The entry neither names nor describes Zed, so it breaks CONTRIBUTING's
        # "tag only what the source supports" — and before the coverage check the
        # auditor had no rule to notice it with.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            categories = tmp / "categories"
            categories.mkdir()
            (categories / "routing.md").write_text(
                "- [Router](https://github.com/o/zedrouter) `{agent: zed}` - "
                "Editor tooling: routes file edits to a typed decision.\n",
                encoding="utf-8",
            )
            code, out = run(categories, write_tags(tmp, {"zed": {"label": "Zed"}}))
        self.assertEqual(code, 1, out)
        self.assertIn("agent value `zed`", out)
        # The old per-entry check never fired: it had nothing to match against.
        self.assertNotIn("which its text does not mention", out)

    def test_the_real_catalog_still_passes(self):
        # Does not re-open the coverage question; it fails only if this ever
        # starts reporting errors on the entries themselves.
        code, out = run(ROOT / "categories", TAGS_PATH)
        self.assertEqual(code, 0, out)
        self.assertNotIn("error:", out)
