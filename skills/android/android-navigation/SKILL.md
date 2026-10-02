---
name: android-navigation
description: "Universal in-app navigation: follow this repo's existing NavHost, graph or fragments. Use when adding a destination, deep link or back-stack hop. Never migrates to Navigation 3."
---

# In-app navigation — universal

How to **add or change a screen hop in this repo**. Not a mandate to adopt Navigation 3.

Load when the diff adds a destination, deep link, back stack, or tab hop.

Sources: [Navigation](https://developer.android.com/guide/navigation), [Compose Navigation](https://developer.android.com/develop/ui/compose/navigation). Official **opt-in** skill for a Nav3 migration only: [android/skills `navigation/navigation-3`](https://github.com/android/skills/tree/main/navigation/navigation-3). Do not load that skill to "modernize" a Nav2/Fragment app.

---

## Follow the graph that exists

1. Read the profile's navigation notes (if any) and grep this module for `NavHost`, `NavDisplay`, `NavController`, `rememberNavController`, fragment `nav_graph`, or a custom navigator.
2. Add the new hop **the same way** the nearest sibling screen does. Same argument types, same back-stack pattern, same DI scope for the destination ViewModel.
3. Do **not** add a second navigation library. Do **not** migrate Nav2 → Nav3, XML graphs → Compose, or Fragments → Compose because a blog post prefers it.

If the profile says nothing, the first matching pattern in the **same module** is the convention. Record what you followed in the implementation notes.

## Routes and arguments

- Reuse the existing route type (string route, `NavKey`, typed routes, enum). Do not invent a parallel scheme.
- Optional args stay optional at existing call sites. A new required arg without updating every caller is a bug.
- Restore state the way this graph already does (`saveState`/`restoreState` on tabs, `rememberSaveable` on the screen, etc.). Do not add a new back-stack library.

## Deep links

- Use the scheme/host from the profile when present. Match an existing `navDeepLink` / manifest intent-filter in this app.
- Device hops for verification: `device-driving` (Maestro). This skill is **in-process** navigation code, not adb.

## Adaptive / multi-pane

- If the module already uses `ListDetailPaneScaffold` / `NavigationSuiteScaffold` / window-size classes, extend that. If it does not, a phone `NavHost` is enough — do not introduce canonical two-pane + Nav3 Scenes on a phone-only ticket.

## Do not

- Force Hilt `hiltViewModel()` if the module uses Koin / manual factories / no DI.
- Put business logic in the navigator.
- Deep-link to a non-exported Activity with `am start -n` and call it done — use the profile's working deep-link form.
