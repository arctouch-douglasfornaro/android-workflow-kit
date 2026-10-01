---
name: android-workflow
description: >-
  Host-driven Android ticket pipeline: plan, implement with tests, gate,
  independent review, device verification with before/after evidence, and PR.
  Use when the user types /android-workflow, /Android Workflow, or asks to run
  this workflow on an Android ticket or Jira id.
disable-model-invocation: true
---

# Android Workflow

Canonical workflow: `~/.ai/workflows/android-workflow.md`. This file is enough to run it.

You are the **orchestrator**. The Python CLI detects, locates, gates and records. Five
agents do the work; you keep your own context small (paths and ≤10-line returns only —
never read the diff, Gradle logs or images yourself).

## Invocation

```text
/android-workflow <ticket-id> <title or description> [--target PATH] [--ticket FILE]
    [--level express|standard|full] [--worktree] [--no-device]
    [--no-commit] [--no-push] [--no-pr] [--base BRANCH]
```

If the user omitted fields, ask once. Do not invent acceptance criteria or business rules.
Delivery (commit, push, PR) is **on by default**: invoking the workflow authorizes it. The
`--no-*` flags stop earlier.

## Resolve paths

1. `TARGET` = `--target`, else the current workspace if it has `settings.gradle(.kts)`.
2. If this repo is the toolkit (has `android_workflow/cli.py`) and TARGET was omitted, STOP.
3. `CLI` = `python3 ~/.ai/bin/android-workflow`. `RUN` = `TARGET/.ai/workflow/<ticket-id>/`.

Record `TARGET`, `RUN` and `CLI` in the first chat message. If the ticket id looks like a
Jira key and this host has a Jira tool (MCP server or connector), fetch the issue body and
pass it to the Planner.

## Agents

| Role | Agent | Tier | Runs |
|---|---|---|---|
| Setup | `aw-setup` | strong | only when `CLI setup` says `needs_setup_agent` (a build change, not a dependency-version bump) |
| Planner | `aw-planner` | strong | standard and full levels |
| Implementer | `aw-implementer` | standard | always; resumed for every fix round |
| Tester | `aw-tester` | standard | full level only |
| Reviewer | `aw-reviewer` | strong | after a green gate |
| Device | `aw-device` | standard | `before` + `after` when `device_required` |
| Delivery | `aw-delivery` | fast | after `finish`, unless `--no-commit`: writes the PR body, runs `CLI deliver` |

Tier maps to opus/sonnet/haiku on Claude Code and high/medium/low reasoning on Codex;
Cursor and Gemini inherit the session model. Role files: `agents/` next to this file.

### How to delegate, per host

Every spawn passes the same inputs: `TARGET`, `RUN`, `CLI`, the mode or fix-round finding IDs,
and nothing else — the agent reads its files itself.

| Host | Spawn | Resume for a fix round |
|---|---|---|
| Claude Code | Agent tool, `subagent_type: aw-<role>` | SendMessage to the agent id |
| Codex (needs `[features] multi_agent_v2 = true`) | `spawn_agent` with agent type `aw-<role>` (`~/.codex/agents/aw-<role>.toml`) | `send_message` / `followup_task` to the same agent |
| Cursor | delegate to subagent `aw-<role>` (`~/.cursor/agents/`) | new subagent with the finding IDs |
| Gemini CLI / Antigravity (`agy agents` lists them) | subagent `aw-<role>` (`~/.gemini/agents/`) | new subagent with the finding IDs |
| Other with subagents | generic subagent: "Read `<skill>/agents/aw-<role>.md` and act as that role. Inputs: …" | same |
| No subagents | run the role inline: read only that role file, do it, write its artifact, then drop it from working memory | — |

If a named agent is not registered, use the generic-subagent row, never skip the role. Missing
registrations: `python3 ~/.ai/bin/android-workflow install-host --user`.

## Commands (exact syntax; do not probe with `--help`)

```text
CLI setup    --target T [--source-repo MAIN]        CLI start    --target T --id ID --title "…" [--type bug --reproduction "…"]
CLI prebuild --target T [--wait|--status]           CLI gate     --target T
CLI finish   --target T [--skip-device "reason"]    CLI deliver  --target T [--subject "ID: …"] [--no-push|--no-pr]
CLI log --target T --stage <Role> --status completed --note "…" [--file P]… [--tokens N] [--wait-seconds S]
python3 ~/.ai/bin/feature_workspace.py --repo-root T --ticket ID --title "…" [--worktree]
```

## Execute

