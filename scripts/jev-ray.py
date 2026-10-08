#!/usr/bin/env python3
"""jev-ray — Read, Analyze, Yield: propose entry tags by asking Jev about the repository.

Issue #154. For each catalog entry that links a GitHub repository, build a short
digest of the repository (description, topics, top-level files, README head,
dependency manifest) and ask Jev four typed questions in one call:

  agent           Choice over the `agent` axis of tags.json, plus `none`
  type            Choice over the `type` axis of tags.json, plus `none`
  agent_evidence  Noul: the repository says which coding agent it targets
  multi_agent     Noul: the repository supports several coding agents

The options and their definitions are read from tags.json at runtime, so a tag
this tool proposes is by construction one build-readme.py will render, and a
decision model cannot return a value that is not in the schema it was handed.

The rules from CONTRIBUTING.md become arithmetic in decide():

  - an agent is proposed only when the evidence Noul clears its threshold
    ("never infer an agent from the fact that a project uses Jev");
  - `multi` only when the multi-agent Noul clears its threshold;
  - below a threshold nothing is proposed ("omit it when the source does not say");
  - a hand-written tag always wins; disagreement is reported, never rewritten;
  - an agent tag the entry sentence does not name is held back, because
    scripts/audit-tags.py would fail it — the description has to change first.

Commands:

  scan      print one decision per entry (default: every GitHub entry)
  patch     add proposed tags to entries that lack them; --dry-run prints a diff
  check     CI mode: report entries whose hand tags disagree with the scan
  evaluate  measure the scan against the hand-tagged entries, optionally
            sweeping thresholds over cached answers

README.md is never touched: it is generated, so run build-readme.py after patch.

Environment:

  TYPESAFE_API_KEY    TypeSafe key (default provider), or
  OPENROUTER_API_KEY  with --provider openrouter
  GITHUB_TOKEN        optional; falls back to `gh auth token`, then anonymous
  JEV_ENDPOINT        override the decisions endpoint (tests)
  GITHUB_API          override https://api.github.com (tests)

Usage:

  python3 scripts/jev-ray.py scan [--only NAME ...] [--changed-since REF]
  python3 scripts/jev-ray.py patch [--dry-run]
  python3 scripts/jev-ray.py check [--changed-since origin/main] [--strict]
  python3 scripts/jev-ray.py evaluate [--sweep]
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import dataclasses
import difflib
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES_DIR = REPO_ROOT / "categories"
TAGS_PATH = REPO_ROOT / "tags.json"
DEFAULT_CACHE = REPO_ROOT / ".jev-ray" / "cache.json"

PROVIDERS = {
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/alpha/decisions", "~typesafe/jev-latest", "OPENROUTER_API_KEY"),
}
RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}

# Same grammar as scripts/build-readme.py: the tag block sits between link and separator.
ENTRY_RE = re.compile(r"^- \[([^\]]+)\]\(([^)\s]+)\)(?:\s*`\{([^}]*)\}`)?\s*-\s*(.+)$")
GITHUB_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)")

# The digest is the model's whole view of a repository. ~2k tokens of README is
# the size the issue budgets for; manifests are short and carry install targets.
README_CHARS = 8000
MANIFEST_CHARS = 2000
MANIFESTS = (
    ".claude-plugin/plugin.json",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "setup.py",
)


# --------------------------------------------------------------------------- vocabulary


def load_vocabulary(path: Path = TAGS_PATH) -> dict[str, dict[str, str]]:
    """Return {axis: {value: definition}} from tags.json, $-keys dropped."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    vocab: dict[str, dict[str, str]] = {}
    for axis, spec in raw["axes"].items():
        if axis.startswith("$"):
            continue
        values = {k: v for k, v in spec["values"].items() if not k.startswith("$")}
        vocab[axis] = {value: (meta or {}).get("definition", value) for value, meta in values.items()}
    return vocab


NONE_AGENT = (
    "The repository does not target a coding agent, or does not say which one. Calling "
    "Claude or OpenAI models, routing through a gateway, or being usable from any program "
    "is not targeting an agent."
)
NONE_TYPE = "None of the other options describes how the project is consumed, or the repository does not say."


