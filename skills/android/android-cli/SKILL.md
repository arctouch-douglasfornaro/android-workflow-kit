---
name: android-cli
description: "The official Android CLI (`android`: docs, screen capture, layout, emulator) when it is on PATH. Use for current Jetpack docs or a layout dump. Never installs Google skills into a repo."
---

# Android CLI — universal

How agents use the official `android` binary when it is installed. This is **not** a substitute for Gradle, Maestro, or this kit's workflow commands.

Load when you need official docs, a device screenshot/layout dump, emulator control, or to **discover** Google's task skills. Setup (`aw-setup`) must **never** install CLI skills into the repo.

Sources: [Android CLI](https://developer.android.com/tools/agents/android-cli), [Android skills](https://developer.android.com/tools/agents/android-skills), catalog [android/skills](https://github.com/android/skills).

---

## Presence

```bash
command -v android
```

Missing: continue with `./gradlew`, `adb`, Maestro. Do **not** fail the run. Do **not** curl-install the CLI unless the human asked.

If present, `android --help` / `android <cmd> -h` wins over memory. Commands below match the public CLI doc; do not invent flags.

## Prefer the CLI for

| Need | Command (from official docs) |
|---|---|
| Search / fetch Android Knowledge Base | `android docs search '…'` then `android docs fetch kb://…` |
| Screenshot | `android screen capture --output=<file>` |
| UI tree | `android layout [--pretty] [--output=…]` |
| Emulator | `android emulator list` / `start` / `stop` |
| List official skills | `android skills list` |

Use Knowledge Base fetch when the ticket depends on a **current** Jetpack API (Navigation 3, AGP 9, insets). Do not paste stale API from training data.

## Official skills vs this kit

Google's skills are **task playbooks** (migrate to Nav3, AGP 9, XML→Compose, R8 analyzer, CameraX, Play Billing, Wear, TV, XR). They are not defaults for every ticket.

- This kit's Android skills live in `~/.ai/skills/android/` (`compose-android`, `compose-performance`, `edge-to-edge`, `android-navigation`, `android-screenshots`, `android-performance`, `ktlint-fixer`) plus `device-driving`. `CLI skills` picks them per ticket into `RUN/skills.json`. Testing conventions come from the repo's own skill, not from this kit.
- Setup records Gradle **facts**. It does not copy the kit's skills into the repo and does not run `android skills add`.
- `android skills add --skill=…` only when the **ticket** is that migration **and** the human wants Google's playbook. Never `--all` as a side effect of a feature run.

## Do not

- `android create` inside an existing app repo.
- Treat `android run --apks=…` as a build — it does not compile; use the profile's Gradle install task.
- Replace Maestro UI driving with `android screen resolve` coordinate taps. Coordinates are last resort (`device-driving` fallback).
