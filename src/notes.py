"""The notes repository: a git checkout the server reads, and proposals it writes.

Layout of the notes repo (see seed/ for a starting copy):
  TEAM.md                 team rules: the working agreement every session must follow
  core/*.md               general knowledge that applies across systems (patterns,
                          procedures, how the team works); part of the core
  DISTILL.md              instructions for the distill prompt
  DISTILL_ALL.md          instructions for the distill_all prompt
  briefings/<system>.md   one briefing per system, with a small header:
                            ---
                            title: TED
                            description: telemetry and logs (Grafana, archiver, ted-api)
                            folders: TED, ted-api
                            ---
  notes/<system>/*.md     everything that does not fit in a briefing

The core is what every session reads before it starts: TEAM.md, core/*.md,
every briefing in full, and an index of every note. core_parts() splits it
into parts small enough for one tool result each.

Reads come from the checkout of NOTES_BRANCH, which refresh() keeps in step
with the remote. Proposals never touch that checkout: submit() builds a commit
with git plumbing on a new proposal/... branch, pushes it and opens a pull
request. Nothing reaches the served notes until a person merges it.
"""

import base64
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

DATA = Path(os.environ.get("KB_DATA", "/data"))
REPO = DATA / "notes"
REMOTE = os.environ.get("NOTES_REMOTE", "")          # empty: local-only repo
BRANCH = os.environ.get("NOTES_BRANCH", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
SEED = Path(__file__).parent / "seed"

MAX_FILES = 20
MAX_BYTES = 64 * 1024
# Claude Code caps one MCP tool result at 25k tokens by default; ~4 characters a token.
PART_CHARS = int(os.environ.get("CORE_PART_CHARS", "60000"))
# Above this the core carries briefing summaries instead of full briefings (~50k tokens).
MAX_CORE = int(os.environ.get("CORE_MAX_CHARS", "200000"))
PATH_OK = re.compile(r"^(core/[a-z0-9][a-z0-9_-]*\.md|briefings/[a-z0-9][a-z0-9_-]*\.md|notes/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_.-]*\.md)$")

# Rejected outright in proposals; the review is the second check.
SECRETS = [
    (re.compile(r"(?i)\b(pass(word|wd)?|pwd|secret)\b\s*[:=]\s*\S{4,}"), "password assignment"),
    (re.compile(r"(?i)\b(token|api[_-]?key)\b\s*[:=]\s*[A-Za-z0-9_\-.]{16,}"), "token assignment"),
    (re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"), "GitHub token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), "GitLab token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS key"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."), "JWT"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]

_lock = threading.Lock()   # one git operation at a time


def _git(*args: str, env: dict | None = None, input: str | None = None) -> str:
    cmd = ["git", "-C", str(REPO), "-c", "user.name=gnops-kb-mcp", "-c", "user.email=gnops-kb-mcp@localhost"]
    if TOKEN and REMOTE.startswith("https://"):
        basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    r = subprocess.run(cmd + list(args), capture_output=True, text=True, input=input,
                       env={**os.environ, "HOME": "/tmp", **(env or {})}, timeout=120)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:500]}")
    return r.stdout.strip()


def init() -> None:
    """Clone NOTES_REMOTE, or create a local repo from seed/ when there is none."""
    with _lock:
        if (REPO / ".git").exists():
            return
        REPO.mkdir(parents=True, exist_ok=True)
        if REMOTE:
            _git("clone", "--branch", BRANCH, REMOTE, ".")
        else:
            _git("init", "-q", "-b", BRANCH)
            shutil.copytree(SEED, REPO, dirs_exist_ok=True)
            _git("add", "-A")
            _git("commit", "-q", "-m", "Seed notes")


def refresh() -> None:
    """Make the checkout match the remote branch (no-op for a local repo)."""
    if not REMOTE:
        return
    with _lock:
        _git("fetch", "-q", "origin", BRANCH)
        _git("reset", "-q", "--hard", f"origin/{BRANCH}")


def read(path: str) -> str | None:
    p = (REPO / path).resolve()
    if REPO.resolve() not in p.parents or not p.is_file():
        return None
    return p.read_text(errors="replace")


def _header(text: str) -> tuple[dict, str]:
    """Split a '---' header of 'key: value' lines from the body."""
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        if v:
            meta[k.strip()] = v.strip()
    return meta, body


def briefings() -> list[dict]:
    out = []
    for p in sorted((REPO / "briefings").glob("*.md")):
        meta, _ = _header(p.read_text(errors="replace"))
        out.append({
            "system": p.stem,
            "title": meta.get("title", p.stem),
            "description": meta.get("description", ""),
            "folders": [f.strip() for f in meta.get("folders", "").split(",") if f.strip()],
        })
    return out


