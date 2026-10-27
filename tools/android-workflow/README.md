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
| `prebuild --target APP [--wait [--timeout S]\|--status] [--force]` | Builds the base commit's app in the background for the `before` capture, in the repository's own base checkout (`$ANDROID_WORKFLOW_STATE_HOME`, default `~/.cache/android-workflow/base/<repo>/checkout`), never in TARGET; its APKs are cached by commit (`…/apks/<sha>/`), one build per repository at a time, inside a build slot. `execution.base_build_timeout_seconds` (default 3600: the first build of the base checkout is cold) |
| `log --target APP --stage Implementer --status started\|completed [--note …] [--file P] [--tokens N] [--slice S2]` | Records when an agent starts and ends (role names); `started` opens the clock the office counts live. Without `--tokens`, a `completed` reads them from the host's transcript (Claude Code, Codex). `--slice`: one Implementer of a team |
| `skills --target APP [--add NAME] [--drop NAME]` | Writes `RUN/skills.json`: the Android skills each agent reads and why, picked from the ticket, the likely files, the code already changed and a failing gate. The app's own skills (`.ai/skills`, `.claude/skills`, `.agents/skills`) win over the kit's (`skills/android/`). `start`, `update-spec` and every Implementer `started` refresh it; `--add`/`--drop` are kept |
| `team --target APP [--check]` | Checks the Tech Lead's `team-plan.json` (one owner per file, at most 3 slices); `--check` writes `team-report.json`: which slice touched what, and what nobody owned |
| `emulator --target APP [--wait\|--stop\|--status] [--avd NAME] [--headless]` | The run's own device, leased until `finish`: a ready device nobody leases (an emulator already open, a phone), else one more emulator started for this run in the background, up to `device.max_emulators` (default 2; `device.avd`, else an AVD not running yet; the same AVD a second time runs `-read-only`); a leased emulator that died is replaced. `--wait` until it booted; `--stop` gives the device back and closes the emulators the workflow started that no lease holds and no other live run is waiting for. A device the developer opened is never closed, and no AVD is ever created |
| `gate --target APP` | Formatter, compile, unit tests, detekt, lint, consumer modules, secret scan. Runs inside one of the machine's build slots (`execution.max_parallel_builds`, default 2, or `ANDROID_WORKFLOW_BUILD_SLOTS`): with every slot taken by other runs it waits and logs why |
| `device replay --target APP [--serial S] [--phase before\|after]` | Runs the Device agent's saved Maestro flows (`RUN/media/flows/`) by code, a failing flow once more. `after` (default) installs the current build and files each flow's captures under `media/after/`; `before` installs the cached base APK when there is one (`--install-base` requires it: the late `before` after the `after` round) and files them under `media/before/`; on the base build, a flow that fails at the new element still files what it captured before that step. `after` builds the APK first with no device held (`device build`, reused when the background build already made it for this source), then `adb install`s it while it holds the run's device. A perf flow's `<flow>-gfxinfo.txt` is filed too, with frames, janky % and p50/p90/p99 in the result. A flow names its own captures (`takeScreenshot: <name>`), that name is the evidence name; `_*.yaml` are helpers, a `<flow>.sh` runs instead of its yaml (`AW_SERIAL`, `AW_MAESTRO`, `AW_OUT`, `AW_FLOW`, `AW_PHASE`). A logcat `.sh` that writes `<flow>-events.txt` (only the matching lines) passes on that count (`events`). Writes `replay.json` / `replay-before.json` with the time per flow, `build_seconds`, `install_seconds`, `flows_seconds` and `over_budget`, measured on the flows alone (`device.after_budget_seconds`, default 300; `device.flow_timeout_seconds`, default 180) |
| `device scaffold\|build\|install\|release --target APP [--serial S]` | `scaffold`: flow templates in `RUN/media/flows/` from the plan's navigation recipe (`_nav.yaml` with the steps as TODOs, one flow per evidence name, a network `.sh` for error/retry names, a `dumpsys gfxinfo` `.sh` on a performance ticket, a logcat `.sh` for an `audio`/`analytics` ticket that keeps only the lines matching `device.event_log_source` plus the event), never overwriting a flow, plus `known_flows` that earlier tickets of the repository proved. `build`: the current APK in a build slot with no device held, kept in `RUN/after-build.json` per source fingerprint (`next` starts it in the background with the gate). `install`: that APK `adb install`ed inside the device's lease, with its sha256 (Setup's Gradle install task only when there is no `build` command or it wrote no APK). `release`: give back this run's device lease (a command still on the device keeps it until it exits). A passing `after` replay keeps the run's flows in the repository's library under `<ticket>@<worktree>`. A Device `log … completed` with PASS is refused unless `replay.json` shows a flow passed for the current source, or the report has `Maestro: unavailable — <why>`; a PASS with an `# Anomalies` entry (or `PARTIAL`) is partial |
| `emulator … --wait-free` | Blocks until this run has a booted device of its own (another run finishes and gives one back, or one more emulator boots) |
| `evidence capture\|ingest\|compare\|list --target APP` | Screenshots/videos in `media/{before,after}/` and `media/compare.md` |
| `finish --target APP [--skip-device "reason"] [--keep-device "reason"] [--draft "reason"]` | Final check: gate, review and device belong to the code that ships. `--draft` turns what is still unresolved into a draft PR's known issues; `--keep-device` keeps an earlier device PASS for a PR feedback round with nothing visible on the device |
| `deliver --target APP [--subject "ID: title"] [--no-commit\|--no-push\|--no-pr] [--keep-body]` | Commits app source only, pushes, opens the PR (as a draft after `finish --draft`). `--keep-body`: a PR feedback round leaves the open PR's description as it is |
| `pr status\|wait\|reply\|rerequest\|ci\|sync\|merge --target APP [--pr N]` | PR Watcher. `status` reads the PR once (comments, threads, reviews, checks with `isRequired`, reviewers, mergeability) and returns one `action`: `respond`, `sync`, `merge`, `wait`, `needs_user`, `merged`, `closed`; the comments themselves go to `RUN/pr-inbox.json`. `wait` polls until there is something to do (`--timeout`, default 540 s). `reply --item ID --body "…"` answers a thread (or quotes a top-level comment), `--skip "why"` marks it handled; threads are never resolved. `ci --check NAME --as ours\|unrelated\|rerun` records a failing check's verdict (`rerun`: GitHub Actions, once). `sync [--continue]` merges the PR branch and then the base in and pushes, never rebasing or force-pushing. `merge` merges only when a fresh read is ready, at the checked head, never with `--admin`. GitHub only; with several `gh` accounts the one that sees the repository is used |
| `office [--target APP] [--no-open] [--prune [--days N]]` | Opens the one pixel-art office for every app and run (`hub.py`, in `~/.local/state/android-workflow/office/` or `$ANDROID_WORKFLOW_OFFICE_HOME`): a Project and a Workflow selector switch it in place. `--target` publishes that app's runs and opens it at its current run; `start`, `status` and `office` print `office_url`, the `file://` link (Cmd+click) that opens it at the app's current run, `office_message` (the `🏢 **Office:** <link>` line the host pastes as is) and, on stderr after the JSON, a banner with the same link; the app's `.ai/workflow/office.html` only points there. Every command keeps it current, and while a run is live a watcher (`live.py`) rewrites that run's `runs/<app>/<ticket>.js` between commands, so clocks, edited files and activity update in place. `--prune` drops runs whose app or run folder is gone (a removed worktree); `--days N` also past runs idle that long; `clean` drops an app's runs |
| `status`, `list`, `clean [--ticket ID \| --stale [HOURS]]` | Run housekeeping: one ticket, unfinished idle runs, or everything when nothing is given |

## Quality gate details

- **Format first.** A formatter that can fix (`format_apply`) runs before its check, scoped to the
  touched modules; files it rewrites outside the change are restored. `quality_gates.auto_format:
  false` opts out.
- **One build.** The remaining Gradle tasks run as one `--continue` build; each still gets its own
  `outcome` (`passed`, `waived`, `failed`, `not_run`, `unverified`) in `gate-report.json`, and the
  full output goes to `RUN/gate-run<N>-attempt<M>-<unit>.log`.
- **Lint scope.** Findings only in files the change does not touch are `waived` and disclosed in
  the PR, and so are Android lint findings on lines of a touched file that the change did not write
  (`lint_scope: "changed_lines"`, the default; the base is the merge-base with the default branch
  once it was merged in). `"changed_files"` fails every finding in a touched file; `"all"` disables
  the waiver.
  `unverified` lint fails.
- **Consumers.** Modules that reference a changed public declaration get compile and unit tests
  (up to `quality_gates.max_consumer_modules`, default 12).
- **Feature docs.** With `feature_docs.glob` recorded, a change under a doc's `covers:` path fails
  unless that doc changed too.
- **Toolkit integrity.** If the toolkit changes after `start`, the gate is `blocked`.

Defaults live in `DEFAULT_CONFIG` (`android_workflow/cli.py`); per-project overrides in `TARGET/.ai/android-workflow.json`.

Optional overrides read by the newer commands (all may be left out):

| Key | Default | Used by |
|---|---|---|
| `device.avd` | the first AVD `emulator -list-avds` prints | `emulator`: which AVD to open when nothing is connected |
| `device.emulator_headless` | `false` (a window you can watch) | `emulator`: `true` starts it with `-no-window` |
| `device.boot_timeout_seconds` | `300` | `emulator --wait` |
| `device.flow_timeout_seconds` | `180` | `device replay`: one flow's time limit |
| `device.after_budget_seconds` | `300` | `device replay`: `over_budget` in `replay.json`, and the Device `after` agent's budget |
| `team.max_parallel` | `3` (1–3) | `team`: how many Implementers may work at once |
| `skills.max` | `4` (1–8) | `skills`: how many skills the Implementer reads |
| `watch.auto_merge` | `true` | `pr`: merge when ready; `false` stops at "ready to merge" |
| `watch.require_review` | `false` | `pr`: always wait for an approving review |
| `watch.unreviewed_merge_after_minutes` | `30` | `pr`: with no reviewer requested, how long the PR stays open before it merges |
| `watch.optional_check_grace_minutes` | `30` | `pr`: a pending check that is not required stops blocking this long after the last push |
| `watch.merge_method` | first allowed of squash, merge, rebase | `pr merge` |
| `watch.max_rounds` / `watch.max_hours` | `6` / `72` | `pr`: hand the PR to the developer after this many fix rounds or hours |
| `watch.bots` | `[]` | `pr`: bots whose top-level comments count as feedback (inline comments always do) |

Set `ANDROID_WORKFLOW_NO_WATCH=1` to keep the office from starting its background watcher (the tests do,
and point `ANDROID_WORKFLOW_OFFICE_HOME` at a throwaway folder).

## Tests

```bash
python3 -m unittest discover -s tests
```
