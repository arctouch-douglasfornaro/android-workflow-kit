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
| `.claude/`, `.codex/`, `.cursor/`, `.gemini/`, `.agents/` | Ready-made folders for each tool. They hold only pointers: links to the skills and small agent files that say "read `~/.ai/skills/android-workflow/agents/aw-…md`". |
| `link.sh` | One-time setup: links those folders into your home so every tool finds them. |
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

Then, once:

```bash
~/.ai/link.sh
```

It links `~/.claude`, `~/.codex`, `~/.cursor`, `~/.gemini` and `~/.agents` to the matching folder in
this kit and enables `multi_agent_v2` in `~/.codex/config.toml` (Codex needs it to spawn agents).
Anything already there with the same name is moved to `~/.ai-backup/`, never deleted.

Everything points at one source of truth, so edits to the workflow apply at once in every tool. Run
`link.sh` again only when the kit adds or removes an agent or skill.

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