def match_folder(cwd: str) -> str | None:
    """The briefing whose folder names appear in cwd, deepest match first."""
    parts = [c.lower() for c in Path(cwd).parts]
    best, depth = None, -1
    for b in briefings():
        for f in b["folders"] + [b["system"]]:
            if f.lower() in parts:
                d = len(parts) - 1 - parts[::-1].index(f.lower())
                if d > depth:
                    best, depth = b["system"], d
    return best


LOOKUP = """\
# How to find things (read this first)

This core is a map, not everything the team knows. Look things up; never guess.

1. Start from this core: the working agreement, general knowledge (core/),
   the system briefings and the index of detailed notes at the end.
2. Not answered here? search_notes with several terms (names, PVs, hosts,
   GNFR/REL keys, error text), then get_note(path) on the hits. Notes for one
   system: list_notes(system). A briefing shown here as a summary:
   get_briefing(system) for the full text.
3. Notes are leads. Before the user acts on a fact (fault status, config,
   versions, live values), check it at the source the note names: Jira, TED
   telemetry and logs, PVs, the repo, the CI build, rpm-repo.
4. Production: you have no access. Ask the user for the exact command and
   host; they run it and paste the output.
5. Not in the KB and not checkable? Say so plainly: "not in the KB". Do not
   fill the gap from general knowledge or a plausible guess, and do not
   present a guess as a finding. When you then find it, offer to distill it.
"""


def _describe(meta: dict, body: str) -> tuple[str, str]:
    """A note's title and one-line description: from its header, else its first heading and paragraph."""
    lines = [ln.strip() for ln in body.splitlines()]
    heading = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("#")), "")
    paras = [p.split("\n") for p in re.split(r"\n\s*\n", "\n".join(lines))]
    para = next((" ".join(p) for p in paras
                 if p[0] and not p[0].startswith(("#", "|", "```", "-", "*", ">", "<", "["))), "")
    desc = meta.get("description") or (para[:157] + "..." if len(para) > 160 else para)
    return meta.get("title") or heading, desc


def note_index(system: str | None = None) -> list[dict]:
    """Files under notes/ (or notes/<system>/), with title and one-line description."""
    root = REPO / "notes" / system if system else REPO / "notes"
    out = []
    for p in sorted(root.glob("**/*.md")):
        text = p.read_text(errors="replace")
        meta, body = _header(text)
        title, desc = _describe(meta, body)
        rel = p.relative_to(REPO)
        out.append({"path": str(rel), "system": rel.parts[1], "title": title or p.stem,
                    "description": desc, "chars": len(text)})
    return out


def _summary(body: str) -> str:
    """A briefing's text up to the end of its first ## section, plus the headings of the rest."""
    secs = re.split(r"(?m)^(?=## )", body.strip())
    n = 2 if not secs[0].startswith("## ") else 1   # any preamble, then the first section
    heads = [r.splitlines()[0].lstrip("# ") for r in secs[n:]]
    return "".join(secs[:n]).strip() + (f"\n\nMore sections (get_briefing): {', '.join(heads)}" if heads else "")


def core_sections() -> list[str]:
    """The core, in reading order: lookup rules, working agreement, general knowledge, briefings, index.

    Briefings go in full while the whole core fits CORE_MAX_CHARS, else as summaries.
    """
    secs = [LOOKUP.strip()]
    team = read("TEAM.md")
    if team:
        secs.append(f"# Working agreement (TEAM.md)\n\n{team.strip()}")
    for p in sorted((REPO / "core").glob("*.md")):
        meta, body = _header(p.read_text(errors="replace"))
        about = f"{meta['description']}\n\n" if meta.get("description") else ""
        secs.append(f"# General knowledge: {p.relative_to(REPO)}\n{about}\n{body.strip()}")
    index = note_index()
    groups: dict[str, list[str]] = {}
    for n in index:
        groups.setdefault(n["system"], []).append(
            f"- {n['path']} ({n['chars'] // 1000 + 1}k): {n['title']}" + (f" - {n['description']}" if n["description"] else ""))
    idx = "# Index of detailed notes (get_note(path); list_notes(system))\n" + "".join(
        f"\n## {g} ({len(rows)})\n" + "\n".join(rows) + "\n" for g, rows in sorted(groups.items()))
    full, short = [], []
    for b in briefings():
        _, body = _header(read(f"briefings/{b['system']}.md") or "")
        head = (f"# Briefing: {b['system']} - {b['title']}\n"
                f"{b['description']} (folders: {', '.join(b['folders']) or b['system']})\n\n")
        full.append(head + body.strip())
        short.append(head + "[summary; full text: get_briefing]\n" + _summary(body))
    fixed = sum(len(x) for x in secs) + len(idx)
    secs += full if fixed + sum(len(x) for x in full) <= MAX_CORE else short
    if index:
        secs.append(idx.strip())
    return secs


