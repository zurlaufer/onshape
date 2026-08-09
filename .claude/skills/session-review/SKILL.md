---
name: session-review
description: Review a real usage session of the FRC copilot and record what it taught us. Run after someone has used the tool for actual CAD work — reads the transcript, measures how the tools were used, and adds an entry to FINDINGS.md.
---

# Reviewing a usage session

A session where someone did real work is the only source of truth about whether
this tool is any good. One of them reshaped the entire tool surface. This is how
to get that value out of the next one.

**Do not ask the model what went wrong.** At the end of a session it has its own
failures in context, it will agree with whatever you suggest, and its account is
a story told afterwards rather than a log. Read the transcript.

## 1. Measure

```bash
./.venv/Scripts/python.exe scripts/analyse_session.py           # newest session
./.venv/Scripts/python.exe scripts/analyse_session.py <prefix>  # a specific one
```

Offline and free. It reports the tool-call mix, which of our modules were
imported directly, how much code the model wrote itself, and which tool results
reported a problem.

## 2. Read around anything surprising

The counts only say where to look. Open the transcript and read the turns
around each of these:

- **A tool called once and then never again.** Find what the model did in the
  next few calls. That is where a tool was judged and rejected — the most
  valuable few minutes in the file.
- **Our modules imported directly.** A module reached this way is one the tools
  do not expose well enough, or one that does not need a tool.
- **A long stretch of Write → Bash → Edit → Bash.** The model is iterating on
  its own code. Ask what it needed that we did not offer.
- **The user's own words**, verbatim. Vocabulary that does not match our
  parameter names is a finding, not a user error.

Transcripts live in `~/.claude/projects/c--Users-zurla-Desktop-onshape/*.jsonl`.

## 3. Write it down

Add an entry to `FINDINGS.md`, newest first:

- **The task**, in the user's words.
- **What the analyser measured** — a small table.
- **What it meant.** This is the part that matters and the part no script can
  produce. Quote timestamps where a decision is visible.
- **What changed** as a result — or explicitly *nothing, and why*. "No change"
  is a legitimate and useful entry.

Then update `STATUS.md` if project state changed, and `CLAUDE.md` if a fact
would have prevented what you just found. Keep `CLAUDE.md` under ~1,500 tokens:
a fact buried in a long file does not change behaviour, which is itself
something a session proved.

## What not to do

**Do not treat "MCP usage %" as a score.** If the model got further by writing
its own script, that is information about the tools, not a failure by the model.
The number is a prompt to ask why.

**Do not fix everything you find.** Pick what the evidence actually supports.
Three of this project's architectural decisions were wrong because a claim was
generalised from a single observation.
