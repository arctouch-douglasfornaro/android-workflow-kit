---
name: edge-to-edge
description: "Universal Android edge-to-edge and window insets (system bars, IME, lists, FAB, dialogs). Use when UI can sit under the status or navigation bar or the keyboard. Never bumps the SDK."
---

# Edge-to-edge — universal

Draw behind system bars and keep tappable UI out of them. Applies to every Android app that ships UI (Compose or Views). Not a product theme, not a migration ticket unless the ticket says so.

**Do not bump `targetSdk` / `compileSdk` as a side effect.** If the profile's target SDK is below 35, still apply insets with `enableEdgeToEdge()`; leave SDK bumps to the ticket.

Sources: [Set up edge-to-edge](https://developer.android.com/develop/ui/compose/system/setup-e2e), [Window insets](https://developer.android.com/develop/ui/compose/system/window-insets). Official task skill (opt-in, not this kit): [android/skills `system/edge-to-edge`](https://github.com/android/skills/tree/main/system/edge-to-edge).

Load when the diff touches an `Activity`, `Scaffold`, `LazyColumn`/`LazyGrid`, FAB, `TextField`, dialog, or system-bar colors.

---

## Activity

- Call `enableEdgeToEdge()` (ActivityX) **before** `setContent` / `setContentView` on every Activity this change touches that does not already call it. Do not add a second call.
- Manifest: `android:windowSoftInputMode="adjustResize"` on Activities that show a keyboard. Do not use the deprecated `SOFT_INPUT_ADJUST_RESIZE` API.
- Prefer `enableEdgeToEdge()` from `ComponentActivity` — it sets status/nav icon contrast. If the Activity uses `WindowCompat.enableEdgeToEdge` instead, icon contrast must be set explicitly (`isAppearanceLightStatusBars` / `isAppearanceLightNavigationBars` inverted vs dark theme). Do not do both.

## Insets — pick one path, do not stack

1. **Scaffold (preferred when the screen already has one):** pass `innerPadding` into the content. Lists: `contentPadding = innerPadding` + `Modifier.consumeWindowInsets(innerPadding)` on the list. **Do not** also `Modifier.padding(innerPadding)` on a parent of a Lazy list (clips scroll-behind).
2. **Material 3 chrome** (`TopAppBar`, `BottomAppBar`, `NavigationBar`, `ModalBottomSheet`, drawer sheets): let the component consume its insets. Do not pad the parent *and* the bar.
3. **No Scaffold:** `Modifier.windowInsetsPadding(WindowInsets.safeDrawing)` or `safeDrawingPadding()` on the content that must stay tappable.

Never apply the same inset twice (Scaffold `contentWindowInsets = WindowInsets.safeDrawing` **and** `imePadding()` on the child).

## IME

- Keyboard must not cover the focused field.
- If Scaffold already uses `contentWindowInsets = WindowInsets.safeDrawing`, do **not** add `imePadding()` on the child.
- If Scaffold uses the default (no IME in `contentWindowInsets`), add `imePadding()` **before** `verticalScroll`.
- `NavigationSuiteScaffold` / list-detail scaffolds often do **not** pass `PaddingValues` inward — inset the **inner** list/FAB, not the scaffold parent (`safeDrawingPadding` on the parent clips edge-to-edge).

## Lists, FAB, dialogs

- First/last Lazy items: inset via `contentPadding`, not parent padding.
- FAB: inside Scaffold, or `safeDrawingPadding()` on the FAB.
- Full-screen `Dialog` (`usePlatformDefaultWidth = false` + `fillMaxSize`): `DialogProperties(decorFitsSystemWindows = false)` and inset the dialog content.

## Views interop

Same contract: `WindowCompat.setDecorFitsSystemWindows(window, false)` (or `enableEdgeToEdge()`), `WindowInsetsCompat` padding on the root that must stay tappable, `fitsSystemWindows` only if that is already the module's pattern. Do not mix `fitsSystemWindows=true` on a child that Compose already padded.

## Do not

- Invent a scrim/status-bar overlay if the existing theme already handles contrast.
- Change navigation-bar contrast (`isNavigationBarContrastEnforced`) unless a bottom bar is drawing into the nav-bar region and the module already does this.
- Treat this skill as "migrate the whole app" unless the ticket is that migration.
