# What the CLI decides

The orchestrators (`skills/android-workflow`, `skills/android-review`) act on what `CLI next` and
`CLI review next` return and never re-derive these rules. This page is for people who maintain or
tune the CLI. The code wins where the two differ.

## android-workflow (`android_workflow/decide.py`)

level `full` for high risk or complexity, lifecycle/room_migration/workmanager surfaces or a
payments/auth ticket; express picked by code for a look-only change (a color, text, size or icon word, no behavior word, at most 2 likely source files, low risk, not a bug): no Planner, no new test, a standard-tier review, `--proofs render` for its AC, a 180 s device check and no base build unless its APK is cached; Planner unless express; a team for 2+
independent parts and 4+ files (`update-spec --parallel-parts/--parallel-files`); gate and
Reviewer together, one fix round for both; a draft after the third red gate in a row, a review
still blocking after one fix, the second device FAIL in a row, a BLOCKED device, the device budget
spent when the developer set one (`device.budget_minutes`, default 0 = no limit, every Device round
together: no round starts past it, a round already running gets 5 more minutes to finish the check
in hand, then the CLI writes a PARTIAL/BLOCKED report of what was verified) or a fix round that changed nothing (in a PR round: `ask_user`, push nothing, never a draft); an approved review kept
when only formatting changed since; a device route for `ui`, navigation, lifecycle… and for `audio`
and `analytics` (an AC about sound or events adds them back whatever the Planner's list says); the
base build only for a visual change; the device `before` only with a passed base build and a
device, else a late `before` that replays the `after` flows on the base APK; a Device PASS
only on flows replayed for the current source (or `Maestro: unavailable — <why>`); a PASS with
Anomalies, or `PARTIAL`, asks the developer; no device → an emulator is started for the run, and
`finish --skip-device "<reason>"` only when there is no SDK or AVD to start one.
The gate itself: formatter (fixing first when it can), then compile, unit tests, detekt and lint
for the touched modules in one build, plus compile and unit tests for modules that use a changed
declaration, a secret scan, and the developer's local review rules on the lines the change adds
(see Review rules: a `block` rule fails the gate as `G-rules`, a `warn` one is only reported). Lint or format findings only in files the change does not touch,
and Android lint findings on lines of a touched file that the change did not write, are
**waived** and disclosed in the PR; never "fix" a waived finding: it widens the diff. Gradle failing
by itself (a lock timeout, a daemon that died or ran out of memory, "Could not create service") is
retried once and never a fix round; still failing, `next` asks the developer. The gate and the
device APK build never build in the same checkout at once.

## android-review (`android_workflow/review.py`)

- **Attribution**: off by default. With `"attribution": {"enabled": true}` in `.ai/android-workflow.json`
  (`"review": false` turns it off for reviews only), the draft carries the host's line (`🤖 Generated with
  Claude Code`, no link) under the review body, every inline comment and every thread reply, shown in
  `review-draft.md` and posted with it. Off, the CLI drops any such line before posting. Agents never write it.
- **Ticket**: every Jira key in the title, body or branch is read into `RUN/ticket/` before the
  Planner; the Planner and the Reviewer both read it.
- **Run**: the change set is the PR's diff (no ticket-text Localizer), and nothing is implemented: no
  generic `plan.md`, no `implementation-notes.md`, no Implementer hand-over. A delta round keeps
  `review-spec.json` (no Planner) when the PR title, body and Jira keys did not change. A merged or
  closed PR marks the run completed.
- **Copied code**: a new source file that is mostly a copy of a file the PR changed or deleted (a
  split, a legacy fork) gets `RUN/copy-diff/<name>.diff` (original at the base vs the new file); the
  Reviewer explains every divergence (intended, or a finding).
- **Local review rules**: the Reviewer also runs the developer's own ast-grep rules
  (`CLI rules check`, android-workflow SKILL → Review rules) on the lines the PR adds; a hit it
  confirms becomes a draft comment like any other finding, posted only with the developer's OK.
  The rules never go to the PR's repository.
- **Device** only when the diff changes UI: Android resources (`res/layout|drawable|values|…`),
  the manifest, `@Composable`/`@Preview` code, screen/view classes (never test sources: `src/test`,
  `src/androidTest`, `sharedTest`, `testFixtures`), or when the spec has an AC with
  `evidence: device` (behavior only a running app shows: an analytics event, a logcat side effect, a
  background job…), UI change or not. The Planner may waive it for a
  pure refactor pinned by tests (`device: skip` + reason in the spec), never for a resource or
  manifest change. The Reviewer can require it (`CLI review device --require`), which beats the
  waiver. `after` = PR head, `before` = the base APK replayed
  by code. No UI change → the review says "not verified on a device: no UI change". At most two
  Device rounds per review round, and `device.budget_minutes` of device time when the developer sets
  one (default 0, no limit): past either, the CLI records BLOCKED (or PARTIAL) with what was verified
  and the draft follows.
  **Carry-over:** in a delta round, a device PASS/PARTIAL of an earlier head is reused (T7 skipped, no
  APK builds) while the PR's commits since that head change no UI; the review says "PASS on abc1234
  carried over: the new commits change no UI" and the carried report still proves the device AC. A UI
  change since then, a force-push that dropped that head, `--require` or the Reviewer's
  `device_required` → the device runs again. So does any PR commit since that head when the device ran
  only for a behavior AC (`evidence: device` on a PR that changes no UI).
- **Reviewer tier** (round 1, from the diff only; the PR text never lowers it): standard when the diff
  has at most `review.small_diff_lines` (150; `0` = always strong) changed lines in at most 5 files and
  no risky area — DI (`@Module`, `@Provides`, `@Binds`, `di/`), Room/serialization (`@Entity`, `@Database`,
  `Migration`, `@Serializable`, `@SerializedName`, `schemas/`, ProGuard), navigation (`res/navigation`,
  `NavHost`, deep links), the manifest, Gradle/build logic, preferences/DataStore, `GlobalScope`,
  `runBlocking`, `CancellationException`, or a payment/auth word in a path. Otherwise strong.
  `pr-context.json` → `reviewer` keeps the tier and why.
- **JVM renders** (same rules as android-workflow, *How visible AC are proved*): an AC with
  `evidence: render` in `review-spec.json` is static UI a render proves. All visible AC `render` and the
  render probe passed in the module → no device, no APK builds: `aw-media` mode `render` renders the PR
  head and judges it, and the draft says "Device: not run — PASS from JVM renders". Device AC too → the
  device runs and the renders run alongside (`with_agent`). No device → renders instead of "not verified".
  A Figma link in the PR or ticket → `fetch_design` first. `CLI render prefer --mode device` turns it off.
- **Media gate**: attachments are read only when the PR changes UI, an AC has `evidence: media`, or the
  device was required; otherwise `aw-media` does not run (`review-state.json` → `media_skipped`).
- **Media** carry-over: a delta round keeps `media-review.json` (no `aw-media`) when the spec was kept
  and every attachment is the same (link, status, SHA-256); any change runs the Media analyst again.
  The device is this run's own lease, as in android-workflow; with `device.dedicated` (default `true`) it is
  always an emulator the workflow started, never the developer's own emulator or phone.
- **CI** is trusted when every required check (else every check) on the PR head passed; only those
  fail it, an optional check failing is listed in the body as info. Pending →
  wait up to `review.ci_wait_minutes` (30), then the local gate; no check at all, or all skipped →
  the local gate for the touched modules. **Documentation only** (every changed file `.md`/`.txt`/…,
  or CI passed on the previous head and the new commits are docs only) → CI is not waited for, the
  gate never runs, and pending CI does not hold the approval (the body says why).
- **Acceptance** is reconciled at draft time with what ran after the Reviewer: a device scenario
  `ACn …: PASS` proves an AC (FAIL makes it unmet), media shown proves it, and an attachment the tool
  could not download is marked "not checked", never held against the author. Each AC keeps its
  `source` (`pr_body` | `jira` | `template`; an AC with none whose text is a checked PR-template item
  is `template`). In `review.json` the Reviewer marks `on_base: true` on an unmet AC the base build
  fails the same way (a device line `ACn …: FAIL, also fails on base` does the same), and an AC that
  waits for a product or design answer gets `status: open` (or `question: "…"`): shown as ❓ with it.
- **Verdict** — approve with comments unless the PR itself introduces a blocker: a blocking finding,
  failing required CI, a failed local gate, a device FAIL, or an unmet AC the PR claims →
  `REQUEST_CHANGES`. Never from a PR-template item (listed only), a failure the base has too, or a
  device FAIL whose every failing scenario is on the base, nor from an AC the Planner marked
  `follow_up` (deferred by the ticket or the PR: ⏭️ in the body; media or render findings about it are
  at most concerns). An AC waiting for an answer, CI still pending, or an unproven claim while CI is
  not green → `COMMENT`; else `APPROVE`. With CI (or the local gate) green and nothing blocking, an
  unproven claim (⚠️) and a BLOCKED or PARTIAL device do not hold the approval: the body says what
  could not be verified. **Concerns and nits approve** (posted with it as comments;
  `review.approve_with_concerns: false` makes concerns `COMMENT` again). `review.max_event: "COMMENT"`
  caps what the CLI picks: `REQUEST_CHANGES` is then only the developer's `edit --event`. Own PR →
  always `COMMENT` (GitHub's rule). `review.allow_approve: false` → `COMMENT` instead of approve.
  `edit --event` rewrites the body's generated first line to match the new event.
- **Quiet delta round**: we already approved an earlier head and the delta Reviewer, the media and
  CI found nothing new (every earlier thread addressed, no device needed) → no CI wait and no second
  review: a draft with `Event: none` that only resolves our addressed threads after the OK, or `done`
  at once when there is nothing to resolve.
- **Body**: the headline, `verified` (what the Reviewer proved, with evidence), checks, device,
  acceptance, other notes. The device line says what ran: "before (base) and after (this PR)" only
  when both were captured, "after only; base replay missing (why)", or "BLOCKED — … not verified".
  A met AC with no text is left out.
- **Comments**: at most `review.max_inline_comments` inline (default 10, max 15), blocking first,
  then concerns, then nits (prefixed `nit:`; `review.nits_inline: false` moves them to the body); a comment off the diff's lines or past the cap goes in
  the body under *Other notes*. A finding with a valid `suggestion` carries GitHub's suggested-change
  block (the author's "Commit suggestion"); one the CLI cannot apply (off the hunk, >10 lines,
  overlapping, no change) is posted as the text fix and listed under `suggestions_as_text` in the draft result.
- **Redraft**: a draft records what it was composed from (spec, `review.json`, media, device report and
  captures, gate, render, CI verdict and check states); when any of it changes after the draft (a device
  run the developer required late), `next` returns `draft` again and names what changed. An `edit`
  keeps that record, so an edited draft is redrafted only when a check actually changed; that redraft
  carries the developer's comment and thread edits (by finding and thread, not by C1/R1/T1) and reports
  `edits_kept` / `edits_not_kept`: an edited body or event is composed again, since both summarize the
  checks. `draft` run again on unchanged checks starts over with no edits.
- **Auto post**: `next` returns `post` instead of `ask_user` when the draft is APPROVE or COMMENT and
  its `gaps` is empty: CI passed (or waived for docs, or the local gate passed with no CI), the device
  PASS when the PR changes UI (or the Planner's waiver), no AC `unverified`, `unmet`, `unchecked` or
  `open`, and no security finding. REQUEST_CHANGES, any gap, a draft the developer edited on this head
  (`touched` carries over a redraft) or `review.auto_post: false` keeps the `ask_user`, whose `why`
  lists the gaps.
- **Post**: one review via `POST /repos/{o}/{r}/pulls/{n}/reviews` pinned to the reviewed commit;
  refused when the draft changed since the OK, was never shown (`next` returning it with `show`, or
  `review shown`; every `edit` or redraft resets it), a check changed after the draft (`inputs_changed`)
  or the PR head moved. The PR is never merged from this mode.
- **Others' comments**: `pr-context.json` carries `other_threads` (inline threads others opened, open
  first, max 40) and `discussion` (PR comments and review bodies, max 30). A finding may repeat a point
  from one of them with more detail (`thread` → the comment links it); `replies` (agree, disagree,
  still_open, addressed; max 5) are listed in the draft under *Replies in others' threads*, posted as
  thread replies right after the review with the same OK (`edit --drop R1` / `--set R1 "text"`),
  recorded as they land (a retry never posts twice) and never resolve the thread.
- **Resolving threads**: only threads **we opened**, only with the developer's OK, never on a thread
  of another reviewer. Two ways: (1) a delta round — the Reviewer marks each of our earlier threads
  `addressed`; the addressed ones are listed in the draft under *Threads to resolve* and resolved
  right after the review posts; (2) the watch — `aw-pr-watcher` mode `review` proposes `resolve` for a
  thread that is settled (the author was right, or the head confirms the fix); it is resolved right
  after its reply lands, never when the reply failed or the item is `ask_user`. Each resolution is
  recorded as it lands (a retry never resolves twice); a failure (no permission) is reported, never
  fatal. On the developer's own PR (android-workflow) threads are still never resolved by us.
- **Replies** (watch): the inbox is the last comment of every open thread we took part in when it is
  not ours (bots left out), plus top-level comments/reviews that @mention us after our first review.
  One batch per round, posted only with the approved id: a thread reply in the thread, a quoting PR
  comment otherwise. Each posted reply is recorded at once (a retry never posts it twice); dropped and
  skipped items are handled; `ask_user` items stay open until the developer answers.

Config, per project in `.ai/android-workflow.json`:
`{"review": {"max_inline_comments": 10, "allow_approve": true, "media_max_mb": 100, "ci_wait_minutes": 30,
"nits_inline": true, "approve_with_concerns": true, "max_event": null, "auto_post": true}}`.

Attachments of a private repo download through the signed URL GitHub renders in the PR's
`body_html` (`private-user-images…?jwt=`); the plain `user-attachments` link answers a login page.
Every `CLI review edit` is logged in `review-decisions.jsonl`: dropped and rewritten comments by
severity with their text, the event override (from, to and the computed reasons) and the body diff:
the measure of which findings developers find useless. The watch also logs as `missed` each comment
the developer writes on the PR by hand after our post (inline, review body or PR comment): what the
review did not catch.

## android-workflow: Concurrency and devices

Overlaps that cannot race on the working tree are safe: Planner and Implementer with the base build
(it builds the base commit in its own checkout, `~/.cache/android-workflow/base/<repo>/`, never in
TARGET) and the emulator boot, the Implementer with the `before` capture, the gate with the Reviewer,
Implementers of a team with each other (one owner per file).

**Two runs at once** (two tickets, two worktrees) share the machine, and the CLI keeps them apart:
- heavy builds (gate, base build, Device build) take one of `execution.max_parallel_builds` slots
  (default 2; `ANDROID_WORKFLOW_BUILD_SLOTS`) and wait for a free one, logged in the stage log;
- one base build per repository at a time; its APKs are cached by base commit, so the second run on
  the same base gets them at once;
- each run has its own device: the first time it needs one it leases a ready device nobody uses (an
  emulator already open, a phone), else one more emulator starts just for it, up to
  `device.max_emulators` (default: one per 12 GB of memory, 2 to 4; AVDs in `device.avd` order (a name or a list), then phones before tablets; a busy or failing one falls back to the next AVD that is not
  running, else a second copy runs `-read-only` once the first has booted); past that, `next`
  answers `wait_device` naming who holds each device. `device.dedicated` (default `true`): only
  emulators the workflow started are leased, never the developer's own emulator or phone; one another
  run gave back is still reused. `false` (or `ANDROID_WORKFLOW_DEDICATED_DEVICE=0`) lets a run lease an
  emulator already open or a phone. A run holds its device only while it is on it: the `next` that
  answers anything else (the Implementer, the gate, the review, a question, delivery) gives it back
  (a command still installing keeps it until it exits) and the emulator stays open for whoever needs
  one. The next device step asks for the same device first, else another of the same AVD, else any free
  one; a `before` captured on another model is set aside and replayed on the new device, so the PR pairs
  like with like. A leased emulator that dies, or is not booted after `device.boot_stall_seconds`
  (360), is closed and replaced. `emulator --stop` closes only what this run held: an emulator another run
  holds is refused by name;
- the Device APK is built before the device is held (`CLI device build`, started with the gate):
  holding the device costs an `adb install`, not a Gradle build;
- with 3 or more runs, keep `max_parallel_builds` at 2 (or 1) and close Android Studio: a cold
  build of a large app per run, plus emulators, swaps the machine.

## android-workflow: JVM renders

Decided by code in `CLI next` (`android_workflow/render.py`), zero tokens. A **render** draws a
composable with Android's real graphics in the unit-test JVM (Robolectric `GraphicsMode.NATIVE` +
`captureToImage`), in 1–3 minutes, with no emulator: the generated test lives in `RUN/render/` and joins
the module's test sources through a Gradle init script for that one call, so the repository is never
written and the source fingerprint never moves. It is never proof of behavior.

- **Off** until `CLI render probe` passed in a module within `render.budget_seconds` (240): with no probe
  on record every route is the device, exactly as before. The probe result lives in
  `.ai/android-workflow.json` → `render.modules`; `render.base_class`, `render.rule`, `render.theme`
  (`{"wrap": "AppTheme(darkTheme = $DARK) { $CONTENT }", "imports": [...]}`) and `render.qualifiers`
  come from Setup.
- The Planner's `--proofs` (one per AC: `unit | render | device | both`) decide the **route**:
  - `render`: every visible AC is `render` and no runtime surface (navigation, deep link, permissions,
    lifecycle, notifications, WorkManager, Room migration, audio, analytics). No device `before`, no
    APK, no emulator; the Render analyst's verdict becomes `device-report.md` (`Proof: JVM render`) and
    its images go to `media/after/render-*.png`, so finish, the PR table and the office read it as usual.
  - `both`: device AC and render AC. The device runs as before; the renders run in parallel (no wall
    time added) for the states it cannot reach. A failing render is a fix round (`R1`).
  - `device`: anything else, and whenever unsure.
  - No device at all (none free or no SDK) and renders on: the renders prove what a screen shows
    instead of nothing; device-only AC stay not verified (a PARTIAL the developer decides on).
- The developer overrides with `CLI render prefer --mode device|render|auto` (`device` = never render).
- A Figma link in the ticket → `fetch_design` once, and the Render analyst compares against it.

## android-workflow: Android skills

Nobody asks for them: `start`, `update-spec` and every Implementer `started` refresh
`RUN/skills.json`, which names the skills each agent reads and why. The candidates are the app's own
skills (`TARGET/.ai/skills`, `.claude/skills`, `.agents/skills`) and the kit's
(`~/.ai/skills/android/`: Compose, Compose performance, performance, navigation, edge-to-edge,
screenshots, Android CLI, ktlint). The signals are the ticket text and surfaces, the likely files
and the code already changed, and a failing gate. A project skill on the same topic wins, at most 4
go to the Implementer (`skills.max` in `.ai/android-workflow.json`), and the Planner can correct the
list (`CLI skills --add/--drop`). Never copy a project's skills into the kit. The shared cache is `TARGET/.ai/workflow/_cache/`; `current.json`
points at the active ticket. A new ticket gets a new folder.

## android-workflow: Run files

Everything the run writes lives in `RUN` (gitignored, never committed): `ticket-spec.json`,
`change-set-map.json`, `plan.md`, `implementation-notes.md`, `gate-report.json`, `review.json`,
`device-report.md`, `pr-description.md`, `stage-log.md`, `stage-metrics.json` and
`media/{before,after}/`, `skills.json`; a team adds `team-plan.json`, `team-report.json` and
`slices/S<n>.md`. The PR Watcher adds `pr-watch.json` (its state: PR, replies posted, comments
handled, check verdicts), `pr-inbox.json` (the comments and checks to handle now), `pr-feedback.json`
and `pr-ci/` (failed CI logs).

## android-workflow: Chat log and telemetry

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
agent's result reports (`--tokens N`); never estimate. When the host's transcript names the agent
(Claude Code, Codex) the CLI's own reading wins and a different `--tokens` is kept as
`tokens_reported`; `processed_tokens` adds up every request of the round (cached input included). When the agent already logged its own
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
