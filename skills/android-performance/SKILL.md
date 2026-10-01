# Android performance — universal

Applies to every Android (Views or Compose) Gradle app. Product-specific budgets (startup ms, screen names) belong in the project profile or `AGENTS.md`, not here.

Load when the ticket mentions jank, scroll, startup, memory, or when the designer/reviewer asks.

---

## Main thread

- No disk, network, or bitmap decode on the main thread.
- No `runBlocking` on Main.
- Compose: `compose-android` + `compose-performance` (no heavy work in composition; defer high-frequency reads).

## Lists and bind

- RecyclerView / Lazy lists: stable ids/keys; onBind/item content must be cheap.
- Do not inflate or compose the world inside bind. Overdraw: avoid stacked full-screen opaque layers.

## Allocations

- Hot paths (draw, bind, scroll): no unnecessary allocations, no hidden `toList()` on every frame, no string concat in `onDraw`.
- Logging in hot paths must be stripped or guarded.

## Images and media

- Size to the view, not full-resolution decode.
- Cancel requests when the row/composable leaves the window (loader default, don't fight it).

## Startup

- Do not add Application/`ContentProvider` work unless the ticket requires it.
- Heavy init: lazy, not in `Application.onCreate`.

## How to prove

- If the ticket has a numeric budget, measure with the tool the profile names (Macrobenchmark, Simpleperf, systrace). If none, say so — do not invent numbers.
- A green unit suite is not a performance proof.

## Do not

- Enable R8/minify "for performance" in debug.
- Add a baseline profile unless the repo already has that pipeline.
- Quote a product SLA that is not in the ticket or profile.
