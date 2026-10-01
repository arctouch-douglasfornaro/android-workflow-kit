# Android Workflow Kit

A tool-agnostic kit for running Android tickets end to end with coding agents
(Claude Code, Codex, Cursor, Gemini CLI / Antigravity, OpenCode and any
[Agent Skills](https://agentskills.io) host). The agent writes the code; a
standard-library Python CLI detects the project, localizes the change, runs the
quality gate and device checks, and refuses to finish without a real source diff.

**Goal:** a precise PR, fast and cheap to produce.

```text
Input (Jira / description) → Orchestrator (setup, branch, level)
  → Planner → Implementer (code + unit tests) → Quality gate → Code reviewer → Device check → Delivery (commit, push, PR)
      fix rounds: gate / reviewer / device ──→ Implementer
```

The workflow is described in one place: [`skills/android-workflow/SKILL.md`](skills/android-workflow/SKILL.md).
The repository mirrors `~/.ai`, which is where every host reads it from.

## Layout

| Path | What it is |
| --- | --- |
| `skills/android-workflow/SKILL.md` | The workflow: stages, agents, levels, stops and delivery rules (single source of truth). |
| `skills/android-workflow/agents/` | The six `aw-*` agents: setup, planner, implementer, reviewer, device, delivery. |
| `skills/device-driving/` | How the Device agent drives the phone or emulator (Maestro first, adb fallback). |
| `tools/android-workflow/` | The Python package behind the `android-workflow` CLI, with its tests. |
| `bin/` | `android-workflow` launcher plus the helpers the CLI calls: `feature_setup.py` (project profile), `feature_workspace.py` (ticket branch/worktree) and `android_probe.py`. |

## Install

Requires Python 3.11+ (standard library only).

```bash
git clone https://github.com/arctouch-douglasfornaro/android-workflow-kit.git ~/.ai
```

Render the `android-workflow` skill and `aw-*` agents for every host:

```bash
python3 ~/.ai/bin/android-workflow install-host --user              # ~/.claude, ~/.codex, ~/.cursor, ~/.gemini, ~/.agents
python3 ~/.ai/bin/android-workflow install-host --target /path/app  # project-level copies + AGENTS.md
```

Codex needs `[features] multi_agent_v2 = true` to spawn the agents. Re-run
`install-host` after editing a role file.

## Use

```text
/android-workflow APP-123 Show empty state on profile
```

Codex: `$android-workflow`. CLI reference: `tools/android-workflow/README.md`.

## Tests

```bash
cd ~/.ai/tools/android-workflow && python3 -m unittest discover -s tests
cd ~/.ai/bin && python3 -m unittest discover -p '*_test.py'
```
