# android-workflow CLI

The Python package behind `python3 ~/.ai/bin/android-workflow`. Python 3.9+, standard library
only, no app-specific facts. It detects the project, records the run, runs the quality gate and
refuses to finish without a verified source diff. The coding agent writes the code.

How the workflow runs (stages, agents, levels, stops) is described in one place only:
[`skills/android-workflow/SKILL.md`](../../skills/android-workflow/SKILL.md).

## Commands

| Command | What it does |
|---|---|
| `setup --target APP [--source-repo MAIN] [--accept] [--force]` | Project profile and quality-gate facts, once per build configuration |
| `start --target APP --id ID --title "…"` | New ticket folder, `ticket-spec.json`, `change-set-map.json` |
| `resume --target APP --question-id Q --answer "…"` | Answers a business question that paused the run |
| `update-spec --target APP --surfaces ui --acceptance "a\|b"` | Planner corrections; the route is recomputed |
| `prebuild --target APP [--wait\|--status]` | Builds the unmodified app in the background for the `before` capture |
| `log --target APP --stage Implementer --status completed --file P [--tokens N]` | Records an agent stage (role names) |
| `gate --target APP` | Formatter, compile, unit tests, detekt, lint, consumer modules, secret scan |
| `evidence capture\|ingest\|compare\|list --target APP` | Screenshots/videos in `media/{before,after}/` and `media/compare.md` |
| `finish --target APP [--skip-device "reason"] [--draft "reason"]` | Final check: gate, review and device belong to the code that ships. `--draft` turns what is still unresolved into a draft PR's known issues |
| `deliver --target APP [--subject "ID: title"] [--no-commit\|--no-push\|--no-pr]` | Commits app source only, pushes, opens the PR (as a draft after `finish --draft`) |
| `status`, `list`, `clean [--ticket ID \| --stale [HOURS]]` | Run housekeeping: one ticket, unfinished idle runs, or everything when nothing is given |

## Quality gate details

- **Format first.** A formatter that can fix (`format_apply`) runs before its check, scoped to the
  touched modules; files it rewrites outside the change are restored. `quality_gates.auto_format:
  false` opts out.
- **One build.** The remaining Gradle tasks run as one `--continue` build; each still gets its own
  `outcome` (`passed`, `waived`, `failed`, `not_run`, `unverified`) in `gate-report.json`, and the
  full output goes to `RUN/gate-run<N>-attempt<M>-<unit>.log`.
- **Lint scope.** Findings only in files the change does not touch are `waived` and disclosed in
  the PR; anything in a touched file fails. `quality_gates.lint_scope: "all"` disables the waiver.
  `unverified` lint fails.
- **Consumers.** Modules that reference a changed public declaration get compile and unit tests
  (up to `quality_gates.max_consumer_modules`, default 12).
- **Feature docs.** With `feature_docs.glob` recorded, a change under a doc's `covers:` path fails
  unless that doc changed too.
- **Toolkit integrity.** If the toolkit changes after `start`, the gate is `blocked`.

Defaults live in `DEFAULT_CONFIG` (`android_workflow/cli.py`); per-project overrides in `TARGET/.ai/android-workflow.json`.

## Tests

```bash
python3 -m unittest discover -s tests
```
