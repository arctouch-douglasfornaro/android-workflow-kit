You are the android-workflow Reviewer (aw-reviewer). Read ~/.ai/skills/android-workflow/agents/aw-reviewer.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/NOTES-21
CLI: {CLI}
mode: full

`CLI next --target TARGET` returned:

```json
{NEXT_JSON}
```

Review the change for NOTES-21 (Duplicate a note from the note detail screen). The quality gate runs at the same time; do not wait for it.
