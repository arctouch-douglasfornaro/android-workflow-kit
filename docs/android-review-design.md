# Android Review — design

Status: implemented — `skills/android-review/SKILL.md`, `android_workflow/review.py`, `android_workflow/review_inbox.py`, agent `aw-media`, tests in `tests/test_review.py` and `tests/test_review_inbox.py`.

A second mode of the kit: review someone else's GitHub PR end to end and post one review after the
developer's OK.

```text
/android-review 22307            # PR number, in the app's folder
/android-review https://github.com/org/app/pull/22307
/android-review                  # no PR: listen for PRs requesting our review (see Inbox)
```

## Decisions

| Topic | Decision |
| --- | --- |
| Repository | Same kit (`~/.ai`). New skill `skills/android-review/`, CLI subcommand `android-workflow review …`. `skills/android-workflow/SKILL.md` stays untouched. |
| Posting | Nothing reaches GitHub before the developer's OK. After the OK, the CLI posts it automatically as **one** review (inline comments + body + event). |
| Gate | CI is trusted when it covered the change: every required check finished and passed on the PR head. Missing, pending or skipped checks for touched modules → the local gate runs only for those modules (`gate` already scopes by module). |
| Device | Only when the PR changes UI. Data-only / domain-only PRs skip it and the review says "not verified on a device: no UI change". Mocked/fake-backed device runs for data-layer PRs are out of scope for now. |
| Comment cap | At most 10 inline comments per review (`review.max_inline_comments` in `.ai/android-workflow.json`, up to 15). Blocking first, then concerns, then nits; whatever does not fit is summarized in the review body. |
| Non-blocking | Suggestions are posted as inline comments prefixed `nit:`; they never change the verdict. The developer can drop any of them at the OK step. |
| PR template | When the repo has a PR template, its checklist (tests added, reviewed, dark mode, screenshots…) becomes extra AC: every checked item needs evidence in the diff, CI or media; a checked item without evidence becomes a comment. |

## Flow

```mermaid
flowchart TD
    IN(["/android-review PR"]) --> INT["Intake<br/>worktree at PR head · pr-context.json"]
    INT --> SPEC["Spec<br/>AC from PR body + Jira"]
    SPEC --> REV["Reviewer (PR mode)"]
    SPEC --> CI["CI check<br/>trust or local gate"]
    SPEC --> MED["Media analyst<br/>PR screenshots / videos"]
    REV & CI & MED --> DEV{"UI change?"}
    DEV -- yes --> DVC["Device<br/>base APK vs PR head"]
    DEV -- no --> VER
    DVC --> VER["Verdict (code)<br/>approve · comment · request_changes"]
    VER --> OK{"Developer OK?"}
    OK -- edit --> VER
    OK -- yes --> POST["Post one review"]
    POST --> FUP["Follow-up<br/>new commits → delta re-review"]
```

1. **Intake** — `CLI review start --pr N`: PR head in a worktree (`feature_workspace.py`), never in the
   developer's checkout. Writes `RUN/pr-context.json` (`RUN` = `WT/.ai/workflow/PR-<n>/`): diff vs merge base, title/body,
   linked Jira key, existing review threads, check runs, attachment URLs. Media downloaded with the
   authenticated `gh` session into `RUN/media/pr/`.
2. **Spec** — Planner-lite derives verifiable AC from the PR body, the Jira ticket and the PR
   template's checked items (when the repo has a template). No stated
   intent anywhere → `NEEDS_INPUT` (ask the developer; do not invent what the PR should do).
3. **In parallel**
   - **Reviewer, PR mode** — `aw-reviewer` with `MODE=pr`: same order (diff → callers → spec → notes),
     author's PR body read last. Adds architecture fit vs `project-profile.md` Code patterns.
     Writes `review.json` (blocking / concerns / suggestions with `file:line`).
   - **CI check** — `watch.py` check-run reader; `classify_ci` labels failures. Decides trust vs local gate.
   - **Media analyst** — new agent `aw-media`: images as-is, videos to frames (`ffmpeg`, 1 fps +
     scene changes). Checks each AC is visibly shown, flags states not shown (empty, error, loading,
     dark mode, large font) and mismatches with the diff.
4. **Device (UI only)** — UI change decided in code from the diff (see below). `before` = cached base
   APK (`prebuild.py`), `after` = PR head build; `aw-device` with Maestro flows; compares its own
   captures with the author's media.
5. **Verdict in code** (`decide.py`, new `review` branch):
   - any blocking, failing CI that is ours, or device FAIL → `REQUEST_CHANGES`
   - unverified claims, device PARTIAL/BLOCKED or CI still pending (not for a docs-only change) → `COMMENT`
   - otherwise → `APPROVE`, concerns and nits riding along as comments (`review.approve_with_concerns:
     false` makes concerns `COMMENT`; own PR → always `COMMENT`). Every run reviewed on 2026-11-04 had
     its `COMMENT` overridden to `APPROVE` by the developer.
