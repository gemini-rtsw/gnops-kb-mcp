Distill what this session learned into the team notes, then submit it for review.

## The standard: expert level

A fresh session that reads the briefing and its notes must be able to work on
the system like the team's expert: explain how it works, find any file,
run every procedure, and diagnose its known failures, without re-reading the
code or asking anyone. Short summaries are not enough. If a reader would have
to rediscover it, write it down.

Test before submitting: for each system, could a new session, with only these
notes, (a) answer "how does X work and where is it?", (b) build, deploy and
roll back, (c) fix the last three problems this session saw, (d) tell which
facts were measured and which were inferred, and re-check the important ones?
If not, add what is missing.

## Steps

1. Call list_briefings. For each system this session worked on, call
   get_briefing(system) and read its existing notes (search_notes, get_note).
2. Go back to the sources. Don't write from memory alone: re-open the repo,
   specs, configs, Makefiles, startup scripts, logs and hosts this session
   used (read only) to check facts and fill gaps: exact paths, PV and record
   names, versions, commit hashes, command output. Your conversation may have
   been compacted; the sources are still there.
3. Write the files. Merge into what is there; don't duplicate it. Correct or
   remove anything shown to be wrong, and say what changed.
4. Call submit_notes once, with every file changed, a short title, a summary
   of what changed and why, and author set to the user's name or email.
5. Tell the user the pull request link in one line.

## The files

### briefings/<system>.md: the map (at most about 150 lines)

Loaded into sessions at start, so it stays short and points to the depth.

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
## Notes index
- notes/<system>/<topic>.md: <one line on what it answers>
```

### notes/<system>/<topic>.md: the depth (no length limit)

Write as many as the system needs, one topic each. The names below are
suggestions: you know the system best, so choose the names and structure that
fit it, as long as the briefing's Notes index lists every note. Aim to cover:

- **architecture.md**: components, processes, hosts, network paths and ports,
  data flow from input to output, how the parts talk (CA, sockets, files,
  HTTP), timing and sequencing, state machines. ASCII diagrams welcome.
- **code-map.md**: repo layout, directory by directory and key file by file:
  what each does, entry points, where the important logic lives
  (`path:function`), generated vs hand-written files, build system.
- **config-reference.md**: every config file, env var, startup script,
  boot parameter and database (.db/.substitutions) that matters, with path,
  meaning and current values (secrets: say where they are, never the value).
- **interfaces.md**: PVs/records (names, types, meaning, who reads/writes
  them), APIs, commands, file formats, message protocols.
- **build-deploy.md**: full build, package, install, upgrade, rollback and
  verify procedures, copy-pasteable, with the expected output and how to tell
  it worked.
- **operations.md**: start/stop/restart, health checks, log locations and
  what normal looks like, routine tasks.
- **troubleshooting.md**: playbook of symptom -> how to confirm -> cause ->
  fix, for every problem seen, with the exact error text so it can be found
  by search. Include investigations: what was ruled out and how.
- **history.md**: dated timeline of changes, migrations and incidents, with
  commits, PRs, tickets (Jira/GNFR) and who decided what.
- **reference-<topic>.md**: tables, pinouts, register maps, version matrices,
  links to external docs and Confluence pages, glossary of the system's terms.
- **people.md** (or a section in the briefing): who owns, maintains, reports
  on and decides about the system, and what each person knows.
- **faults/GNFR-xxxxx.md**: one per open or significant fault, for example:
  ```
  # GNFR-xxxxx: <title>
  Status: <Jira status> (checked against Jira YYYY-MM-DD)
  Re-check: <tool call or command that shows the current status>
  ## Symptom      (when, what was seen, impact)
  ## Evidence     (each item: what, source, date, confidence)
  ## Reasoning    (how the evidence leads to the current conclusion)
  ## Suspects     (still open)
  ## Ruled out    (each with why)
  ## Changes made (what, when, who, before -> after values)
  ## Next step    (what, who owns it, what it's waiting on)
  ## Loose ends   (gaps, unexplained data, unanswered Jira comments)
  ```

Skip a topic only if it truly doesn't apply.

## Rules

- Be specific: full paths, hostnames, exact commands, exact error messages,
  versions, commit hashes, PV names, numbers with units. "Configure the
  IOC" is useless; the file, the line and the value are useful.
- Explain why, not just what: the reason behind a design, a workaround or a
  decision is what lets a reader handle the next case.
- Keep failed approaches when the lesson is "don't do X because Y".
- Absolute dates (YYYY-MM-DD). Mark facts that may go stale with
  "(as of YYYY-MM-DD)" and unverified ones with "(unverified)".
- Provenance for conclusions and for facts someone will act on (fault status,
  current config, live values, causes): the date, the source (GNFR key, PV,
  log query, file:line, person), the confidence (seen in data / inferred /
  reported by <person> / guess), the reasoning that leads from evidence to
  conclusion, and the exact command or tool call that re-checks it. Don't
  tag every line; plain reference facts (paths, file layout) don't need it.
- Name the people involved and their roles.
- Never include passwords, tokens or keys; say where they are stored.
- Leave out chat and anything about people's moods.
