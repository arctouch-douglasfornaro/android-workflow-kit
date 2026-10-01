# Workflow: workflow-help

Read-only. Explains how this kit works **in this repo**. Does not probe, does not write, does not run Gradle.

```
/workflow-help
```

---

## Steps

1. If `<repo>/.ai/project-profile.md` is missing, explain that the first feature run creates it automatically. `/workflow-setup` is optional preparation, not a prerequisite. Do not run setup from this read-only command.
2. If the profile exists, summarize the rows a human needs to run a ticket: app module, compile / unit-test / lint tasks, lint engine, UI toolkit, DI, feature-doc glob (or `none`).
3. Print the kit family:

| Command | When |
|---|---|
| `/workflow-setup [--force]` | New repo, or Gradle/`AGENTS.md` changed |
| `/workflow-help` | This screen |
| `/workflow-clean [<feature-id> \| --all]` | Delete run logs/media |
| `/feature-workflow <ticket> <title>` | One change, through PR |
| `/android-workflow <ticket> <title>` | Host-driven T0–T9 using `~/.ai/bin/android-workflow`; runs live in `.ai/workflow/<ticket-id>/` |

4. Point at canonical specs: `~/.ai/ANDROID.md`, `~/.ai/workflows/feature-workflow.md` (phase map), `~/.ai/workflows/android-workflow.md` (host-driven CLI pipeline).
4b. Platform skills (Compose, Compose performance, edge-to-edge, in-app navigation, screenshots, Android CLI) live in `~/.ai/skills/`. They are not created by setup. Google's `android skills add` catalog is opt-in per ticket.
5. An unknown required task must be resolved before validation; it cannot be silently skipped. A proven inapplicable engine can be recorded as such.
6. Do **not** dump `AGENTS.md` or the whole profile. Facts only.

## Does not

- ❌ Re-run the probe
- ❌ Invent task names
- ❌ Start a feature run
