---
name: quizlet-standup
description: Write the developer's Quizlet standup from their own GitHub and Slack activity — the last working day (default) or the week. Use for /quizlet-standup, "my standup", "what did I do yesterday", "weekly summary" or "resumo da semana".
---

# Quizlet Standup

Turn the developer's own activity into a standup they can paste into Slack. Read-only: never post,
comment, react or change anything anywhere.

## Input

`$ARGUMENTS` (or the user's words when the host has no arguments):

- `day` (default) — since the previous working day (Friday on a Monday).
- `week` — the last 7 days.
- `since YYYY-MM-DD` — a custom start.
- `pt` — write in Portuguese. The default is English, since the standup is posted at Quizlet.
- `dms` — also search the developer's DMs and group DMs on Slack. Off by default.

## 1. GitHub (every host)

Run, with the period from the input:

```bash
python3 ~/.ai/skills/quizlet-standup/scripts/github_activity.py --period day   # or --period week / --since YYYY-MM-DD
```

It prints JSON:
- `authored`: the developer's PRs in the `quizlet` org updated in the window. Each one has `state`, `merged`, `checks` and `review_decision` (open ones), and `reviewers`.
- `reviewed`: other people's PRs the developer reviewed in the window, with `my_reviews`.

On `error`, tell the user what failed (usually `gh auth status`, or the active `gh` account has no
access to the org) and continue with Slack only.

## 2. Slack (when the host has a Slack tool)

Use whatever Slack search tool this host offers (for example a Slack connector or MCP server with
a message search). Search the developer's own messages in the window:
`from:me after:<the day before since>`; Slack's `after:` excludes the day it names. Use the
current user's ID (`from:<@U…>`) when the tool gives one and `from:me` doesn't work. Search
**channels only** (public and private, no DMs or group DMs). DMs are mostly chat and stay private;
search them only when the input says `dms`. Read the threads behind the messages that look like
work: decisions, questions asked, help given, incidents, releases and announcements. Skip social
chatter.

If this host has no Slack tool, say so in one line ("Slack not connected here: GitHub only") and
go on. Never ask the user for a Slack token.

Privacy: summarize what the developer did. Never quote other people, private channels or DMs
verbatim, and leave out anyone's personal details. Name a teammate only when the work needs it
(e.g. "paired with the iOS dev on X").

## 3. Write the standup

Group the work by ticket (`LEARN-1234` from the PR title or Slack), not by source, and merge a PR
and its Slack thread into one line. Only count a PR as done if it was merged on or after `since`.

```text
*Yesterday* (or *This week*)
• LEARN-4008 Arrival source from share/Classroom deep links — merged
• LEARN-3578 Question-level SDUI events — PR open, waiting for review
• Reviewed 8 PRs (set page redesign LEARN-4010, LEARN-4005, …)
• <Slack: a decision, a question answered, an incident>

*Today*
• <next step for each open PR: address review, fix CI, get it reviewed; the in-progress tickets>

*Blockers*
• <a PR with failing checks, changes requested, or waiting for review more than 1 working day; else "None">
```

Rules:
- One line per item, ≤ 15 words, and the outcome before the detail. Link PRs only in the `week`
  version, where the list is longer.
- **Today:** infer it from open PRs and threads that are still open. Mark it as a guess
  ("probably") when nothing says it. The developer edits it before posting.
- **Week:** the same sections, with a one-line headline first (the main thing shipped) and
  a count of merged PRs and reviews.
- Never invent work. A day with little activity gets a short standup.

Output only the standup. Under it, add one line saying which sources were used (GitHub, Slack)
and which were missing.
