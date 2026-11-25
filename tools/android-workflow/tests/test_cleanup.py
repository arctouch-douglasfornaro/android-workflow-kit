"""`CLI cleanup`: worktrees of merged or closed PRs go; open PRs keep theirs unless --all, which archives the run."""

from __future__ import annotations

import json
import os

os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))
# The developer\'s own autonomy config never reaches a test.
os.environ["ANDROID_WORKFLOW_CONFIG"] = "/nonexistent/android-workflow-config.json"

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import cleanup, watch

GIT = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *GIT, *args], text=True, capture_output=True, check=True).stdout.strip()


def fake_gh(states: dict[str, str]):
    """`pr view N` and `pr list --head BRANCH` answered from `states` (key: the number or the branch)."""
    def run(args, **_):
        if args[:2] == ["pr", "view"]:
            state = states.get(args[2])
            out = json.dumps({"number": int(args[2]), "state": state}) if state else ""
            return subprocess.CompletedProcess(args, 0 if state else 1, out, "")
        if args[:2] == ["pr", "list"]:
            branch = args[args.index("--head") + 1]
            entry = states.get(branch)
            rows = [{"number": int(entry.split(":")[0]), "state": entry.split(":")[1]}] if entry else []
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")
    return run


class CleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.main = base / "app"
        self.main.mkdir()
        git(self.main, "init", "-q")
        (self.main / "README.md").write_text("app\n", encoding="utf-8")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-qm", "init")
        self.ticket = base / "wt-T-1-thing"
        git(self.main, "worktree", "add", "-q", str(self.ticket), "-b", "me/T-1-thing")
        run = self.ticket / ".ai/workflow/T-1"
        run.mkdir(parents=True)
        (run / "run-state.json").write_text('{"status": "completed"}', encoding="utf-8")
        (run / "media").mkdir()
        (run / "media/big.png").write_bytes(b"png")
        old = 1_600_000_000
        for item in run.rglob("*"):
            os.utime(item, (old, old))  # an idle run: no session is working in it
        self.review = base / "wt-review-PR-7"
        git(self.main, "worktree", "add", "-q", "--detach", str(self.review), "HEAD")
        launch = self.main / ".ai/workflow/_cache/review-launch"
        launch.mkdir(parents=True)
        (launch / "PR-7.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        for patch in (mock.patch.object(watch, "github_env", return_value={}),):
            patch.start()
            self.addCleanup(patch.stop)

    def clean(self, states: dict[str, str], **kwargs) -> dict:
        with mock.patch.object(watch, "_gh", side_effect=fake_gh(states)):
            return cleanup.cleanup(self.ticket, **kwargs)

    def test_only_merged_or_closed_prs_lose_their_worktree(self) -> None:
        result = self.clean({"7": "MERGED", "me/T-1-thing": "5:OPEN"})
        self.assertEqual([item["pr"] for item in result["removed"]], [7])
        self.assertFalse(self.review.exists())
        self.assertTrue(self.ticket.exists())
        self.assertFalse((self.main / ".ai/workflow/_cache/review-launch/PR-7.sh").exists())
        self.assertEqual(result["kept"][0]["state"], "OPEN")

    def test_a_merged_pr_also_loses_its_local_branch(self) -> None:
        result = self.clean({"7": "OPEN", "me/T-1-thing": "5:MERGED"})
        self.assertEqual([item["branch"] for item in result["removed"]], ["me/T-1-thing"])
        self.assertTrue(result["removed"][0]["branch_deleted"])
        self.assertEqual(git(self.main, "branch", "--list", "me/T-1-thing"), "")

    def test_all_archives_open_runs_and_a_recreated_worktree_gets_them_back(self) -> None:
        result = self.clean({"7": "OPEN", "me/T-1-thing": "5:OPEN"}, scope="all")
        archived = self.main / ".ai/workflow/_archive/T-1"
        self.assertEqual(len(result["removed"]), 2)
        self.assertTrue((archived / "run-state.json").is_file())
        self.assertFalse((archived / "media").exists())  # captures are not kept
        again = Path(self.tmp.name).resolve() / "wt-T-1-again"
        git(self.main, "worktree", "add", "-q", str(again), "me/T-1-thing")
        self.assertTrue(cleanup.restore_run(again, "T-1"))
        self.assertTrue((again / ".ai/workflow/T-1/run-state.json").is_file())
        self.assertFalse(cleanup.restore_run(again, "T-1"))  # never over a run that is there

    def test_uncommitted_work_is_never_removed_and_dry_run_removes_nothing(self) -> None:
        (self.ticket / "README.md").write_text("edited\n", encoding="utf-8")
        result = self.clean({"7": "MERGED", "me/T-1-thing": "5:MERGED"})
        self.assertIn("uncommitted", result["kept"][0]["reason"])
        self.assertTrue(self.ticket.exists())
        (self.ticket / "README.md").write_text("app\n", encoding="utf-8")
        self.assertFalse(self.review.exists())  # the clean one went
        dry = self.clean({"me/T-1-thing": "5:MERGED"}, dry_run=True)
        self.assertEqual([item["pr"] for item in dry["removed"]], [5])
        self.assertTrue(self.ticket.exists())


class InUseTests(CleanupTests):
    def test_a_worktree_a_session_just_touched_is_kept_unless_forced(self) -> None:
        (self.ticket / ".ai/workflow/T-1/run-state.json").write_text('{"status": "running"}', encoding="utf-8")
        kept = self.clean({"7": "OPEN", "me/T-1-thing": "5:MERGED"})
        self.assertIn("min ago", kept["kept"][-1]["reason"])
        self.assertTrue(self.ticket.exists())
        forced = self.clean({"7": "OPEN", "me/T-1-thing": "5:MERGED"}, force=True, pr=5)
        self.assertEqual([item["pr"] for item in forced["removed"]], [5])


if __name__ == "__main__":
    unittest.main()
