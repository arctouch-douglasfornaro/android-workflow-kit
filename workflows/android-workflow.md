# Android workflow (host-driven T0–T9)

Tool-agnostic pipeline for one Android ticket. The **coding host** (Cursor, Claude Code,
Codex, Gemini CLI / Antigravity) orchestrates seven agents (`aw-setup`, `aw-planner`,
`aw-implementer`, `aw-tester`, `aw-reviewer`, `aw-device`, `aw-delivery`) that learn the
project, plan, **write the code**, test, review, verify on device and open the PR. The CLI at
`python3 ~/.ai/bin/android-workflow` detects, locates, gates, logs, and refuses `finish`
without a source diff.

This is a sibling of [`feature-workflow.md`](feature-workflow.md). Use this command when
the caller types `/android-workflow`. Use `/feature-workflow` when they want the profile,
snapshot, isolated reviewer, and ship helper already in this kit.

Index: [`../ANDROID.md`](../ANDROID.md). Higher-priority host security and approval rules win.

## Invocation

```text
/android-workflow <ticket-id> <title or description> [--target PATH] [--ticket FILE]
    [--level express|standard|full] [--worktree] [--no-device]
    [--no-commit] [--no-push] [--no-pr] [--base BRANCH]
```

Codex: `$android-workflow`. If the workspace is this toolkit (has `android_workflow/cli.py`)
and `--target` is missing, ask for the Android app path.

Do not invent acceptance criteria or business rules. Batch unanswered high-impact questions
and STOP.

Workspace is in-place by default. Without `--worktree`, keep the current branch when it is not
the detected base branch; when it is on the base branch (normally `main`), create or reuse the
ticket branch in the same checkout. Use `--worktree` only when the user explicitly requests an
isolated worktree.

## CLI

```sh
python3 ~/.ai/bin/feature_workspace.py --repo-root TARGET --ticket TICKET --title "..." [--worktree]
python3 ~/.ai/bin/android-workflow setup --target TARGET [--source-repo MAIN] [--accept] [--force]
python3 ~/.ai/bin/android-workflow start --target TARGET --id TICKET --title "..."
python3 ~/.ai/bin/android-workflow prebuild --target TARGET [--wait | --status]   # base APK, in the background
python3 ~/.ai/bin/android-workflow status --target TARGET
python3 ~/.ai/bin/android-workflow log --target TARGET --stage Implementer --status completed --file PATH [--tokens N] [--wait-seconds S]
python3 ~/.ai/bin/android-workflow gate --target TARGET
python3 ~/.ai/bin/android-workflow device --target TARGET
python3 ~/.ai/bin/android-workflow update-spec --target TARGET --surfaces ui --acceptance "a|b"
python3 ~/.ai/bin/android-workflow finish --target TARGET [--skip-device "reason"]
python3 ~/.ai/bin/android-workflow deliver --target TARGET [--subject "ID: title"] [--no-commit|--no-push|--no-pr]
python3 ~/.ai/bin/android-workflow install-host --user   # registers skill + aw-* agents in every host
python3 ~/.ai/bin/android-workflow list --target TARGET
python3 ~/.ai/bin/android-workflow clean --target TARGET --ticket TICKET
python3 ~/.ai/bin/android-workflow evidence capture --target TARGET --phase before --name shade
python3 ~/.ai/bin/android-workflow evidence ingest --target TARGET --phase after --name shade --file shot.png
python3 ~/.ai/bin/android-workflow evidence baseline --target TARGET --from NOTIF-1
python3 ~/.ai/bin/android-workflow evidence compare --target TARGET --previous NOTIF-1
```

Python package and its tests: `~/.ai/tools/android-workflow` (everything for this workflow
lives under `~/.ai`). The bin wrapper adds that path. Agent role files: `~/.ai/skills/android-workflow/agents/`
(Claude Code wrappers in `~/.claude/agents/aw-*.md`). CLI stage notes: `stage-skills/`.

## Run layout (target app)

Generated files are **not** source. Do not commit them. One folder per ticket:

```text
TARGET/.ai/workflow/
├── current.json          # active ticket pointer
├── _cache/               # T0: project-config.json, env.json, repo-map.json
└── TICKET-ID/            # this ticket's logs, plan, notes, PR body, media/
```

A new `start` creates a new folder. The previous ticket stays until
`clean --ticket` or `clean --all` (android-workflow runs + `_cache/` only;
feature-workflow `_state-cache.json` and `_setup/` are left alone).
`/workflow-clean <id>` can also remove the same ticket folder because it shares
`.ai/workflow/<id>/`.

