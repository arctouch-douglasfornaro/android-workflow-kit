You are the android-workflow PR Watcher (aw-pr-watcher). Read ~/.ai/skills/android-workflow/agents/aw-pr-watcher.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/NOTES-22
CLI: PATH={FAKEBIN}:$PATH python3 ~/.ai/bin/android-workflow
AW_STAGE=PR Watcher
mode: triage

`CLI pr wait --target TARGET` returned:

```json
{
  "action": "respond",
  "pr": 42,
  "items": 5,
  "ci": 1,
  "note": "read RUN/pr-inbox.json"
}
```
