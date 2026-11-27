---
name: android-workflow
description: >-
  Host-driven Android ticket pipeline: plan, implement with tests, gate,
  independent review, device verification with before/after evidence, PR, and a
  PR Watcher that answers review comments, fixes CI and merges when approved.
  Use when the user types /android-workflow, /Android Workflow, or asks to run
  this workflow on an Android ticket, Jira or Linear id, or to watch or answer the
  comments of a PR this workflow opened ("Use o Android Workflow", "PR Watcher").
  With no ticket it listens for the developer's own open PRs and watches each
  one in its own session until merged ("watch my PRs", "fica ouvindo meus PRs").
---

# Android Workflow

This file is the single source of truth for the workflow. Each agent's own rules live in
`agents/aw-*.md` next to it.

**Goal:** a precise PR, fast and cheap. Every rule below serves one of the three: precision
(project patterns, verifiable AC, tests, gate, independent review, device proof), speed
(parallel work, scoped builds, skipped stages on simple tickets) and cost (small orchestrator
context, the cheapest model tier that does each job).

You are the **orchestrator**. The Python CLI detects, locates, gates and records. The agents do
the work; you keep your own context small (paths and ≤10-line returns only — never read the
diff, Gradle logs or images yourself). You only run CLI commands and spawn or resume agents:
- a merge or sync conflict → the Implementer, never you;
- a screenshot or render the developer asks for → `aw-media` mode `render` (`CLI render run`), never
  a test written into the repository's sources;
- Figma → only `fetch_design` (frames saved as data, as its `how` says); judging a design is the
  Planner's and `aw-media`'s;
- never edit an agent's artifact (`plan.md`, `review.json`, `device-report.md`, `pr-feedback.json`…),
  nor read the toolkit's source to get past the CLI;
- never run a stage's CLI command (`deliver`, `render verdict`, `finish`…) while its agent still runs;
- the CLI rejects an artifact → resume the agent that wrote it with the `errors`.

## Invocation

```text
/android-workflow <ticket-id> <title or description> [--target PATH] [--ticket FILE]
    [--level express|standard|full] [--worktree] [--no-device]
    [--no-commit] [--no-push] [--no-pr] [--no-watch] [--base BRANCH]
/android-workflow <ticket-id> --watch [--pr NUMBER]      (only the PR Watcher, for a PR already open)
/android-workflow [--watch] [--target PATH]            (no ticket: listener of your own PRs, see *Listener*)
```

Codex: `$android-workflow`. With no ticket id it is the listener, never a question. If the user
omitted other fields of a ticket run, ask once. Do not invent acceptance
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
3. `RUN` = `TARGET/.ai/workflow/<ticket-id>/`. `CLI` is shorthand in these docs for
   `python3 ~/.ai/bin/android-workflow`: in Bash always write the full command
   (`python3 ~/.ai/bin/android-workflow next --target …`), never through a shell variable
   (`CLI=…; $CLI next` exits 127 in zsh, and the permission allowlist matches only the full form).

Record `TARGET`, `RUN` and `CLI` in the first chat message, and end that message with the office
the user opens to watch the agents: `office_message` from the `start` output, copied as is, on its
own line (a bold `Office:` label and a `file://` link that one click, Cmd+click in a terminal, opens
at this run; `status` prints it too). Never reword it, build that link yourself or read that page. Fetch the ticket and pass it to the Planner
(`start` returns `ticket_source`: the key and its tracker, `linear` or `jira`):
a key whose prefix is in `tickets.linear_prefixes` (`.ai/android-workflow.json`, default `["LT"]`)
or a linear.app link → the Linear tool (`get_issue`, then `list_comments`); any other key → the Jira
tool (`getJiraIssue`). A Jira ticket that says it moved to Linear → read the Linear issue too. The
acceptance criteria come from the issue and its comments.
A tracker with no GitHub autolink (Linear) gets a clickable ticket in the PR body when the project's
`.ai/android-workflow.json` has `tickets.links`, a URL per tracker with `{key}`
(`{"linear": "https://linear.app/<workspace>/issue/{key}", "jira": "https://<site>.atlassian.net/browse/{key}"}`):
`CLI deliver` turns the bare key into that link. The main checkout's value also counts for its worktrees.

