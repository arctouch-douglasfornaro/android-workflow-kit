---
name: aw-delivery
description: "android-workflow Delivery: writes pr-description.md from the repo PR template, then runs `CLI deliver`, which commits app source only, pushes and opens the PR/MR (a draft when the run stopped with open issues). Never force-pushes, skips hooks or commits workflow artifacts."
kind: local
---

Pointer only. Read `~/.ai/skills/android-workflow/agents/aw-delivery.md` and act exactly as that role:
it is the source of truth for this agent and wins over anything written here.
