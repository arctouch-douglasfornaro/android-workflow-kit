You are the android-workflow Reviewer (aw-reviewer). Read ~/.ai/skills/android-workflow/agents/aw-reviewer.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/PR-41
CLI: {CLI}
mode: pr

`CLI review next --target TARGET` returned:

```json
{
  "next": "checks",
  "why": "independent checks, all at once",
  "run": [
    {
      "agent": "aw-reviewer",
      "mode": "pr",
      "stage": "Reviewer",
      "read": [
        "RUN/pr-context.json",
        "RUN/review-spec.json",
        "RUN/ticket/NOTES-30.md"
      ]
    }
  ]
}
```

Review PR #41 (NOTES-30 Search also finds archived notes). The worktree is checked out at the PR head.
