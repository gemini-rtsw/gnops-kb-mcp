Distill what this session learned into the team notes, then submit it for review.

Goal: a fresh session that loads the briefing should start where this session
is now, without re-learning anything.

1. Call list_briefings. For each system this session worked on, call
   get_briefing(system) (if it exists) and search_notes for related notes.
2. Write the updated files. Merge into what is there; don't duplicate it.
   Remove or correct anything this session showed to be wrong.
   - briefings/<system>.md: at most about 150 lines, with this header and sections:
     ```
     ---
     title: <System name>
     description: <one line: what it is, main parts>
     folders: <local folder names that mean this system, comma separated>
     ---
     ## What it is
     ## Hosts and access
     ## Repos and branches
     ## How it works
     ## Procedures
     ## Gotchas
     ## Decisions (with why)
     ## Open work (with dates)
     ```
   - notes/<system>/<topic>.md: detail that doesn't fit in the briefing
     (a full procedure, an investigation, a reference table).
3. Keep only lasting facts: hosts, paths, commands that worked, causes found,
   decisions and why, open work. Use absolute dates (YYYY-MM-DD).
   Leave out: passwords, tokens and keys (say where they are stored instead),
   dead ends (unless the lesson is "don't do X because Y"), chat, and
   anything about the people's moods.
4. Call submit_notes once, with every file changed, a short title, a summary
   of what changed and why, and author set to the user's name or email.
5. Tell the user the pull request link in one line.
