from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The one office every app publishes to: a throwaway folder, never the developer's own.
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from android_workflow import watch
from android_workflow.cli import (
    TOOLKIT_FINGERPRINT_FILE,
    append_stage_log,
    diff_base,
    main,
    read_json,
    run,
    source_changes,
    toolkit_changes,
    toolkit_fingerprint,
)
from android_workflow.paths import run_dir
from tests.test_workflow import AndroidProject, approve_review, fake_gradle_project, implement

GIT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *GIT, *args], text=True, capture_output=True, check=True).stdout.strip()


def lint_gate(root: Path, issues: list[str], overrides: dict | None = None) -> tuple[int, dict]:
    fake_gradle_project(root, {"tasks": {":app:lintDebug": "lint"}, "lint_issues": issues})
    if overrides:
        path = root / ".ai/android-workflow.json"
        path.write_text(json.dumps({**read_json(path), **overrides}), encoding="utf-8")
    run(root, {"id": "L-1", "title": "Change ProfileScreen", "type": "chore"})
    implement(root)
    code = main(["gate", "--target", str(root)])
    report = read_json(run_dir(root) / "gate-report.json")
    return code, next(step for step in report["steps"] if step["command"].endswith(":app:lintDebug"))


SCREEN = "app/src/main/java/com/example/ProfileScreen.kt"


