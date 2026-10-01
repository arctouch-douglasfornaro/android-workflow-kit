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

This file is the single source of truth for the workflow. Each agent's own rules live in
`agents/aw-*.md` next to it.

**Goal:** a precise PR, fast and cheap. Every rule below serves one of the three: precision
(project patterns, verifiable AC, tests, gate, independent review, device proof), speed
(parallel work, scoped builds, skipped stages on simple tickets) and cost (small orchestrator
context, the cheapest model tier that does each job).

You are the **orchestrator**. The Python CLI detects, locates, gates and records. Six agents do
the work; you keep your own context small (paths and ≤10-line returns only — never read the
diff, Gradle logs or images yourself).

## Invocation

```text
/android-workflow <ticket-id> <title or description> [--target PATH] [--ticket FILE]
    [--level express|standard|full] [--worktree] [--no-device]
    [--no-commit] [--no-push] [--no-pr] [--base BRANCH]
```

Codex: `$android-workflow`. If the user omitted fields, ask once. Do not invent acceptance
criteria or business rules. Delivery (commit, push, PR) is **on by default**: invoking the
workflow authorizes it, and **every run that produced code ends in a PR** — ready for review when
every check passed, a draft listing what is still open when it did not. The `--no-*` flags stop
earlier.

## Resolve paths

1. `TARGET` = `--target`, else the current workspace if it has `settings.gradle(.kts)`.
2. If this repo is the toolkit (has `android_workflow/cli.py`) and TARGET was omitted, STOP.
3. `CLI` = `python3 ~/.ai/bin/android-workflow`. `RUN` = `TARGET/.ai/workflow/<ticket-id>/`.

Record `TARGET`, `RUN` and `CLI` in the first chat message, plus the office page the user can open
to watch the agents: `TARGET/.ai/workflow/office.html` (`CLI office --target TARGET` opens it). Never
read that page yourself. If the ticket id looks like a
Jira key and this host has a Jira tool (MCP server or connector), fetch the issue body and
pass it to the Planner.

## Agents

| Role | Agent | Tier | Runs |
|---|---|---|---|
| Setup | `aw-setup` | strong | only when `CLI setup` says `needs_setup_agent` (a build change, not a dependency-version bump) |
| Planner | `aw-planner` | strong | standard and full levels |
| Implementer | `aw-implementer` | standard | always: code **and** its unit tests; resumed for every fix round |
| Reviewer | `aw-reviewer` | strong | after a green gate |
| Device | `aw-device` | standard | `before` + `after` when `device_required` |
| Delivery | `aw-delivery` | fast | after `finish`, unless `--no-commit`: writes the PR body, runs `CLI deliver` |

Tier maps to opus/sonnet/haiku on Claude Code and high/medium/low reasoning on Codex;
Cursor and Gemini inherit the session model.

### How to delegate, per host

Every spawn passes the same inputs: `TARGET`, `RUN`, `CLI`, the level, the mode or fix-round
finding IDs, and nothing else — the agent reads its files itself.

| Host | Spawn | Resume for a fix round |
|---|---|---|
| Claude Code | Agent tool, `subagent_type: aw-<role>` | SendMessage to the agent id |
| Codex (needs `[features] multi_agent_v2 = true`) | `spawn_agent` with agent type `aw-<role>` (`~/.codex/agents/aw-<role>.toml`) | `send_message` / `followup_task` to the same agent |
| Cursor | delegate to subagent `aw-<role>` (`~/.cursor/agents/`) | new subagent with the finding IDs |
| Gemini CLI / Antigravity (`agy agents` lists them) | subagent `aw-<role>` (`~/.gemini/agents/`) | new subagent with the finding IDs |
| Other with subagents | generic subagent: "Read `<skill>/agents/aw-<role>.md` and act as that role. Inputs: …" | same |
| No subagents | run the role inline: read only that role file, do it, write its artifact, then drop it from working memory | — |

