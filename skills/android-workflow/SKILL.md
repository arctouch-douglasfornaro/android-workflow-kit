---
name: android-workflow
description: >-
  Host-driven Android ticket pipeline: plan, implement with tests, gate,
  independent review, device verification with before/after evidence, PR, and a
  PR Watcher that answers review comments, fixes CI and merges when approved.
  Use when the user types /android-workflow, /Android Workflow, or asks to run
  this workflow on an Android ticket or Jira id.
disable-model-invocation: true
---

# Android Workflow

This file is the single source of truth for the workflow. Each agent's own rules live in
`agents/aw-*.md` next to it.

**Goal:** a precise PR, fast and cheap. Every rule below serves one of the three: precision
(project patterns, verifiable AC, tests, gate, independent review, device proof), speed
(parallel work, scoped builds, skipped stages on simple tickets) and cost (small orchestrator
context, the cheapest model tier that does each job).

You are the **orchestrator**. The Python CLI detects, locates, gates and records. Seven agents do
the work; you keep your own context small (paths and ≤10-line returns only — never read the
diff, Gradle logs or images yourself).

## Invocation

```text
/android-workflow <ticket-id> <title or description> [--target PATH] [--ticket FILE]
    [--level express|standard|full] [--worktree] [--no-device]
    [--no-commit] [--no-push] [--no-pr] [--no-watch] [--base BRANCH]
/android-workflow <ticket-id> --watch [--pr NUMBER]      (only the PR Watcher, for a PR already open)
```

Codex: `$android-workflow`. If the user omitted fields, ask once. Do not invent acceptance
criteria or business rules. Delivery (commit, push, PR) is **on by default**: invoking the
workflow authorizes it, and **every run that produced code ends in a PR** — ready for review when
every check passed, a draft listing what is still open when it did not. The `--no-*` flags stop
earlier. Once the PR is open, the **PR Watcher** follows it until it is merged (step 5) unless
`--no-watch`, `--no-pr`, `--no-push` or `--no-commit` was given.

**Delivery is never skipped silently.** The `/android-workflow` command *is* the user's explicit,
in-this-turn request to commit, push and open the PR: a standing memory or preference such as "never
commit or push on your own" is satisfied by it and does not stop Delivery (only a `--no-*` flag does).
A permission prompt or a denied tool call is not the user deciding against the PR:
- denied on a bookkeeping command (`CLI log`, `CLI status`): it changes nothing in git; continue the
  run (log again later with a neutral note) and keep Delivery going;
- denied on `CLI deliver` itself: do not stop and do not work around it; tell the user in one line
  that the PR is ready to open and ask them to approve that command (or allow
  `python3 ~/.ai/bin/android-workflow` in their permissions), then run it again once they answer.
Keep log notes neutral (`--note "writing the PR description"`), never a list of git verbs such as
"commit, push, open PR": a host's safety check may read those words as a destructive git command.

## Resolve paths

1. `TARGET` = `--target`, else the current workspace if it has `settings.gradle(.kts)`.
2. If this repo is the toolkit (has `android_workflow/cli.py`) and TARGET was omitted, STOP.
3. `CLI` = `python3 ~/.ai/bin/android-workflow`. `RUN` = `TARGET/.ai/workflow/<ticket-id>/`.

Record `TARGET`, `RUN` and `CLI` in the first chat message, and end that message with the office
the user opens to watch the agents: `office_message` from the `start` output, copied as is, on its
own line (a bold `Office:` label and a `file://` link that one click, Cmd+click in a terminal, opens
at this run; `status` prints it too). Never reword it, build that link yourself or read that page. If the ticket id looks like a
Jira key and this host has a Jira tool (MCP server or connector), fetch the issue body and
pass it to the Planner.

## Agents

| Role | Agent | Tier | Runs |
|---|---|---|---|
| Setup | `aw-setup` | strong | only when `CLI setup` says `needs_setup_agent` (a build change, not a dependency-version bump) |
| Planner | `aw-planner` | strong | standard and full levels |
| Tech Lead | `aw-tech-lead` | standard | only for a team (`CLI next` decides, Execute 4): splits the plan into slices, then integrates them |
| Implementer | `aw-implementer` | standard | always: code **and** its unit tests; resumed for every fix round. A team runs up to 3 at once, one per slice |
| Reviewer | `aw-reviewer` | strong (delta rounds: standard) | at the same time as the gate, once the Implementer is done |
| Device | `aw-device` | standard | `before` + `after` when `device_required` |
| Delivery | `aw-delivery` | fast | after `finish`, unless `--no-commit`: writes the PR body, runs `CLI deliver` |
| PR Watcher | `aw-pr-watcher` | standard | after the PR opens, unless `--no-watch`: triages new comments and failing checks, answers on the PR |

