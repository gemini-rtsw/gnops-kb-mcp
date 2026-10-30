## Team rules

- Commits: end with a plain `Co-Authored-By: Claude` line, with no email
  address. Never put an address (anything with `@`) in it: GitHub matches it to
  an account and counts that account as a repo contributor. No
  `Claude-Session:` trailer, and no "Generated with Claude Code" in pull
  requests. The person is the responsible author.
- Builds and hosting: use gemini-rtsw-ci and GitHub (github.com/gemini-rtsw)
  whenever possible: new and migrated packages build with gemini-rtsw-ci and
  publish to ghcr.io/gemini-rtsw/rpm-repo. Use GitLab, SVN or hand builds
  only where a system hasn't moved yet, and say so in its briefing.
- Replies: concise, one step at a time.
- Production systems: read freely; ask before any change, restart or install.
- Notes: the bar is expert level. A briefing is a map; the depth (architecture,
  code map, config, procedures, troubleshooting, history) goes in
  notes/<system>/. Follow DISTILL.md, and go back to the sources rather than
  writing from memory.
