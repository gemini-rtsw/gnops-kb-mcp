# gnops-kb-mcp: the team's shared notes for Claude sessions, served over MCP.
# The notes live in a git checkout on the /data volume.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KB_DATA=/data

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

# The unit runs the container --read-only; only /data and /tmp are writable.
USER nobody
EXPOSE 8002
CMD ["python", "server.py", "--http"]
