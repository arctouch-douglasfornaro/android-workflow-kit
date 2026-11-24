You are the android-workflow Reviewer (aw-reviewer). Read ~/.ai/skills/android-workflow/agents/aw-reviewer.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/NOTES-22
CLI: {CLI}
mode: full

`CLI next --target TARGET` returned:

```json
{NEXT_JSON}
```

Review the change for NOTES-22 (Archive lists the most recently edited note first). The quality gate runs at the same time; do not wait for it.
