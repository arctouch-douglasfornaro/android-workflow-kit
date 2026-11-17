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

## 3. Locales (detected, never guessed)

The script in step 1 prints them; `--locales` prints only them:

```bash
python3 ~/.ai/skills/release-notes/scripts/release_changes.py --locales
```

- **Store locales** come first: the keys of the store metadata file (`STORE_METADATA.json`
  `localizations`) or the folders under `fastlane/metadata/android` / `fastlane/metadata`. These are
  the languages the store listing already has, so write exactly these.
- **App languages** come from the app itself: Android `res/values-*/strings.xml`, Compose
  `composeResources/values-*`, iOS `*.lproj`, or a `Language` enum with ISO codes in Kotlin/Swift.
  Bare codes get the store's default region (`pt` → `pt-BR`, `es` → `es-ES`, `en` → `en-US`). The
  default `values/` folder has no code: the script can't tell its language, so assume en-US unless
  the strings say otherwise.
- If the app has a language the store texts don't, the script says so: ask once whether to add it.
  With no store file, write every app language. Nothing found at all: ask the user.

Write each language natively (not word-for-word): Brazilian Portuguese for pt-BR, Spain Spanish for
es-ES, Latin American Spanish for es-419, US English for en-US, and so on.

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

where `notes.json` is `{"en-US": {"whatsNew": "...", "promotionalText": "..."}, ...}` (write it in a
temp/scratch folder, not in the project). It also fails when a detected locale is missing. Shorten
and re-check until every locale passes; never cut a line in the middle.

## 5. Save (only with `save`, or when the user asks)

Write `whatsNew` and `promotionalText` into the existing store metadata file, per locale, keeping
every other field and the file's formatting (2-space JSON, UTF-8, no ASCII escaping). Don't commit
unless asked. If the project has no such file, don't create one; just show the texts.

## Output

1. **Google Play** — one code block with every locale's What's New, in the exact format the Play
   Console release notes field takes, so it is pasted once. Generate it with
   `release_changes.py --play <notes.json>` (locales sorted by tag, each wrapped in its own tag, no
   blank lines between them):

   ```
   <en-US>
   • ...
   </en-US>
   <es-ES>
   • ...
   </es-ES>
   <pt-BR>
   • ...
   </pt-BR>
   ```

2. **App Store** — for each locale, a heading, then the What's New and the Promotional Text in
   separate code blocks (App Store Connect takes them one field and one locale at a time), each with
   its character count and limit.

After that, one line with the baseline used and one line listing what was left out on purpose
(e.g. economy changes, ads, analytics).
