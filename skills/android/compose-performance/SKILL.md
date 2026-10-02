---
name: compose-performance
description: "Universal Jetpack Compose performance: deferred reads, skipping, Lazy keys, no allocations in draw. Use when the change touches Compose lists or animation, or the ticket mentions jank, scroll or recomposition."
---

# Compose performance — universal

Compose-specific jank and wasted recomposition. App-wide rules (main thread, images, startup, R8) stay in `android-performance`.

Load when the diff touches `@Composable`, lists, animation, or a jank/scroll ticket.

Sources: [Compose performance](https://developer.android.com/develop/ui/compose/performance), [Follow best practices](https://developer.android.com/develop/ui/compose/performance/bestpractices), [Strong Skipping](https://developer.android.com/develop/ui/compose/performance/stability/strongskipping). There is **no** official Android skill for this — Google's catalog skips "basic Compose" on purpose; this kit still pins the rules agents miss.

---

## Phases

Defer reads of rapidly changing state to **layout or draw**, not composition:

- `Modifier.offset { IntOffset(...) }` / `drawBehind { }` reading `animatable.value` — not `Modifier.offset(x.dp)` that reads state in composition.
- Pass lambdas (`() -> T`) or `State<T>` into children that should not recompose when `T` changes.

Do not allocate in `draw*` / `onDraw` every frame (new `Brush`, `Path`, `Paint`, string concat).

## Skipping

- Parameters should be stable (immutable data, `ImmutableList`, primitives). Do not wrap unstable types in a new lambda/`List` at the call site every composition if a sibling already hoists a remembered value.
- `key(...)` in Lazy items that move or update. `items(list, key = { it.id })`.
- Do not read a high-frequency state (scroll, pointer, animation) in a parent that also composes a large child tree — read it in the leaf or in a layout/draw modifier.

## Lists

- Item content cheap: no `ViewModel` lookup per row if the parent can pass a row model.
- Do not nest Lazy in unbounded scroll.
- `derivedStateOf` for values derived from frequently changing state when the **derived** value changes much less often (e.g. "scrolled past threshold").

## Effects

- `LaunchedEffect` keys = the values the effect uses. Missing keys = stale captures and extra work.
- Do not `collect` a flow in composition and again in an effect.

## How to prove

- If the ticket names jank: Layout Inspector / Compose recomposition counts, or the profiler the profile names. A green unit suite is not proof.
- Do not add Macrobenchmark or a baseline profile unless the repo already has that pipeline (`android-performance`).

## Do not

- "Optimize" by disabling strong skipping or turning on minify in debug.
- Rewrite working UI to `BoxWithConstraints` / `SubcomposeLayout` for performance without evidence.
- Quote frame-time SLAs that are not in the ticket or profile.
