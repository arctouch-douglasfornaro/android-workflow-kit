---
name: release-notes
description: Write the store texts for a new app release — "What's New" / release notes (App Store and Google Play) and the App Store Promotional Text — in every store locale, from the commits since the last store notes. Use for /release-notes, "what's new", "release notes", "notas da versão", "texto promocional", "promotional text" or "prepare the store texts for the release".
---

# Release Notes

Turn what changed since the last release into player-facing store texts, in every locale the app
ships in, within the store limits. Writes text only: never uploads to a store, never submits a
review, never changes the app version or build number.

## Input

`$ARGUMENTS` (or the user's words when the host has no arguments), all optional:

- `since <ref>` — commit, tag or date (YYYY-MM-DD) to start from. Default: see step 1.
- `save` — also write the texts into the project's store metadata file (step 5). Default: only show them.
- `version <x.y.z>` — the version being released, to label the output. Default: read it from the project.

## 1. Find the baseline (what players already saw)

Run, from the project root:

```bash
python3 ~/.ai/skills/release-notes/scripts/release_changes.py [--since <ref>]
```

It prints the version found in the project, the baseline it picked and why, and the `feat`/`fix`
commits since then. Default baseline, in order: the last commit that changed the store metadata file
(`STORE_METADATA.json`, `fastlane/metadata/**`, `metadata/**` or `store/**`), else the latest tag,
else the last 30 commits. If the user says a store never shipped the previous notes (e.g. the App
Store version stayed in draft), ask once whether to merge those older notes into this release.

## 2. Pick what goes in

Read each commit subject (and its body or diff when the subject is unclear). Keep only what a
player notices:

- **In:** new modes, screens, features and rewards; visible redesigns; easier controls; bug fixes
  players hit (one grouped line, e.g. "Fixes for small screens and combos").
- **Out:** analytics, ads and monetization tuning, economy nerfs (fewer coins or boosters), refactors,
  tests, CI, tooling, website, store assets, debug-only features. Never announce something that
  makes the game worse for the player; never mention internal names, task numbers or people.
- Merge commits about the same feature into one line. Lead with the biggest changes.
- Use the app's own feature names in each language (look them up in the app's string files, e.g.
  `Localization.kt`, `strings.xml`, `Localizable.strings`), so the notes match the UI.

## 3. Locales

Write every locale the project ships in the stores. Find them in the store metadata file (keys like
`en-US`, `pt-BR`, `es-ES`) or `fastlane/metadata/{android,ios}/<locale>`. If none is found, use the
app's UI languages and say so. Write each language natively (not word-for-word): Brazilian
Portuguese for pt-BR, Spain Spanish for es-ES, US English for en-US.

## 4. Texts and limits

For each locale:

- **What's New** — short bullet lines starting with `• `, one change per line, 6–10 lines, friendly
  and concrete ("Daily Missions: three goals a day with coin rewards."). **≤ 500 characters**, so the
  same text works as Google Play release notes (500) and App Store What's New (4000).
- **Promotional Text** (App Store only, can change without a new build) — one or two sentences
  selling the highlights of this release. **≤ 170 characters.**

Check the limits with the script (it counts characters the way the stores do):

```bash
python3 ~/.ai/skills/release-notes/scripts/release_changes.py --check <notes.json>
```

where `notes.json` is `{"en-US": {"whatsNew": "...", "promotionalText": "..."}, ...}`. Shorten and
re-check until every locale passes; never cut a line in the middle.

## 5. Save (only with `save`, or when the user asks)

Write `whatsNew` and `promotionalText` into the existing store metadata file, per locale, keeping
every other field and the file's formatting (2-space JSON, UTF-8, no ASCII escaping). Don't commit
unless asked. If the project has no such file, don't create one; just show the texts.

## Output

For each locale, a heading, then the What's New and the Promotional Text in separate code blocks
(easy to copy), each with its character count and limit. After that, one line with the baseline
used and one line listing what was left out on purpose (e.g. economy changes, ads, analytics).