class ChangedLinesLintTests(unittest.TestCase):
    def test_an_old_finding_on_an_unchanged_line_of_a_touched_file_is_waived(self) -> None:
        with AndroidProject() as root:
            code, lint = lint_gate(root, [f"{SCREEN}:2: Error: Old problem [EmptySuperCall]"])
        self.assertEqual(code, 0)
        self.assertEqual(lint["outcome"], "waived")
        self.assertEqual(lint["waiver"]["reason"], "findings_on_unchanged_lines")

    def test_an_old_finding_whose_message_wraps_onto_the_next_line_is_still_waived(self) -> None:
        issues = [
            f"{SCREEN}:2: Error: Old problem whose message is long\n"
            "enough to wrap onto a second line [DiscouragedApi]\n"
            "        super.onCleared()\n"
            "        ~~~~~~~~~",
            "other/src/Old.kt:4: Error: Short one [EmptySuperCall]",
        ]
        with AndroidProject() as root:
            fake_gradle_project(root, {
                "tasks": {":app:lintDebug": "lint"},
                "lint_issues": issues,
                "lint_summary": "2 errors, 0 warnings\n",
            })
            run(root, {"id": "L-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            code = main(["gate", "--target", str(root)])
            report = read_json(run_dir(root) / "gate-report.json")
            lint = next(step for step in report["steps"] if step["command"].endswith(":app:lintDebug"))
        self.assertEqual(code, 0)
        self.assertEqual(lint["outcome"], "waived")
        self.assertEqual(lint["waiver"]["count"], 2)

    def test_a_finding_on_a_line_the_change_wrote_still_fails(self) -> None:
        with AndroidProject() as root:
            code, lint = lint_gate(root, [
                f"{SCREEN}:2: Error: Old problem [OldId]",
                f"{SCREEN}:3: Error: New problem [OldId]",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(lint["outcome"], "failed")
        self.assertEqual(lint["in_scope_findings"], 1)

    def test_changed_files_scope_keeps_old_findings_in_a_touched_file_failing(self) -> None:
        with AndroidProject() as root:
            code, lint = lint_gate(
                root, [f"{SCREEN}:2: Error: Old problem [OldId]"], {"quality_gates": {"lint_scope": "changed_files"}},
            )
        self.assertEqual(code, 1)
        self.assertEqual(lint["outcome"], "failed")


class DiffBaseTests(unittest.TestCase):
    def test_merging_the_default_branch_does_not_widen_the_change(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            git(root, "branch", "-M", "main")
            git(root, "checkout", "-qb", "feature")
            run(root, {"id": "M-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            git(root, "add", "-A")
            git(root, "commit", "-qm", "feature work")
            git(root, "checkout", "-q", "main")
            (root / "core/domain/Other.kt").write_text("class Other\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "someone else's work")
            git(root, "update-ref", "refs/remotes/origin/main", "main")
            git(root, "checkout", "-q", "feature")
            git(root, "merge", "-q", "--no-edit", "main")

            self.assertEqual(diff_base(root), git(root, "rev-parse", "main"))
            self.assertNotIn("core/domain/Other.kt", source_changes(root))

    def test_without_a_remote_the_pinned_base_is_kept(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            run(root, {"id": "M-2", "title": "Change ProfileScreen", "type": "chore"})
            self.assertEqual(diff_base(root), git(root, "rev-parse", "HEAD"))


class StageLogDedupeTests(unittest.TestCase):
    def test_one_round_logged_by_the_agent_and_the_orchestrator_is_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            append_stage_log(agent_dir, "T4", "started", "host", "plumb size")
            append_stage_log(agent_dir, "T4", "completed", "aw_implementer", "size plumbed with tests")
            append_stage_log(agent_dir, "T4", "completed", "host", "size plumbed")
            append_stage_log(agent_dir, "T4", "started", "host", "fix round 1")
            append_stage_log(agent_dir, "T4", "completed", "host", "fixed")
            rows = [line for line in (agent_dir / "stage-log.md").read_text(encoding="utf-8").splitlines()
                    if "| Implementer | completed" in line]
        self.assertEqual(len(rows), 2)
        self.assertIn("size plumbed with tests", rows[0])

    def test_cli_evidence_lines_do_not_hide_the_agent_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            append_stage_log(agent_dir, "T7", "started", "host", "before")
            append_stage_log(agent_dir, "T7", "completed", "cli", "evidence ingest a")
            append_stage_log(agent_dir, "T7", "completed", "host", "before captured")
            log = (agent_dir / "stage-log.md").read_text(encoding="utf-8")
        self.assertIn("before captured", log)


class RestartKeepsVerdictsTests(unittest.TestCase):
    def test_start_again_keeps_an_approved_review_of_the_same_source(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            ticket = {"id": "R-1", "title": "Change ProfileScreen", "type": "chore"}
            run(root, ticket)
            implement(root)
            self.assertEqual(approve_review(root), 0)
            run(root, ticket)
            review = read_json(run_dir(root) / "review.json")
            state = read_json(run_dir(root) / "run-state.json")
        self.assertEqual(review["status"], "approved")
        self.assertEqual(state["stages"]["T6"]["verdict"], "approved")

    def test_start_again_drops_a_review_of_older_source(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            ticket = {"id": "R-2", "title": "Change ProfileScreen", "type": "chore"}
            run(root, ticket)
            implement(root)
            self.assertEqual(approve_review(root), 0)
            implement(root, "fun other() = Unit\n")
            run(root, ticket)
            review = read_json(run_dir(root) / "review.json")
        self.assertEqual(review["status"], "not_run")


class ToolkitProseTests(unittest.TestCase):
    def test_editing_a_role_file_does_not_block_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            recorded = toolkit_fingerprint()
            prose = [path for path in recorded if path.endswith(".md")]
            if not prose:
                self.skipTest("no markdown in the toolkit fingerprint")
            recorded[prose[0]] = "0" * 64
            (agent_dir / TOOLKIT_FINGERPRINT_FILE).write_text(json.dumps({"files": recorded}), encoding="utf-8")
            self.assertEqual(toolkit_changes(agent_dir), [])


class WatchLogKeyTests(unittest.TestCase):
    def test_ci_moving_between_checks_is_one_wait(self) -> None:
        first = {"action": "wait", "waiting_for": ["checks running: build, lint", "review from a"]}
        later = {"action": "wait", "waiting_for": ["checks running: unitTests", "review from a"]}
        approved = {"action": "wait", "waiting_for": ["checks running: unitTests"]}
        self.assertEqual(watch._log_key(first, ""), watch._log_key(later, ""))
        self.assertNotEqual(watch._log_key(later, ""), watch._log_key(approved, ""))


if __name__ == "__main__":
    unittest.main()
