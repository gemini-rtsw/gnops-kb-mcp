# gnops-kb-mcp

[MCP](https://modelcontextprotocol.io) server that gives every Claude session
the Gemini RTSW team's shared notes, and lets sessions propose updates to them.
Shipped as a container and installed by RPM (gemini-rtsw-ci type C).

The notes are markdown in a git repo (`NOTES_REMOTE`). The server reads the
merged branch; sessions can only propose changes, as pull requests that a
person reviews and merges. See `src/seed/` for the layout and the distill
instructions a new notes repo starts with.

| MCP | What it does |
|---|---|
| server instructions | a mandatory order to read the core first, then the list of systems; sent to every session at connect, rebuilt after each refresh. Kept well under the ~4k characters Claude Code shows |
| `get_core(part)` | the core every session reads before it starts: `TEAM.md`, `core/*.md` (general cross-system knowledge), every briefing in full, and an index of every note; split into parts of `CORE_PART_CHARS` (60k chars, under Claude Code's 25k-token tool result cap). Until a session has read every part, the other tools refuse and name the next part |
| `list_briefings`, `get_briefing` | per-system briefings (`briefings/<system>.md`); the core carries them in full while the core fits `CORE_MAX_CHARS` (200k chars), else as summaries |
| `list_notes`, `search_notes`, `get_note` | the detailed notes (`notes/<system>/*.md`): one system's list with descriptions, ranked word search (title and description hits first), one file |
| `submit_notes` | propose new or updated files: a branch plus a pull request; passwords and tokens are rejected, and core/ and notes/ files need a `title:`/`description:` header (the description is what the index shows) |
| prompt `brief` | `/mcp__gnops-kb__brief ted` loads a briefing into the session |
| prompt `distill` | `/mcp__gnops-kb__distill` distills the current session and submits it |
| prompt `distill_all` | `/mcp__gnops-kb__distill_all` distills your past local sessions |

A session is the MCP session (`Mcp-Session-Id`); idle sessions are kept for
`SESSION_IDLE_HOURS` (24) so a chat that sits for a while doesn't have to read
the core again.

Plain HTTP: `GET /start?cwd=<dir>` returns the order to read the core and names
the folder's system (for the optional hook below), `GET /usage` (per session:
core read, searches, notes opened; every call is also appended to
`$KB_DATA/usage.jsonl`), `GET /health`.

## Install

```
sudo dnf install gnops-kb-mcp
sudo vi /etc/sysconfig/gnops-kb-mcp      # NOTES_REMOTE, GITHUB_TOKEN, port
sudo systemctl enable --now gnops-kb-mcp
```

The unit runs `ghcr.io/gemini-rtsw/gnops-kb-mcp:<version>-git<hash>` with
`--read-only`; the notes checkout lives in `/var/lib/gnops-kb-mcp`.

## Connect (each person, once)

```
claude mcp add --scope user --transport http gnops-kb http://<host>:8002/mcp
```

Optional, to repeat the order to read the core when a session starts, resumes
or compacts: add to `~/.claude/settings.json`

```json
"hooks": {
  "SessionStart": [{
    "matcher": "startup|resume|clear|compact",
    "hooks": [{"type": "command",
               "command": "curl -sfG -m 3 --data-urlencode \"cwd=$PWD\" http://<host>:8002/start || true"}]
  }]
}
```

## Develop

```
docker build -t gnops-kb-mcp:dev .
mkdir -p /tmp/kb && chmod 777 /tmp/kb
docker run --rm --network host -v /tmp/kb:/data gnops-kb-mcp:dev   # local-only notes repo
python test_client.py http://localhost:8002/mcp
./gemini-rtsw-ci/build_rpm.sh --profile lightweight --el 9
```
