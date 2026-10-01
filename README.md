# Android Workflow Kit

A tool-agnostic kit for running Android tickets end to end with coding agents
(Claude Code, Codex, Cursor, Gemini CLI / Antigravity and any
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

### 1. Requirements on your machine

| Needed for | What |
| --- | --- |
| Always | Python 3.11+, `git`, and an Android project that builds with Gradle |
| Opening the PR | `gh` (GitHub) or `glab` (GitLab), logged in. Without it you get a link to open the PR by hand |
| Device check | An emulator or phone visible in `adb devices`. Maestro is installed automatically on first use |
| Jira tickets | A Jira connector (MCP) in your coding tool, so the ticket text is fetched for you |

### 2. Run it

Open the **Android app's folder** in your coding tool and type:

```text
/android-workflow APP-123 Show empty state on the profile screen
```

On Codex use `$android-workflow`. The first argument is the ticket id (a Jira key works); the rest
is the task. That's all: the workflow creates the branch, writes the code and tests, checks
everything and opens the PR.

Optional flags, added at the end:

| Flag | Effect |
| --- | --- |
| `--level express\|standard\|full` | Force how deep it goes (normally chosen for you, see below) |
| `--no-device` | Skip the device check (the PR says so) |
| `--worktree` | Work in a separate worktree instead of the current checkout |
| `--base BRANCH` | Base branch for the PR when it is not the repo default |
| `--no-pr` / `--no-push` / `--no-commit` | Stop before opening the PR / pushing / committing |
| `--target PATH` | The app's path, when your tool is not opened in it |

### 3. What happens

1. **Branch** — on the base branch it creates `<user>/<TICKET>-<slug>`; on any other branch it keeps working there.
2. **Setup (first run only)** — learns the project's patterns and quality tools and saves them in `.ai/project-profile.md`. Later tickets reuse it.
3. **Level** — `express` for small, clear changes (skips the Planner), `full` for risky ones (lifecycle, migrations, payments, auth), `standard` for everything else. Bugs are never express.
4. **Planner** — reads the code and writes verifiable acceptance criteria. Meanwhile the unchanged app is built in the background.
5. **Implementer** — writes the change and the unit tests that prove it. In parallel, if a device is connected, the **before** screenshots are captured.
6. **Quality gate** — formatter, compile, unit tests, detekt and lint for the modules touched. Failures go back to the Implementer (up to 2 times).
7. **Code reviewer** — an independent review of the diff: bugs, side effects, callers, duplication, project conventions. Blocking findings go back to the Implementer once.
8. **Device check** — installs the app, navigates to the change and captures the **after** screenshots or video.
9. **PR** — commits only app code, pushes and opens the PR.

You see one line per stage in the chat, e.g. `Reviewer: approved, 0 blocking → review.json`.

### 4. What you get

- **A PR, every time code was written.** Ready for review when every check passed; a **draft** that
  lists what is still open when a check kept failing (gate, review or device). The commit and PR
  carry no tool or AI attribution and follow the repo's PR template when there is one.
- **Before/after evidence** in `.ai/workflow/<TICKET>/media/before/` and `media/after/`, plus
  `media/compare.md`. Drag them into the PR.
- **A log of the run** in `.ai/workflow/<TICKET>/stage-log.md`, with time and tokens per stage in
  `stage-metrics.json` to spot what was slow or expensive.

Everything under `.ai/workflow/` stays on your machine; it is git-ignored and never committed.

### 5. When it stops to ask you

It only stops **before writing code**, when it would otherwise have to guess: a business rule the
ticket does not define, a bug without steps to reproduce it, or a project fact Setup could not find.
Answer in the chat and it continues.

### 6. Housekeeping

```bash
python3 ~/.ai/bin/android-workflow list  --target .               # runs in this app
python3 ~/.ai/bin/android-workflow clean --target . --ticket APP-123
python3 ~/.ai/bin/android-workflow clean --target . --stale       # unfinished runs idle for 24h
```

Update the kit with `git -C ~/.ai pull`; every tool picks the change up at once.

More: the full workflow rules are in [`skills/android-workflow/SKILL.md`](skills/android-workflow/SKILL.md)
and the CLI reference in [`tools/android-workflow/README.md`](tools/android-workflow/README.md).

## Tests

```bash
cd ~/.ai/tools/android-workflow && python3 -m unittest discover -s tests
cd ~/.ai/bin && python3 -m unittest discover -p '*_test.py'
```
