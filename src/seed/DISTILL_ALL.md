Distill this user's past local Claude sessions into the team notes.

1. List the sessions: `~/.claude/projects/*/*.jsonl`. Skip any session id
   already listed in `~/.claude/gnops-kb-distilled.txt`. Show the user the
   list (date, folder, first prompt, size) and ask which to include.
2. For each chosen session, use a subagent so the transcript stays out of
   your context. It should read only the useful parts, with this filter:
   ```
   python3 -c 'import json,sys
   for l in open(sys.argv[1]):
       o=json.loads(l); m=o.get("message",{}); c=m.get("content")
       if o.get("isCompactSummary") or o.get("type") in ("user","assistant"):
           if isinstance(c,str): print(m.get("role"),":",c[:4000])
           elif isinstance(c,list):
               for b in c:
                   if b.get("type")=="text": print(m.get("role"),":",b["text"][:4000])' FILE
   ```
   and return the lasting facts per system, following the rules in the
   distill prompt (no secrets, no dead ends, absolute dates). Compaction
   summaries are the best source.
3. Group the facts by system. For each system: get_briefing and search_notes,
   merge, and call submit_notes once per system.
4. Append the session ids you submitted to `~/.claude/gnops-kb-distilled.txt`.
5. Give the user the pull request links.
