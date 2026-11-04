"""gnops-kb: the team's shared knowledge for Claude sessions, served over MCP.

Every session that connects gets:
  instructions   a short, mandatory order to read the core first, and the
                 list of systems (rebuilt after each refresh, so edits reach
                 everyone). Claude Code truncates instructions at about 4k
                 characters, so the order comes first and the rest stays short.
  tools          get_core(part) - the core: TEAM.md, core/*.md, every briefing
                 and an index of every note, in parts that fit one tool result;
                 list_briefings, get_briefing, list_notes, search_notes, get_note,
                 submit_notes (proposes changes as a pull request; never
                 edits the served notes). Each part of the core ends with a key
                 fragment; the other tools require the joined key (core_key), so
                 only a session that read every part can use them.
  prompts        brief(system), distill, distill_all

Plain HTTP, for a SessionStart hook:
  GET /start?cwd=<dir>   the order to read the core, plus the briefing for that
                         folder when one matches
  GET /usage?n=200       the most recent tool calls (do sessions read the core, search, open notes)
  GET /health

Run:
  python server.py          stdio
  python server.py --http   Streamable HTTP on $MCP_HOST:$MCP_PORT (default
                            0.0.0.0:8002), endpoint /mcp; this is how the
                            container runs
"""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import threading
import time

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

import notes

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))
# How long a core key stays valid after the core changes (a merged note changes it).
KEY_HOURS = float(os.environ.get("CORE_KEY_HOURS", "24"))

mcp = MCPServer("gnops-kb")


def core_order() -> str:
    parts = _core()[1]["parts"]
    size = sum(len(p) for p in parts) // 4000 + 1
    return (
        f"MANDATORY, before your first reply and before any other tool call: call gnops-kb "
        f"get_core(part=1), then part=2 and so on through part={len(parts)} ({len(parts)} parts, "
        "each with the version the previous part names, "
        f"about {size}k tokens). Read each part to its end: the last line of every part holds one "
        "fragment of the core key, and every other gnops-kb tool requires the whole key "
        "(core_key). Do this in every session, even when the task looks small or unrelated: the "
        "core is the team's working agreement (what you may and may not do on production, and how "
        "the user works with you) and the knowledge you need to work at expert level. After your "
        "context is compacted or cleared, read it again."
    )


def build_instructions() -> str:
    systems = ", ".join(b["system"] for b in notes.briefings()) or "none yet"
    return (
        f"{core_order()}\n\n"
        "gnops-kb holds the Gemini RTSW team's shared knowledge. Treat it as a well-informed "
        "colleague's notes, not as current truth: before you repeat a fact the user will act on "
        "(fault status, current config, live values), check it at the source the note names (Jira, "
        "logs, PVs, the repo, the build); if the source disagrees, say so and propose a note update. "
        "Keep apart what you checked from what you only read.\n"
        f"Systems with a briefing (all included in the core): {systems}.\n"
        "Look things up, never guess: the core starts with how to find things (search_notes, get_note, "
        "list_notes, then the primary source). If something is not in the KB and you cannot check it, "
        "say so.\n"
        "When the user asks to distill, save or share what this session learned, call "
        "get_note(\"DISTILL.md\") and follow it; for their past sessions, get_note(\"DISTILL_ALL.md\"). "
        "Changes go through submit_notes and are reviewed before anyone sees them. When this "
        "session learned something that took real digging, or showed a note to be wrong, offer "
        "to distill before finishing."
    ).strip()


def reload() -> None:
    rebuild()
    mcp._lowlevel_server.instructions = build_instructions()


# The gate. Claude Code speaks the stateless MCP protocol (2026-07-28): no
# Mcp-Session-Id and nothing else that ties two calls to one conversation, so the
# server cannot remember who has read the core. Instead every part ends with one
# fragment of a key, and the other tools require the whole key: only a session
# that read every part to its end has it.
#
# The core is built once per refresh, not per call. Each build is a version (a
# hash of its text). get_core names the version in every part and serves later
# parts from it, so a note merged while a session reads the core does not mix two
# versions into a key nobody issued. A replaced version stays readable, and its
# key valid, for KEY_HOURS, so a merge does not lock out sessions mid-task.
_cores: dict[str, dict] = {}   # version -> {"parts", "key", "retired": time replaced or None}
_current = ""
_lock = threading.Lock()


def _fragments(version: str, n: int) -> list[str]:
    return [hashlib.sha256(f"{version}:{i}".encode()).hexdigest()[:4] for i in range(n)]


def rebuild() -> None:
    """Build the core from the checkout; called after every refresh."""
    global _current
    parts = notes.core_parts()
    version = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:8]
    now = time.time()
    with _lock:
        if version != _current:
            if _current:
                _cores[_current]["retired"] = now
            _cores[version] = {"parts": parts, "key": "-".join(_fragments(version, len(parts))),
                               "retired": None}
            _current = version
        for v in [v for v, c in _cores.items() if c["retired"] and now - c["retired"] > KEY_HOURS * 3600]:
            del _cores[v]


