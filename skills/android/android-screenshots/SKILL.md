---
name: android-screenshots
description: "Universal Android screenshots and Compose previews. Use for @Preview of a changed screen, or when the Device stage needs a still. Never adds a screenshot library."
---

# Screenshots and previews — universal

How to **prove a UI change** with a picture. Capture tooling is not a new test framework.

Load when the diff is user-visible, when writing `@Preview`, or when the Device stage needs a still.

Sources: [Compose tooling / Preview](https://developer.android.com/develop/ui/compose/tooling/previews), [Compose preview screenshot testing](https://developer.android.com/studio/preview/compose-screenshot-testing). Device capture: Android CLI `android screen capture` when `android` is on PATH ([Android CLI](https://developer.android.com/tools/agents/android-cli)); otherwise Maestro via `device-driving`.

---

## Static UI (no motion)

1. New or changed screens: a `@Preview` (or this repo's preview annotation) with representative state. No network, no real DB, no activity lookup.
2. If the profile **Preview screenshots** / **Screenshot** row names a task (`compose preview screenshot`, Roborazzi, Paparazzi, …): run **that** task. Do not add another screenshot library.
3. If the row is empty / `none`: Preview is enough for the still. The Device stage may grab a frame. Do not introduce Compose screenshot testing, Roborazzi, or Paparazzi "while here".

## Motion / interaction

The Device stage + `device-driving` (Maestro). A Preview is not proof of a swipe or IME.

## Device stills (when a device is connected)

Preference order:

1. `android screen capture --output=<path>` if `command -v android` succeeds.
2. Maestro screenshot step (`device-driving`).
3. `adb exec-out screencap -p` only under the device-driving fallback rule.

Inspect hierarchy with `android layout` (JSON) when the CLI is present, instead of dumping the entire `uiautomator` XML.

Write files under `RUN/media/` through `CLI evidence ingest`. Do not commit device PNGs to `src/` unless the repo already stores goldens there.

## Goldens and scaffolding

- Update goldens only when the profile has a screenshot pipeline **and** the ticket's visual change is intentional.
- Delete `src/screenshotTest/**` (or equivalent) scaffolding added only to grab a bitmap before the Device stage returns.

## Do not

- Assert `assertExists` on a zero-size node and call it visual proof — assert real geometry.
- Check in annotated / debug overlays (`android screen capture --annotate`) as product evidence.
