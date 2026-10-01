# Phase 9 — Authorized delivery

Use deterministic shipping directly or [ship-agent.md](../../agents/ship-agent.md), then [pr-author.md](../../agents/pr-author.md).
Execute only explicitly authorized commit/push/PR actions. Auto mode does not expand permissions.
`--no-commit` implies `--no-push` and `--no-pr`; `--no-push` implies `--no-pr`.
Once permission is established, do not request repeated delivery confirmation except under `--no-auto` or host requirements.
Higher-priority attribution, security, and host Git policies win; this workflow cannot override them.

## Preconditions

- Final full-diff snapshot includes committed, staged, unstaged, untracked, and deleted source.
- Gate PASS, actual isolated review PASS, device PASS or justified NOT_REQUIRED all match final sources.
- Required device BLOCKED/FAIL halts; never deliver using a "degraded" checklist.
- Documentation is already included; no final unconditional formatting or code edits.
- Deferred findings and explicit scope changes are disclosed, not used to relabel failed AC.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA check --run-dir RUN
```

`check` defaults to all stages. Hashes bind evidence to source; they do not prove a human/agent artifact is truthful.
No isolated reviewer means no normal review PASS. A disclosed explicit waiver does not license forging it or bypassing automated checks.

## Commit and push

```sh
python3 ~/.ai/bin/feature_ship.py --ticket TICKET --subject "Subject" \
  --body-file RUN/commit-body.txt --summary-file RUN/09-ship.md \
  --repo-root REPO --run-dir RUN --base PINNED_BASE_SHA [--no-push] [--paths PATH ...]
```

Paths are absolute where applicable; run from the selected workspace.
Non-dry-run shipping REQUIRES `--run-dir`; never omit it to bypass state.
Pass `--preserve-attribution` when host policy requires attribution; the helper otherwise strips attribution-shaped lines.
The ship helper must check state before commit and again after hooks, before push.
If the installed helper lacks that contract, stop and report it rather than silently using an unsafe path.

Inspect status and the full staged/unstaged/untracked/deleted change set before staging.
Stage only authorized feature paths, explicitly including new files and deletions.
Do not use `commit -a`, broad staging with unrelated dirt, or include secrets/local run artifacts.
Respect existing user changes and the selected base; no force push, base-branch push, or unrequested history rewrite.
Follow host hook policy; never skip hooks or silently change machine configuration.
If hooks modify source, STOP before push: impacted gate → clean delta review → targeted device when required.
Commit bookkeeping alone may preserve source identity; content changes never do.
Inspect resulting commit/status and record actual SHA, included paths, push result, and any blockers.

For a committed-but-blocked attempt, retain the recorded full SHA. After revalidating the
exact current commit (gate, isolated review, and required device), use the same shipping command
with `--resume-commit FULL_SHA`. This never creates/amends a commit; it requires clean source/index,
matching HEAD, and current receipts before an authorized push. Never resume an unrelated commit.
Dry-run writes summaries only under the workflow artifact directory and is not delivery approval.

## PR

Give `pr-author` only the template, final full diff/snapshot, ticket key, shipping facts, concise verification facts, and carryover if any.
Do not pass agent transcripts, implementation narratives, the whole log, or review prose.
Use `09-carryover.md` only for actual nonblocking gaps, external findings, or explicitly changed scope.
Required blocked checks cannot be laundered through carryover.

Keep template headings only. 2–3 lines of prose. No invented sections.
No agent/tool attribution in the commit message or PR body.
No invented test counts, media URLs, approval, or claim that pending checks passed.
Verify the body and authorization, then open the PR using the host's supported GitHub tooling.
Keep local media paths honest; never pretend private/local files are public attachments.
Record the real PR URL, or the action skipped by flag/permission.
Correct a later-proven stale claim only within authorized PR edits; do not rewrite history to polish reporting.

## Final report

State delivered/skipped/blocked, commit SHA/PR URL if any, verification snapshot, device outcome, meaningful gaps, and measured duration.
No new build/test run merely to populate the report. Reuse final unchanged evidence.