def core_parts(limit: int = PART_CHARS) -> list[str]:
    """The core packed into parts of at most limit characters, split between sections where possible."""
    chunks = []
    for sec in core_sections():
        while len(sec) > limit:   # an oversize section is split on a line break
            cut = sec.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(sec[:cut])
            sec = sec[cut:].lstrip("\n")
        chunks.append(sec)
    parts, cur = [], ""
    for c in chunks:
        if cur and len(cur) + len(c) + 2 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{c}" if cur else c
    if cur:
        parts.append(cur)
    return parts or ["(the notes repo is empty)"]


def search(query: str, limit: int) -> list[dict]:
    """Rank files by how many query words they contain, with title and description hits counting most."""
    words = list(dict.fromkeys(w.lower() for w in re.findall(r"[\w.:-]+", query) if len(w) > 1))
    if not words:
        return []
    hits = []
    for p in REPO.glob("**/*.md"):
        if ".git" in p.parts:
            continue
        text = p.read_text(errors="replace")
        meta, body = _header(text)
        title, desc = _describe(meta, body)
        rel = str(p.relative_to(REPO))
        low, head = text.lower(), f"{rel} {title} {desc}".lower()
        found = [w for w in words if w in low]
        if not found:
            continue
        score = (100 * len(found) + 20 * sum(w in head for w in words)
                 + sum(min(low.count(w), 10) for w in found))
        lines = [ln.strip() for ln in body.splitlines() if any(w in ln.lower() for w in words)]
        hits.append({"path": rel, "title": title, "description": desc, "score": score,
                     "matched": f"{len(found)}/{len(words)} words", "lines": lines[:6]})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def check(changes: list[dict]) -> list[str]:
    """Problems that block a proposal: bad paths, oversize files, secrets."""
    errs = []
    if not 1 <= len(changes) <= MAX_FILES:
        errs.append(f"pass 1-{MAX_FILES} files")
    for c in changes:
        path, content = c.get("path", ""), c.get("content", "")
        if not PATH_OK.match(path):
            errs.append(f"{path}: must be core/<name>.md, briefings/<system>.md or notes/<system>/<name>.md (lowercase)")
        meta, _ = _header(content)
        if PATH_OK.match(path) and not (meta.get("title") and meta.get("description")):
            errs.append(f"{path}: start with a header block: ---, title: <name>, description: <one line "
                        "saying what this file answers, so a session can find it from the index>, ---")
        if len(content.encode()) > MAX_BYTES:
            errs.append(f"{path}: over {MAX_BYTES // 1024} KB")
        for n, line in enumerate(content.splitlines(), 1):
            for rx, what in SECRETS:
                if rx.search(line):
                    errs.append(f"{path}:{n}: looks like a {what}; say where it is stored instead")
    return errs


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "notes"


def submit(title: str, summary: str, author: str, changes: list[dict]) -> dict:
    """Commit changes on a new proposal branch off BRANCH; push and open a PR if there is a remote."""
    branch = f"proposal/{time.strftime('%Y%m%d-%H%M%S')}-{_slug(title)}"
    msg = f"{title}\n\n{summary}\n\nProposed-by: {author}\n"
    with _lock:
        base = _git("rev-parse", f"origin/{BRANCH}" if REMOTE else BRANCH)
        with tempfile.NamedTemporaryFile(dir="/tmp") as idx:
            env = {"GIT_INDEX_FILE": idx.name}
            _git("read-tree", base, env=env)
            for c in changes:
                blob = _git("hash-object", "-w", "--stdin", input=c["content"])
                _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{c['path']}", env=env)
            tree = _git("write-tree", env=env)
        if tree == _git("rev-parse", f"{base}^{{tree}}"):
            return {"status": "unchanged", "detail": "the notes already contain exactly this"}
        commit = _git("commit-tree", tree, "-p", base, "-m", msg)
        _git("update-ref", f"refs/heads/{branch}", commit)
        if not REMOTE:
            git = "docker exec -e HOME=/tmp gnops-kb-mcp git -C /data/notes"
            return {"status": "proposed", "branch": branch,
                    "review": f"on the server: {git} diff {BRANCH}...{branch}",
                    "merge": f"{git} -c user.name=review -c user.email=review@localhost merge {branch}"}
        _git("push", "-q", "origin", f"{branch}:{branch}")
    return {"status": "proposed", "branch": branch, "pull_request": _open_pr(branch, title, msg)}


def _open_pr(branch: str, title: str, body: str) -> str:
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(\.git)?$", REMOTE)
    if not (TOKEN and m):
        return f"open one for branch {branch} (no GITHUB_TOKEN set)"
    r = httpx.post(f"https://api.github.com/repos/{m[1]}/{m[2]}/pulls", timeout=30,
                   headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
                   json={"title": title, "head": branch, "base": BRANCH, "body": body})
    r.raise_for_status()
    return r.json()["html_url"]
