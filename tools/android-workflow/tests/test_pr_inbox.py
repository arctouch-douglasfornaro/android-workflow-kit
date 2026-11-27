"""PR Watcher inbox: which of the developer's own PRs get a watch session, in which checkout, with which ticket."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))
os.environ["ANDROID_WORKFLOW_CONFIG"] = "/nonexistent/android-workflow-config.json"

import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import pr_inbox, review_inbox
from android_workflow.paths import cache_dir, run_dir, set_current


def pr(number: int, **fields: object) -> dict:
    value = {"number": number, "title": f"LT-{number}: thing", "url": f"https://github.com/o/r/pull/{number}",
             "isDraft": False, "isCrossRepository": False, "headRefName": f"dev/LT-{number}-thing",
             "headRefOid": "a" * 40}
    value.update(fields)
    return value


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True, check=True).stdout.strip()


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.main = Path(self.tmp.name) / "app"
        self.main.mkdir()
        self.settings = pr_inbox.config(self.main)
        self.busy: dict[int, str] = {}
        patches = [mock.patch.object(pr_inbox, "checkout_for", return_value=None),
                   mock.patch.object(pr_inbox, "watched", side_effect=lambda _m, _c, n: self.busy.get(n))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def scan(self, *prs: dict, first: bool = False) -> dict:
        return pr_inbox.scan(self.main, {p["number"]: p for p in prs}, self.settings, first)

    def launched(self, number: int) -> None:
        path = cache_dir(self.main) / pr_inbox.STATE_FILE
        state = json.loads(path.read_text())
        state["prs"][str(number)]["launched_at"] = 1
        path.write_text(json.dumps(state))

    def test_a_new_pr_is_launched_once_while_listening(self) -> None:
        self.assertEqual([i["pr"] for i in self.scan(pr(1))["launch"]], [1])
        self.launched(1)
        self.assertEqual(self.scan(pr(1))["launch"], [])

    def test_the_first_pass_relaunches_a_pr_whose_watch_ended(self) -> None:
        self.scan(pr(1))
        self.launched(1)
        self.assertEqual([i["pr"] for i in self.scan(pr(1), first=True)["launch"]], [1])

    def test_a_pr_already_watched_is_never_launched(self) -> None:
        self.busy[1] = "its watch tab is running"
        found = self.scan(pr(1), first=True)
        self.assertEqual((found["launch"], [r["pr"] for r in found["running"]]), ([], [1]))
        del self.busy[1]  # that session ended: the developer stopped it, a later pass leaves it
        self.assertEqual(self.scan(pr(1))["launch"], [])

    def test_forks_are_reported_once_and_drafts_follow_the_config(self) -> None:
        found = self.scan(pr(1, isCrossRepository=True), pr(2, isDraft=True))
        self.assertEqual([i["pr"] for i in found["skipped"]], [1])
        self.assertEqual([i["pr"] for i in found["launch"]], [2])
        self.assertEqual(self.scan(pr(1, isCrossRepository=True))["skipped"], [])
        self.settings["drafts"] = False
        self.assertEqual([i["pr"] for i in self.scan(pr(3, isDraft=True))["skipped"]], [3])


class CheckoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        git(root, "init", "--bare", "-b", "main", str(self.origin))
        self.main = root / "app"
        git(root, "clone", str(self.origin), str(self.main))
        git(self.main, "config", "user.email", "t@t")
        git(self.main, "config", "user.name", "t")
        (self.main / "a.txt").write_text("a")
        git(self.main, "add", ".")
        git(self.main, "commit", "-m", "a")
        git(self.main, "push", "origin", "HEAD:main")
        git(self.main, "push", "origin", "HEAD:refs/heads/dev/LT-7-thing")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_branch_with_no_checkout_gets_its_own_worktree_tracking_origin(self) -> None:
        self.assertIsNone(pr_inbox.checkout_for(self.main, "dev/LT-7-thing"))
        path, report = pr_inbox.add_worktree(self.main, "dev/LT-7-thing")
        self.assertEqual(path.name, "wt-LT-7-thing")
        self.assertEqual(pr_inbox.checkout_for(self.main, "dev/LT-7-thing"), path.resolve())
        self.assertEqual(git(path, "rev-parse", "--abbrev-ref", "@{u}"), "origin/dev/LT-7-thing")
        self.assertIn("bootstrap", report)

    def test_the_main_checkout_on_that_branch_is_used_as_is(self) -> None:
        git(self.main, "checkout", "-b", "dev/LT-8")
        self.assertEqual(pr_inbox.checkout_for(self.main, "dev/LT-8"), self.main.resolve())

    def test_the_ticket_comes_from_the_run_then_the_branch_then_the_title(self) -> None:
        set_current(self.main, "LT-9")
        (run_dir(self.main, "LT-9")).mkdir(parents=True)
        (run_dir(self.main, "LT-9") / "run-state.json").write_text("{}")
        self.assertEqual(pr_inbox.ticket_for(self.main, pr(9)), ("LT-9", True))
        (run_dir(self.main, "LT-9") / "pr-watch.json").write_text(json.dumps({"pr": {"number": 5}}))
        self.assertEqual(pr_inbox.ticket_for(self.main, pr(9)), ("LT-9", False))  # another PR's run: the branch
        self.assertEqual(pr_inbox.ticket_for(None, pr(3, headRefName="dev/fix", title="mf-12 crash")), ("MF-12", False))
        self.assertEqual(pr_inbox.ticket_for(None, pr(3, headRefName="fix", title="crash")), ("PR-3", False))


class OpenWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "wt"
        self.target.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_pr_with_no_run_gets_its_title_in_the_prompt(self) -> None:
        found = review_inbox.open_watch(self.target, 4, "LT-4", title='Fix "the" crash', dry_run=True)
        self.assertEqual(found["prompt"], "/android-workflow LT-4 \"Fix 'the' crash\" --watch --pr 4")
        script = Path(found["script"]).read_text()
        self.assertIn("WATCH-4.pid", script)
        self.assertIn(shlex.quote(found["prompt"]), script)
        plain = review_inbox.open_watch(self.target, 4, "LT-4", dry_run=True)
        self.assertEqual(plain["prompt"], "/android-workflow LT-4 --watch --pr 4")

    def test_a_worktree_watch_script_lives_in_the_main_checkout(self) -> None:
        main = Path(self.tmp.name) / "app"
        main.mkdir()
        git(main, "init", "-q")
        git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        tree = Path(self.tmp.name) / "wt-LT-4"
        git(main, "worktree", "add", "-q", "-b", "dev/LT-4", str(tree))
        found = review_inbox.open_watch(tree, 4, "LT-4", dry_run=True)
        script = Path(found["script"])
        self.assertEqual(script.parent, review_inbox.launch_dir(main.resolve()))  # Terminal One: same project
        text = script.read_text()
        self.assertIn(f"cd {shlex.quote(str(tree))}", text)  # the session still runs in the worktree
        self.assertIn(str(review_inbox.pid_file(tree, 4, "WATCH")), text)


if __name__ == "__main__":
    unittest.main()
