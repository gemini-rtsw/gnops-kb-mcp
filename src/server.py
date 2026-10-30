"""gnops-kb: the team's shared knowledge for Claude sessions, served over MCP.

Every session that connects gets:
  instructions   TEAM.md plus a list of briefings and when to load them
                 (rebuilt after each refresh, so edits reach everyone)
  tools          list_briefings, get_briefing, search_notes, get_note,
                 submit_notes (proposes changes as a pull request; never
                 edits the served notes)
  prompts        brief(system), distill, distill_all

Plain HTTP, for a SessionStart hook:
  GET /start?cwd=<dir>   the briefing for that folder (204 when none matches)
  GET /health

Run:
  python server.py          stdio
  python server.py --http   Streamable HTTP on $MCP_HOST:$MCP_PORT (default
                            0.0.0.0:8002), endpoint /mcp; this is how the
                            container runs
"""

import argparse
import asyncio
import os
import socket
import threading
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

import notes

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))

mcp = MCPServer("gnops-kb")


def build_instructions() -> str:
    team = notes.read("TEAM.md") or ""
    rows = "\n".join(
        f"- {b['system']}: {b['description']} (folders: {', '.join(b['folders']) or b['system']})"
        for b in notes.briefings()
    )
    return (
        "gnops-kb holds the Gemini RTSW team's shared notes.\n"
        "Before working on one of these systems, call get_briefing(system) once and treat it as "
        "background you already know. Use search_notes for details not in the briefing.\n"
        f"{rows or '- (no briefings yet)'}\n"
        "When the user asks to distill, save or share what this session learned, call "
        "get_note(\"DISTILL.md\") and follow it; for their past sessions, get_note(\"DISTILL_ALL.md\"). "
        "Changes go through submit_notes and are reviewed before anyone sees them.\n\n"
        f"{team}"
    ).strip()


def reload() -> None:
    mcp._lowlevel_server.instructions = build_instructions()


def _briefing(system: str) -> str:
    text = notes.read(f"briefings/{system}.md")
    if text is None:
        known = ", ".join(b["system"] for b in notes.briefings()) or "none yet"
        raise ToolError(f"no briefing for {system!r}; known: {known}")
    return text


@mcp.tool()
def list_briefings() -> list[dict]:
    """List the systems that have a briefing, with the folders each one covers."""
    return notes.briefings()


@mcp.tool()
def get_briefing(system: str) -> str:
    """The full briefing for one system (e.g. "ted"): state, hosts, procedures, gotchas, open work.

    Load it once at the start of work on that system.
    """
    return _briefing(system)


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> list[dict]:
    """Search all briefings and notes for words; returns paths and the matching lines.

    Follow up with get_note(path) to read a whole note.
    """
    return notes.search(query, max(1, min(limit, 50)))


@mcp.tool()
def get_note(path: str) -> str:
    """Read one file from the notes repo by path, e.g. "notes/ted/grafana.md"."""
    text = notes.read(path)
    if text is None:
        raise ToolError(f"no such note: {path}")
    return text


class Change(BaseModel):
    path: str = Field(description='"briefings/<system>.md" or "notes/<system>/<name>.md", lowercase')
    content: str = Field(description="the complete new file content (not a diff)")


@mcp.tool()
async def submit_notes(title: str, summary: str, author: str, changes: list[Change]) -> dict:
    """Propose new or updated notes. They become a pull request for review; nothing is published directly.

    Each change is the complete new content of one file. Read the current file
    first (get_briefing / get_note) and merge into it rather than replacing it.
    author is the person whose session this came from (name or email).
    No passwords, tokens or keys: they are rejected.
    """
    items = [c.model_dump() for c in changes]
    errs = notes.check(items)
    if errs:
        raise ToolError("not submitted:\n" + "\n".join(errs[:30]))
    return await asyncio.to_thread(notes.submit, title, summary, author, items)


@mcp.prompt()
def brief(system: str) -> str:
    """Load a system's briefing into this session."""
    return (f"Here is the team briefing for {system}. Treat it as background you already know; "
            f"reply only with a one-line acknowledgement.\n\n{_briefing(system)}")


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
    system = notes.match_folder(request.query_params.get("cwd", ""))
    if not system:
        return Response(status_code=204)
    return PlainTextResponse(f"Team briefing for {system} (from gnops-kb):\n\n{_briefing(system)}")


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
        mcp.run("streamable-http", host=args.host, port=args.port, transport_security=security)
