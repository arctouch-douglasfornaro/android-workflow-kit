---
name: android-performance
description: "Universal Android performance (main thread, lists, allocations, images, startup). Use when the ticket mentions jank, scroll, startup, memory or slowness."
---

# Android performance

Load when the ticket mentions jank, scroll, startup, memory or slowness. Product budgets (startup
ms, screen names) come from the project profile or `AGENTS.md`. Compose specifics:
`compose-performance`.

- Prove it: with a numeric budget in the ticket, measure with the tool the profile names
  (Macrobenchmark, Simpleperf, systrace); without one, say so and never invent numbers. A green unit
  suite is not a performance proof.
- No new Application or `ContentProvider` startup work unless the ticket asks for it.
- Never enable R8/minify in debug "for performance", add a baseline profile when the repo has no
  such pipeline, or quote an SLA that is not in the ticket or profile.
