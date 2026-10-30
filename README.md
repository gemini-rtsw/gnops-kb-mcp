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
| server instructions | team rules (`TEAM.md`) and the list of briefings, sent to every session at connect; rebuilt after each refresh |
| `list_briefings`, `get_briefing` | per-system briefings (`briefings/<system>.md`) |
| `search_notes`, `get_note` | everything else (`notes/<system>/*.md`) |
| `submit_notes` | propose new or updated files: a branch plus a pull request; passwords and tokens are rejected |
| prompt `brief` | `/mcp__gnops-kb__brief ted` loads a briefing into the session |
| prompt `distill` | `/mcp__gnops-kb__distill` distills the current session and submits it |
| prompt `distill_all` | `/mcp__gnops-kb__distill_all` distills your past local sessions |

Plain HTTP: `GET /start?cwd=<dir>` returns the briefing for that folder (for
the optional hook below), `GET /health`.

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

Optional, to load the briefing automatically when a session starts or
compacts in a matching folder: add to `~/.claude/settings.json`

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