1. **Workspace first, in-place by default:** run `feature_workspace.py` without `--worktree`
   unless the user asked for one. On a non-base branch it continues there; on the base branch it
   creates or reuses `<user>/<ID>-<slug>` in the same checkout. With `--worktree`, `TARGET`
   becomes the printed `worktree_path`. The script initialises what the main checkout already
   uses (initialised submodules, `local.properties`) and prints `bootstrap_*` lines; relay any
   `bootstrap_submodule … failed` or `bootstrap_not_copied` line to the user and STOP until they
   answer; no agent works around it (nor runs the repository's own worktree setup scripts).
   An `other_ticket_branches:` line means this ticket already ran: name those branches and ask
   whether to continue before `start`, so a repeat run is a choice, not an accident.
2. **Setup, once, on the final `TARGET`:** `CLI setup --target TARGET [--source-repo <main checkout>]`.
   `ready` → reuse (the profile is inherited or refreshed when only dependency versions moved).
   `needs_setup_agent` → spawn `aw-setup` with the JSON; its questions are batched to the user
   once; a missing formatter or app id never blocks a domain-only ticket.
3. **Bootstrap/Triage/Localizer:** `CLI start` (add `--type bug --reproduction "..."`,
   `--acceptance "a|b"`, `--surfaces` when known). `paused` → ask the human, then `CLI resume`.
4. **Level** (`--level` wins; otherwise decide from the ticket and `ticket-spec.json`):
   - `express`: chore/feature, low risk, the ticket names the screen or file, ≤3 files,
     no business question. Skip the Planner: run `CLI update-spec` yourself with the AC and
     surfaces from the ticket text (a visible change is `ui`).
   - `full`: high risk or complexity, or surfaces lifecycle, room_migration, workmanager,
     payments/auth. Planner + Tester.
   - `standard`: everything else. Planner, no Tester.
   Bugs are never express (the Planner checks the reproduction).
5. **Base build in the background:** when the route has a device stage, a device is connected and
   not `--no-device`, run `CLI prebuild --target TARGET` right after `start`. It returns at once and
   builds the unmodified source while the Planner reads code. Nothing may edit source until it
   ends. The Planner never edits, so spawn it now (standard/full): `aw-planner` with TARGET, RUN
   and the ticket text. It corrects the CLI's guesses with `CLI update-spec`. `NEEDS_INPUT` →
   batch the questions to the user and STOP until answered.
6. **Device before** (only if `device_required` and `visual`, device connected, not `--no-device`):
   `CLI prebuild --target TARGET --wait`. `passed` → spawn `aw-device` mode `before` (it installs
   the prebuilt APK, no build) and start the Implementer without waiting for the capture: the
   capture touches only the emulator and `RUN/media`. Any other status (`tainted`, `failed`,
   `skipped`) → `aw-device` `before` builds and captures itself, and the Implementer waits for it.
   No device → skip before, note it.
7. **Implementer:** spawn `aw-implementer`. Keep its agent id for fix rounds.
8. **Tester** (full): spawn `aw-tester`. Production findings go back to the Implementer.
9. **Quality gate:** `CLI gate --target TARGET`. The formatter fixes first when Setup found one that
   can (`format_apply`); otherwise the format check runs alone before the slow tasks. Then compile,
   unit tests, detekt and lint for the touched modules in one `--continue` build, plus compile and
   unit tests for downstream modules that reference a changed declaration (`consumer_modules`), and
   the feature-doc check when the project records `feature_docs.glob`. A lint or format
   failure whose findings are all in files this change does not touch is **waived**, recorded in
   `gate-report.json` → `waivers` and logged; the PR must disclose it (`deliver` adds the line if the
   body omits it). Findings in a touched file fail the gate, whether or not they predate the branch.
   `unverified` lint also fails. Red → resume the Implementer with `gate-report.json` (max 2), then
   re-gate. Third red → STOP. Never ask the user to waive lint by hand and never route around a
   refused `finish`. If the gate itself looks wrong (a misclassified waiver, a parser miss), STOP
   and report it as a toolkit defect for the human: no agent edits the toolkit
   (`~/.ai`, where the toolkit lives) during a run. `start` fingerprints the toolkit; after
   any change the gate reports `blocked` (`toolkit_modified_during_run`) and `finish` refuses.
10. **Reviewer:** spawn `aw-reviewer`. `changes_requested` → resume the Implementer with the
    blocking IDs once, re-gate, then resume the Reviewer for a delta review. Still blocking →
    STOP.
11. **Device after** (if `device_required`, not `--no-device`): spawn `aw-device` mode
    `after`. FAIL → Implementer once → gate → Reviewer delta → Device again. Second FAIL →
    STOP. BLOCKED when required → STOP and say what unblocks (device, login, flag).
12. **Finish:** `CLI finish --target TARGET`. It refuses unless the gate, the approved review
    and the device PASS all belong to the current source (any later edit makes them stale) and
    a visual ticket has `media/after/`. `--no-device` or no device: `--skip-device "<reason>"`,
    which is logged and must be repeated in the PR body.
13. **Delivery:** spawn `aw-delivery` with flags. It writes `pr-description.md`, then `CLI deliver`
    commits app source only, pushes (git hooks run, nothing is force-pushed) and opens the PR.

Overlaps that cannot race on the working tree are safe: Planner with the base build, Implementer
with the `before` capture. Anything that edits source waits for the base build.

## Chat log

Speak in **role names**, never bare T-codes. One line per stage:
`{Role}: {result} → {artifact}`. Example: `Reviewer: approved, 0 blocking → review.json`.
Durable log: `RUN/stage-log.md`. Every `log` for an
agent stage passes the token total that agent's result reports (`--tokens N`); never estimate.
`log` answers with a `warnings` entry when an agent stage has none; if the host truly exposes no
count, say so once in the final summary. When
you paused to ask the user, add `--wait-seconds S` so waiting is not counted as work.

## Stop

Setup fact that blocks this ticket (e.g. unknown install task for a UI ticket), unanswered
business question, missing bug reproduction, red gate after 2 fixes, blocking
review after one fix, second device FAIL, required device BLOCKED. Preserve work and say
what unblocks.

## Done

`finish` exited 0, TARGET has a source diff outside `.ai/workflow/`, review is `approved`,
device is PASS (or not required), and — unless a `--no-*` flag stopped earlier — the PR is
open. Show: PR URL, changed files, stage log path, and the absolute `media/before` and
`media/after` paths so the user can drag the evidence into the PR.

Commit message and PR body are product copy only: no host/agent/tool names, no
`Generated with …`, `🤖`, `Assisted-by` or agent `Co-Authored-By`.
