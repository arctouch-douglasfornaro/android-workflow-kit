---
name: compose-android
description: "Universal Jetpack Compose rules: state, lists, side effects, layout and previews. Use when the change touches @Composable code. Does not encode a design-system catalog; a project's own Compose skill wins."
---

# Compose (Android) — universal

Jetpack Compose rules that apply to **every** Android Compose project. No design-system package names, no product screens, no `features/` layout.

Load when the diff touches `@Composable` or Compose compiler/plugin. A project's own UI-kit or Compose skill (listed in `RUN/skills.json`) wins where the two disagree.

Related: `compose-performance`, `edge-to-edge`, `android-navigation`, `android-screenshots`. App-wide jank/startup: `android-performance`.

---

## Composition and state

- State that survives recomposition: `remember` / `rememberSaveable` as appropriate. Do not recompute expensive work in the composable body; `remember`/`derivedStateOf`.
- `LaunchedEffect` keys must be the values the effect depends on. `Unit`/`true` only when it should run once per enter.
- `DisposableEffect` must `onDispose` what it acquired.
- Hoist state to the lowest owner that needs it. Do not put ViewModel lookups in leaf widgets if a sibling already owns the VM.
- Never block the main thread in composition or `remember` lambdas (I/O, decode, regex on large strings).

## Lists

- `LazyColumn`/`LazyRow`/`LazyGrid`: stable `key`s for items that can move or update.
- Do not wrap Lazy lists in unbounded `Column`+`verticalScroll` (nested scroll / infinite height).
- Prefer `item`/`items` content that is restartable; avoid capturing the whole list in a lambda if a key/item lambda exists.

## Side effects and threading

- UI updates on Main. Heavy work in `viewModelScope` + injected dispatchers, not `GlobalScope`.
- `collectAsState` / `collectAsStateWithLifecycle` for flows that drive UI. Do not collect in `LaunchedEffect` *and* in the composition without a reason.
- No `runBlocking` in Composables or ViewModels used by UI.

## Layout and performance

- `Modifier` order is load-bearing (clickable vs padding vs background). Match the sibling pattern in the same module.
- Avoid allocating in `draw*` / `onDraw` every frame.
- Images: use the project's image loader if the profile or a project skill names one; otherwise Coil/Glide already on the classpath. Never decode bitmaps in composition.
- `ConstraintLayout` in Compose only when the module already uses it.

## Previews

See `android-screenshots`. New screens: `@Preview` with representative state; no network or real database.

## Tests

- Logic in ViewModel / use case / mapper — not in the Composable — so unit tests do not need Compose.
- Semantics / displayed-node asserts when a test claims the user *sees* something (not `assertExists` alone).

## Do not

- Invent a design-system component when the project has a catalog skill — use that skill.
- Copy modifiers or colors as magic hex if the module uses a theme (`MaterialTheme` or a project theme). Follow **this module**.