## Agents

| Role | Agent | Tier | Runs |
|---|---|---|---|
| Setup | `aw-setup` | strong (only small steps: standard) | only for the `missing_steps` of `CLI setup`, with the tier its `spawn` entry names |
| Planner | `aw-planner` | strong | standard and full levels |
| Tech Lead | `aw-tech-lead` | standard | only for a team (`CLI next` decides, Execute 4): splits the plan into slices, then integrates them |
| Implementer | `aw-implementer` | standard | always: code **and** its unit tests; resumed for every fix round. A team runs up to 3 at once, one per slice |
| Reviewer | `aw-reviewer` | strong (delta rounds: standard) | at the same time as the gate, once the Implementer is done |
| Device | `aw-device` | standard | `before` + `after` when `device_required` |
| Delivery | `aw-delivery` | fast | after `finish`, unless `--no-commit`: writes the PR body, runs `CLI deliver` |
| PR Watcher | `aw-pr-watcher` | standard | after the PR opens, unless `--no-watch`: triages new comments and failing checks, answers on the PR |

Tier maps to opus/sonnet/haiku on Claude Code and to `gpt-6.1-sol`/`gpt-6.1-terra`/`gpt-6-luna` with
high/medium/low reasoning on Codex (`model` and `model_reasoning_effort` in `.codex/agents/aw-*.toml`;
a newer model is a one-line change there and in the test that pins them); Cursor and Gemini inherit
the session model. Setup is strong because it runs once per build configuration and every later run
trusts what it records.

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
CLI feature-map --target T [--init] [--learn ROWS.md]   (the shared map: path, status, uncovered entry points)
CLI rules patterns|check|list --target T [--min N] [--base SHA]   CLI rules test --target T --rule F.yml   (the developer's local review rules)
CLI setup    --target T [--source-repo MAIN]        CLI start    --target T --id ID --title "…" [--type bug --reproduction "…"] [--level L]
CLI resume   --target T --question-id Q --answer "…" CLI update-spec --target T --surfaces ui --acceptance "a|b"
CLI prebuild --target T [--wait [--timeout S]|--status] [--force]   CLI gate     --target T
CLI emulator --target T [--wait] [--wait-free] [--stop]            CLI team     --target T [--check]
CLI skills   --target T [--add NAME] [--drop NAME]    CLI delta    --target T   (since the last review)
CLI finish   --target T [--skip-device "r"] [--no-device [--developer-said "quote"]] [--draft "r"] [--accept-partial "what"]
CLI deliver  --target T [--subject "ID: …"] [--no-push|--no-pr]
CLI log --target T --stage <Role> --status started --note "<what it is about to do>"
CLI log --target T --stage <Role> --status completed --note "…" [--file P]… [--tokens N] [--wait-seconds S]
CLI log … --stage Implementer --slice S2 …           (one Implementer of a team)
CLI office --target T                               (opens the office at this run; it updates itself)
CLI pr status|wait|merge --target T [--pr N] [--timeout S]       CLI pr sync --target T [--continue]
CLI pr reply --target T --item ID (--body "…" | --skip "why") [--edit]   CLI pr rerequest --target T --reviewer LOGIN
CLI pr base --target T --set BRANCH   (stack on another PR / back to the default branch)   CLI pr gh --target T -- <gh args>
CLI pr launch --target T   (the PR Watcher continues in its own terminal tab after this session)
CLI pr finish --target T [--pr N]   (merged/closed: worktree removed, the launched watch session stopped)
CLI log … --stage Implementer --status completed --no-code "why"   (a round that changed no code on purpose)
CLI pr body --target T --section "<heading>" --file F.md   (replaces only that section of the open PR's description)
CLI pr ci --target T --check NAME --as ours|unrelated|rerun --reason "…"
CLI finish … [--keep-device "why nothing visible changed"]     CLI deliver … [--keep-body]
CLI evidence capture|ingest|compare|list --target T [--phase before|after] [--name N] [--file F]
CLI device replay --target T [--serial S] [--phase before|after] [--install-base]   (runs the saved Maestro flows by code: `after` installs the current build, `before` the cached base APK)
CLI render probe --target T --module :mod        (Setup: does a JVM render work in this module, and how fast)
CLI render run|verdict|route --target T          (render RUN/render-request.json; record RUN/render-review.json; the route)
CLI render prefer --target T --mode auto|device|render   CLI render design --target T --skip "why"
CLI device launch --target T [--serial S] [--pins JSON] [--link URI]   (cold start with feature-flag pins, confirmed in logcat, then the deep link)
CLI device scaffold|build|install|release --target T [--serial S]   (flow templates from the plan's recipe; the current APK with no device held; it installed on the device; give the device back)
CLI status|list --target T                          CLI clean --target T [--ticket ID | --stale [HOURS]]
CLI cleanup --target T [--all] [--pr N] [--base] [--dry-run]   (worktrees of merged/closed PRs; --all archives open runs first)
```

## Execute

1. **Workspace first, in-place by default:** run `feature_workspace.py` without `--worktree`
   unless the user asked for one. On this ticket's branch it continues there; on the base branch it
   creates or reuses `<user>/<ID>-<slug>` in the same checkout. With `--worktree`, or when it prints
   `auto_worktree:` (the checkout is on another ticket's branch, or another ticket's run is going
   there: never share one checkout between two sessions), `TARGET` becomes the printed
   `worktree_path`. `start` refuses a checkout where another ticket's agent is still working; only
   when the user confirms that session is gone, `start --take-over`. Starting a ticket in a new
   worktree closes its unfinished copy in the other checkout as `moved`. The script initialises what the main checkout already
   uses (initialised submodules, `local.properties`) and prints `bootstrap_*` lines; relay any
   `bootstrap_submodule … failed` or `bootstrap_not_copied` line to the user and STOP until they
   answer; no agent works around it (nor runs the repository's own worktree setup scripts).
   An `other_ticket_branches:` line means this ticket already ran: name those branches and ask
   whether to continue before `start`, so a repeat run is a choice, not an accident.
2. **Setup, once, on the final `TARGET`:** `CLI setup --target TARGET [--source-repo <main checkout>]`.
   `ready` → reuse (the profile is inherited or refreshed when only dependency versions moved). A
   worktree adopts the main checkout's accepted setup by itself (`adopted_setup`) when no build file
   differs; never copy the profile or run `setup --accept` by hand.
   Every run checks; only what is missing runs. For each entry in `spawn`, spawn `aw-setup` with its
   `steps` and `tier` and the JSON: a `blocking` entry (`needs_setup_agent`) finishes before `start`,
   its questions batched to the user once; the `feature_map` entry runs in the background and is never
   waited on (the Planner and the Device use the map when it is there). `profile` is a full Setup;
   `profile_delta` re-checks only the build files that changed or the rows edited since acceptance. A
   missing formatter or app id never blocks a domain-only ticket.
3. **Ticket:** `CLI start` (add `--type bug --reproduction "..."`, `--acceptance "a|b"`,
   `--surfaces` when known, and the developer's `--level` and `--no-device`). It writes `ticket-spec.json` (with the
   level the rules give) and `change-set-map.json` (the likely files).
4. **From here on, `CLI next` decides.** Run `CLI next --target TARGET [--no-device] [--no-watch]`
   and do the one thing it returns, then run it again. It reads the run's files (state, verdicts,
   source fingerprints, round counts) and answers with `next` and a one-line `why`; the same files
   always give the same answer, and each answer is appended to `RUN/decisions.jsonl`. Do not
   re-derive what it decided (level, team or single, which checks to run, fix round or draft,
   device or skip): those rules live in the CLI, not in your reading of this file. When it hands a
   stage to an agent it already logged that stage `started` (`logged`), so do not log it again; a
   repeated `next` while the agent works says `wait`. Every background command that ends (the gate,
   `device build`, `pr wait`, an emulator), passed, failed or killed, is followed in that same turn by
   `CLI next` and one line to the developer: a session never goes quiet while the run waits on
   nothing. A gate whose process died is marked not finished by `next`, which runs it again.
   - The level is the CLI's: `express` for a look-only change (a color, text, size or icon on at most
     2 files, low risk, not a bug), which skips the Planner, writes no new test, gets a standard-tier
     review and one render or device capture with no base build; `full` for risky work; `standard`
     otherwise. Only the developer's `--level` overrides it.
   - `update_spec` (express): run `CLI update-spec` as `how` says, with the AC and surfaces from the ticket text (a
     visible change is `ui`), then `next`.
   - `spawn`: spawn (or, with `resume`, resume) `agent` with `mode`, `level`, `ids` (fix-round
     finding IDs) and `read` (the reports they come from), as in *How to delegate*. With `tier:
     standard` use the standard tier (Claude Code: `model: sonnet`; Codex: `model: gpt-6.1-terra`, reasoning
     `medium`, when `spawn_agent` takes model overrides, else the agent's own `.toml` model).
     `also` lists commands to start in the same turn, in the background (the emulator, the base
     build, a second `next` that says whether to capture the device `before`). `slices` → one
     `aw-implementer` per slice in the same turn, each logged with `--stage Implementer --slice S<n>`.
     `first` → run it before spawning. `replay`, `scaffold`, `done` and `serial` → pass them to `aw-device`
     (the serial is leased to this run while it is on the device; the next answer that needs no device
     gives it back, and `released_device` says so). `budget_seconds` → pass
     it to `aw-device` (`before` writes the flows while the Implementer works, `after` replays them and judges).
   - `checks`: run what `run` lists in the same turn: `CLI gate --target TARGET` (long command
     timeout, e.g. 30 min) and/or `agent` (`aw-reviewer`, `mode` `full` or `delta` with the `fixed`
     IDs and the `tree` it reviews) in the background. A review.json of another tree is refused at `log`. The review only reads code, so it never waits for the gate. Start `also`
     in the background too (`CLI device build`: the APK the Device installs, built before it holds a
     device).
   - `fix` (a team): resume the Implementer of each slice in `routes` with its IDs (logged with its
     `--slice`), the Tech Lead for `Tech Lead`.
   - `wait`, `wait_prebuild`, `wait_device`: an agent, a build or a device is still busy; run
     `command` if given (long timeout or in the background), else wait for the agent's return, then
     `next`. A `wait` with `if_returned`: when that agent already returned, run it (its `completed`
     was never logged, so `next` would wait forever). `log … --status completed` refuses a stage whose
     file (plan.md, implementation-notes.md, review.json, device-report.md, pr-description.md) is
     missing, empty, the stub or older than the round's `started`; `next` then answers `spawn` with
     `resume` for that agent: resume it to write the file, never write it yourself. `wait_device` starting with `booting`: an
     emulator is starting for this run (`CLI emulator --wait`); otherwise it names the run holding each
     device and `CLI emulator --wait-free` returns once this run has one. Past `device.wait_max_seconds`
     (900) of waiting it answers `ask_user` instead (keep waiting / JVM renders / skip the device).
   - `spawn` with `with_agent` (route `both`): spawn both in the same turn: `aw-device` for the AC that
     need a running app and the `with_agent` (`aw-media` mode `render`) for `render_ac`, the states the
     device does not reach. Tell the Device agent those AC are the renders' and not to spend its budget on them.
   - `spawn` of `aw-media` mode `render` (route `render`, or no device): pass `ac`, `modules`, `design`,
     `budget_seconds`; it writes `RUN/render-request.json`, runs `CLI render run`, judges the images and
     runs `CLI render verdict` (in the `render` route that verdict is the device report). No emulator, no APK.
   - `fetch_design`: the ticket links a Figma frame. With the Figma tool (MCP: `create_session`, then
     `get_design_context` for each `links` entry; `get_screenshot` too when the host can save it as a file)
     write each `writes` file as data: size, layout and spacing, colors, typography, texts. No Figma tool →
     `CLI render design --target TARGET --skip "<why>"`. Then `next`.
   - `replay_before`: run `command` (the saved flows on the cached base APK, no agent: the `before`
     column the run could not capture earlier), then `next`.
   - `finish`: run `command` as is. With `draft`, the PR opens as a draft listing what is open.
     **Never skip the device on your own.** `finish --no-device` is refused unless the run was started
     with `--no-device` or `--developer-said "<the developer's words, verbatim>"` quotes them; the stage log
     and the PR quote those words, and nothing says the developer asked otherwise.
     The one exception is the developer's standing rule: when the Device could not reach the changed
     surface by navigation (`Unreachable: <why>` in a BLOCKED or PARTIAL device-report.md, nothing under
     Anomalies), `next` answers `finish` (`--skip-device` / `--accept-partial` with that reason) and you run
     it without asking; the PR opens ready for review and says the device did not verify it.
   - `ask_user`: ask `question` once (batched), then run `then`. With `options` (a PARTIAL device
     check), offer them as the choices and run the one the developer picks.
   - `watch` / `done`: step 5 / the Done summary; run `also` (`CLI emulator --stop`: gives the device
     back and closes the emulators the workflow started that no other run is waiting for) first.

   Agent returns that `next` cannot read from files are yours: `NEEDS_INPUT` (Planner) → if `next` does
   not already say `ask_user`, run `CLI update-spec --target TARGET --question "q1|q2"`; ask, `CLI resume` each
   answer, then `next` (it re-runs the Planner with the answers); `NEEDS_OTHER_SLICE` → the Tech
   Lead; `BLOCKED` from the Implementer (an environment error) → tell the user; `NEEDS_DECISION`
   from the Implementer (a Device FAIL whose fix contradicts an AC or the plan's pattern) → ask the
   developer that one question, then resume it with the answer (an AC change: `CLI update-spec
   --acceptance … --proofs …` first), never a redesign fix round on your own; `NEEDS_APPROVAL`
   (Delivery) → see *Invocation*. A red gate is never waived by hand; a gate that itself looks wrong
   (a misclassified waiver, a parser miss) is a toolkit defect: `finish --draft`. No agent edits the
   toolkit (`~/.ai`) during a run; `start` fingerprints the code that judges a run, and a change to it
   is noted in the stage log and fingerprinted again (the run goes on; never re-run `start` for it).
   `start` on a run that is not finished resumes it: every AC, stage, verdict and metric stays; a new
   AC is only appended.

   The rules `next` applies live in `android_workflow/decide.py` (summary: `docs/cli-decisions.md`).

5. **Watch the PR** (unless `--no-watch` or a `--no-*` flag stopped before the PR; `--watch` starts
    here for a ticket whose run exists, and with no run: `CLI start` with the PR's title, then this).
    A request such as "see the PR comments", "veja os comentários do PR" or "resolve the comments"
    is this loop too, never a one-shot look (`pr status` alone, or spawning `aw-pr-watcher` by hand):
    `pr wait` → watcher → fix → push → reply → `pr wait`. The CLI reads the PR; you act on the one
    `action` it returns, then run its `then` (the next command, `pr wait` again). Loop:
    `CLI pr wait --target TARGET [--pr N]`: it polls GitHub (every minute, slower while nothing
    changes) and returns as soon as there is something to do. Run it in the background where the
    host allows (Claude Code: `run_in_background` with `--timeout 3300`), else in the foreground
    with the default `--timeout 540` and a 10-minute command timeout; `timed_out` → run it again.
    The loop never pauses for the developer: while an `ask_user` waits for an answer, `pr wait` keeps
    running in the background, and every `pr reply` returns `then` with the next `pr wait`. The watch
    ends only on `merged`, `closed`, `needs_user` or the developer saying stop. When this session is
    about to end with the watch still open (the developer leaves, "fica ouvindo", "keep watching"), run
    `CLI pr launch --target TARGET`: the watch continues in its own terminal tab
    (`/android-workflow <ID> --watch --pr N`), never twice for the same PR.
    Every GitHub call goes through the CLI, which picks the PR author's account: fix a posted answer
    with `pr reply --edit`, stack or unstack the PR with `pr base --set`, and anything else with
    `pr gh -- …`, never a bare `gh` or a hand-written `GH_TOKEN=…`.
    - `respond`: `CLI log --stage "PR Watcher" --status started --note "<n> comment(s), <m> check(s)"`,
      spawn `aw-pr-watcher` mode `triage`. It answers questions on the PR itself and returns the
      `changes`, `ask_user` and CI verdicts (`RUN/pr-feedback.json`). Then run
      `CLI pr feedback --target TARGET`; `invalid` → resume the watcher with its `errors`, never pass
      them to the developer. Review comments are acted on without asking: every `change` goes into the
      fix round below with no confirmation, and is answered on its thread once pushed.
      `ask_user` (only `product`, `breaks_ac`, `large_scope` or `second_disagreement`, each with the
      watcher's `recommendation`) → one question per prompt, in the developer's language, the
      recommendation as the default choice, never merged with a permission prompt or a host problem,
      while the changes go on in parallel; the answer becomes a change, or a reply through
      `CLI pr reply`. Never ask "should I apply these comments?" or "may I reply?": the watch is the
      author's request to answer reviewers.
      `changes` → a fix round on the same Implementer (IDs `PR-1`…, `RUN/pr-feedback.json`), then the
      gate and a delta Reviewer in parallel (`CLI next`, same limits), then Device `after` only when a
      change has `device: true`; otherwise `next` answers `CLI finish --target TARGET --keep-device "<why nothing
      visible changed>"`, which works even when the run never had a device PASS (analytics, renders,
      a draft) and the PR says the device was not re-checked. A partial device check the developer
      already accepted is accepted again without asking. Never commit or push with plain git: a
      refused `finish` or `deliver` is a toolkit gap to report. Then, with no Delivery agent:
      `CLI deliver --target TARGET --subject "<ID>: <what this round changed>" --keep-body`. Then resume
      `aw-pr-watcher` mode `reply` with the pushed `sha` and the change IDs (`--keep-body` keeps the
      developer's edits; when the round changes what the description says, the watcher updates that
      section with `CLI pr body`). A gate or review that stays
      red after its fix rounds: push nothing, tell the developer what is open, keep watching.
    - `sync` (`behind` or `conflict`): `CLI pr sync --target TARGET`. `synced` → wait again.
      `conflict` → the Implementer resolves the listed files (keeping both intents), the gate runs,
      then `CLI pr sync --target TARGET --continue`; it commits the resolved files and every other
      tracked file the round fixed (new files: `git add` them first, it says which). Never rebase or
      force-push. The gate measures the change against the merge-base with the PR's base branch (the
      commit being merged in while the merge is open), so the base branch's own files are never linted
      as ours and a merge that adds nothing keeps every verdict fresh. A PR that was merged or closed
      is never pushed to again: `deliver` refuses, and a new round needs a new branch.
    - `merge` (only with an approval, `watch.require_review` being the default, and only into the
      default branch: a stacked PR waits for the lower one): with `auto_merge` (the default) →
      `CLI pr merge --target TARGET`; it reads the PR again,
      merges with the method the repository allows (squash first) only if still ready, and never
      bypasses branch protection. `not_ready` → wait again. Without `auto_merge` → tell the developer
      it is ready and stop.
    - `wait` (`waiting_for`: running checks, reviewers who have not approved, a draft, branch rules):
      nothing to do; wait again. Say what it waits for once, not on every poll.
    Failing checks the CLI can classify never reach the agent: a runner or network failure (or a
    cancelled/timed-out job) is re-run once by `pr wait` itself (`wait` for `re-run of …`, logged).
    The rest reach `aw-pr-watcher` with a `hint` (`ours` when the log names a file this PR changed).
    - `merged` / `closed` → `CLI log --stage "PR Watcher" --status completed --note "<merged|closed>"`,
      then, last, `CLI pr finish --target TARGET` (removes this PR's worktree and stops the session
      `pr launch` started; `session.itself` → the summary in one message, nothing else runs), stop. Asked to
      delete worktrees → `CLI cleanup` (never `git worktree remove` or `rm -rf` by hand: an open PR's
      run is archived by it and `start` in a recreated worktree resumes it). `needs_user` (only when the project sets `watch.max_rounds` or `watch.max_hours`; both default to no limit) → tell the developer and stop.
    Merge rule: the PR is approved (GitHub's review decision) and nobody may still be asking for
    changes; a reviewer still requested who has not answered does not hold an approved PR back
    (`watch.wait_for_requested_reviewers: true` waits for them all). Without any approval it waits
    (`watch.require_review`, on by default); only with `require_review: false` does a PR nobody was
    asked to review merge once green and open for `watch.unreviewed_merge_after_minutes` (default 30).
    A stacked PR (base is not the default branch) never merges: the lower PR goes first. Required checks must pass; a
    failing optional check blocks until the watcher calls it `unrelated`. Your own comments on the PR
    are a review too (you checking the PR): the watcher answers them like anyone else's (`own: true`),
    and never answers its own replies. Threads are answered, never resolved.

## Listener (no ticket id)

`/android-workflow` or `/android-workflow --watch` with no ticket is a **listener** of the developer's
own open PRs in this app's repository (`TARGET` = the main checkout): it never watches a PR itself.
Each PR gets its own agent session in a new terminal tab running the ordinary
`/android-workflow <ID> --watch --pr N` (step 5) in the checkout of the PR's branch (the main checkout
on that branch, its worktree, or a new `wt-<branch>` worktree the CLI creates), until it is merged.

1. First chat line: the repository and that each PR is watched in its own tab. Then
   `CLI pr inbox --target TARGET --once`: it launches a tab for every open PR with no live watch (a PR
   whose checkout has a running watch, or a session that worked there in the last 15 min, is left
   alone). Relay its `summary` lines.
2. Loop: `CLI pr inbox --target TARGET --wait` (background, `--timeout 3300` on Claude Code) and act on
   its `status`, then wait again:
   - `new`: run every `commands` entry (`CLI pr launch --target TARGET --pr N`) and relay its `tell` line;
     `failed` or an error → relay it with the script path the developer can run. With a task board
     (Terminal One), one task per PR ("Watch PR #N: <title>").
   - `skipped`: one line per PR with its `why` (a fork; a draft when `watch.inbox.drafts` is false).
   - `idle` / `timed_out` / `offline`: wait again silently.
   Never `--once` again in the loop: a tab the developer closed stays closed until they start the
   listener again or run `/android-workflow <ID> --watch`.
3. The listener ends only with this session or when the developer says stop. The launched sessions
   keep running and end themselves with `CLI pr finish` when their PR is merged or closed.

Two runs at once (two tickets, two worktrees) are kept apart by the CLI: build slots, one
device leased per run (`wait_device` names who holds each), base APKs cached by commit. With 3 or
more runs, tell the developer to keep `execution.max_parallel_builds` at 2 or less and close
Android Studio.

## How visible AC are proved (JVM render or device)

`CLI next` picks the route (`device`, `render` or `both`) by code from the Planner's `--proofs`;
act on its `spawn`. A render draws a composable in the unit-test JVM, never proof of behavior,
and is off until `CLI render probe` passed in the module. The developer overrides with
`CLI render prefer --mode device|render|auto`.

## Review rules (the developer's, local)

Findings that keep coming back become ast-grep rules that never enter the app's repository:
`MAIN/.ai/review-rules/*.yml` (this app; `.ai/review-rules/` is git-excluded) and `~/.ai/review-rules/`
(every app; an app file of the same name wins). A rule's `severity: error` is `block` (gate fails,
the Implementer fixes it), anything else `warn` (reported to the Implementer and the Reviewer, never a
failure). They match only lines the change adds, so old code never trips them. Without `ast-grep`
(`brew install ast-grep`) the step is skipped, never failed.

