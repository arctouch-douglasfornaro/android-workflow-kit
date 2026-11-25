# Notes for coding agents in this kit

## Rule: run the evals after changing an agent's instructions

A change to `skills/android-workflow/SKILL.md`, `skills/android-review/SKILL.md`, `skills/android-bug-triage/SKILL.md`,
`skills/android-workflow/agents/aw-*.md` or `skills/device-driving/SKILL.md` changes how the workflow
agents behave, and the unit tests do not see it. Before you commit such a change:

1. Run the evals of the agents it affects with the `eval-playbook` skill (`skills/eval-playbook/SKILL.md`):
   `python3 ~/.ai/bin/skill_eval.py list` shows which case covers which agent; an `aw-X.md` edit runs
   that agent's cases, a `SKILL.md` edit runs all of them.
2. Compare with the last score (`python3 ~/.ai/bin/skill_eval.py report`). A drop of 1 point or more, or a
   failed check, is a regression: fix the instructions (never the case) or tell the developer why the
   drop is expected. A smaller drop is noise until one rerun confirms it.
3. Put the scores and their deltas in your final message to the developer.

No case covers the agent you changed: say so in that message, and add a case when the change is about
behavior a case could check. Today every `aw-*` agent has a case (`aw-setup` and `aw-planner` also on the
contrasting `tasks-app`; `aw-planner` in `triage` mode for android-bug-triage; `aw-reviewer` in workflow and
`pr` mode; `aw-media` in `pr` mode; `aw-device` in `after` mode on a leased emulator, which also exercises
`device-driving`; `aw-pr-watcher` in `triage` mode), and `workflow-e2e` / `review-e2e` run the android-workflow
and android-review `SKILL.md` end to end. Not covered yet: `aw-media` mode `render`, `aw-tech-lead` mode
`integrate`, `aw-pr-watcher` modes `reply`/`review`, `aw-device` modes `before`/`repro` (`skills/eval-playbook/SKILL.md` → Add a case).

Edits to the Python CLI (`tools/android-workflow/`, `bin/*.py`) need their unit tests instead
(README → Tests), not the evals.
