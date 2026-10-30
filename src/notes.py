"""The notes repository: a git checkout the server reads, and proposals it writes.

Layout of the notes repo (see seed/ for a starting copy):
  TEAM.md                 team rules, sent to every session in the server instructions
  DISTILL.md              instructions for the distill prompt
  DISTILL_ALL.md          instructions for the distill_all prompt
  briefings/<system>.md   one briefing per system, with a small header:
                            ---
                            title: TED
                            description: telemetry and logs (Grafana, archiver, ted-api)
                            folders: TED, ted-api
                            ---
  notes/<system>/*.md     everything that does not fit in a briefing

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
PATH_OK = re.compile(r"^(briefings/[a-z0-9][a-z0-9_-]*\.md|notes/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_.-]*\.md)$")

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


def search(query: str, limit: int) -> list[dict]:
    """Rank notes by how often the query words occur; return matching lines."""
    words = [w.lower() for w in re.findall(r"[\w.:-]+", query) if len(w) > 1]
    if not words:
        return []
    hits = []
    for p in REPO.glob("**/*.md"):
        if ".git" in p.parts:
            continue
        text = p.read_text(errors="replace")
        low = text.lower()
        score = sum(low.count(w) for w in words)
        if not score:
            continue
        lines = [ln.strip() for ln in text.splitlines() if any(w in ln.lower() for w in words)]
        hits.append({"path": str(p.relative_to(REPO)), "score": score, "lines": lines[:8]})
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
            errs.append(f"{path}: must be briefings/<system>.md or notes/<system>/<name>.md (lowercase)")
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