def build_questions(vocab: dict[str, dict[str, str]]) -> dict[str, dict]:
    """The four questions, one call per entry. Atomic questions, combined in decide()."""
    return {
        "agent": {
            "type": "choice",
            "instructions": (
                "Which coding agent is this project built for? Answer from what the repository "
                "says it targets or installs into, not from which model or gateway it calls."
            ),
            "criteria": {**vocab["agent"], "none": NONE_AGENT},
        },
        "type": {
            "type": "choice",
            "instructions": "How do users consume this project?",
            "criteria": {**vocab["type"], "none": NONE_TYPE},
        },
        "agent_evidence": {
            "type": "noul",
            "instructions": (
                "The repository explicitly states that it is built for, or installs into, a "
                "specific coding agent such as Claude Code, Codex, Pi, Cursor or Cline. Calling a "
                "model, routing through a gateway, or containing a CLAUDE.md or AGENTS.md file "
                "used while developing the repository does not count."
            ),
        },
        "multi_agent": {
            "type": "noul",
            "instructions": (
                "The repository explicitly supports several coding agents, or is built to be "
                "agent-agnostic across coding agents. A library that any program could call "
                "does not count."
            ),
        },
    }


# --------------------------------------------------------------------------- catalog


@dataclasses.dataclass
class Entry:
    file: str
    line_no: int  # 1-based
    name: str
    url: str
    tags: dict[str, str]
    description: str

    @property
    def github(self) -> tuple[str, str] | None:
        m = GITHUB_RE.match(self.url)
        if not m:
            return None
        return m.group(1), m.group(2).removesuffix(".git")


def parse_tags(raw: str | None) -> dict[str, str]:
    tags: dict[str, str] = {}
    for pair in (raw or "").split(","):
        key, _, value = pair.partition(":")
        if key.strip() and value.strip():
            tags[key.strip().lower()] = value.strip().lower()
    return tags


def format_tags(tags: dict[str, str]) -> str:
    return ", ".join(f"{axis}: {tags[axis]}" for axis in ("agent", "type") if axis in tags)


def read_entries(root: Path | None = None) -> list[Entry]:
    entries: list[Entry] = []
    for path in sorted((root or CATEGORIES_DIR).glob("*.md")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = ENTRY_RE.match(line)
            # The template line every category file starts its list with is not an entry.
            if not m or m.group(2) == "URL":
                continue
            name, url, raw, desc = m.groups()
            entries.append(Entry(path.name, no, name, url, parse_tags(raw), desc))
    return entries


def retag_line(line: str, tags: dict[str, str]) -> str:
    """Rewrite one entry line with the given tag block, keeping everything else."""
    m = re.match(r"^(- \[[^\]]+\]\([^)\s]+\))(?:\s*`\{[^}]*\}`)?\s*-\s*(.+)$", line)
    if not m:
        raise ValueError(f"not an entry line: {line[:80]}")
    block = f" `{{{format_tags(tags)}}}`" if tags else ""
    return f"{m.group(1)}{block} - {m.group(2)}"


def changed_urls(ref: str, root: Path = REPO_ROOT) -> set[str]:
    """URLs of entry lines added or modified since `ref` (a submission, in CI)."""
    out = subprocess.run(
        ["git", "diff", "--unified=0", f"{ref}...HEAD", "--", "categories/"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    urls = set()
    for line in out.splitlines():
        if line.startswith("+- [") and not line.startswith("+++"):
            m = ENTRY_RE.match(line[1:])
            if m:
                urls.add(m.group(2))
    return urls


# --------------------------------------------------------------------------- HTTP


def _request(url: str, *, headers: dict[str, str], body: dict | None = None,
             timeout: float = 30.0, retries: int = 3) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "jev-ray", **headers},
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            if err.code not in RETRY_STATUS or attempt == retries:
                return err.code, err.read()
        except urllib.error.URLError as err:
            if attempt == retries:
                raise RuntimeError(f"{url}: {err.reason}") from err
        time.sleep(min(8.0, 0.5 * 2 ** attempt) * (1 - 0.25 * random.random()))
    raise AssertionError("unreachable")


class GitHub:
    def __init__(self, token: str | None = None, api: str | None = None) -> None:
        self.api = (api or os.environ.get("GITHUB_API") or "https://api.github.com").rstrip("/")
        self.headers = {"Accept": "application/vnd.github+json"}
        token = token if token is not None else _github_token()
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str) -> tuple[int, bytes]:
        return _request(f"{self.api}{path}", headers=self.headers)

    def json(self, path: str):
        status, body = self.get(path)
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"GitHub {path}: HTTP {status} {body[:200]!r}")
        return json.loads(body)


