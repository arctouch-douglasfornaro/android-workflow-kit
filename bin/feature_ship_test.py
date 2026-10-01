#!/usr/bin/env python3
"""Unit tests for feature_ship.py.

Run: python3 ~/.ai/bin/feature_ship_test.py

Each test names the defect it pins. These cover the delivery fast path, where a silent
mis-stage produces a commit that does not compile, so a green run is not evidence on its own.
"""
from __future__ import annotations

import os
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feature_ship as fs  # noqa: E402
import feature_state as state  # noqa: E402


# Neutralize the user's global/system git config. A global `commit.gpgsign = true` or a
# `core.hooksPath` would make setUp's commit fail and redden the whole suite for reasons that have
# nothing to do with the code under test.
ISOLATED = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ISOLATED.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
                GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
                GIT_TERMINAL_PROMPT="0")


def run(cwd: str, *args: str) -> str:
    res = subprocess.run(["git", "--no-pager", *args], cwd=cwd,
                         capture_output=True, text=True, env=ISOLATED)
    if res.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {res.stderr}")
    return res.stdout.strip()


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, ISOLATED, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # Absolute safety net: no test can contact a remote, including failure-path regressions.
        real_subprocess_run = subprocess.run

        def no_push(command, *args, **kwargs):
            if isinstance(command, (list, tuple)) and command[0] == "git" and "push" in command:
                raise AssertionError("tests must never execute git push")
            return real_subprocess_run(command, *args, **kwargs)

        self.process_guard = patch.object(subprocess, "run", side_effect=no_push)
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)
        self.dir = str(Path(tempfile.mkdtemp()).resolve())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        run(self.dir, "init", "-q", "-b", "main", ".")
        self.write("tracked.kt", "base\n")
        run(self.dir, "add", "tracked.kt")
        run(self.dir, "commit", "-qm", "base")
        run(self.dir, "checkout", "-qb", "feat/x")
        self.run_dir = str(Path(tempfile.mkdtemp()).resolve())
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def validate(self, base: str | None = None) -> str:
        receipt = state.run(self.dir, self.run_dir, base, "gate", "gate.log",
                            [sys.executable, "-c", "pass"])
        self.assertEqual("PASS", receipt["status"], receipt)
        recorded_base = receipt["snapshot_after"]["base_commit"]
        for phase in ("review", "device"):
            # Capture BEFORE manual work, against the run's immutable base, not current HEAD.
            expected = state.snapshot(self.dir, recorded_base)["fingerprint"]
            Path(self.run_dir, phase + ".md").write_text("Fixture evidence: " + phase)
            state.record(self.dir, self.run_dir, recorded_base, phase,
                         "PASS" if phase == "review" else "NOT_REQUIRED",
                         phase + ".md", reason="No device needed for fixture",
                         expected_fingerprint=expected)
        state.verify_ready(self.dir, self.run_dir)
        return self.run_dir

    def ship(self, *args: str, validate: bool = True, push: bool = False) -> int:
        if validate:
            self.validate()
        return fs.main(["--ticket", "MF-1", "--subject", "S", "--repo-root", self.dir,
                        "--run-dir", self.run_dir, *([] if push else ["--no-push"]), *args])

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(rel) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


class TestCollectPaths(RepoCase):
    def test_untracked_files_are_staged(self) -> None:
        """`git add -u` omitted untracked files, so a feature of new files committed without them."""
        self.write("brand_new.kt", "new\n")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("brand_new.kt", plan.to_stage)

    def test_tracked_modification_is_staged(self) -> None:
        self.write("tracked.kt", "base\nmore\n")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("tracked.kt", plan.to_stage)

    def test_workflow_artifacts_are_excluded(self) -> None:
        self.write(".ai/workflow/RUN/log.md", "notes\n")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertEqual([], [p for p in plan.to_stage if p.startswith(".ai/workflow/")])
        self.assertIn(".ai/workflow/RUN/log.md", plan.excluded)

    def test_deletion_is_staged(self) -> None:
        os.remove(os.path.join(self.dir, "tracked.kt"))
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("tracked.kt", plan.to_stage)

    def test_rename_yields_only_the_new_path(self) -> None:
        """The old path is gone from disk and index; staging it fails the whole ship."""
        run(self.dir, "mv", "tracked.kt", "renamed.kt")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("renamed.kt", plan.to_stage)
        self.assertNotIn("tracked.kt", plan.to_stage)

    def test_rename_never_records_the_arrow_as_a_filename(self) -> None:
        run(self.dir, "mv", "tracked.kt", "renamed.kt")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertFalse([p for p in plan.to_stage if "->" in p], plan.to_stage)

    def test_conflicts_are_reported_not_staged(self) -> None:
        run(self.dir, "checkout", "-q", "main")
        self.write("tracked.kt", "main side\n")
        run(self.dir, "commit", "-qam", "main change")
        run(self.dir, "checkout", "-q", "feat/x")
        self.write("tracked.kt", "feature side\n")
        run(self.dir, "commit", "-qam", "feature change")
        subprocess.run(["git", "merge", "main"], cwd=self.dir, capture_output=True, text=True)
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("tracked.kt", plan.conflicted)
        self.assertNotIn("tracked.kt", plan.to_stage)

    def test_paths_argument_restricts_staging(self) -> None:
        self.write("one.kt", "1\n")
        self.write("two.kt", "2\n")
        plan = fs.collect_paths_to_stage(self.dir, ["one.kt"])
        self.assertIn("one.kt", plan.to_stage)
        self.assertIn("two.kt", plan.excluded)

    def test_paths_argument_accepts_a_directory_prefix(self) -> None:
        self.write("src/a.kt", "a\n")
        self.write("other/b.kt", "b\n")
        plan = fs.collect_paths_to_stage(self.dir, ["src"])
        self.assertIn("src/a.kt", plan.to_stage)
        self.assertIn("other/b.kt", plan.excluded)

    def test_clean_tree_stages_nothing(self) -> None:
        """Drives the refusal that replaced silently adopting HEAD as the run's commit."""
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertEqual([], plan.to_stage)