Legacy flat dumps in `TARGET/.agent/*.json|*.md` are migrated on the next CLI
command.

## Stage map

| Role | CLI | Owner | Evidence |
|---|---|---|---|
| Workspace | — | `feature_workspace.py` | current branch or ticket branch in-place by default; worktree only by explicit request (submodules and `local.properties` initialised from the main checkout) |
| Setup | `setup` | CLI + `aw-setup` when needed | `.ai/project-profile.md`, `.ai/android-workflow.json`; reused or refreshed without an agent when only dependency versions moved |
| Bootstrap | T0 | CLI | `_cache/env.json`, `_cache/repo-map.json` |
| Triage | T1 | CLI + `aw-planner` | `ticket-spec.json` |
| Localizer | T2 | CLI locate + `aw-planner` | `change-set-map.json` |
| Planner | T3 | `aw-planner` (standard, full) | `plan.md` |
| Implementer | T4 | `aw-implementer` | source diff, `implementation-notes.md` |
| Tester | T4 | `aw-tester` (full) | tests proven to fail without the change |
| Base build | — | CLI `prebuild` (background) | `prebuild.json`, overlaps the Planner |
| Quality gate | T5 | CLI | `gate-report.json` (auto-format, waivers) |
| Reviewer | T6 | `aw-reviewer` (isolated) | `review.json` |
| Device | T7 | `aw-device` (before + after) | `device-report.md`, `media/` |
| Delivery | T8 | CLI `finish` + `aw-delivery` → CLI `deliver` | `pr-description.md`, `delivery.json`, PR |
| Telemetry | T9 | CLI | `stage-metrics.json`, `stage-log.md` |

Narrate each stage in one line with the **role name**, never a bare T-code:
`Implementer: ProfileScreen.kt → implementation-notes.md`. T-codes are CLI flags only
(`--stage T4`). Durable log: `TARGET/.ai/workflow/<ticket-id>/stage-log.md`.

Reviewer is `aw-reviewer`, not `/feature-workflow`'s `feature-reviewer`. The Implementer
compiles the affected module before the first `gate`. Write `pr-description.md` from the notes; `finish`
keeps a real body. After `start`, read at most the top 8 change-set files.

`run` without an Implementer adapter stops at `awaiting_host`. That is not success.

## Implementer

Read `ticket-spec.json`, `change-set-map.json`, `plan.md` if present.
Replace the notes stub (`Host has not implemented yet.`).
Write tests that fail without the behavior.
Do not delete tests, suppress lint, swallow exceptions, or invent business rules.

## Artifact headings (English)

- `plan.md`: Objective, Steps, Out of scope, Verification
- `implementation-notes.md`: Decisions, Trade-offs, Out of scope, Assumptions
- `device-report.md`: Status, Device, Scenarios, Evidence, Not verified
- `pr-description.md`: TARGET's PR/MR template when one exists (see
  [`../agents/pr-author.md`](../agents/pr-author.md) for where to look). Copy that
  file's headings, order, checkboxes, and HTML comments. Fill only those sections.
  2–3 lines of prose total. Do not add Summary, Why, Verification, Risks, or
  Reviewer notes unless they are already in the template. No template: 2–3
  sentences and no extra headings. Path is `ticket-spec.json` → `pr_template`.

## Stops

Unanswered business question, missing bug reproduction, red gate after retries, blocking
review after one fix, required device FAIL.

## Done

`finish` exits 0 **and** TARGET has a source diff (`.kt` / `.java` / `.xml` outside
`.ai/workflow/` and `.agent/`), review is approved and device is PASS or not required.
Then `aw-delivery` writes `pr-description.md` and runs `CLI deliver`, unless a `--no-*` flag says
otherwise: commit app source (not `.ai/workflow/`), push (git hooks run; no force, no bypass), and open the PR/MR
from `pr-description.md`, ready for review (not draft). Commit message and PR body are
product copy only — no `Generated with …`, `Made with Cloud Code`, `🤖`,
`Assisted-by`, or agent `Co-Authored-By`. Screenshots live in
`TARGET/.ai/workflow/<ticket>/media/{before,after}/` so the user can attach them on
the PR afterwards.

Skill: [`../skills/android-workflow/SKILL.md`](../skills/android-workflow/SKILL.md).
