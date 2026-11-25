You are the android-workflow Planner (aw-planner). Read ~/.ai/skills/android-workflow/agents/aw-planner.md and act exactly as that role.

TARGET: {TARGET}
RUN: {TARGET}/.ai/workflow/TASKS-5
CLI: {CLI}
mode: standard

`CLI next --target TARGET --level standard` returned:

```json
{NEXT_JSON}
```

Ticket TASKS-5: Show a "Completed" label on done tasks in the task detail screen

When a task is done, the task detail screen should show a "Completed" label under the title, so people see it at a glance without reading the checkbox. Open tasks show no label.
