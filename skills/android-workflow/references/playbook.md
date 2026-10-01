# Playbook (host-driven)

The coding agent **is** the runtime. The CLI does not implement Kotlin. Without a source
diff, the run is incomplete.

Canonical copy for every host: `~/.ai/workflows/android-workflow.md`.

## Roles

Always use the **role name**, in chat and in CLI flags (`--stage Implementer`).

| Role | Who | Tool |
|---|---|---|
| Setup | CLI + `aw-setup` | `setup`: profile, code patterns, quality gates (once per config) |
| Workspace | host | `feature_workspace.py`: ticket branch or worktree before any edit |
| Bootstrap | machine | `android-workflow bootstrap` / `start` |
| Triage | CLI + `aw-planner` | CLI writes `ticket-spec.json`; Planner makes AC verifiable |
| Localizer | CLI + `aw-planner` | `start` locates; Planner confirms the top 8 |
| Planner | `aw-planner` (standard, full) | `plan.md` with AC table + navigation recipe |
| Implementer | `aw-implementer` | code, tests, `implementation-notes.md` |
| Tester | `aw-tester` (full) | edge-case tests proven to fail without the change |
| Quality gate | machine | `gate` |
| Reviewer | `aw-reviewer` (isolated) | `review.json` |
| Device | `aw-device` when `device_required` | `device-report.md`, `media/` |
| Delivery | CLI `finish` + `aw-delivery` | `pr-description.md`, branch, commit, PR |
| Telemetry | machine | `stage-metrics.json` + `stage-log.md` |

## Commands

Work in the **Android app** (`TARGET`). CLI: `python3 ~/.ai/bin/android-workflow`.

```bash
CLI=(python3 ~/.ai/bin/android-workflow)

$CLI start --target TARGET --id APP-123 --title "..."
# Optional: --surfaces notifications,ui  (inferred from the title when omitted)


# If status=paused: ask the human, then
$CLI resume --target TARGET --question-id ID --answer "..."

# Planner corrects the CLI's guesses; the route is recomputed
$CLI update-spec --target TARGET --surfaces ui,navigation --acceptance "a|b" --risk medium

# Implementer: YOU write code in TARGET. Then:
$CLI log --target TARGET --stage Implementer --status completed --note "touched files" --file path/to/Changed.kt

$CLI gate --target TARGET
$CLI log --target TARGET --stage Reviewer --status completed --note "review" --tokens 41234

# If the route includes Device
$CLI device --target TARGET

$CLI log --target TARGET --stage Device --status completed --note "PASS"
$CLI finish --target TARGET            # or --skip-device "no device connected"
$CLI status --target TARGET
$CLI list --target TARGET
$CLI evidence capture --target TARGET --phase before --name shade
$CLI evidence ingest --target TARGET --phase after --name shade --file shot.png
$CLI evidence baseline --target TARGET --from NOTIF-1
$CLI evidence compare --target TARGET --previous NOTIF-1
```

Artifacts live in `TARGET/.ai/workflow/<ticket-id>/` (gitignored). The Bootstrap cache is
`TARGET/.ai/workflow/_cache/`. `current.json` points at the active ticket. A new
`start` creates a new folder and does not overwrite the last ticket. `clean --ticket`
removes one run; `clean --stale [HOURS]` removes unfinished runs (`awaiting_host`, `paused`,
`running`) idle for HOURS (default 24); `clean --all` removes android-workflow runs and
`_cache/` only.

`finish` fails if Implementer left no source diff plus real notes, if `review.json` is not
`approved`, if a required Device verdict is not PASS, if a visual ticket has no `media/after/`,
or if the source changed after the gate, review or device verdict (each is fingerprinted when
logged). That is intentional.

## Cost

- The orchestrator never reads the diff, Gradle output or media; agents return ≤10 lines
  and point at files. Resume the same Implementer for fix rounds instead of a new one.
- Reviewer is `aw-reviewer`, not `/feature-workflow`'s `feature-reviewer`.
- After `start`, read at most the **top 8** `change-set-map.json` files. Ignore `.idea`,
  unrelated screens, and generic matches.
- Compile the affected module before the first `gate`. Do not use `gate` as the compiler.
- Call `gate` once per green compile. Do not paste Gradle logs into chat; `gate-report.json` is the
  summary and `RUN/gate-attempt-*.log` the full Gradle output.
- Write `pr-description.md` yourself from the notes. `finish` will not overwrite a real body.
  Use TARGET's PR/MR template when `ticket-spec.json` → `pr_template` (or `.github` /
  `.gitlab` templates) exists: copy its headings; do not add new ones. 2–3 lines of
  prose. No template: 2–3 sentences, no extra headings.
  No host/agent/tool names. No `Generated with …`, `Made with Cloud Code`, `🤖`,
  `Assisted-by`, or agent `Co-Authored-By`. `finish` strips those lines.
- One line per stage in chat, using the **role name** (Implementer, Reviewer, …). Role files are the agents' prompts; the orchestrator does not read them.
- `aw-device` writes a real `device-report.md` (>400 bytes) so the CLI `device` command keeps
  it. Do not use `device` (it runs `connectedDebugAndroidTest`, not the feature)
  unless the repo's instrumented suite is the proof.
- Visual proof lives in `TARGET/.ai/workflow/<ticket>/media/{before,after}/`. Capture
  **before** Implementer, **after** the fix, then `evidence compare`. Next ticket: `evidence baseline --from <previous>`.
  Do not paste PNG/MP4 into chat; only paths. Prefer `android screen capture`, else this CLI
  (`adb screencap`). Maestro still drives the UI; copy stills in with `evidence ingest`.

## Chat narration

One line after each stage, **role name first**: `{Role}: {result} → {artifact}`.
Never use stage codes: the CLI takes and prints role names.
Example: `Implementer: empty state on ProfileScreen → implementation-notes.md`.
The durable log is `stage-log.md` (`| Implementer | completed | … |`).

## Implementer rules

- Read `ticket-spec.json` and the **top 8** files in `change-set-map.json`. `plan.md` if present.
- Touch only the change set, unless new evidence is justified in the notes.
- Write tests that fail without the behavior.
- Replace the `implementation-notes.md` stub (do not leave "Host has not implemented yet.").
- Forbidden: deleting/weakening tests, suppressing lint, inventing a business rule, swallowing exceptions.

## Stops

- Unanswered business question → `paused` (do not invent).
- Bug without `reproduction` → `escalated` at Planner.
- Red Quality gate → fix (max 2 returns to Implementer) or escalate.
- Reviewer `blocking` → one return to Implementer, then re-gate.
- Reproduced device FAIL → one fix; second real failure escalates.

## Done

Only declare done when `finish` exits 0, `run-state.json` is `completed`, and TARGET has a
source diff outside `.ai/workflow/` / `.agent/`. The CLI never opens a GitHub PR;
`aw-delivery` does (unless `--no-commit`/`--no-push`/`--no-pr`): commit app source (not
`.ai/workflow/`), push, and `gh pr create` using `pr-description.md`. Commit subject/body and the PR body are
the ticket intent only — no Cloud Code, Claude, Cursor, Gemini, Codex, or other
agent attribution. Point them at `media/before` and `media/after`
to attach screenshots on the PR.
