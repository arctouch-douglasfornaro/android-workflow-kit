You are the android-workflow Media analyst (aw-media). Read ~/.ai/skills/android-workflow/agents/aw-media.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/PR-44
CLI: {CLI}
mode: pr

`CLI review next --target TARGET` returned:

```json
{
  "next": "checks",
  "why": "independent checks, all at once",
  "run": [
    {
      "agent": "aw-media",
      "mode": "pr",
      "stage": null,
      "read": [
        "RUN/pr-context.json",
        "RUN/review-spec.json",
        "RUN/media/pr/"
      ]
    }
  ]
}
```
