#!/bin/bash
# gnops-kb one-line installer for a Mac or Linux machine with Docker.
#
#   curl -fsSL https://raw.githubusercontent.com/gemini-rtsw/gnops-kb-mcp/main/install.sh | bash
#
# Writes the settings to ~/.gnops-kb, starts the gnops-kb-mcp container (restarts with
# Docker), and registers it with Claude Code. Re-run it to upgrade: settings and the
# token are kept. For a shared server use the RPM instead (README, "Install").
#
# Needs: Docker running; a GitHub account with access to gemini-rtsw; two tokens:
#   - GHCR (classic token, read:packages), for `docker login ghcr.io`, to pull the image
#   - notes (fine-grained, owner gemini-rtsw, repo gnops-kb-notes only,
#     Contents + Pull requests read/write), asked for below and stored in the env file
#
# Overrides (environment): GNOPS_KB_IMAGE, GNOPS_KB_PORT (8002), GNOPS_KB_DIR (~/.gnops-kb),
# GNOPS_KB_NAME (container, gnops-kb-mcp), GITHUB_TOKEN (skips the prompt).
# Written for bash 3.2 (macOS).
set -euo pipefail

IMAGE="${GNOPS_KB_IMAGE:-ghcr.io/gemini-rtsw/gnops-kb-mcp:latest}"
PORT="${GNOPS_KB_PORT:-8002}"
DIR="${GNOPS_KB_DIR:-$HOME/.gnops-kb}"
NAME="${GNOPS_KB_NAME:-gnops-kb-mcp}"
ENV_FILE="$DIR/gnops-kb.env"
URL="http://localhost:$PORT/mcp"

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Docker
command -v docker >/dev/null || die "Docker is not installed (Mac: Docker Desktop)."
docker info >/dev/null 2>&1 || die "Docker is not running, or this user can't use it. Start Docker and re-run."

# 2. Image (private on GHCR)
say "Pulling $IMAGE"
if ! docker pull --platform linux/amd64 "$IMAGE" >/dev/null; then
    die "Could not pull $IMAGE. Log in to GHCR with a classic token that has read:packages:
       docker login ghcr.io
   then re-run this installer."
fi

# 3. Settings (kept across re-runs)
mkdir -p "$DIR/data"
chmod 700 "$DIR"
chmod 777 "$DIR/data"     # the container runs as nobody and keeps the notes checkout here
token=""
[ -f "$ENV_FILE" ] && token="$(sed -n 's/^GITHUB_TOKEN=//p' "$ENV_FILE")"
[ -n "${GITHUB_TOKEN:-}" ] && token="$GITHUB_TOKEN"
if [ -z "$token" ]; then
    [ -r /dev/tty ] || die "No terminal to ask for the token; set GITHUB_TOKEN and re-run."
    echo "Notes token: fine-grained, owner gemini-rtsw, repository gnops-kb-notes only,"
    echo "permissions Contents and Pull requests read/write."
    printf 'Paste it (not shown): '
    read -rs token < /dev/tty
    echo
    [ -n "$token" ] || die "No token given."
fi
(
    umask 077
    cat > "$ENV_FILE" <<EOF
NOTES_REMOTE=https://github.com/gemini-rtsw/gnops-kb-notes.git
NOTES_BRANCH=main
GITHUB_TOKEN=$token
REFRESH_SECONDS=300
EOF
)
say "Settings in $ENV_FILE"

# 4. Container (replaced only now that the new image is here)
docker rm -f "$NAME" >/dev/null 2>&1 || true
say "Starting container $NAME on localhost:$PORT"
docker run -d --name "$NAME" --restart unless-stopped --platform linux/amd64 \
    -p "127.0.0.1:$PORT:8002" \
    --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
    --env-file "$ENV_FILE" -v "$DIR/data:/data" \
    "$IMAGE" >/dev/null

ok=""
for _ in $(seq 1 60); do
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
done
if [ -z "$ok" ]; then
    docker logs --tail 20 "$NAME" >&2 || true
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    rm -f "$ENV_FILE"
    die "The server did not come up (log above; a wrong, expired or unapproved token shows as
   a git clone error). Container and settings removed; re-run to enter the token again."
fi
say "Server up: $(curl -s "http://localhost:$PORT/health" | cut -c1-80)..."

# 5. Claude Code
if command -v claude >/dev/null; then
    if claude mcp get gnops-kb >/dev/null 2>&1; then
        say "Claude Code already has gnops-kb (check its URL is $URL with: claude mcp get gnops-kb)"
    else
        claude mcp add --scope user --transport http gnops-kb "$URL" && say "Added gnops-kb to Claude Code"
    fi
else
    say "Claude Code not found. When it is installed, run:"
    echo "    claude mcp add --scope user --transport http gnops-kb $URL"
fi

cat <<EOF

Done. Start a new Claude Code session to use gnops-kb.
  Logs:      docker logs $NAME
  Upgrade:   re-run the install line
  Remove:    docker rm -f $NAME; rm -rf $DIR; claude mcp remove gnops-kb -s user
EOF