Tier maps to opus/sonnet/haiku on Claude Code and high/medium/low reasoning on Codex;
Cursor and Gemini inherit the session model.

### How to delegate, per host

Every spawn passes the same inputs: `TARGET`, `RUN`, `CLI`, `AW_STAGE=<Role>` (`AW_STAGE=Implementer/S2`
for a slice), the level, the mode or fix-round finding IDs, and nothing else — the agent reads its
files itself. On Codex, name the agent `aw_<role>` (`aw_implementer_s2` for a slice, `aw_tech_lead`):
its prompt is not readable on disk, so the name is how the office tells whose tokens it is reading.

| Host | Spawn | Resume for a fix round |
|---|---|---|
| Claude Code | Agent tool, `subagent_type: aw-<role>` | SendMessage to the agent id |
| Codex (needs `[features] multi_agent_v2 = true`) | `spawn_agent` with agent type `aw-<role>` (`~/.codex/agents/aw-<role>.toml`) | `send_message` / `followup_task` to the same agent |
| Cursor | delegate to subagent `aw-<role>` (`~/.cursor/agents/`) | new subagent with the finding IDs |
| Gemini CLI / Antigravity (`agy agents` lists them) | subagent `aw-<role>` (`~/.gemini/agents/`) | new subagent with the finding IDs |
| Other with subagents | generic subagent: "Read `<skill>/agents/aw-<role>.md` and act as that role. Inputs: …" | same |
| No subagents | run the role inline: read only that role file, do it, write its artifact, then drop it from working memory | — |