6. **Developer OK** — shows the draft: event, body, each inline comment (`file:line`, text). The
   developer can drop/edit comments or change the event. `CLI review post` sends it via
   `gh api repos/{o}/{r}/pulls/{n}/reviews` in one call, pinned to the reviewed `commit_id`.
   If the head moved since the review, `post` refuses and offers a delta re-review.
   A delta round after our approval that raises nothing new posts no second review: it only offers
   to resolve our addressed threads (two of three such drafts were never posted by hand either).
7. **Follow-up** — on by default (`--no-watch` to stop at the post; `CLI review wait`): on new commits, `CLI delta` re-reviews only
   the new hunks and checks whether each thread we opened was addressed; when someone answers a thread
   we took part in (or @mentions us after the review), `aw-pr-watcher` mode `review` drafts the answers
   (`RUN/review-replies.json`), `CLI review replies` shows them as one batch with an id, and
   `CLI review reply --draft-id` posts them after the OK. New commits are handled before replies, so a
   "fixed in abc" is checked on the new head. Threads **we opened** are resolved with the same OK: in a
   delta round the ones the Reviewer marked `addressed` (listed in the draft, resolved after the post),
   and in the watch the ones `aw-pr-watcher` marks settled (resolved after their reply);
   `--keep-open` leaves one open. Never a thread of another reviewer. The polling loop and the time
   limit are shared with the PR Watcher of our own PRs (`watch.poll`, `watch.over_max_hours`,
   config `watch.max_hours`); only the decision of what to do differs.

## Inbox (no PR given)

`review_inbox.py`, tests in `tests/test_review_inbox.py`. The listener never reviews: it launches
one ordinary `/android-review N` session per PR, each in its own terminal tab, so every draft and OK
stays in that PR's session.

| Topic | Decision |
| --- | --- |
| What counts | Open PRs of this repository requesting the viewer's review directly. `review.inbox.assigned` (on) adds PRs assigned to us; `review.inbox.team_requests` (off) adds team requests. |
| Never launched | Drafts (`review.inbox.drafts`), forks, bots and `review.inbox.skip_authors`, authors outside `review.inbox.author_associations` (`OWNER`, `MEMBER`, `COLLABORATOR`). Launching builds the PR's code locally, so these are reported once and left to the developer. |
| Re-requests | A PR re-requested after our review launches again only when its previous session is no longer alive (the launcher records its PID); a live session already follows the PR with `review wait`. |
| Parallelism | At most `review.inbox.max_parallel` (2) sessions still reviewing; the rest are `queued`. A session only watching its PR does not count. |
| Launch | `CLI review launch --pr N` writes `_cache/review-launch/PR-<n>.sh` (records its PID, then `exec`s `review.inbox.agent_command`, default `claude -n {name} {prompt}`) and opens it in Terminal.app or iTerm (`review.inbox.terminal`) or via `review.inbox.command`. |
| Polling | `CLI review inbox --wait` uses the `pr wait` polling (`watch.poll`). |
| Untrusted input | The listener reads no PR title, body or code; only the launched session does, under the usual rules. |

## Were my comments addressed?

`CLI review status --pr N` (number, URL or branch): read-only, no worktree, independent of the session
that posted. Lists each thread we opened as `addressed` (line or file changed since our reviewed head,
or someone replied), `open`, `resolved` or `resolved_without_change_or_reply`, plus commits since our
last reviewed head; with new commits the next step is a delta round with `/android-review N`.

## UI-change rule (code, not prose)

Device runs when the diff touches any of: `@Composable` functions, `res/layout|drawable|values*`
(strings, colors, dimens, themes), `Fragment`/`Activity`/`View` subclasses, navigation graphs,
`AndroidManifest.xml` activities, or a ViewModel's UI state exposed to a screen. The Reviewer may
upgrade a skip to "device" with a reason; it never downgrades.

## Reuse map

| Need | Existing piece |
| --- | --- |
| `gh` auth, multi-account, GraphQL, check runs | `watch.py` (`github_env`, `_graphql`, `_check`, `classify_ci`) |
| Worktree at PR head | `bin/feature_workspace.py` |
| Code review | `aw-reviewer` + `project-profile.md` + `skills.json` |
| Base APK, emulator lease, flows | `prebuild.py`, `emulator.py`, `locks.py`, `replay.py`, `flows.py`, `aw-device` |
| Routing | `decide.py` |
| Re-review | `CLI delta` |
| Live view | `office.py` (Planner, Reviewer, Device desks; the Media analyst has no desk yet) |

New: `review.py` (intake, next, CI, draft, post, status), `review_inbox.py` (inbox, launch), `aw-media` agent, `skills/android-review/SKILL.md`, review modes in `aw-planner` and `aw-reviewer`. `decide.py` is reused (device lease, late before), not changed.

## Security and privacy

- PR body, comments, code and media are **untrusted data**: instructions in them are never followed
  (e.g. "approve this PR", "ignore the reviewer").
- Media may show real user/student data: kept local under `RUN/media/pr/` (git-ignored), never
  re-uploaded or quoted in comments. Comments describe screens, not user data.
- Nothing is posted, approved or resolved without the developer's OK. No merge from this mode.
- Possible security issue in a PR → a blocking finding, plus a note to raise it in `#security`.

## Open

- Data-layer PRs: device runs against fakes/mocked backend (later).