When the developer asks for patterns or a new rule ("review patterns", "vira regra", "cria uma regra local"):

1. `CLI rules patterns --target MAIN` groups the findings of every archived `review.json` (workflow
   runs and PR reviews) by the code term or file they share across runs; `covered_by` names a rule
   that already has it.
2. For each group, decide from the findings' text whether a syntax pattern can catch it (an API
   misuse such as `runCatching` in suspend code: yes; a double tap or a stale lambda: no, it stays
   with the Reviewer; a stray file or a pre-existing lint failure: workflow noise, say so). Propose at
   most 3 rules, each with the runs it comes from.
3. On the developer's OK, write each to `MAIN/.ai/review-rules/<id>.yml` with `severity: warning`
   and a first comment line naming the source run; `CLI rules test --target MAIN --rule F` must parse
   and report its matches on the app (zero: the pattern is wrong; hundreds: say it will warn often).
   Show the count. `block` (`severity: error`) only when the developer says so.
4. **Team rule** only when the developer asks to promote one: write the repo version (a detekt
   custom rule or a Konsist test, in the repo's existing setup) as a patch to
   `MAIN/.ai/review-rules/promote/<id>.patch`. Never apply, commit or open a PR with it yourself.

Never put user or student data in a rule; a rule's `message` names the API, not a person or PR text.

## Android skills

`RUN/skills.json` names the skills each agent reads; the CLI refreshes it (`start`, `update-spec`,
every Implementer `started`) and the Planner corrects it (`CLI skills --add/--drop`). A project
skill on the same topic wins. Never copy a project's skills into the kit.

## Chat log and telemetry

Right before you spawn or resume an agent that `CLI next` did not already log (its answer says
`logged`; Setup, the PR Watcher and team slices are yours), run `CLI log --stage <Role> --status
started --note "<one line>"`: it starts that agent's clock and lights its desk in the office.
Every `completed` passes the token total the agent's result reports (`--tokens N`), never an
estimate; add `--wait-seconds S` when you paused to ask the user. A `log … --status started` that
warns the toolkit code changed since `start` → run `CLI start` for the ticket again before the gate.
Speak in role names, one line per stage: `Reviewer: approved, 0 blocking → review.json`.

## Stop

**Before any code exists** — a setup fact that blocks this ticket (e.g. unknown install task for a
UI ticket), an unanswered business question, a missing bug reproduction — there is nothing to put
in a PR: STOP, ask the user, and continue once answered.

**After the code exists** — red gate after 2 fixes, blocking review after one fix, second device
FAIL, device BLOCKED with a device connected, a toolkit defect — never end without a PR:
`CLI finish --target TARGET --draft "<why it stopped>"` records every unresolved check as a known
issue, then `aw-delivery` delivers a **draft** PR that lists them. Tell the user what unblocks it.
A later delivery of the same run with no open issues marks that draft ready for review by itself.

## Done

`finish` exited 0, TARGET has a source diff outside `.ai/workflow/`, and — unless a `--no-*` flag
stopped earlier — the PR is open (and, while watching, until it is merged, closed or handed to the developer): ready for review when the gate passed, the review is `approved`
and the device is PASS (or not required, or PARTIAL and accepted by the developer); a draft with its open
issues otherwise.
If the PR Watcher was still watching, say in one line that it stops with this session and that
`/android-workflow <ID> --watch` resumes it.
Show: PR URL (and whether it is a draft), changed files, stage log path, and whether the captures were
uploaded into the PR (`media.upload` with no upload warning). When they were not, show the absolute
`media/before` and `media/after` paths so the user can drag the evidence into the PR. End with `office_message` from
`CLI status --target TARGET`, as is, on its own line (also when you stop to ask the user).

The PR description on GitHub is the source of truth once the PR is open. Every update (`CLI deliver`,
`CLI pr body`) reads it first. A section edited there since the workflow's last post
(`RUN/pr-body-posted.md`) keeps that text. Only a section the workflow changed and nobody touched
on the PR is replaced, and a new section is added. `RUN/pr-description.md` then mirrors the PR.
Never write a description back over edits made on the PR.

Commit message and PR body are product copy only: no host/agent/tool names, no
`Generated with …`, `🤖`, `Assisted-by` or agent `Co-Authored-By`. Only when the project sets
`"attribution": {"enabled": true}` in `.ai/android-workflow.json` does `CLI deliver` add the host's
`Generated with` line to the PR body and its `Co-Authored-By` trailer to the commit (keys: `text`,
`commit_trailer`, `pull_request`, `review`: the review body and every `CLI pr reply`; `ANDROID_WORKFLOW_HOST` forces the host). Agents never write it.