If a named agent is not registered, use the generic-subagent row, never skip the role. Missing
registrations: run `~/.ai/link.sh` once (it links every host's folder to this kit).

## Commands (exact syntax; do not probe with `--help`)

Never read a `gh` token into the environment (`GH_TOKEN=$(gh auth token …)`): the CLI picks the
logged-in `gh` account that can see the repository by itself, and a token in the shell can leak into
logs. Pass `--target` as an absolute path; the output is compact JSON, read it whole. If the host
refuses a command (`pr merge` included), tell the developer and stop; never retry it another way.

```text
python3 ~/.ai/bin/feature_workspace.py --repo-root T --ticket ID --title "…" [--worktree]
CLI next     --target T [--no-device] [--no-watch] [--level express|standard] [--dry-run]   (the next action)
CLI setup    --target T [--source-repo MAIN]        CLI start    --target T --id ID --title "…" [--type bug --reproduction "…"] [--level L]
CLI resume   --target T --question-id Q --answer "…" CLI update-spec --target T --surfaces ui --acceptance "a|b"
CLI prebuild --target T [--wait [--if-running]|--status]           CLI gate     --target T
CLI emulator --target T [--wait] [--stop]           CLI team     --target T [--check]
CLI skills   --target T [--add NAME] [--drop NAME]    CLI delta    --target T   (since the last review)
CLI finish   --target T [--skip-device "r"] [--no-device] [--draft "r"] CLI deliver  --target T [--subject "ID: …"] [--no-push|--no-pr]
CLI log --target T --stage <Role> --status started --note "<what it is about to do>"
CLI log --target T --stage <Role> --status completed --note "…" [--file P]… [--tokens N] [--wait-seconds S]
CLI log … --stage Implementer --slice S2 …           (one Implementer of a team)
CLI office --target T                               (opens the office at this run; it updates itself)
CLI pr status|wait|merge --target T [--pr N] [--timeout S]       CLI pr sync --target T [--continue]
CLI pr reply --target T --item ID (--body "…" | --skip "why")     CLI pr rerequest --target T --reviewer LOGIN
CLI pr ci --target T --check NAME --as ours|unrelated|rerun --reason "…"
CLI finish … [--keep-device "why nothing visible changed"]     CLI deliver … [--keep-body]
CLI evidence capture|ingest|compare|list --target T [--phase before|after] [--name N] [--file F]
CLI device replay --target T [--serial S]            (re-runs the saved Maestro flows on the current build)
CLI status|list --target T                          CLI clean --target T [--ticket ID | --stale [HOURS]]
```

## Execute

1. **Workspace first, in-place by default:** run `feature_workspace.py` without `--worktree`
   unless the user asked for one. On a non-base branch it continues there; on the base branch it
   creates or reuses `<user>/<ID>-<slug>` in the same checkout. With `--worktree`, `TARGET`
   becomes the printed `worktree_path`. The script initialises what the main checkout already
   uses (initialised submodules, `local.properties`) and prints `bootstrap_*` lines; relay any
   `bootstrap_submodule … failed` or `bootstrap_not_copied` line to the user and STOP until they
   answer; no agent works around it (nor runs the repository's own worktree setup scripts).
   An `other_ticket_branches:` line means this ticket already ran: name those branches and ask
   whether to continue before `start`, so a repeat run is a choice, not an accident.
2. **Setup, once, on the final `TARGET`:** `CLI setup --target TARGET [--source-repo <main checkout>]`.
   `ready` → reuse (the profile is inherited or refreshed when only dependency versions moved).
   `needs_setup_agent` → spawn `aw-setup` with the JSON; its questions are batched to the user
   once; a missing formatter or app id never blocks a domain-only ticket.
3. **Ticket:** `CLI start` (add `--type bug --reproduction "..."`, `--acceptance "a|b"`,
   `--surfaces` when known, and the developer's `--level`). It writes `ticket-spec.json` (with the
   level the rules give) and `change-set-map.json` (the likely files).
4. **From here on, `CLI next` decides.** Run `CLI next --target TARGET [--no-device] [--no-watch]`
   and do the one thing it returns, then run it again. It reads the run's files (state, verdicts,
   source fingerprints, round counts) and answers with `next` and a one-line `why`; the same files
   always give the same answer, and each answer is appended to `RUN/decisions.jsonl`. Do not
   re-derive what it decided (level, team or single, which checks to run, fix round or draft,
   device or skip): those rules live in the CLI, not in your reading of this file. When it hands a
   stage to an agent it already logged that stage `started` (`logged`), so do not log it again; a
   repeated `next` while the agent works says `wait`.
   - `choose_level`: the rules allow express; only the ticket text can say whether it fits (`rule`).
     Run the `express` or the `standard` command it gives (`CLI next --level …`). The CLI refuses
     express for a bug, an open question or risk above low, and picks `full` itself.
   - `update_spec` (express): run `CLI update-spec` with the AC and surfaces from the ticket text (a
     visible change is `ui`), then `next`.
   - `spawn`: spawn (or, with `resume`, resume) `agent` with `mode`, `level`, `ids` (fix-round
     finding IDs) and `read` (the reports they come from), as in *How to delegate*. With `tier:
     standard` use the standard tier (Claude Code: `model: sonnet`; Codex: reasoning `medium`).
     `also` lists commands to start in the same turn, in the background (the emulator, the base
     build, a second `next` that says whether to capture the device `before`). `slices` → one
     `aw-implementer` per slice in the same turn, each logged with `--stage Implementer --slice S<n>`.
     `first` → run it before spawning. `replay` → pass it to `aw-device`.
   - `checks`: run what `run` lists in the same turn: `CLI gate --target TARGET` (long command
     timeout, e.g. 30 min) and/or `agent` (`aw-reviewer`, `mode` `full` or `delta` with the `fixed`
     IDs) in the background. The review only reads code, so it never waits for the gate.
   - `fix` (a team): resume the Implementer of each slice in `routes` with its IDs (logged with its
     `--slice`), the Tech Lead for `Tech Lead`.
   - `wait`, `wait_prebuild`, `wait_device`: an agent or a build is still running; run `command` if
     given (long timeout or in the background), else wait for the agent's return, then `next`.
   - `finish`: run `command` as is. With `draft`, the PR opens as a draft listing what is open.
   - `ask_user`: ask `question` once (batched), then run `then`.
   - `watch` / `done`: step 5 / the Done summary; run `also` (it closes the emulator the workflow
     started) first.

   Agent returns that `next` cannot read from files are yours: `NEEDS_INPUT` (Planner) → batch the
   questions to the user and STOP until answered, then resume it; `NEEDS_OTHER_SLICE` → the Tech
   Lead; `BLOCKED` from the Implementer (an environment error) → tell the user; `NEEDS_APPROVAL`
   (Delivery) → see *Invocation*. A red gate is never waived by hand; a gate that itself looks wrong
   (a misclassified waiver, a parser miss) is a toolkit defect: `finish --draft`. No agent edits the
   toolkit (`~/.ai`) during a run; `start` fingerprints it, and after a change the gate reports
   `blocked` (`toolkit_modified_during_run`) and only a draft PR can ship.

   What the CLI decides, for reference (change it in `android_workflow/decide.py`, not here):
   level `full` for high risk or complexity, lifecycle/room_migration/workmanager surfaces or a
   payments/auth ticket, express only when allowed; Planner unless express; a team for 2+
   independent parts and 4+ files (`update-spec --parallel-parts/--parallel-files`); gate and
   Reviewer together, one fix round for both; a draft after the third red gate in a row, a review
   still blocking after one fix, the second device FAIL in a row, a BLOCKED device or a fix round
   that changed nothing (in a PR round: `ask_user`, push nothing, never a draft); an approved review kept
   when only formatting changed since; the device `before` only with a passed base build and a
   device; `finish --skip-device "<reason>"` only when adb sees no device.
   The gate itself: formatter (fixing first when it can), then compile, unit tests, detekt and lint
   for the touched modules in one build, plus compile and unit tests for modules that use a changed
   declaration, and a secret scan. Lint or format findings only in files the change does not touch,
   and Android lint findings on lines of a touched file that the change did not write, are
   **waived** and disclosed in the PR; never "fix" a waived finding: it widens the diff.

5. **Watch the PR** (unless `--no-watch` or a `--no-*` flag stopped before the PR; `--watch` starts
    here for a ticket whose run exists, and with no run: `CLI start` with the PR's title, then this).
    The CLI reads the PR; you act on the one `action` it returns, then wait again. Loop:
    `CLI pr wait --target TARGET [--pr N]`: it polls GitHub (every minute, slower while nothing
    changes) and returns as soon as there is something to do. Run it in the background where the
    host allows (Claude Code: `run_in_background` with `--timeout 3300`), else in the foreground
    with the default `--timeout 540` and a 10-minute command timeout; `timed_out` → run it again.
    - `respond`: `CLI log --stage "PR Watcher" --status started --note "<n> comment(s), <m> check(s)"`,
      spawn `aw-pr-watcher` mode `triage`. It answers questions on the PR itself and returns the
      `changes`, `ask_user` and CI verdicts (`RUN/pr-feedback.json`).
      `ask_user` → ask the developer in one message (the rest goes on); their answer becomes a change,
      or a reply through `CLI pr reply`.
      `changes` → a fix round on the same Implementer (IDs `PR-1`…, `RUN/pr-feedback.json`), then the
      gate and a delta Reviewer in parallel (`CLI next`, same limits), then Device `after` only when a
      change has `device: true` (when `next` asks for the device and none has it, do not spawn it); otherwise `CLI finish --target TARGET --keep-device "<why nothing
      visible changed>"` (a plain `finish` when the route has no device). Then, with no Delivery agent:
      `CLI deliver --target TARGET --subject "<ID>: <what this round changed>" --keep-body`. Then resume
      `aw-pr-watcher` mode `reply` with the pushed `sha` and the change IDs. A gate or review that stays
      red after its fix rounds: push nothing, tell the developer what is open, keep watching.
    - `sync` (`behind` or `conflict`): `CLI pr sync --target TARGET`. `synced` → wait again.
      `conflict` → the Implementer resolves the listed files (keeping both intents), the gate runs,
      then `CLI pr sync --target TARGET --continue`. Never rebase or force-push.
    - `merge`: with `auto_merge` (the default) → `CLI pr merge --target TARGET`; it reads the PR again,
      merges with the method the repository allows (squash first) only if still ready, and never
      bypasses branch protection. `not_ready` → wait again. Without `auto_merge` → tell the developer
      it is ready and stop.
    - `wait` (`waiting_for`: running checks, reviewers who have not approved, a draft, branch rules):
      nothing to do; wait again. Say what it waits for once, not on every poll.
    Failing checks the CLI can classify never reach the agent: a runner or network failure (or a
    cancelled/timed-out job) is re-run once by `pr wait` itself (`wait` for `re-run of …`, logged).
    The rest reach `aw-pr-watcher` with a `hint` (`ours` when the log names a file this PR changed).
    - `merged` / `closed` → `CLI log --stage "PR Watcher" --status completed --note "<merged|closed>"`,
      stop. `needs_user` (too many rounds, or watched for `watch.max_hours`) → tell the developer and stop.
    Merge rule: every reviewer requested on the PR must approve and nobody may still be asking for
    changes; without any reviewer, the PR merges once green and open for
    `watch.unreviewed_merge_after_minutes` (default 30), so a reviewer added after it opened is not
    raced; `watch.require_review: true` always waits for an approval. Required checks must pass; a
    failing optional check blocks until the watcher calls it `unrelated`. Your own comments on the PR
    are yours: the watcher only answers other people and bots. Threads are answered, never resolved.

Overlaps that cannot race on the working tree are safe: Planner with the base build and the
emulator boot, the Implementer's reading with the base build (it edits only after
`prebuild --wait --if-running`), the Implementer with the `before` capture, the gate with the
Reviewer, Implementers of a team with each other (one owner per file).

## Run files

Everything the run writes lives in `RUN` (gitignored, never committed): `ticket-spec.json`,
`change-set-map.json`, `plan.md`, `implementation-notes.md`, `gate-report.json`, `review.json`,
`device-report.md`, `pr-description.md`, `stage-log.md`, `stage-metrics.json` and
`media/{before,after}/`, `skills.json`; a team adds `team-plan.json`, `team-report.json` and
`slices/S<n>.md`. The PR Watcher adds `pr-watch.json` (its state: PR, replies posted, comments
handled, check verdicts), `pr-inbox.json` (the comments and checks to handle now), `pr-feedback.json`
and `pr-ci/` (failed CI logs).

## Android skills

Nobody asks for them: `start`, `update-spec` and every Implementer `started` refresh
`RUN/skills.json`, which names the skills each agent reads and why. The candidates are the app's own
skills (`TARGET/.ai/skills`, `.claude/skills`, `.agents/skills`) and the kit's
(`~/.ai/skills/android/`: Compose, Compose performance, performance, navigation, edge-to-edge,
screenshots, Android CLI, ktlint). The signals are the ticket text and surfaces, the likely files
and the code already changed, and a failing gate. A project skill on the same topic wins, at most 4
go to the Implementer (`skills.max` in `.ai/android-workflow.json`), and the Planner can correct the
list (`CLI skills --add/--drop`). Never copy a project's skills into the kit. The shared cache is `TARGET/.ai/workflow/_cache/`; `current.json`
points at the active ticket. A new ticket gets a new folder.

## Chat log and telemetry

Right before you spawn or resume an agent that `CLI next` did not already log (its answer says
`logged`; Setup, the PR Watcher and team slices are yours: Setup's logs before `start` wait for that
`start`, never land in the previous ticket's run), run `CLI log --stage <Role> --status started --note
"<one line: what it will do>"`. It costs nothing, it starts that agent's clock (the office counts it
second by second), and it is what lights up its desk on the office page. The page updates itself
while the run is live, files the Implementers edit included; you never refresh it.

Speak in **role names**, never bare T-codes. One line per stage:
`{Role}: {result} → {artifact}`. Example: `Reviewer: approved, 0 blocking → review.json`.
Durable log: `RUN/stage-log.md`; time and tokens per stage: `RUN/stage-metrics.json` (used to
debug runs and find improvements). Every `log` for an agent stage passes the token total that
agent's result reports (`--tokens N`); never estimate. When the agent already logged its own
`completed` (with `--file`), log `completed` again with `--tokens`: a second `completed` of the same
round merges into the first, it is not counted twice (and the chat log keeps one line). A `log …
--status started` that warns the toolkit code changed since `start` → run `CLI start` for the ticket
again before the gate (it keeps the Planner's decisions, an approved review and a PASS device report
of the unchanged source, and archives the earlier metrics). Edits to role files or this skill never
block the gate. `log` answers with a `warnings` entry
when an agent stage has none; if the host truly exposes no count, say so once in the final
summary. When you paused to ask the user, add `--wait-seconds S` so waiting is not counted.
While an agent works, the office reads its tokens so far from the host's own transcript (Claude
Code, Codex), and a `completed` without `--tokens` takes them from there; you still pass `--tokens`
whenever the agent's result reports them.

## Stop

**Before any code exists** — a setup fact that blocks this ticket (e.g. unknown install task for a
UI ticket), an unanswered business question, a missing bug reproduction — there is nothing to put
in a PR: STOP, ask the user, and continue once answered.

**After the code exists** — red gate after 2 fixes, blocking review after one fix, second device
FAIL, device BLOCKED with a device connected, a toolkit defect — never end without a PR:
`CLI finish --target TARGET --draft "<why it stopped>"` records every unresolved check as a known
issue, then `aw-delivery` delivers a **draft** PR that lists them. Tell the user what unblocks it.

## Done

`finish` exited 0, TARGET has a source diff outside `.ai/workflow/`, and — unless a `--no-*` flag
stopped earlier — the PR is open (and, while watching, until it is merged, closed or handed to the developer): ready for review when the gate passed, the review is `approved`
and the device is PASS (or not required); a draft with its open issues otherwise.
Show: PR URL (and whether it is a draft), changed files, stage log path, and the absolute `media/before` and
`media/after` paths so the user can drag the evidence into the PR. End with `office_message` from
`CLI status --target TARGET`, as is, on its own line (also when you stop to ask the user).

Commit message and PR body are product copy only: no host/agent/tool names, no
`Generated with …`, `🤖`, `Assisted-by` or agent `Co-Authored-By`.
