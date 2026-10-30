"""Smoke-test a running gnops-kb server: instructions, tools, prompts, a proposal.

  python test_client.py http://HOST:8002/mcp

Submits a small test proposal (a branch, or a pull request when the server has
a remote) and checks that a fake password is rejected.
Needs the mcp package (pip install -r requirements.txt).
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(s: ClientSession, tool: str, args: dict):
    res = await s.call_tool(tool, args)
    sc = res.structured_content
    if sc is not None:
        data = sc.get("result", sc)  # non-dict returns are wrapped as {"result": ...}
    else:
        data = res.content[0].text if res.content else ""
    print(f"{tool}({json.dumps(args)[:80]}) -> {'ERROR ' if res.is_error else ''}{json.dumps(data)[:400]}")
    return data


BRIEFING = """---
title: Test system
description: smoke-test briefing from test_client.py
folders: kbtest
---
## What it is
A briefing written by test_client.py.
"""


async def main(url: str):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("server:", init.server_info.name)
            print("instructions:\n" + (init.instructions or "")[:600] + "\n")
            print("tools:  ", [t.name for t in (await s.list_tools()).tools])
            print("prompts:", [p.name for p in (await s.list_prompts()).prompts])
            await call(s, "list_briefings", {})
            await call(s, "search_notes", {"query": "co-authored"})
            await call(s, "get_note", {"path": "TEAM.md"})
            await call(s, "submit_notes", {
                "title": "Test proposal", "summary": "from test_client.py", "author": "test",
                "changes": [{"path": "briefings/kbtest.md", "content": BRIEFING}]})
            await call(s, "submit_notes", {
                "title": "Must be rejected", "summary": "secret check", "author": "test",
                "changes": [{"path": "notes/kbtest/x.md", "content": "db password = hunter22\n"}]})
            p = await s.get_prompt("distill", {})
            print("distill prompt:", p.messages[0].content.text[:120].replace("\n", " "), "...")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
