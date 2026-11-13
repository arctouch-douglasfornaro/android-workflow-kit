"""Android Review inbox: which PRs start a review session, which wait, which are only reported."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import review_inbox as inbox
from android_workflow.paths import cache_dir


def pr(number: int, **fields: object) -> dict:
    value = {"number": number, "title": f"PR {number}", "url": f"https://github.com/o/r/pull/{number}",
             "isDraft": False, "isCrossRepository": False, "authorAssociation": "MEMBER",
             "headRefOid": "a" * 40, "author": {"__typename": "User", "login": "dev"}, "reasons": ["review_requested"]}
    value.update(fields)
    return value


class InboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "app"
        self.target.mkdir()
        self.config = inbox.inbox_config(self.target)
        self.alive: set[int] = set()
        self.watching: set[int] = set()
        patches = [mock.patch.object(inbox, "session_alive", side_effect=lambda _t, n: n in self.alive),
                   mock.patch.object(inbox, "session_watching", side_effect=lambda _t, n: n in self.watching)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def scan(self, *prs: dict) -> dict:
        return inbox.scan(self.target, {p["number"]: p for p in prs}, self.config)

    def launched(self, number: int) -> None:
        path = cache_dir(self.target) / inbox.STATE_FILE
        state = json.loads(path.read_text())
        state["prs"][str(number)]["launched_at"] = 1
        state["prs"][str(number)].pop("relaunch", None)
        path.write_text(json.dumps(state))

    def test_a_new_request_is_launched_once(self) -> None:
        first = self.scan(pr(1))
        self.assertEqual([item["pr"] for item in first["launch"]], [1])
        self.launched(1)
        self.assertEqual(self.scan(pr(1))["launch"], [])

    def test_a_rerequest_after_our_review_launches_again_unless_a_session_still_runs(self) -> None:
        self.scan(pr(1))
        self.launched(1)
        self.scan()  # our review removed the request
        again = self.scan(pr(1))
        self.assertEqual([(i["pr"], i["why"]) for i in again["launch"]], [(1, "rerequested")])
        self.alive.add(1)
        self.scan()
        self.assertEqual(self.scan(pr(1))["launch"], [])  # the live session's own watch handles it

    def test_a_rerequest_not_launched_yet_is_retried(self) -> None:
        self.scan(pr(1))
        self.launched(1)
        self.scan()
        self.assertEqual(len(self.scan(pr(1))["launch"]), 1)
        self.assertEqual(len(self.scan(pr(1))["launch"]), 1)  # launch never ran: still due

    def test_untrusted_or_draft_prs_are_reported_once_and_never_launched(self) -> None:
        found = self.scan(pr(1, isCrossRepository=True), pr(2, authorAssociation="NONE"), pr(3, isDraft=True),
                          pr(4, author={"__typename": "Bot", "login": "dependabot"}))
        self.assertEqual(found["launch"], [])
        self.assertEqual([item["pr"] for item in found["skipped"]], [1, 2, 3, 4])
        self.assertIn("fork", found["skipped"][0]["why"])
        self.assertEqual(self.scan(pr(1, isCrossRepository=True))["skipped"], [])
        self.assertEqual([i["pr"] for i in self.scan(pr(3, isDraft=False))["launch"]], [3])  # ready for review

    def test_max_parallel_queues_while_sessions_review_but_not_while_they_only_watch(self) -> None:
        self.alive |= {1, 2}
        self.scan(pr(1), pr(2))
        found = self.scan(pr(1), pr(2), pr(3))
        self.assertEqual((found["launch"], [q["pr"] for q in found["queued"]]), ([], [3]))
        self.watching.add(1)
        self.assertEqual([i["pr"] for i in self.scan(pr(1), pr(2), pr(3))["launch"]], [3])

    def test_queries_follow_the_config(self) -> None:
        self.assertEqual([r for r, _ in inbox.queries("o/r", self.config)], ["review_requested", "assigned"])
        team = {**self.config, "team_requests": True, "assigned": False}
        self.assertEqual([r for r, _ in inbox.queries("o/r", team)], ["review_requested", "team_review_requested"])
        self.assertTrue(all("repo:o/r" in q and "-author:@me" in q for _, q in inbox.queries("o/r", team)))


class LaunchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "my app"
        self.target.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_script_records_its_pid_and_execs_the_review(self) -> None:
        script = inbox.write_script(self.target, 42, inbox.inbox_config(self.target))
        text = script.read_text()
        self.assertIn(f"cd {shlex.quote(str(self.target))}", text)
        self.assertIn("PR-42.pid", text)
        self.assertIn("exec claude -n 'review PR-42' '/android-review 42 --target", text)

    def test_a_running_session_is_not_launched_twice_and_the_pid_is_checked(self) -> None:
        path = inbox.pid_file(self.target, 42)
        path.parent.mkdir(parents=True)
        path.write_text(str(os.getpid()))
        self.assertTrue(inbox.session_alive(self.target, 42))
        self.assertEqual(inbox.launch(self.target, "42")["status"], "running")
        dead = subprocess.Popen(["true"])
        dead.wait()
        path.write_text(str(dead.pid))
        self.assertFalse(inbox.session_alive(self.target, 42))

    def test_launch_opens_the_configured_terminal_and_marks_the_pr(self) -> None:
        (self.target / ".ai").mkdir()
        (self.target / ".ai" / "android-workflow.json").write_text(
            json.dumps({"review": {"inbox": {"terminal": "command", "command": "echo {script}"}}}))
        self.assertEqual(inbox.launch(self.target, "#7", dry_run=True)["command"][-1].split()[0], "echo")
        result = inbox.launch(self.target, "https://github.com/o/r/pull/7")
        self.assertEqual(result["status"], "launched")
        state = json.loads((cache_dir(self.target) / inbox.STATE_FILE).read_text())
        self.assertEqual(state["prs"]["7"]["launches"], 1)

    def test_terminal_kinds(self) -> None:
        script = Path("/tmp/x y.sh")
        with mock.patch.object(inbox.sys, "platform", "darwin"), mock.patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
            self.assertIn("iTerm", " ".join(inbox.open_command(script, dict(inbox.DEFAULT_INBOX))))
        with mock.patch.object(inbox.sys, "platform", "darwin"), mock.patch.dict(os.environ, {"TERM_PROGRAM": "TerminalOne"}):
            self.assertIn('"Terminal"', " ".join(inbox.open_command(script, dict(inbox.DEFAULT_INBOX))))
        with self.assertRaises(ValueError):
            inbox.open_command(script, {**inbox.DEFAULT_INBOX, "terminal": "command", "command": "open"})


if __name__ == "__main__":
    unittest.main()
