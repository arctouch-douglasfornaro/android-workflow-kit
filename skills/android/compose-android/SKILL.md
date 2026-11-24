---
name: compose-android
description: "Universal Jetpack Compose rules: state, lists, side effects, layout and previews. Use when the change touches @Composable code. Does not encode a design-system catalog; a project's own Compose skill wins."
---

# Compose (Android)

Load when the diff touches `@Composable` code. A project's own UI-kit or Compose skill (listed in
`RUN/skills.json`) wins where the two disagree. Related: `compose-performance`, `edge-to-edge`,
`android-navigation`, `android-screenshots`.

- Follow **this module**: its state holder, theme, image loader, `Modifier` order and layout
  choices (`ConstraintLayout` only where the module already uses it). Never hex colors where a theme
  exists, never a new design-system component when the project has a catalog skill.
- Logic lives in the ViewModel, use case or mapper, so unit tests do not need Compose. A test that
  claims the user *sees* something asserts on displayed semantics, not `assertExists` alone.
- New screens get a `@Preview` with representative state and no network or real database
  (`android-screenshots`).