def _core(version: str = "") -> tuple[str, dict | None]:
    """The given version of the core (the current one when empty), or None once it has expired."""
    with _lock:
        v = version.strip().lower() or _current
        return v, _cores.get(v)


def _valid(key: str) -> bool:
    with _lock:
        return key.strip().lower() in {c["key"] for c in _cores.values()}


def _require_core(core_key: str) -> None:
    if _valid(core_key):
        return
    n = len(_core()[1]["parts"])
    raise ToolError(
        f"{'Wrong' if core_key else 'Missing'} core_key. Read the team core first: get_core(part=1) "
        f"through get_core(part={n}), each to its last line, and join the {n} key fragments with '-' "
        "in part order. It holds the working agreement and the knowledge this tool's answers depend on."
    )


# Usage, to see whether sessions look things up: one JSON line per tool call in
# $KB_DATA/usage.jsonl, and the most recent calls at GET /usage.
USAGE = notes.DATA / "usage.jsonl"
_usage: list[dict] = []


def _log(ctx: Context, tool: str, ok: bool = True, **args) -> None:
    meta = ctx.request_context.meta or {}
    client = (meta.get("io.modelcontextprotocol/clientInfo") or {}).get("name", "")
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "ok": ok, "client": client,
           "session": ((ctx.headers or {}).get("mcp-session-id") or "")[:8], **args}
    with _lock:
        _usage.append(rec)
        del _usage[:-1000]
    try:
        with open(USAGE, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _gate(ctx: Context, core_key: str, tool: str, **args) -> None:
    try:
        _require_core(core_key)
    except ToolError:
        _log(ctx, tool, ok=False, **args)
        raise
    _log(ctx, tool, **args)


KEY = Field("", description="the core key: the key fragments from the last line of each get_core part, joined with '-'")


@mcp.tool()
def get_core(ctx: Context, part: int = 1,
             version: str = Field("", description="the core version named by the previous part; empty for part 1")) -> str:
    """MANDATORY first call in every session: the team's core knowledge, in parts.

    Call part=1, then 2, ... through the last part, passing the version each part names, and
    read each to its end: its last line holds one fragment of the core key that every other
    tool requires. Holds how to find things, the working agreement (TEAM.md), general
    cross-system knowledge (core/), the system briefings, and an index of every detailed note.
    """
    _log(ctx, "get_core", part=part, version=version)
    v, core = _core(version)
    if core is None:
        raise ToolError(f"Core version {version} has expired. Start again: get_core(part=1) with no version.")
    parts = core["parts"]
    n = len(parts)
    if not 1 <= part <= n:
        raise ToolError(f"part must be 1-{n}")
    frag = _fragments(v, n)[part - 1]
    nxt = (f'Now call get_core(part={part + 1}, version="{v}").' if part < n else
           f"That was the last part. Your core key is the {n} fragments joined with '-' in part order "
           "(part 1's first); pass it as core_key to every other gnops-kb tool. Follow the working "
           "agreement for the rest of this session.")
    return (f"[gnops-kb core version {v}, part {part} of {n}]\n\n{parts[part - 1]}\n\n"
            f"[End of part {part} of {n}. Core key fragment {part}: {frag}. {nxt}]")


def _briefing(system: str) -> str:
    text = notes.read(f"briefings/{system}.md")
    if text is None:
        known = ", ".join(b["system"] for b in notes.briefings()) or "none yet"
        raise ToolError(f"no briefing for {system!r}; known: {known}")
    return text


@mcp.tool()
def list_briefings(ctx: Context, core_key: str = KEY) -> list[dict]:
    """List the systems that have a briefing, with the folders each one covers."""
    _gate(ctx, core_key, "list_briefings")
    return notes.briefings()


@mcp.tool()
def get_briefing(ctx: Context, system: str, core_key: str = KEY) -> str:
    """The full briefing for one system (e.g. "ted"): state, hosts, procedures, gotchas, open work.

    The core includes every briefing, in full or as a summary; use this for the full text.
    """
    _gate(ctx, core_key, "get_briefing", system=system)
    return _briefing(system)


@mcp.tool()
def search_notes(ctx: Context, query: str, core_key: str = KEY, limit: int = 10) -> list[dict]:
    """Search all notes for words: ranked by how many words a file contains, title and description first.

    Use several terms (names, PVs, hosts, GNFR/REL keys, error text). Returns path, title,
    description and matching lines; read the whole file with get_note(path).
    """
    _gate(ctx, core_key, "search_notes", query=query)
    return notes.search(query, max(1, min(limit, 50)))


@mcp.tool()
def list_notes(ctx: Context, system: str, core_key: str = KEY) -> list[dict]:
    """The detailed notes for one system (notes/<system>/), with title and one-line description."""
    _gate(ctx, core_key, "list_notes", system=system)
    found = notes.note_index(system)
    if not found:
        known = sorted({n["system"] for n in notes.note_index()})
        raise ToolError(f"no notes for {system!r}; systems with notes: {', '.join(known)}")
    return found


@mcp.tool()
def get_note(ctx: Context, path: str, core_key: str = KEY) -> str:
    """Read one file from the notes repo by path, e.g. "notes/ted/grafana.md"."""
    _gate(ctx, core_key, "get_note", path=path)
    text = notes.read(path)
    if text is None:
        raise ToolError(f"no such note: {path}")
    return text


class Change(BaseModel):
    path: str = Field(description='"core/<name>.md" (general, cross-system), "briefings/<system>.md" '
                                  'or "notes/<system>/<name>.md", lowercase')
    content: str = Field(description="the complete new file content (not a diff)")


@mcp.tool()
async def submit_notes(ctx: Context, title: str, summary: str, author: str, changes: list[Change],
                       core_key: str = KEY) -> dict:
    """Propose new or updated notes. They become a pull request for review; nothing is published directly.

    Follow DISTILL.md (get_note("DISTILL.md")). Write for a new session that has to work
    at expert level with no history: for conclusions and facts someone will act on, give
    the date, the source (GNFR key, PV, log query, file:line, person), the confidence
    (seen in data / inferred / reported by someone / guess), the reasoning, and the
    command that re-checks it. For open faults also give suspects, what's ruled out and
    why, the next step and its owner, and changes made with before and after values.
    Keep the briefing short (current state plus pointers); put the detail in notes/.

    Every core/ and notes/ file starts with a header (--- / title: / description: / ---);
    the description is one line saying what the file answers, and it is what sessions
    see in the index, so write it for someone deciding whether to open the file.

    Each change is the complete new content of one file. Read the current file
    first (get_briefing / get_note) and merge into it rather than replacing it.
    author is the person whose session this came from (name or email).
    No passwords, tokens or keys: they are rejected.
    """
    _gate(ctx, core_key, "submit_notes", title=title)
    items = [c.model_dump() for c in changes]
    errs = notes.check(items)
    if errs:
        raise ToolError("not submitted:\n" + "\n".join(errs[:30]))
    return await asyncio.to_thread(notes.submit, title, summary, author, items)


@mcp.prompt()
def brief(system: str) -> str:
    """Load a system's briefing into this session."""
    return (f"Here is the team briefing for {system}. Treat it as a well-informed colleague's notes, "
            f"not as current truth: check facts the user will act on at their source. Reply only with a one-line acknowledgement.\n\n{_briefing(system)}")


@mcp.prompt()
def distill() -> str:
    """Distill what this session learned into the team notes and submit it for review."""
    return notes.read("DISTILL.md") or "DISTILL.md is missing from the notes repo."


@mcp.prompt()
def distill_all() -> str:
    """Distill this user's past local Claude sessions into the team notes."""
    return notes.read("DISTILL_ALL.md") or "DISTILL_ALL.md is missing from the notes repo."


@mcp.custom_route("/start", methods=["GET"])
async def start(request: Request) -> Response:
    text = core_order()
    system = notes.match_folder(request.query_params.get("cwd", ""))
    if system:
        text += f"\n\nThis folder belongs to {system}; its briefing is in the core."
    return PlainTextResponse(text)


@mcp.custom_route("/usage", methods=["GET"])
async def usage(request: Request) -> Response:
    """The most recent tool calls, newest first: do sessions read the core, search, open notes?

    ok=false is a call refused for a missing or wrong core key.
    """
    with _lock:
        rows = _usage[::-1][:int(request.query_params.get("n", "200"))]
    return JSONResponse(rows)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"ok": True, "briefings": [b["system"] for b in notes.briefings()]})


def refresher() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        try:
            notes.refresh()
        except Exception as e:  # keep serving the last good checkout
            print(f"refresh failed: {e}", flush=True)
        reload()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8002")))
    args = parser.parse_args()

    notes.init()
    notes.refresh()
    reload()
    threading.Thread(target=refresher, daemon=True).start()

    if not args.http:
        mcp.run()
    else:
        # Only accept requests addressed to this machine by name (DNS-rebinding guard).
        names = [socket.getfqdn(), socket.gethostname(), "localhost", "127.0.0.1"]
        names += os.environ.get("MCP_ALLOWED_HOSTS", "").split()
        names = list(dict.fromkeys(n for n in names if n))
        security = TransportSecuritySettings(
            allowed_hosts=[f"{n}:*" for n in names],
            allowed_origins=[f"http://{n}:*" for n in names],
        )
        # Clients on the older, stateful protocol keep an idle session a day instead of 30 min.
        mcp.run("streamable-http", host=args.host, port=args.port, transport_security=security,
                session_idle_timeout=24 * 3600)