class TestCommitScope(RepoCase):
    def test_partial_source_commit_is_rejected_before_staging(self) -> None:
        self.write("unrelated.kt", "someone else\n")
        run(self.dir, "add", "unrelated.kt")
        self.write("mine.kt", "mine\n")
        head = run(self.dir, "rev-parse", "HEAD")
        index = run(self.dir, "ls-files", "--stage")
        self.assertEqual(1, self.ship("--paths", "mine.kt"))
        self.assertEqual(head, run(self.dir, "rev-parse", "HEAD"))
        self.assertEqual(index, run(self.dir, "ls-files", "--stage"))

    def test_workflow_artifacts_never_reach_the_commit(self) -> None:
        self.write(".ai/workflow/RUN/log.md", "notes\n")
        run(self.dir, "add", "-A", ".ai")
        self.write("mine.kt", "mine\n")
        rc = self.ship()
        self.assertEqual(0, rc)
        committed = run(self.dir, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual([], [c for c in committed if c.startswith(".ai/")])

    def test_porcelain_prefix_is_not_stripped(self) -> None:
        """`git()` strips stdout, which eats the leading space of the FIRST porcelain record --
        ` M a.kt` becomes `M a.kt`, so an unstaged file reads as staged and its path is sliced
        one char short (`.kt`)."""
        self.write("tracked.kt", "base\nmodified\n")
        lines = fs.porcelain_lines(self.dir)
        self.assertTrue(lines)
        self.assertEqual(" M", lines[0][:2])
        self.assertEqual("tracked.kt", lines[0][3:])


class TestRenameCommit(RepoCase):
    def test_staged_rename_records_the_deletion(self) -> None:
        """`git add` errors on a rename's old path, but `git commit -- <spec>` needs it to record
        the deletion. Passing new-only left the old file in the commit AND `D <old>` staged, so the
        tree held a duplicate Kotlin class and the pushed commit did not compile."""
        os.makedirs(os.path.join(self.dir, "src"))
        self.write("src/Old.kt", "class Old\n")
        run(self.dir, "add", "src/Old.kt")
        run(self.dir, "commit", "-qm", "add old")
        run(self.dir, "mv", "src/Old.kt", "src/New.kt")

        rc = self.ship()
        self.assertEqual(0, rc)
        tree = run(self.dir, "ls-tree", "-r", "--name-only", "HEAD").split()
        self.assertIn("src/New.kt", tree)
        self.assertNotIn("src/Old.kt", tree)
        self.assertEqual([], fs.collect_paths_to_stage(self.dir).to_stage)

    def test_commit_pathspec_carries_both_rename_sides(self) -> None:
        os.makedirs(os.path.join(self.dir, "src"))
        self.write("src/Old.kt", "class Old\n")
        run(self.dir, "add", "src/Old.kt")
        run(self.dir, "commit", "-qm", "add old")
        run(self.dir, "mv", "src/Old.kt", "src/New.kt")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertEqual(["src/New.kt"], plan.to_stage)
        self.assertIn("src/Old.kt", plan.commit_pathspec)
        self.assertIn("src/New.kt", plan.commit_pathspec)


class TestAiTreeExclusion(RepoCase):
    def test_tracked_ai_files_are_committed(self) -> None:
        """`.ai/` is a tracked, team-owned tree in real repos. Excluding the whole prefix silently
        dropped a ticket's own edits to `.ai/rules/` and made a docs-only ticket exit 1."""
        os.makedirs(os.path.join(self.dir, ".ai", "rules"))
        self.write(".ai/rules/compose.md", "rule\n")
        run(self.dir, "add", ".ai/rules/compose.md")
        run(self.dir, "commit", "-qm", "add rules")
        self.write(".ai/rules/compose.md", "rule\nmore\n")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn(".ai/rules/compose.md", plan.to_stage)

    def test_run_artifacts_and_the_profile_are_excluded(self) -> None:
        os.makedirs(os.path.join(self.dir, ".ai", "workflow", "RUN"))
        self.write(".ai/workflow/RUN/log.md", "notes\n")
        self.write(".ai/project-profile.md", "profile\n")
        self.write("code.kt", "code\n")
        plan = fs.collect_paths_to_stage(self.dir)
        self.assertIn("code.kt", plan.to_stage)
        self.assertIn(".ai/workflow/RUN/log.md", plan.excluded)
        self.assertIn(".ai/project-profile.md", plan.excluded)


class TestTicketPrefix(unittest.TestCase):
    def test_a_ticket_that_prefixes_another_is_still_prepended(self) -> None:
        """`startswith` treated MF-233 as already present in "MF-2333: ...", so the commit carried
        the wrong ticket."""
        out = fs.format_commit_message("MF-233", "MF-2333: do a thing")
        self.assertTrue(out.startswith("MF-233 MF-2333:"), out)

    def test_an_exact_ticket_match_is_not_duplicated(self) -> None:
        out = fs.format_commit_message("MF-2333", "MF-2333: do a thing")
        self.assertEqual("MF-2333: do a thing", out)


class TestPathsNormalization(RepoCase):
    def test_absolute_paths_inside_the_repo_are_normalized(self) -> None:
        self.write("one.kt", "1\n")
        plan = fs.collect_paths_to_stage(self.dir, [os.path.join(self.dir, "one.kt")])
        self.assertIn("one.kt", plan.to_stage)

    def test_absolute_path_outside_the_repo_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fs.collect_paths_to_stage(self.dir, ["/etc"])


class TestCommitMessage(unittest.TestCase):
    def test_ticket_prefix_added_once(self) -> None:
        self.assertEqual("MF-1 Do a thing", fs.format_commit_message("MF-1", "Do a thing"))
        self.assertEqual("MF-1 Do a thing", fs.format_commit_message("MF-1", "MF-1 Do a thing"))

    def test_prose_containing_generated_with_survives(self) -> None:
        """An unanchored pattern deleted the subject and git promoted a body line, losing the ticket."""
        subject = "Regenerate README generated with the doc skill"
        out = fs.format_commit_message("MF-3", subject)
        self.assertTrue(out.startswith("MF-3 Regenerate README generated with"), out)

    def test_real_attribution_trailer_is_stripped_from_body(self) -> None:
        out = fs.format_commit_message(
            "MF-4", "Real work",
            "Body line.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
            "🤖 Generated with Claude Code",
        )
        self.assertNotIn("Claude", out)
        self.assertIn("Body line.", out)
        self.assertTrue(out.startswith("MF-4 Real work"))
        cloud = fs.format_commit_message(
            "MF-4", "Real work",
            "Body line.\n\nMade with Cloud Code\nCo-Authored-By: Cloud Code <cloudcode@google.com>\n",
        )
        self.assertNotIn("Cloud Code", cloud)
        self.assertIn("Body line.", cloud)

    def test_body_prose_mentioning_a_tool_name_survives(self) -> None:
        out = fs.format_commit_message("MF-4", "Real work", "The enum file is generated with buildSrc.")
        self.assertIn("generated with buildSrc", out)

    def test_long_subject_trims_on_a_word_boundary(self) -> None:
        subject = ("Handle unavailable sets deleted private and protected across the entire SRS "
                   "flashcards flow end to end")
        out = fs.format_commit_message("MF-5", subject).splitlines()[0]
        self.assertLessEqual(len(out), 72)
        self.assertFalse(out.endswith("-"), out)
        self.assertTrue(subject.split()[0] in out)
        # no mid-word cut: the final token is a whole word from the input
        self.assertIn(out.split()[-1], subject.split() + ["MF-5"])

    def test_attribution_only_subject_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            fs.format_commit_message("", "🤖 Generated with Claude Code")

    def test_attribution_pattern_requires_line_start(self) -> None:
        self.assertIsNone(fs.ATTRIBUTION_PATTERN.search("see the file generated with care"))
        self.assertIsNotNone(fs.ATTRIBUTION_PATTERN.search("Co-Authored-By: Claude <x@y>"))
        self.assertIsNotNone(fs.ATTRIBUTION_PATTERN.search("🤖 Generated with Claude Code"))
        self.assertIsNotNone(fs.ATTRIBUTION_PATTERN.search("Made with Cloud Code"))
        self.assertIsNotNone(fs.ATTRIBUTION_PATTERN.search("Co-Authored-By: Cloud Code <cloudcode@google.com>"))


class TestArgumentParsing(unittest.TestCase):
    def test_bare_paths_flag_is_rejected(self) -> None:
        """With nargs="*", a bare `--paths` (an empty shell var expanding away) yielded [], which is
        falsy -- skipping the filter and staging every dirty file, the opposite of the intent."""
        with self.assertRaises(SystemExit):
            fs.main(["--ticket", "MF-1", "--subject", "S", "--repo-root", ".", "--paths"])


class TestAttributionGuard(unittest.TestCase):
    def test_pattern_matches_a_trailer_on_a_later_line(self) -> None:
        """The final safety net applies the ^-anchored pattern to the JOINED message; without
        re.MULTILINE it could only ever match the first line, making the guard dead."""
        msg = "MF-1 Real subject\n\nBody line.\nCo-Authored-By: Claude <x@y>"
        self.assertIsNotNone(fs.ATTRIBUTION_PATTERN.search(msg))

    def test_pattern_still_ignores_mid_line_prose(self) -> None:
        msg = "MF-1 Real subject\n\nThe enum file is generated with buildSrc.\n"
        self.assertIsNone(fs.ATTRIBUTION_PATTERN.search(msg))


class TestValidatedDelivery(RepoCase):
    def assert_blocked_without_mutation(self, *args: str) -> None:
        head = run(self.dir, "rev-parse", "HEAD")
        index = run(self.dir, "ls-files", "--stage")
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(1, self.ship(*args, validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list
                          if c.args[0][0] in ("add", "commit", "push")])
        self.assertEqual(head, run(self.dir, "rev-parse", "HEAD"))
        self.assertEqual(index, run(self.dir, "ls-files", "--stage"))

    def test_missing_run_dir_is_required_even_without_push(self) -> None:
        self.write("tracked.kt", "change\n")
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(1, fs.main(["--ticket", "T", "--subject", "S", "--no-push",
                                         "--repo-root", self.dir]))
        self.assertFalse(calls.called)

    def test_missing_receipts(self) -> None:
        self.write("tracked.kt", "change\n")
        self.assert_blocked_without_mutation()

    def test_stale_source(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        self.write("tracked.kt", "not validated\n")
        self.assert_blocked_without_mutation()

    def test_corrupt_state(self) -> None:
        self.write("tracked.kt", "change\n")
        self.validate()
        Path(self.run_dir, state.STATE_FILE).write_text("{broken")
        self.assert_blocked_without_mutation()

    def test_legacy_manual_approval_without_expected_fingerprint_blocks_ship(self) -> None:
        self.write("tracked.kt", "validated\n")
        for phase in ("review", "device"):
            with self.subTest(phase=phase):
                self.validate()
                path = Path(self.run_dir, state.STATE_FILE)
                receipts = json.loads(path.read_text())
                del receipts["phases"][phase]["expected_fingerprint"]
                path.write_text(json.dumps(receipts))
                self.assert_blocked_without_mutation()

    def test_stale_manual_approval_expected_fingerprint_blocks_ship(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        path = Path(self.run_dir, state.STATE_FILE)
        receipts = json.loads(path.read_text())
        receipts["phases"]["review"]["expected_fingerprint"] = "0" * 64
        path.write_text(json.dumps(receipts))
        self.assert_blocked_without_mutation()

    def test_missing_prerequisite_blocks_ship_despite_later_approvals(self) -> None:
        self.write("tracked.kt", "validated\n")
        for phase in ("gate", "review"):
            with self.subTest(phase=phase):
                self.validate()
                path = Path(self.run_dir, state.STATE_FILE)
                receipts = json.loads(path.read_text())
                del receipts["phases"][phase]
                path.write_text(json.dumps(receipts))
                self.assert_blocked_without_mutation()

    def test_recorded_base_is_used_before_and_after_commit_not_moving_branch(self) -> None:
        recorded_base = run(self.dir, "rev-parse", "main")
        self.write("already_committed.kt", "previous feature work\n")
        run(self.dir, "add", "already_committed.kt")
        run(self.dir, "commit", "-qm", "earlier feature work")
        self.write("tracked.kt", "validated\n")
        self.validate(base="main")
        # Move the branch ref without changing source; receipts pin its original commit.
        run(self.dir, "branch", "-f", "main", "HEAD")
        ready = state.verify_ready(self.dir, self.run_dir)
        self.assertEqual(recorded_base, ready["base_commit"])
        self.assert_blocked_without_mutation("--base", "main")
        real_snapshot = state.snapshot
        snapshots = []

        def snapshot(root, base, **kwargs):
            result = real_snapshot(root, base, **kwargs)
            snapshots.append((base, result))
            return result

        with patch.object(state, "snapshot", side_effect=snapshot):
            self.assertEqual(0, self.ship(validate=False))
        self.assertEqual([recorded_base] * 3, [base for base, _ in snapshots])
        after = snapshots[-1][1]
        self.assertEqual(ready["content_fingerprint"], after["content_fingerprint"])
        self.assertNotEqual(ready["fingerprint"], after["fingerprint"])
        self.assertEqual({}, after["working_overrides"])
        self.assertEqual(fs.source_tree(self.dir, after["head"]), fs.source_index(self.dir))

    def test_missing_or_changed_evidence(self) -> None:
        self.write("tracked.kt", "change\n")
        self.validate()
        evidence = Path(self.run_dir, "review.md")
        evidence.write_text("modified")
        self.assert_blocked_without_mutation()
        evidence.unlink()
        self.assert_blocked_without_mutation()

    def test_failed_gate_and_unapproved_phases(self) -> None:
        self.write("tracked.kt", "change\n")
        self.validate()
        state.run(self.dir, self.run_dir, None, "gate", "gate.log",
                  [sys.executable, "-c", "raise SystemExit(1)"])
        self.assert_blocked_without_mutation()
        for phase, status in (("review", "FAIL"), ("device", "BLOCKED")):
            self.validate()
            state.record(self.dir, self.run_dir, None, phase, status, phase + ".md")
            self.assert_blocked_without_mutation()

    def test_validation_api_error_fails_closed(self) -> None:
        self.write("tracked.kt", "change\n")
        with patch.object(state, "verify_ready", side_effect=RuntimeError("unreadable artifact")):
            self.assert_blocked_without_mutation()

    def test_change_during_validation_capture_blocks_staging(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        real_verify = state.verify_ready

        def change(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            self.write("tracked.kt", "concurrent writer\n")
            return result

        with patch.object(state, "verify_ready", side_effect=change):
            self.assert_blocked_without_mutation()

    def test_only_state_snapshots_read_source_with_cache_and_one_post_hook_pass(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        real_snapshot, real_working = state.snapshot, state._working
        inside_snapshot = False
        heads = []

        def snapshot(*args, **kwargs):
            nonlocal inside_snapshot
            inside_snapshot = True
            try:
                result = real_snapshot(*args, **kwargs)
                heads.append(result["head"])
                return result
            finally:
                inside_snapshot = False

        def working(*args, **kwargs):
            self.assertTrue(inside_snapshot, "ship must not independently hash source")
            cache = args[3] if len(args) > 3 else kwargs.get("cache")
            self.assertIsNotNone(cache, "state cache must remain enabled")
            return real_working(*args, **kwargs)

        old_head = run(self.dir, "rev-parse", "HEAD")
        with patch.object(state, "snapshot", side_effect=snapshot), \
                patch.object(state, "_working", side_effect=working), \
                patch.object(state, "verify_ready", wraps=state.verify_ready) as verify:
            self.assertEqual(0, self.ship(validate=False))
        self.assertEqual(1, verify.call_count)
        new_head = run(self.dir, "rev-parse", "HEAD")
        self.assertEqual([old_head, old_head, new_head], heads)

    def test_unstaged_partial_source_also_rejected(self) -> None:
        self.write("mine.kt", "mine\n")
        self.write("tracked.kt", "unrelated\n")
        self.validate()
        self.assert_blocked_without_mutation("--paths", "mine.kt")

    def test_hidden_assume_unchanged_source_is_not_silently_omitted(self) -> None:
        run(self.dir, "update-index", "--assume-unchanged", "tracked.kt")
        self.write("tracked.kt", "hidden\n")
        self.write("mine.kt", "mine\n")
        self.validate()
        self.assert_blocked_without_mutation()

    def test_valid_addition_deletion_and_literal_filename(self) -> None:
        os.remove(os.path.join(self.dir, "tracked.kt"))
        self.write(":(glob)*.txt", "literal filename\n")
        self.write("space and\nnewline.txt", "new\n")
        self.assertEqual(0, self.ship())
        self.assertEqual([], fs.collect_paths_to_stage(self.dir).to_stage)
        self.assertEqual("literal filename", run(self.dir, "show", "HEAD::(glob)*.txt"))
        self.assertNotIn("tracked.kt", run(self.dir, "ls-tree", "-r", "--name-only", "HEAD"))

    def test_generated_profile_metadata_may_be_excluded_by_paths(self) -> None:
        self.write("mine.kt", "mine\n")
        for name in state.EXCLUDED:
            self.write(name, "generated\n")
        run(self.dir, "add", ".ai")
        self.assertEqual(0, self.ship("--paths", "mine.kt"))
        self.assertEqual(["mine.kt"], run(self.dir, "show", "--name-only", "--format=", "HEAD").split())

    def test_dry_run_without_receipts_is_explicitly_not_delivery(self) -> None:
        self.write("mine.kt", "mine\n")
        output = io.StringIO()
        summary = os.path.join(self.run_dir, "09-ship.md")
        with contextlib.redirect_stdout(output), patch.object(
                state, "verify_ready", side_effect=AssertionError("must not validate preview")):
            self.assertEqual(0, fs.main(["--ticket", "T", "--subject", "S", "--repo-root",
                                        self.dir, "--dry-run", "--run-dir", self.run_dir,
                                        "--summary-file", summary]))
        for text in (output.getvalue(), Path(summary).read_text()):
            self.assertIn("validation not performed", text)
            self.assertIn("not a valid delivery", text)
        self.assertEqual("", run(self.dir, "diff", "--cached", "--name-only"))
        self.assertEqual("base", run(self.dir, "log", "-1", "--format=%s"))

    def test_verified_commit_can_reach_mock_push_despite_head_change(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        old_head = run(self.dir, "rev-parse", "HEAD")
        real_git = fs.git
        pushes = []

        def mock_push(args, *rest, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return ""
            return real_git(args, *rest, **kwargs)

        summary = os.path.join(self.run_dir, "09-ship.md")
        with patch.object(fs, "git", side_effect=mock_push):
            self.assertEqual(0, self.ship("--summary-file", summary, validate=False, push=True))
        self.assertEqual([["push", "-u", "origin", "feat/x"]], pushes)
        self.assertNotEqual(old_head, run(self.dir, "rev-parse", "HEAD"))
        self.assertIn("committed source matches validated worktree", Path(summary).read_text())
        with self.assertRaisesRegex(ValueError, "stale"):
            state.verify_ready(self.dir, self.run_dir)

    def test_summary_cannot_overwrite_source(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        self.assert_blocked_without_mutation("--summary-file", "tracked.kt")

    def test_git_normalization_cannot_silently_change_validated_bytes(self) -> None:
        self.write(".gitattributes", "*.txt text eol=lf\n")
        self.write("normalized.txt", "validated\r\n")
        self.validate()
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list if c.args[0][0] == "push"])

    def test_mock_push_failure_writes_failure_summary(self) -> None:
        self.write("tracked.kt", "validated\n")
        real_git = fs.git

        def rejected(args, *rest, **kwargs):
            if args[0] == "push":
                raise RuntimeError("mock remote rejected")
            return real_git(args, *rest, **kwargs)

        summary = os.path.join(self.run_dir, "09-ship.md")
        with patch.object(fs, "git", side_effect=rejected):
            self.assertEqual(1, self.ship("--summary-file", summary, push=True))
        self.assertIn("FAILED", Path(summary).read_text())
        self.assertIn("mock remote rejected", Path(summary).read_text())

    def test_preserve_host_required_attribution_in_actual_commit(self) -> None:
        self.write("tracked.kt", "validated\n")
        body = "Body\n\n🤖 Generated with [Firebender](https://firebender.com)\n\n" \
               "Co-Authored-By: Firebender <help@firebender.com>"
        self.assertEqual(0, self.ship("--preserve-attribution", "--body", body))
        self.assertIn(body, run(self.dir, "log", "-1", "--format=%B"))

    def test_format_preserves_attribution_when_requested(self) -> None:
        body = "Body\nCo-Authored-By: Claude <x@y>\n🤖 Generated with Claude Code"
        self.assertIn(body, fs.format_commit_message("T", "S", body, preserve_attribution=True))


class TestHookGuards(RepoCase):
    def hook_case(self, timing: str, change) -> None:
        self.write("mine.kt", "validated\n")
        self.validate()
        old_head = run(self.dir, "rev-parse", "HEAD")
        real_git = fs.git

        def simulate_hook(args, *rest, **kwargs):
            if args[0] == "commit" and timing == "before":
                change()
            result = real_git(args, *rest, **kwargs)
            if args[0] == "commit" and timing == "after":
                change()
            return result

        # Simulate hook endpoints without executable/shell portability assumptions.
        with patch.object(fs, "git", side_effect=simulate_hook) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list if c.args[0][0] == "push"])
        self.assertNotEqual(old_head, run(self.dir, "rev-parse", "HEAD"))

    def test_hook_changes_committed_selected_content(self) -> None:
        self.hook_case("before", lambda: self.write("mine.kt", "hook content\n"))

    def test_post_commit_unstaged_change_to_previously_clean_source(self) -> None:
        self.hook_case("after", lambda: self.write("tracked.kt", "hook content\n"))

    def test_post_commit_index_only_change_with_worktree_restored(self) -> None:
        def change():
            self.write("mine.kt", "index-only\n")
            run(self.dir, "add", "mine.kt")
            self.write("mine.kt", "validated\n")
        self.hook_case("after", change)

    def test_committed_content_changed_but_worktree_restored(self) -> None:
        self.write("mine.kt", "validated\n")
        self.validate()
        real_git = fs.git

        def hook(args, *rest, **kwargs):
            if args[0] == "commit":
                self.write("mine.kt", "hook version\n")
            result = real_git(args, *rest, **kwargs)
            if args[0] == "commit":
                self.write("mine.kt", "validated\n")
            return result

        with patch.object(fs, "git", side_effect=hook) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list if c.args[0][0] == "push"])

    def test_post_commit_untracked_addition(self) -> None:
        self.hook_case("after", lambda: self.write("new.kt", "hook\n"))

    def test_post_commit_deletion(self) -> None:
        self.hook_case("after", lambda: os.remove(os.path.join(self.dir, "tracked.kt")))

    def test_post_commit_rename(self) -> None:
        self.hook_case("after", lambda: os.rename(os.path.join(self.dir, "tracked.kt"),
                                                 os.path.join(self.dir, "renamed.kt")))

    def test_post_commit_mode_change(self) -> None:
        self.hook_case("after", lambda: os.chmod(os.path.join(self.dir, "tracked.kt"), 0o755))

    def test_post_commit_symlink_target_change(self) -> None:
        os.symlink("tracked.kt", os.path.join(self.dir, "link"))

        def change():
            os.unlink(os.path.join(self.dir, "link"))
            os.symlink("mine.kt", os.path.join(self.dir, "link"))
        self.hook_case("after", change)

    def test_hook_changes_block_no_push_success_too(self) -> None:
        self.write("mine.kt", "validated\n")
        self.validate()
        real_git = fs.git

        def hook(args, *rest, **kwargs):
            result = real_git(args, *rest, **kwargs)
            if args[0] == "commit":
                self.write("mine.kt", "post-commit\n")
            return result

        with patch.object(fs, "git", side_effect=hook):
            self.assertEqual(1, self.ship(validate=False))

    def test_symlink_content_is_compared_without_following(self) -> None:
        self.write("mine.kt", "validated\n")
        os.symlink("tracked.kt", os.path.join(self.dir, "link"))
        self.assertEqual(0, self.ship())
        self.assertEqual("120000", run(self.dir, "ls-tree", "HEAD", "link").split()[0])

    def test_hook_failure_never_pushes(self) -> None:
        self.write("mine.kt", "validated\n")
        self.validate()
        real_git = fs.git

        def fail_commit(args, *rest, **kwargs):
            if args[0] == "commit":
                raise RuntimeError("hook rejected commit")
            return real_git(args, *rest, **kwargs)

        with patch.object(fs, "git", side_effect=fail_commit) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list if c.args[0][0] == "push"])


class TestDeliveryBlockers(RepoCase):
    def test_main_is_protected_with_remote_ref_and_sha(self) -> None:
        run(self.dir, "checkout", "main")
        sha = run(self.dir, "rev-parse", "HEAD")
        run(self.dir, "update-ref", "refs/remotes/origin/main", sha)
        self.write("tracked.kt", "change\n")
        for base in ("origin/main", sha):
            with self.subTest(base=base), patch.object(fs, "git", wraps=fs.git) as calls:
                self.assertEqual(1, self.ship("--base", base))
                self.assertFalse([c for c in calls.call_args_list
                                  if c.args[0][0] in ("add", "commit", "push")])

    def test_custom_default_and_supplied_remote_branch_are_protected(self) -> None:
        run(self.dir, "checkout", "-b", "release/stable")
        run(self.dir, "update-ref", "refs/remotes/origin/release/stable", "HEAD")
        run(self.dir, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release/stable")
        self.write("tracked.kt", "change\n")
        self.assertEqual(1, self.ship())
        run(self.dir, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.assertEqual(1, self.ship("--base", "origin/release/stable"))

    def test_explicit_base_sha_must_match_receipts(self) -> None:
        pinned = run(self.dir, "rev-parse", "HEAD")
        self.write("extra.kt", "extra\n")
        run(self.dir, "add", "extra.kt")
        run(self.dir, "commit", "-qm", "extra")
        self.write("tracked.kt", "change\n")
        self.validate(base=pinned)
        self.assertEqual(1, self.ship("--base", "HEAD", validate=False))
        self.assertEqual(0, self.ship("--base", pinned, validate=False))

    def test_summary_cannot_write_source_or_escape_artifacts_in_any_mode(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        unsafe = ["tracked.kt", "../escape.md", os.path.join(self.run_dir, "..", "escape.md"),
                  os.path.join(self.run_dir, state.STATE_FILE)]
        os.symlink(os.path.join(self.dir, "tracked.kt"), os.path.join(self.run_dir, "link.md"))
        unsafe.append(os.path.join(self.run_dir, "link.md"))
        os.symlink(self.dir, os.path.join(self.run_dir, "linked"))
        unsafe.append(os.path.join(self.run_dir, "linked", "tracked.kt"))
        for dry in ([], ["--dry-run"]):
            for name in unsafe:
                with self.subTest(dry=dry, name=name):
                    self.assertEqual(1, self.ship(*dry, "--summary-file", name, validate=False))
                    self.assertEqual("validated\n", Path(self.dir, "tracked.kt").read_text())

    def test_dry_summary_without_run_dir_only_allows_workflow_children(self) -> None:
        self.write("tracked.kt", "validated\n")
        args = ["--ticket", "T", "--subject", "S", "--repo-root", self.dir, "--dry-run"]
        self.assertEqual(1, fs.main([*args, "--summary-file", "tracked.kt"]))
        self.assertEqual(1, fs.main([*args, "--summary-file", os.path.join(self.run_dir, "out.md")]))
        self.assertEqual(0, fs.main([*args, "--summary-file", ".ai/workflow/preview/09-ship.md"]))
        self.assertEqual("validated\n", Path(self.dir, "tracked.kt").read_text())

    def test_blocked_commit_summary_and_fresh_resume_without_history_rewrite(self) -> None:
        self.write("tracked.kt", "validated\n")
        self.validate()
        real_git = fs.git

        def hook(args, *rest, **kwargs):
            if args[0] == "commit":
                self.write("tracked.kt", "hook content\n")
            return real_git(args, *rest, **kwargs)

        with patch.object(fs, "git", side_effect=hook):
            self.assertEqual(1, self.ship(validate=False, push=True))
        sha = run(self.dir, "rev-parse", "HEAD")
        summary = Path(self.run_dir, "09-ship.md")
        self.assertIn(sha, summary.read_text())
        self.assertIn("BLOCKED", summary.read_text())
        self.assertEqual(1, self.ship("--resume-commit", sha, validate=False))
        self.write(".ai/workflow/local-notes.md", "generated\n")
        run(self.dir, "add", ".ai/workflow/local-notes.md")
        self.validate()
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(0, self.ship("--resume-commit", sha, "--summary-file", str(summary),
                                         validate=False))
        self.assertFalse([c for c in calls.call_args_list
                          if c.args[0][0] in ("add", "commit", "push")])
        self.assertEqual(sha, run(self.dir, "rev-parse", "HEAD"))
        self.assertIn("resumed approved HEAD", summary.read_text())

        def mocked_push(args, *rest, **kwargs):
            return "" if args[0] == "push" else real_git(args, *rest, **kwargs)

        with patch.object(fs, "git", side_effect=mocked_push) as calls:
            self.assertEqual(0, self.ship("--resume-commit", sha, validate=False, push=True))
        self.assertEqual(1, len([c for c in calls.call_args_list if c.args[0][0] == "push"]))

    def test_resume_rejects_wrong_sha_and_dirty_source_even_when_validated(self) -> None:
        self.validate()
        sha = run(self.dir, "rev-parse", "HEAD")
        self.assertEqual(1, self.ship("--resume-commit", sha[:8], validate=False))
        for mutation in ("worktree", "index"):
            self.write("tracked.kt", "dirty\n")
            if mutation == "index":
                run(self.dir, "add", "tracked.kt")
                self.write("tracked.kt", "base\n")
            self.validate()
            with patch.object(fs, "git", wraps=fs.git) as calls:
                self.assertEqual(1, self.ship("--resume-commit", sha, validate=False, push=True))
            self.assertFalse([c for c in calls.call_args_list
                              if c.args[0][0] in ("add", "commit", "push")])


class TestSubmoduleSource(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.sub = os.path.join(self.dir, "dependency")
        os.makedirs(self.sub)
        run(self.sub, "init", "-q", "-b", "main", ".")
        self.write("dependency/source.txt", "dependency\n")
        run(self.sub, "add", "source.txt")
        run(self.sub, "commit", "-qm", "dependency base")
        run(self.dir, "add", "dependency")
        run(self.dir, "commit", "-qm", "add gitlink")
        self.write("mine.kt", "validated\n")

    def test_clean_gitlink_content_survives_parent_commit(self) -> None:
        self.assertEqual(0, self.ship())

    def test_uninitialized_gitlink_blocks_before_staging(self) -> None:
        self.validate()
        shutil.rmtree(self.sub)
        os.makedirs(self.sub)
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list
                          if c.args[0][0] in ("add", "commit", "push")])

    def test_dirty_gitlink_blocks_before_staging(self) -> None:
        self.validate()
        self.write("dependency/source.txt", "dirty\n")
        with patch.object(fs, "git", wraps=fs.git) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list
                          if c.args[0][0] in ("add", "commit", "push")])

    def test_hook_dirtying_gitlink_blocks_push(self) -> None:
        self.validate()
        real_git = fs.git

        def hook(args, *rest, **kwargs):
            result = real_git(args, *rest, **kwargs)
            if args[0] == "commit":
                self.write("dependency/source.txt", "hook edit\n")
            return result

        with patch.object(fs, "git", side_effect=hook) as calls:
            self.assertEqual(1, self.ship(validate=False, push=True))
        self.assertFalse([c for c in calls.call_args_list if c.args[0][0] == "push"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
