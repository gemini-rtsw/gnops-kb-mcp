Distill this user's past local Claude sessions into the team notes.

Prefer asking each session to distill itself: a live session still holds
the tool output (file contents, command results) where the real detail is,
and can re-open its sources. Transcript extracts keep only the chat text and
produce thin notes. Use the subagent steps below only for sessions that are
no longer running, and hold them to the same standard in DISTILL.md.

1. List the sessions: `~/.claude/projects/*/*.jsonl`. Skip any session id
   already listed in `~/.claude/gnops-kb-distilled.txt` unless the user asks
   for a deeper pass. Show the user the list (date, folder, first prompt,
   size, still running or not: `pgrep -af <id>`) and ask which to include.
2. For a running session: ask the user to say "remember this" in it (or send
   it that request), so it follows DISTILL.md itself.
3. For a finished session, use one subagent per session so the transcript
   stays out of your context. Give it DISTILL.md. It should read the whole
   transcript, including tool results (they hold the file contents and command
   output), then re-open the session's repos and files (read only) to verify
   and fill gaps. Compaction summaries say what mattered; tool results give
   the detail.
4. Group the results by system. For each system: get_briefing, read the
   existing notes, merge, and call submit_notes once per system.
5. Append the session ids you submitted to `~/.claude/gnops-kb-distilled.txt`.
6. Give the user the pull request links.