If a named agent is not registered, use the generic-subagent row, never skip the role. Missing
registrations: run `~/.ai/link.sh` once (it links every host's folder to this kit).

## Commands (exact syntax; do not probe with `--help`)

```text
python3 ~/.ai/bin/feature_workspace.py --repo-root T --ticket ID --title "…" [--worktree]
CLI setup    --target T [--source-repo MAIN]        CLI start    --target T --id ID --title "…" [--type bug --reproduction "…"]
CLI resume   --target T --question-id Q --answer "…" CLI update-spec --target T --surfaces ui --acceptance "a|b"
CLI prebuild --target T [--wait|--status]           CLI gate     --target T
CLI finish   --target T [--skip-device "r"] [--draft "r"] CLI deliver  --target T [--subject "ID: …"] [--no-push|--no-pr]
CLI log --target T --stage <Role> --status started --note "<what it is about to do>"
CLI log --target T --stage <Role> --status completed --note "…" [--file P]… [--tokens N] [--wait-seconds S]
CLI office --target T                               (opens the office page; it updates itself)
CLI evidence capture|ingest|compare|list --target T [--phase before|after] [--name N] [--file F]
CLI status|list --target T                          CLI clean --target T [--ticket ID | --stale [HOURS]]
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
3. **Ticket:** `CLI start` (add `--type bug --reproduction "..."`, `--acceptance "a|b"`,
   `--surfaces` when known). It writes `ticket-spec.json` and `change-set-map.json` (the likely
   files). `paused` → ask the human, then `CLI resume`.
4. **Level** (`--level` wins; otherwise decide from the ticket and `ticket-spec.json`):
   - `express`: chore/feature, low risk, the ticket names the screen or file, ≤3 files,
     no business question. Skip the Planner: run `CLI update-spec` yourself with the AC and
     surfaces from the ticket text (a visible change is `ui`).
   - `full`: high risk or complexity, or surfaces lifecycle, room_migration, workmanager,
     payments/auth. Planner, and the Implementer also covers the edge cases its role file lists.
   - `standard`: everything else. Planner.
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
7. **Implementer:** spawn `aw-implementer` with the level. It writes the code and the unit tests
   that prove it. Keep its agent id for fix rounds.
8. **Quality gate:** `CLI gate --target TARGET`. It runs the project's formatter (fixing first when
   it can), then compile, unit tests, detekt and lint for the touched modules in one build, plus
   compile and unit tests for modules that use a changed declaration, and a secret scan. Lint or
   format findings only in files the change does not touch are **waived** and disclosed in the PR;
   findings in a touched file fail. Red → resume the Implementer with `gate-report.json` (max 2),
   then re-gate. Third red → draft PR (see Stop). Never ask the user to waive lint by hand. If
   the gate itself looks wrong (a misclassified waiver, a parser miss), report it as a toolkit
   defect and deliver a draft PR: no agent edits the toolkit (`~/.ai`) during a run. `start`
   fingerprints the toolkit; after any change the gate reports `blocked`
   (`toolkit_modified_during_run`) and only a draft PR can ship.
9. **Reviewer:** spawn `aw-reviewer`. `changes_requested` → resume the Implementer with the
   blocking IDs once, re-gate, then resume the Reviewer for a delta review. Still blocking →
   draft PR.
10. **Device after** (if `device_required`, not `--no-device`): spawn `aw-device` mode
    `after`. It verifies the AC on the device and writes `media/compare.md` (before vs after).
    FAIL → Implementer once → gate → Reviewer delta → Device again. Second FAIL → draft PR.
    BLOCKED with a device connected (login, feature flag, screen unreachable) → draft PR that says
    what unblocks it. No device connected at all → skip it at Finish (`--skip-device`).
11. **Finish** (final check before the PR): `CLI finish --target TARGET`. It confirms that the
    gate, the approved review and the device PASS all belong to the code that will ship (any
    later edit makes them stale) and that a visual ticket has `media/after/`. `--no-device` or
    no device: `--skip-device "<reason>"`, which is logged and repeated in the PR body. When a
    check is still open after its fix rounds: `--draft "<why it stopped>"` (see Stop).
12. **Delivery:** spawn `aw-delivery` with flags. It writes `pr-description.md`, then `CLI deliver`
    commits app source only, pushes (git hooks run, nothing is force-pushed) and opens the PR,
    ready for review.

Overlaps that cannot race on the working tree are safe: Planner with the base build, Implementer
with the `before` capture. Anything that edits source waits for the base build.

## Run files

Everything the run writes lives in `RUN` (gitignored, never committed): `ticket-spec.json`,
`change-set-map.json`, `plan.md`, `implementation-notes.md`, `gate-report.json`, `review.json`,
`device-report.md`, `pr-description.md`, `stage-log.md`, `stage-metrics.json` and
`media/{before,after}/`. The shared cache is `TARGET/.ai/workflow/_cache/`; `current.json`
points at the active ticket. A new ticket gets a new folder.

## Chat log and telemetry

Right before you spawn or resume an agent, run `CLI log --stage <Role> --status started --note
"<one line: what it will do>"`. It costs nothing, it starts that agent's clock, and it is what lights
up its desk on the office page.

Speak in **role names**, never bare T-codes. One line per stage:
`{Role}: {result} → {artifact}`. Example: `Reviewer: approved, 0 blocking → review.json`.
Durable log: `RUN/stage-log.md`; time and tokens per stage: `RUN/stage-metrics.json` (used to
debug runs and find improvements). Every `log` for an agent stage passes the token total that
agent's result reports (`--tokens N`); never estimate. `log` answers with a `warnings` entry
when an agent stage has none; if the host truly exposes no count, say so once in the final
summary. When you paused to ask the user, add `--wait-seconds S` so waiting is not counted.

## Stop

**Before any code exists** — a setup fact that blocks this ticket (e.g. unknown install task for a
UI ticket), an unanswered business question, a missing bug reproduction — there is nothing to put
in a PR: STOP, ask the user, and continue once answered.

**After the code exists** — red gate after 2 fixes, blocking review after one fix, second device
FAIL, device BLOCKED with a device connected, a toolkit defect — never end without a PR:
`CLI finish --target TARGET --draft "<why it stopped>"` records every unresolved check as a known
issue, then `aw-delivery` delivers a **draft** PR that lists them. Tell the user what unblocks it.

## Done

`finish` exited 0, TARGET has a source diff outside `.ai/workflow/`, and — unless a `--no-*` flag
stopped earlier — the PR is open: ready for review when the gate passed, the review is `approved`
and the device is PASS (or not required); a draft with its open issues otherwise.
Show: PR URL (and whether it is a draft), changed files, stage log path, and the absolute `media/before` and
`media/after` paths so the user can drag the evidence into the PR.

Commit message and PR body are product copy only: no host/agent/tool names, no
`Generated with …`, `🤖`, `Assisted-by` or agent `Co-Authored-By`.
