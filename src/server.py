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
                 edits the served notes). Until a session has read every part
                 of the core, the other tools refuse and say which part is next.
  prompts        brief(system), distill, distill_all

Plain HTTP, for a SessionStart hook:
  GET /start?cwd=<dir>   the order to read the core, plus the briefing for that
                         folder when one matches
  GET /usage             per-session tool use (did sessions read the core, search, open notes)
  GET /health

Run:
  python server.py          stdio
  python server.py --http   Streamable HTTP on $MCP_HOST:$MCP_PORT (default
                            0.0.0.0:8002), endpoint /mcp; this is how the
                            container runs
"""

import argparse
import asyncio
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
# The SDK drops an idle HTTP session after 30 minutes, and a reconnect would make
# the session read the core again. Claude sessions sit idle for hours.
SESSION_IDLE_SECONDS = float(os.environ.get("SESSION_IDLE_HOURS", "24")) * 3600

mcp = MCPServer("gnops-kb")


def core_order() -> str:
    parts = notes.core_parts()
    size = sum(len(p) for p in parts) // 4000 + 1
    return (
        f"MANDATORY, before your first reply and before any other tool call: call gnops-kb "
        f"get_core(part=1), then get_core(part=2) and so on until it says the core is complete "
        f"({len(parts)} parts, about {size}k tokens). Do this in every session, even when the task "
        "looks small or unrelated: the core is the team's working agreement (what you may and may "
        "not do on production, and how the user works with you) and the knowledge you need to work "
        "at expert level. The other gnops-kb tools refuse until you have read it. After your context "
        "is compacted or cleared, read it again."
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
    mcp._lowlevel_server.instructions = build_instructions()


# Parts of the core each session has read: {session key: (parts, last seen)}.
# A new session (reconnect, new chat) reads it again.
_core_read: dict[str, tuple[set[int], float]] = {}
_core_lock = threading.Lock()
DONE = 0   # in a session's set once it has read every part


def _session_key(ctx: Context) -> str:
    """The Mcp-Session-Id over HTTP (the SDK hands each request a new session object); stdio has one session."""
    return (ctx.headers or {}).get("mcp-session-id") or "stdio"


def _seen(ctx: Context) -> set[int]:
    now = time.time()
    with _core_lock:
        for k in [k for k, (_, t) in _core_read.items() if now - t > SESSION_IDLE_SECONDS]:
            del _core_read[k]
        parts, _ = _core_read.get(_session_key(ctx), (set(), now))
        _core_read[_session_key(ctx)] = (parts, now)
        return parts


# Usage, to see whether sessions look things up: one JSON line per tool call in
# $KB_DATA/usage.jsonl, and a per-session summary at GET /usage.
USAGE = notes.DATA / "usage.jsonl"
_usage: dict[str, dict] = {}


def _log(ctx: Context, tool: str, **args) -> None:
    key = _session_key(ctx)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _core_lock:
        u = _usage.setdefault(key, {"session": key[:8], "first": now, "calls": {}, "searches": [], "read": []})
        u["last"] = now
        u["calls"][tool] = u["calls"].get(tool, 0) + 1
        if tool == "search_notes":
            u["searches"].append(args.get("query", ""))
        elif tool in ("get_note", "get_briefing", "list_notes"):
            u["read"].append(str(next(iter(args.values()), "")))
        while len(_usage) > 500:   # oldest sessions first
            del _usage[next(iter(_usage))]
    try:
        with open(USAGE, "a") as f:
            f.write(json.dumps({"t": now, "session": key[:8], "tool": tool, **args}) + "\n")
    except OSError:
        pass


def _require_core(ctx: Context) -> None:
    seen = _seen(ctx)
    if DONE in seen:   # a refresh may change the part count later; finished stays finished
        return
    n = len(notes.core_parts())
    nxt = next((i for i in range(1, n + 1) if i not in seen), n)
    raise ToolError(
        f"Read the team core first: call get_core(part={nxt}) and continue through part {n}. "
        "It holds the working agreement and the knowledge this tool's answers depend on."
    )


@mcp.tool()
def get_core(ctx: Context, part: int = 1) -> str:
    """MANDATORY first call in every session: the team's core knowledge, in parts.

    Start with part=1 and call each next part until the result says the core is complete.
    Holds how to find things, the working agreement (TEAM.md), general cross-system
    knowledge (core/), the system briefings, and an index of every detailed note. The
    other tools refuse until every part has been read.
    """
    _log(ctx, "get_core", part=part)
    parts = notes.core_parts()
    n = len(parts)
    if not 1 <= part <= n:
        raise ToolError(f"part must be 1-{n}")
    seen = _seen(ctx)
    seen.add(part)
    missing = [i for i in range(1, n + 1) if i not in seen]
    if not missing:
        seen.add(DONE)
        tail = ("The core is complete. Follow the working agreement for the rest of this session. "
                "Use get_note(path) for any note in the index that bears on the task.")
    else:
        tail = f"Not complete yet: call get_core(part={missing[0]}) now."
    return f"[gnops-kb core, part {part} of {n}]\n\n{parts[part - 1]}\n\n[{tail}]"


def _briefing(system: str) -> str:
    text = notes.read(f"briefings/{system}.md")
    if text is None:
        known = ", ".join(b["system"] for b in notes.briefings()) or "none yet"
        raise ToolError(f"no briefing for {system!r}; known: {known}")
    return text


@mcp.tool()
def list_briefings(ctx: Context) -> list[dict]:
    """List the systems that have a briefing, with the folders each one covers."""
    _require_core(ctx)
    _log(ctx, "list_briefings")
    return notes.briefings()


@mcp.tool()
def get_briefing(ctx: Context, system: str) -> str:
    """The full briefing for one system (e.g. "ted"): state, hosts, procedures, gotchas, open work.

    The core includes every briefing, in full or as a summary; use this for the full text.
    """
    _require_core(ctx)
    _log(ctx, "get_briefing", system=system)
    return _briefing(system)


@mcp.tool()
def search_notes(ctx: Context, query: str, limit: int = 10) -> list[dict]:
    """Search all notes for words: ranked by how many words a file contains, title and description first.

    Use several terms (names, PVs, hosts, GNFR/REL keys, error text). Returns path, title,
    description and matching lines; read the whole file with get_note(path).
    """
    _require_core(ctx)
    _log(ctx, "search_notes", query=query)
    return notes.search(query, max(1, min(limit, 50)))


@mcp.tool()
def list_notes(ctx: Context, system: str) -> list[dict]:
    """The detailed notes for one system (notes/<system>/), with title and one-line description."""
    _require_core(ctx)
    _log(ctx, "list_notes", system=system)
    found = notes.note_index(system)
    if not found:
        known = sorted({n["system"] for n in notes.note_index()})
        raise ToolError(f"no notes for {system!r}; systems with notes: {', '.join(known)}")
    return found


@mcp.tool()
def get_note(ctx: Context, path: str) -> str:
    """Read one file from the notes repo by path, e.g. "notes/ted/grafana.md"."""
    _require_core(ctx)
    _log(ctx, "get_note", path=path)
    text = notes.read(path)
    if text is None:
        raise ToolError(f"no such note: {path}")
    return text


class Change(BaseModel):
    path: str = Field(description='"core/<name>.md" (general, cross-system), "briefings/<system>.md" '
                                  'or "notes/<system>/<name>.md", lowercase')
    content: str = Field(description="the complete new file content (not a diff)")


@mcp.tool()
async def submit_notes(ctx: Context, title: str, summary: str, author: str, changes: list[Change]) -> dict:
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
    _require_core(ctx)
    _log(ctx, "submit_notes", title=title)
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
    """Per-session tool use, newest first: did sessions read the core, search, open notes?"""
    with _core_lock:
        rows = [{**u, "core_done": DONE in _core_read.get(k, (set(), 0))[0]} for k, u in _usage.items()]
    return JSONResponse(rows[::-1])


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
        mcp.run("streamable-http", host=args.host, port=args.port, transport_security=security,
                session_idle_timeout=SESSION_IDLE_SECONDS)
