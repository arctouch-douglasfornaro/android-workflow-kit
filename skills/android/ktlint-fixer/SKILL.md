---
name: ktlint-fixer
description: "Runs the Kotlin formatter or linter this repo actually uses (kotlinter, ktlint-gradle, Spotless, detekt). Use when the quality gate reports format or lint findings in a touched file."
---

# KtLint / Kotlin format — universal

Runs **whatever Kotlin formatter/linter this repo actually uses**. The engine and task names come from `<repo>/.ai/project-profile.md` (and `format_apply` / `format_check` in the gate), not from this file. The quality gate already runs the formatter first; use this only for findings it could not fix.

---

## Resolve the engine

1. Read the profile's lint and format commands.
2. If the profile is missing, grep Gradle for:
   - `org.jmailen.kotlinter` → check `lintKotlin`, format `formatKotlin`
   - `org.jlleitschuh.gradle.ktlint` → `ktlintCheck` / `ktlintFormat`
   - Spotless + ktlint → `spotlessCheck` / `spotlessApply`
   - Detekt-only → `detekt` (report; do not pretend it is ktlint)
3. If nothing matches: `engine: none`. **Do not fail the workflow** — record n/a.

Never assume `lintKotlin`.

---

## Check

Run the **check** task on modules the diff touches (profile compile/test sibling pattern). Green → done.

## Format

Run the **format** task, then check again. Remaining violations: read the tool's report path if the profile names one; otherwise the Gradle log.

## Rules this skill does not own

Style rules live in the repo's `.editorconfig` / ktlint config. Do not invent indent width or filename rules that contradict the repo.
