# Android Multi-Agent Workflow

Host-driven pipeline for Android tickets. Cursor, Claude Code, Codex, Gemini CLI /
Cloud Code (any Agent Skills host) **write the code**. The CLI detects the app, locates
once, logs stages, runs gate/device, and refuses `finish` without a source diff.

Python 3.11+, standard library only. No app-specific facts.

Canonical host docs live in `~/.ai` (tool-agnostic):

- Workflow: `~/.ai/workflows/android-workflow.md`
- Skill: `~/.ai/skills/android-workflow/SKILL.md`
- CLI: `python3 ~/.ai/bin/android-workflow`

## Use from any host

```text
/android-workflow APP-123 Show empty state on profile
```

Codex: `$android-workflow`.

The agent must narrate each stage **by role name** (Triage, Localizer, Implementer,
Reviewer, … — never stage codes), call `start`, **write Kotlin/tests (Implementer)**, `gate`,
review, `device` if the route requires it, `finish`. Without a source diff outside
`.ai/workflow/` (and the legacy `.agent/` dump), `finish` fails. Opening a PR is
`deliver` commits, pushes and opens the PR (git hooks run, nothing is forced) when the user asked for delivery.

## CLI on the target app

```bash
python3 ~/.ai/bin/android-workflow start --target APP --id APP-123 --title "Empty state" --surfaces ui
python3 ~/.ai/bin/android-workflow status --target APP
# host implements
python3 ~/.ai/bin/android-workflow log --target APP --stage Implementer --status completed --note "ProfileScreen" --file app/src/main/java/com/example/ProfileScreen.kt
python3 ~/.ai/bin/android-workflow gate --target APP
python3 ~/.ai/bin/android-workflow finish --target APP
python3 ~/.ai/bin/android-workflow prebuild --target APP [--wait]   # base APK in the background
python3 ~/.ai/bin/android-workflow deliver --target APP [--subject "APP-123: title"] [--no-push|--no-pr]
python3 ~/.ai/bin/android-workflow list --target APP
python3 ~/.ai/bin/android-workflow evidence capture --target APP --phase before --name shade
python3 ~/.ai/bin/android-workflow evidence baseline --target APP --from NOTIF-1
python3 ~/.ai/bin/android-workflow evidence compare --target APP --previous NOTIF-1
```

`run` without an Implementer adapter no longer completes: it stops at `awaiting_host` so the agent
writes code. Shell adapters in `stage_adapters` + `--execute-external` remain valid for
headless mode.

## Structure

Everything lives under `~/.ai`; there is no second copy to keep in sync.

```text
~/.ai/
├── bin/android-workflow                  # launcher: adds tools/android-workflow to sys.path
├── bin/feature_setup.py, feature_workspace.py
├── skills/android-workflow/              # SKILL.md, agents/aw-*.md, stage-skills/, artifacts.schema.json
├── workflows/android-workflow.md
└── tools/android-workflow/               # this package: android_workflow/, tests/, config/
```

On the **target app**, generated runs go here (gitignored):

```text
.ai/workflow/
├── current.json          # pointer to the active ticket
├── _cache/               # Bootstrap: project-config.json, env.json, repo-map.json
└── APP-123/              # one folder per ticket
    ├── ticket-spec.json
    ├── change-set-map.json
    ├── plan.md
    ├── implementation-notes.md
    ├── stage-log.md
    ├── gate-report.json
    ├── device-report.md
    ├── pr-description.md
    ├── run-state.json
    └── media/
        ├── before/shade.png
        ├── after/shade.png
        ├── manifest.json
        └── compare.md
```

A new ticket creates a new folder and does not overwrite the previous one.
`list --target APP` shows runs. `clean --target APP --ticket APP-123` deletes one.
`clean --target APP --stale [HOURS]` deletes unfinished runs idle for HOURS (default 24).
`clean --target APP --all` deletes android-workflow runs and `_cache/` only — it
leaves feature-workflow files such as `_state-cache.json` and `_setup/`.

Agent role files live in `~/.ai/skills/android-workflow/agents/`.
`install-host --user` renders them for every host: `~/.claude/agents/*.md`, `~/.cursor/agents/*.md`,
`~/.gemini/agents/*.md` (Gemini CLI and Antigravity) and `~/.codex/agents/*.toml` (Codex needs
`[features] multi_agent_v2 = true`). Re-run it after editing a role file.

Artifact markdown headings are English for plan, notes, and device report.
`pr-description.md` follows the target app's PR/MR template when one exists;
otherwise it is 2–3 sentences with no extra headings.

## Bootstrap and overrides

```bash
python3 ~/.ai/bin/android-workflow configure --target APP [--overrides overrides.json]
python3 ~/.ai/bin/android-workflow bootstrap --target APP
```

Bootstrap does not run a baseline build. Overrides merge recursively. Android variants are not
guessed beyond the defaults (`assembleDebug`, `testDebugUnitTest`, …).

## Ticket

```json
{
  "id": "APP-123",
  "title": "Show empty state on profile",
  "description": "Show guidance when there are no items.",
  "type": "feature",
  "reproduction": null,
  "acceptance_criteria": ["Profile shows guidance when empty"],
  "surfaces": ["ui"],
  "complexity": "low",
  "risk": "low",
  "business_questions": []
}
```

An unanswered business question pauses the run:

```bash
python3 ~/.ai/bin/android-workflow resume --target APP --question-id eligibility --answer "Active contracts only"
```

## Stages

Chat and CLI flags use the role name (`log --stage Reviewer`). Stage ids (`T0`–`T9`) exist
only as keys inside the run files; `--stage T4` is still accepted.

- **Bootstrap:** detect environment and repo map.
- **Triage:** normalize ticket and route; pause on a business gap.
- **Localizer:** single global scan → `change-set-map.json`.
- **Planner:** plan if bug / medium-or-high risk or complexity.
- **Implementer:** **the host writes code**; CLI accepts only a source diff + real notes.
- **Quality gate:** formatter fix (when the project has one), then lint/compile/test per module and a secret scan; lint or format findings only in files the change does not touch are waived, recorded and disclosed; up to two corrections.
- **Reviewer:** narrow review; blocking returns to Implementer once.
- **Device:** device if the surface is visual/runtime.
- **Delivery:** `pr-description.md` from the artifacts, then `deliver` (commit, push, PR) in one process.
- **Telemetry:** metrics and `stage-log.md`.

Time/token budget at 80% stops remaining stages and still writes a handoff with gaps.

## Tests

```bash
python3.12 -m unittest discover -s tests -v
```
