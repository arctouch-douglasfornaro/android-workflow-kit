You are the android-workflow Planner (aw-planner). Read ~/.ai/skills/android-workflow/agents/aw-planner.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/NOTES-12
CLI: {CLI}
mode: standard

`CLI next --target TARGET --level standard` returned:

```json
{NEXT_JSON}
```

Ticket NOTES-12: Show when a note was last edited on the note detail screen

On the note detail screen, under the title, show when the note was last edited, like "Edited 5 minutes ago". People keep asking whether the note they are looking at is the latest version.
