---
name: eval-playbook
description: >-
  Measure whether the android-workflow agents (aw-setup, aw-planner, …) still do their job after
  a change to their instructions: each case runs the real agent on a synthetic app, checks what it
  wrote by code, has a judge of another model score it against a rubric, and compares the score
  with the last run. Use after editing a SKILL.md or agents/aw-*.md of android-workflow or
  android-review, or when the user types /eval-playbook, asks to "run the evals", "rodar os evals",
  "avaliar as skills", or to keep improving a skill until it scores 10.
---

# Eval playbook

`EVAL` is shorthand for `python3 ~/.ai/bin/skill_eval.py`; write the full command in Bash.
Cases live in `~/.ai/evals/cases/<id>/` (case.json, prompt, extra files), the synthetic app in
`~/.ai/evals/fixtures/`. Scores go to `~/.cache/android-workflow/evals/history.jsonl` with the kit
commit, so a change is judged against the run before it.

## Run

1. **Pick the cases.** `EVAL list`. The developer named cases or an agent → those; you just edited
   `agents/aw-X.md` → every case whose `agent` is `aw-X`; a `SKILL.md` → all. Say which, in one line.
2. **Prepare all of them** in one turn: `EVAL prepare --case ID` each. Keep `id`, `agent`, `tier`,
   `workdir`, `return_file`, `prompt`.
3. **Spawn every agent in the same turn** (they run in parallel, one workspace each): `subagent_type`
   = `agent`, the `prompt` exactly as returned (never add that it is a test, an eval or graded;
   an agent that knows behaves differently), model from `tier` (strong → opus, standard → sonnet,
   fast → haiku), a neutral description ("Set up notes app"). Never help it or fix its files: what it
   leaves is the result. The one exception is a question: an agent that returns `NEEDS_INPUT` on a case
   with `answers` gets that answer once, as a developer would give it (`CLI resume --question-id … --answer
   "<answers>"` for each question it recorded, then resume the agent saying the answers are recorded).
   Without `answers`, the question is the result. Save only the final reply.
4. **Save each final reply** verbatim to its `return_file` (Write).
5. **Score**, per run: `EVAL check --run ID`, then `EVAL judge --run ID` (Codex, read-only in the
   workspace; `--judge claude --model sonnet` only when Codex is not installed or fails twice; the
   judge is never the agent's own model), then `EVAL record --run ID --agent-model <model>`. Judges of
   different runs can go in parallel (background Bash).
6. **Report** `EVAL report` as a table: case, score, delta, failed checks, rubric items under 7. Then,
   for each loss, one line with the cause read from the judge's reasons and the check details
   (`~/.cache/android-workflow/evals/runs/<run>/run.json`), and the instruction that would fix it
   (file and section of the agent's `.md`).
7. `EVAL clean` keeps the last 10 workspaces.

## Improve (hill climbing)

When the developer asks to improve until a target ("até 10", "until every case is 9+"), or runs this
under `/loop`:

1. Fix the agent's instructions, never the case: a check or rubric that is wrong is the developer's
   call (say why and ask). One cause per edit, in the agent's own words and section.
2. Re-run only the cases that lost points, then every case of that agent once before stopping (an edit
   can break another case).
3. Stop at the target, or after 3 rounds with no gain, and report the curve (score per round) and the
   edits. Commit the instruction edits as usual for this repo; the history stays local.

A score moves with the model's randomness: one run that drops by less than 1 point is noise; rerun
that case once before blaming an edit.

## Add a case

`~/.ai/evals/cases/<id>/case.json`: `agent`, `tier`, `fixture`, `prepare` (`overrides`, `accept_profile`,
`copy` (`render: true` fills `{BASE_SHA}`, `{HEAD_SHA}`, `{TARGET}` in the copied file), `commit` (`message`; the
import is the base, a commit is e.g. the PR head of a `pr` review), `branch`, `git` (`args`), `cli` with `as` to fill `{NAME}` in the prompt (`field` keeps one value of its answer); `{FAKEBIN}` is a
folder next to the workspace for stand-ins like a logging `gh`, put first on the prompt's CLI `PATH`), `snapshot` (files compared by `unchanged`),
`prompt_file`, `answers` (what the developer says to a NEEDS_INPUT, optional), `checks` (`json_equals`, `json_contains`, `file_exists`, `file_absent`, `regex`,
`not_regex`, `cli`, `mutation` (undo the behavior with `find`/`replace` in `file`, run `command`: the agent's tests
must fail), `unchanged`, `sections_unchanged`, `no_secrets`, `map_row`, `not_mapped`, each with
a `weight`), `rubric` (`id`, `weight`, `criterion` the judge scores 0–10 against the code), `judge_reads`.
The prompt reads like the host's real spawn and never says eval, test, grade or rubric; the fixture
is synthetic (no real app code, no user data, no real accounts). `python3 -m unittest skill_eval_test`
in `~/.ai/bin` checks every case loads.

## Never

Tell the agent it is evaluated, edit its output, change a check to make a run pass, put real code
or user data in a fixture, or run the judge with the agent's own model.