def _github_token() -> str | None:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _decode_content(payload: dict | None) -> str:
    if not payload or payload.get("encoding") != "base64":
        return ""
    return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")


def clean_readme(text: str) -> str:
    """Drop what carries no meaning for the questions: images, badges, HTML, blank runs."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", text)  # linked badges
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # images
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_code(text: str) -> str:
    text = re.sub(r"```.*?```", "[code block omitted]", text, flags=re.S)
    return re.sub(r"`[^`\n]+`", "[code]", text)


def build_digest(owner: str, repo: str, gh: GitHub) -> dict:
    """Repository → the state paragraph Jev reads. Four GitHub calls at most."""
    meta = gh.json(f"/repos/{owner}/{repo}")
    if meta is None:
        return {"error": "repository not found"}
    listing = gh.json(f"/repos/{owner}/{repo}/contents/") or []
    names = sorted(item["name"] + ("/" if item.get("type") == "dir" else "") for item in listing)
    readme = clean_readme(_decode_content(gh.json(f"/repos/{owner}/{repo}/readme")))

    manifest_name, manifest = None, ""
    top = {item["name"] for item in listing}
    for candidate in MANIFESTS:
        if candidate.split("/")[0] in top:
            text = _decode_content(gh.json(f"/repos/{owner}/{repo}/contents/{candidate}"))
            if text:
                manifest_name, manifest = candidate, text
                break

    parts = [f"Repository: {meta['full_name']}"]
    if meta.get("description"):
        parts.append(f"Description: {meta['description']}")
    if meta.get("topics"):
        parts.append(f"Topics: {', '.join(meta['topics'])}")
    if names:
        parts.append(f"Top-level files: {', '.join(names[:60])}")
    if manifest_name:
        parts.append(f"{manifest_name}:\n{manifest[:MANIFEST_CHARS]}")
    if readme:
        parts.append(f"README:\n{readme[:README_CHARS]}")
    return {"state": "\n\n".join(parts), "archived": bool(meta.get("archived"))}


def resolve_model(provider: str = "typesafe") -> str:
    """The model a cached answer belongs to, without needing a key.

    scan() has to key the cache even offline, where no Jev is built because there
    may be no API key at all, so the model is resolved apart from Jev.__init__ —
    keeping the two in one place is what stops a cache from handing one model's
    answers to another.
    """
    return os.environ.get("JEV_MODEL") or PROVIDERS[provider][1]


class Jev:
    def __init__(self, provider: str = "typesafe", key: str | None = None) -> None:
        endpoint, _, env = PROVIDERS[provider]
        self.endpoint = os.environ.get("JEV_ENDPOINT") or endpoint
        self.model = resolve_model(provider)
        self.key = key or os.environ.get(env) or os.environ.get("JEV_API_KEY")
        if not self.key:
            raise SystemExit(f"jev-ray: set {env} (or JEV_API_KEY) to call Jev")
        self.input_tokens = 0
        self.waf_fallbacks = 0

    def _post(self, state: str, questions: dict) -> tuple[int, bytes]:
        return _request(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            body={"model": self.model, "state": state, "questions": questions},
            timeout=90,
        )

    def ask(self, state: str, questions: dict) -> dict:
        status, body = self._post(state, questions)
        if status == 403 and body.lstrip().startswith(b"<"):
            # The API's edge firewall answers some READMEs with an HTML 403 page:
            # shell snippets such as `curl -X POST http:` or `python -m x` read as
            # command injection. Code rarely carries the agent claim, so retry without it.
            self.waf_fallbacks += 1
            status, body = self._post(strip_code(state), questions)
        if status != 200:
            raise RuntimeError(f"Jev: HTTP {status} {body[:300]!r}")
        reply = json.loads(body)
        self.input_tokens += (reply.get("usage") or {}).get("input_tokens") or 0
        return reply["answers"]


# --------------------------------------------------------------------------- decision


@dataclasses.dataclass(frozen=True)
class Thresholds:
    agent: float = 0.6     # confidence of the agent Choice
    evidence: float = 0.7  # the repository says which agent it targets
    multi: float = 0.7     # the repository supports several agents
    type: float = 0.6      # confidence of the type Choice


def _confidence(answer: dict) -> float:
    if answer.get("confidence") is not None:
        return float(answer["confidence"])
    # Jev's own formula when a gateway omits it: top probability rescaled from uniform.
    values = [float(v) for v in (answer.get("probabilities") or {}).values()]
    n = len(values)
    return 0.0 if n < 2 else max(0.0, (n * max(values) - 1) / (n - 1))


def decide(answers: dict, th: Thresholds) -> dict[str, str]:
    """Answers → proposed tags. Pure, so thresholds can be swept over cached answers."""
    proposed: dict[str, str] = {}
    evidence = float(answers["agent_evidence"]["noul"])
    multi = float(answers["multi_agent"]["noul"])
    agent = answers["agent"]
    if evidence >= th.evidence:
        if multi >= th.multi:
            proposed["agent"] = "multi"
        elif agent["choice"] not in ("none", "multi") and _confidence(agent) >= th.agent:
            proposed["agent"] = agent["choice"]
    kind = answers["type"]
    if kind["choice"] != "none" and _confidence(kind) >= th.type:
        proposed["type"] = kind["choice"]
    return proposed


def _load_audit_support() -> dict[str, str | None]:
    """The regexes audit-tags.py checks a tag against, so a patch never fails CI."""
    spec = importlib.util.spec_from_file_location("audit_tags", REPO_ROOT / "scripts" / "audit-tags.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SUPPORT


@dataclasses.dataclass
class Result:
    entry: Entry
    status: str  # agree | abstain | propose | disagree | hold | none | skip | error
    proposed: dict[str, str]
    answers: dict | None = None
    note: str = ""

    def summary(self) -> str:
        a = self.answers
        if not a:
            return self.note
        ag, ty = a["agent"], a["type"]
        return (
            f"agent={ag['choice']}@{_confidence(ag):.2f} type={ty['choice']}@{_confidence(ty):.2f} "
            f"evidence={a['agent_evidence']['noul']:.2f} multi={a['multi_agent']['noul']:.2f}"
            + (f" — {self.note}" if self.note else "")
        )


def classify(entry: Entry, answers: dict, th: Thresholds, support: dict,
             all_types: bool = False) -> Result:
    """Compare the proposal with the hand tags. Hand tags always win.

    A `type` is only added to an entry that carries or gains an `agent`, unless
    all_types: tags exist for the "Find by coding agent" index, and most entries
    carry none by design (CONTRIBUTING.md). Tagging every library in the catalog
    is a vocabulary decision for the maintainer, not a side effect of a scan.
    """
    proposed = decide(answers, th)
    disagreements = [
        f"{axis}: hand={entry.tags[axis]} scan={proposed[axis]}"
        for axis in ("agent", "type")
        if axis in entry.tags and axis in proposed and entry.tags[axis] != proposed[axis]
    ]
    if disagreements:
        return Result(entry, "disagree", proposed, answers, "; ".join(disagreements))

    additions = {axis: v for axis, v in proposed.items() if axis not in entry.tags}
    notes = []
    agent = additions.get("agent")
    if agent and support.get(agent) and not re.search(support[agent], f"{entry.name} {entry.description}", re.I):
        del additions["agent"]
        notes.append(f"agent: {agent} held back — the entry sentence does not name it (audit-tags.py would fail)")
    if "type" in additions and not all_types and "agent" not in entry.tags and "agent" not in additions:
        del additions["type"]
    if additions:
        return Result(entry, "propose", additions, answers, "; ".join(notes))
    if notes:
        return Result(entry, "hold", proposed, answers, "; ".join(notes))
    if not entry.tags:
        return Result(entry, "none", {}, answers)
    # A hand tag the scan did not reach is not a confirmation of it.
    unconfirmed = [axis for axis in entry.tags if axis not in proposed]
    if unconfirmed:
        return Result(entry, "abstain", {}, answers, f"below threshold on {', '.join(unconfirmed)}")
    return Result(entry, "agree", {}, answers)


# --------------------------------------------------------------------------- scan


class Cache:
    """Answers keyed by (url, digest, questions, model), so re-running or re-tuning is free.

    The model is part of the key because an answer is that model's judgment:
    without it, whatever populated the cache first is later reported as another
    model's decision, which is exactly what `evaluate` measures. The endpoint is
    deliberately left out — it is transport, and tests override it per run.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.data: dict = {}
        if path and path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def key(url: str, state: str, questions: dict, model: str) -> str:
        blob = json.dumps([url, state, questions, model], sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")


def scan(entries: list[Entry], *, jev: Jev | None, gh: GitHub, cache: Cache,
         questions: dict, model: str, workers: int, offline: bool = False) -> dict[str, dict]:
    """Return {url: {"answers": ..., "archived": ...} | {"skip"/"error": reason}}."""

    def one(entry: Entry) -> tuple[str, dict]:
        repo = entry.github
        if not repo:
            return entry.url, {"skip": "not a GitHub repository"}
        digest_key = f"digest:{entry.url}"
        try:
            digest = cache.data.get(digest_key) if offline else None
            if digest is None:
                digest = build_digest(*repo, gh)
                cache.data[digest_key] = digest
            if "error" in digest:
                return entry.url, {"error": digest["error"]}
            key = Cache.key(entry.url, digest["state"], questions, model)
            if key not in cache.data:
                if offline or jev is None:
                    return entry.url, {"skip": "no cached answers (offline)"}
                cache.data[key] = jev.ask(digest["state"], questions)
            return entry.url, {"answers": cache.data[key], "archived": digest.get("archived")}
        except Exception as exc:  # one bad repository must not sink the pass
            return entry.url, {"error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = dict(pool.map(one, entries))
    cache.save()
    return results


def results_for(entries, raw, th, support, all_types: bool = False) -> list[Result]:
    out = []
    for entry in entries:
        r = raw[entry.url]
        if "answers" in r:
            res = classify(entry, r["answers"], th, support, all_types)
            if r.get("archived"):
                res.note = (res.note + "; " if res.note else "") + "repository is archived"
            out.append(res)
        else:
            out.append(Result(entry, "skip" if "skip" in r else "error", {}, None, r.get("skip") or r.get("error")))
    return out


# --------------------------------------------------------------------------- commands


def cmd_patch(results: list[Result], dry_run: bool) -> int:
    by_file: dict[str, list[Result]] = {}
    for r in results:
        if r.status == "propose":
            by_file.setdefault(r.entry.file, []).append(r)
    if not by_file:
        print("nothing to patch.")
        return 0
    for name, items in sorted(by_file.items()):
        path = CATEGORIES_DIR / name
        before = path.read_text(encoding="utf-8").splitlines(keepends=True)
        after = list(before)
        for r in items:
            i = r.entry.line_no - 1
            ending = "\n" if after[i].endswith("\n") else ""
            after[i] = retag_line(after[i].rstrip("\n"), {**r.entry.tags, **r.proposed}) + ending
        if dry_run:
            sys.stdout.writelines(difflib.unified_diff(before, after, f"a/categories/{name}", f"b/categories/{name}"))
        else:
            path.write_text("".join(after), encoding="utf-8")
            print(f"patched {len(items)} entr{'y' if len(items) == 1 else 'ies'} in categories/{name}")
    if not dry_run:
        print("now run: python3 scripts/build-readme.py && python3 scripts/audit-tags.py")
    return 0


def cmd_check(results: list[Result], strict: bool) -> int:
    ci = os.environ.get("GITHUB_ACTIONS") == "true"
    flagged = [r for r in results if r.status in ("disagree", "propose", "hold")]
    for r in flagged:
        verb = {"disagree": "disagrees with the hand tag", "propose": "would add", "hold": "would add"}[r.status]
        detail = r.note if r.status != "propose" else format_tags(r.proposed)
        msg = f"jev-ray {verb}: {detail} ({r.summary()})"
        if ci:
            level = "warning" if r.status == "disagree" else "notice"
            print(f"::{level} file=categories/{r.entry.file},line={r.entry.line_no}::{r.entry.name}: {msg}")
        else:
            print(f"{r.entry.file}:{r.entry.line_no}: {r.entry.name}: {msg}")
    disagreements = sum(r.status == "disagree" for r in flagged)
    print(f"{len(results)} scanned, {disagreements} disagreement(s), {len(flagged) - disagreements} proposal(s).")
    return 1 if strict and disagreements else 0


def evaluate(entries: list[Entry], raw: dict, th: Thresholds) -> dict:
    """Agreement with the hand tags, per axis, plus false agent proposals on untagged entries.

    Untagged entries are weak negatives: tags are optional, so an agent proposal
    there is either a miss by the hand tagger or a false positive — worth reading.
    """
    stats = {axis: {"agree": 0, "wrong": 0, "abstain": 0} for axis in ("agent", "type")}
    unsolicited = []
    for entry in entries:
        r = raw.get(entry.url, {})
        if "answers" not in r:
            continue
        proposed = decide(r["answers"], th)
        for axis in ("agent", "type"):
            if axis in entry.tags:
                if axis not in proposed:
                    stats[axis]["abstain"] += 1
                elif proposed[axis] == entry.tags[axis]:
                    stats[axis]["agree"] += 1
                else:
                    stats[axis]["wrong"] += 1
        if "agent" not in entry.tags and "agent" in proposed:
            unsolicited.append((entry.name, proposed["agent"]))
    return {"axes": stats, "unsolicited_agent": unsolicited}


def cmd_evaluate(entries, raw, th: Thresholds, sweep: bool) -> int:
    report = evaluate(entries, raw, th)
    for axis, s in report["axes"].items():
        total = sum(s.values())
        precision = s["agree"] / (s["agree"] + s["wrong"]) if s["agree"] + s["wrong"] else 0.0
        print(f"{axis:5}  labelled={total:3}  agree={s['agree']:3}  wrong={s['wrong']:3}  "
              f"abstain={s['abstain']:3}  precision={precision:.2f}  coverage={s['agree'] / total if total else 0:.2f}")
    print(f"agent proposed on untagged entries: {len(report['unsolicited_agent'])}")
    for name, agent in report["unsolicited_agent"]:
        print(f"  {name} → {agent}")
    for entry in entries:
        r = raw.get(entry.url, {})
        if entry.tags and "answers" in r:
            proposed = decide(r["answers"], th)
            miss = {a: (entry.tags[a], proposed.get(a)) for a in entry.tags if proposed.get(a) != entry.tags[a]}
            if miss:
                print(f"  miss: {entry.name}: " + ", ".join(f"{a} hand={h} scan={s}" for a, (h, s) in miss.items()))
    if sweep:
        print("\nsweep (agent axis): evidence  agent-conf  multi  → agree / wrong / abstain / unsolicited")
        grid = [0.5, 0.6, 0.7, 0.8, 0.9]
        rows = []
        for ev in grid:
            for ac in grid:
                for mu in grid:
                    t = dataclasses.replace(th, evidence=ev, agent=ac, multi=mu)
                    rep = evaluate(entries, raw, t)
                    s = rep["axes"]["agent"]
                    rows.append((s["wrong"], -s["agree"], len(rep["unsolicited_agent"]), ev, ac, mu, s, rep))
        for wrong, _, unsol, ev, ac, mu, s, _ in sorted(rows)[:10]:
            print(f"  {ev:.1f}  {ac:.1f}  {mu:.1f}  → {s['agree']} / {s['wrong']} / {s['abstain']} / {unsol}")
        print("\nsweep (type axis): conf → agree / wrong / abstain")
        for tc in grid:
            s = evaluate(entries, raw, dataclasses.replace(th, type=tc))["axes"]["type"]
            print(f"  {tc:.1f}  → {s['agree']} / {s['wrong']} / {s['abstain']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("scan", "patch", "check", "evaluate"))
    parser.add_argument("--only", nargs="+", metavar="NAME", help="entries whose name matches exactly")
    parser.add_argument("--changed-since", metavar="REF", help="only entries added or edited since REF")
    parser.add_argument("--tagged", action="store_true", help="only entries that already carry tags")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="typesafe")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help=f"default: {DEFAULT_CACHE.relative_to(REPO_ROOT)}")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--offline", action="store_true", help="use cached digests and answers only")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="patch: print the diff instead of writing")
    parser.add_argument("--strict", action="store_true", help="check: exit 1 on disagreement")
    parser.add_argument("--sweep", action="store_true", help="evaluate: sweep thresholds over the answers")
    parser.add_argument("--json", action="store_true", help="scan: print JSON instead of text")
    parser.add_argument("--all-types", action="store_true",
                        help="propose a type for entries without an agent too (off: tags follow the agent index)")
    defaults = Thresholds()
    for field in dataclasses.fields(Thresholds):
        parser.add_argument(f"--{field.name}-threshold", type=float, default=getattr(defaults, field.name))
    args = parser.parse_args(argv)

    th = Thresholds(**{f.name: getattr(args, f"{f.name}_threshold") for f in dataclasses.fields(Thresholds)})
    entries = read_entries()
    if args.only:
        entries = [e for e in entries if e.name in set(args.only)]
    if args.changed_since:
        urls = changed_urls(args.changed_since)
        entries = [e for e in entries if e.url in urls]
    if args.tagged:
        entries = [e for e in entries if e.tags]
    if not entries:
        print("no entries selected.")
        return 0

    questions = build_questions(load_vocabulary())
    # Resolved once, outside Jev, because an offline run still has to key the cache.
    model = resolve_model(args.provider)
    cache = Cache(None if args.no_cache else args.cache)
    jev = None if args.offline else Jev(args.provider)
    raw = scan(entries, jev=jev, gh=GitHub(), cache=cache, questions=questions,
               workers=args.workers, model=model, offline=args.offline)
    if jev and jev.input_tokens:
        # Published rate at the time of writing: $0.042 per million input tokens, output free.
        print(f"[jev-ray] {jev.input_tokens} input tokens ≈ ${jev.input_tokens * 0.042 / 1e6:.4f}"
              f"{f', {jev.waf_fallbacks} retried without code' if jev.waf_fallbacks else ''}", file=sys.stderr)

    if args.command == "evaluate":
        return cmd_evaluate(entries, raw, th, args.sweep)

    results = results_for(entries, raw, th, _load_audit_support(), args.all_types)
    if args.command == "patch":
        return cmd_patch(results, args.dry_run)
    if args.command == "check":
        return cmd_check(results, args.strict)

    if args.json:
        print(json.dumps([{"file": r.entry.file, "line": r.entry.line_no, "name": r.entry.name,
                           "url": r.entry.url, "hand": r.entry.tags, "status": r.status,
                           "proposed": r.proposed, "note": r.note, "answers": r.answers}
                          for r in results], indent=1))
    else:
        for r in results:
            tags = format_tags(r.proposed) if r.proposed else "-"
            print(f"{r.status:8} {r.entry.name[:40]:40} hand=[{format_tags(r.entry.tags)}] scan=[{tags}] {r.summary()}")
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
