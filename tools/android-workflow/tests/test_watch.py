from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The one office every app publishes to: a throwaway folder, never the developer's own.
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"

import copy
import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import delivery, watch
from android_workflow.paths import run_dir
from tests.test_delivery_prebuild import finished_run, git
from tests.test_workflow import AndroidProject

NOW = 1_800_000_000.0
OLD = "2026-01-01T00:00:00Z"
ME = "dev"


def stamp(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - seconds_ago))


def user(login: str) -> dict:
    return {"__typename": "User", "login": login}


def bot(login: str) -> dict:
    return {"__typename": "Bot", "login": login}


def check(name: str, conclusion: str = "SUCCESS", required: bool = True, completed: str = OLD) -> dict:
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion or None,
            "detailsUrl": f"https://github.com/o/r/actions/runs/7/job/{abs(hash(name)) % 1000}",
            "isRequired": required, "completedAt": completed}


def pull_request(**changes) -> dict:
    pr = {
        "id": "PR_1", "number": 5, "url": "https://github.com/o/r/pull/5", "title": "D-1: Change", "state": "OPEN",
        "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
        "createdAt": OLD, "headRefName": "user/D-1-change", "headRefOid": "abc123def4567890", "baseRefName": "main",
        "reviewRequests": {"nodes": []},
        "latestOpinionatedReviews": {"nodes": [{"author": user("ana"), "state": "APPROVED", "submittedAt": OLD}]},
        "reviews": {"nodes": []}, "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
        "commits": {"nodes": [{"commit": {"oid": "abc123def4567890", "committedDate": OLD,
                                          "statusCheckRollup": {"contexts": {"nodes": [check("build")]}}}}]},
    }
    for key, value in changes.items():
        pr[key] = value
    return pr


def thread(*comments: tuple[str, dict, str], resolved: bool = False) -> dict:
    return {"id": "T_1", "isResolved": resolved, "isOutdated": False, "path": "app/Main.kt", "line": 3,
            "comments": {"nodes": [{"id": cid, "author": author, "body": body, "createdAt": OLD, "url": "u"}
                                   for cid, author, body in comments]}}


def fresh_watch(**changes) -> dict:
    state = {"posted": [], "handled": {}, "ci": {}, "rounds": 0}
    state.update(changes)
    return state


CONFIG = dict(watch.DEFAULT_WATCH)


def decide(pr: dict, state: dict | None = None, **config) -> dict:
    return watch.assess(pr, ME, state or fresh_watch(), {**CONFIG, **config}, NOW)


class AssessTests(unittest.TestCase):
    def test_a_merged_or_closed_pr_ends_the_watch(self) -> None:
        self.assertEqual(decide(pull_request(state="MERGED"))["action"], "merged")
        self.assertEqual(decide(pull_request(state="CLOSED"))["action"], "closed")

    def test_approved_and_green_is_ready_to_merge(self) -> None:
        result = decide(pull_request())
        self.assertEqual(result["action"], "merge")
        self.assertTrue(result["auto_merge"])

    def test_a_reviewer_comment_in_a_thread_is_work(self) -> None:
        result = decide(pull_request(reviewThreads={"nodes": [thread(("C1", user("ana"), "Rename this"))]}))
        self.assertEqual(result["action"], "respond")
        self.assertEqual(result["items"][0]["id"], "C1")
        self.assertEqual(result["items"][0]["thread"], "T_1")
        self.assertEqual(result["items"][0]["path"], "app/Main.kt")

    def test_a_thread_whose_last_word_is_ours_or_the_developers_waits_for_the_reviewer(self) -> None:
        ours = thread(("C1", user("ana"), "Rename this"), ("C2", user(ME), "Done in abc123"))
        self.assertEqual(decide(pull_request(reviewThreads={"nodes": [ours]}), fresh_watch(posted=["C2"]))["action"], "merge")
        by_hand = thread(("C1", bot("cursor"), "Stale reload"), ("C2", user(ME), "No longer applies"))
        self.assertEqual(decide(pull_request(reviewThreads={"nodes": [by_hand]}))["action"], "merge")

    def test_a_new_reviewer_reply_after_ours_is_work_again(self) -> None:
        convo = thread(("C1", user("ana"), "Rename"), ("C2", user(ME), "Done"), ("C3", user("ana"), "Also the test"))
        result = decide(pull_request(reviewThreads={"nodes": [convo]}), fresh_watch(posted=["C2"], handled={"C1": {}}))
        self.assertEqual([item["id"] for item in result["items"]], ["C3"])
        self.assertTrue(result["items"][0]["conversation"][1]["ours"])

    def test_resolved_and_handled_threads_are_not_work(self) -> None:
        resolved = thread(("C1", user("ana"), "Rename"), resolved=True)
        self.assertEqual(decide(pull_request(reviewThreads={"nodes": [resolved]}))["action"], "merge")
        handled = thread(("C1", user("ana"), "Nit"))
        self.assertEqual(decide(pull_request(reviewThreads={"nodes": [handled]}), fresh_watch(handled={"C1": {}}))["action"], "merge")

    def test_bot_review_threads_count_but_bot_summaries_and_own_comments_do_not(self) -> None:
        comments = {"nodes": [
            {"id": "IC1", "author": bot("codecov"), "body": "Coverage report", "url": "u"},
            {"id": "IC2", "author": user(ME), "body": "cc @ana", "url": "u"},
            {"id": "IC3", "author": user("ana"), "body": "Can you add a screenshot?", "url": "u"},
        ]}
        pr = pull_request(comments=comments, reviewThreads={"nodes": [thread(("C1", bot("cursor"), "Bug: race"))]})
        result = decide(pr)
        self.assertEqual(sorted(item["id"] for item in result["items"]), ["C1", "IC3"])
        self.assertTrue(next(item for item in result["items"] if item["id"] == "C1")["bot"])
        self.assertEqual(len(decide(pr, bots=["codecov"])["items"]), 3)

    def test_a_review_body_with_changes_requested_is_work(self) -> None:
        reviews = {"nodes": [{"id": "R1", "author": user("ana"), "state": "CHANGES_REQUESTED", "body": "Use the shared dialog", "url": "u"},
                             {"id": "R2", "author": user("ana"), "state": "APPROVED", "body": "LGTM", "url": "u"}]}
        result = decide(pull_request(reviews=reviews))
        self.assertEqual([item["id"] for item in result["items"]], ["R1"])

    def test_a_failing_check_is_work_once_per_head(self) -> None:
        failing = pull_request(commits={"nodes": [{"commit": {"oid": "abc123def4567890", "committedDate": OLD,
                                                              "statusCheckRollup": {"contexts": {"nodes": [check("lint", "FAILURE")]}}}}]})
        result = decide(failing)
        self.assertEqual(result["action"], "respond")
        self.assertEqual(result["ci"][0]["id"], "ci:abc123def456:lint")
        after = decide(failing, fresh_watch(ci={"ci:abc123def456:lint": "ours"}))
        self.assertEqual(after["action"], "wait")
        self.assertIn("failing checks: lint", after["waiting_for"][0])

    def test_an_optional_check_failing_without_this_change_does_not_block(self) -> None:
        contexts = [check("build"), check("mobile-security", "FAILURE", required=False)]
        pr = pull_request(mergeStateStatus="UNSTABLE", commits={"nodes": [{"commit": {
            "oid": "abc123def4567890", "committedDate": OLD, "statusCheckRollup": {"contexts": {"nodes": contexts}}}}]})
        self.assertEqual(decide(pr, fresh_watch(ci={"ci:abc123def456:mobile-security": "unrelated"}))["action"], "merge")

    def test_a_required_check_called_unrelated_still_blocks(self) -> None:
        pr = pull_request(commits={"nodes": [{"commit": {"oid": "abc123def4567890", "committedDate": OLD,
                                                         "statusCheckRollup": {"contexts": {"nodes": [check("build", "FAILURE")]}}}}]})
        self.assertEqual(decide(pr, fresh_watch(ci={"ci:abc123def456:build": "unrelated"}))["action"], "wait")

    def test_a_check_that_fails_again_after_its_rerun_comes_back(self) -> None:
        key = "ci:abc123def456:flaky"
        rerun_at = NOW - 600
        def pr_with(completed: str) -> dict:
            return pull_request(commits={"nodes": [{"commit": {"oid": "abc123def4567890", "committedDate": OLD,
                "statusCheckRollup": {"contexts": {"nodes": [check("flaky", "FAILURE", completed=completed)]}}}}]})
        state = fresh_watch(ci={key: "rerun"}, ci_at={key: rerun_at})
        self.assertEqual(decide(pr_with(stamp(900)), state)["action"], "wait")  # the old failure, rerun still queued
        self.assertEqual(decide(pr_with(stamp(60)), state)["action"], "respond")

    def test_running_checks_wait_but_an_optional_one_stops_blocking_after_the_grace(self) -> None:
        contexts = [check("build"), check("bugbot", "", required=False)]
        def pr_pushed(ago: float) -> dict:
            return pull_request(commits={"nodes": [{"commit": {"oid": "abc123def4567890", "committedDate": stamp(ago),
                                                              "statusCheckRollup": {"contexts": {"nodes": contexts}}}}]})
        self.assertEqual(decide(pr_pushed(60))["action"], "wait")
        self.assertEqual(decide(pr_pushed(3600))["action"], "merge")
        required = pull_request(commits={"nodes": [{"commit": {"oid": "x", "committedDate": OLD, "statusCheckRollup": {
            "contexts": {"nodes": [check("build", "")]}}}}]})
        self.assertIn("checks running: build", decide(required)["waiting_for"][0])

    def test_every_requested_reviewer_must_approve(self) -> None:
        pending = pull_request(reviewRequests={"nodes": [{"requestedReviewer": user("bob")}]}, reviewDecision="REVIEW_REQUIRED")
        result = decide(pending)
        self.assertEqual(result["action"], "wait")
        self.assertIn("review from bob", result["waiting_for"])
        changes = pull_request(latestOpinionatedReviews={"nodes": [
            {"author": user("ana"), "state": "APPROVED"}, {"author": user("bob"), "state": "CHANGES_REQUESTED"}]})
        self.assertIn("re-review from bob", decide(changes)["waiting_for"])

    def test_without_reviewers_it_merges_only_after_the_pr_has_been_open_a_while(self) -> None:
        nobody = {"latestOpinionatedReviews": {"nodes": []}, "reviewDecision": None}
        young = decide(pull_request(createdAt=stamp(300), **nobody))
        self.assertEqual(young["action"], "wait")
        self.assertIn("a reviewer may still be added", young["waiting_for"][0])
        self.assertEqual(decide(pull_request(createdAt=stamp(3600), **nobody))["action"], "merge")
        self.assertEqual(decide(pull_request(**nobody), require_review=True)["action"], "wait")

    def test_the_repository_rules_still_decide(self) -> None:
        self.assertEqual(decide(pull_request(mergeStateStatus="BLOCKED"))["action"], "wait")
        self.assertEqual(decide(pull_request(reviewDecision="REVIEW_REQUIRED", latestOpinionatedReviews={"nodes": []}))["action"], "wait")

    def test_behind_or_conflicting_asks_for_a_sync(self) -> None:
        self.assertEqual(decide(pull_request(mergeStateStatus="BEHIND"))["reason"], "behind")
        conflict = decide(pull_request(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertEqual((conflict["action"], conflict["reason"]), ("sync", "conflict"))

    def test_a_draft_never_merges(self) -> None:
        result = decide(pull_request(isDraft=True))
        self.assertEqual(result["action"], "wait")
        self.assertIn("draft", result["waiting_for"][0])

    def test_too_many_rounds_hands_it_to_the_developer(self) -> None:
        pr = pull_request(reviewThreads={"nodes": [thread(("C9", user("ana"), "One more"))]})
        self.assertEqual(decide(pr, fresh_watch(rounds=6))["action"], "needs_user")


class StatusTests(unittest.TestCase):
    def run_status(self, root: Path, pr: dict) -> dict:
        data = {"viewer": {"login": ME}, "repository": {"squashMergeAllowed": True, "mergeCommitAllowed": True,
                                                        "rebaseMergeAllowed": False, "pullRequest": pr}}
        with mock.patch.object(watch, "github_env", return_value={}), \
                mock.patch.object(watch, "_remote", return_value=("github.com", "o/r")), \
                mock.patch.object(watch, "_graphql", return_value=copy.deepcopy(data)), \
                mock.patch.object(watch, "_ci_log", return_value=None):
            return watch.status(root, 5)

    def test_status_writes_the_inbox_and_returns_no_comment_text(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            pr = pull_request(reviewThreads={"nodes": [thread(("C1", user("ana"), "Secret detail of the review"))]})
            result = self.run_status(root, pr)
            inbox = json.loads((run_dir(root) / "pr-inbox.json").read_text())
            state = json.loads((run_dir(root) / "pr-watch.json").read_text())
            log = (run_dir(root) / "stage-log.md").read_text()
            again = self.run_status(root, pr)
            rounds = json.loads((run_dir(root) / "pr-watch.json").read_text())["rounds"]
            desk = json.loads((run_dir(root) / "run-state.json").read_text())["stages"].get("T10")
        self.assertEqual(result["action"], "respond")
        self.assertEqual(result["items"], 1)
        self.assertNotIn("Secret detail", json.dumps(result))
        self.assertEqual(inbox["items"][0]["body"], "Secret detail of the review")
        self.assertEqual(state["pr"]["number"], 5)
        self.assertEqual(state["merge_methods"], ["squash", "merge"])
        self.assertIn("| PR Watcher | respond | cli | 1 new comment(s) |", log)
        self.assertEqual(again["action"], "respond")
        self.assertEqual(rounds, 1)  # the same batch read twice is one round
        self.assertIsNone(desk)  # an agent is about to work on it: the desk is lit by its `started`

    def test_waiting_marks_the_desk(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            result = self.run_status(root, pull_request(mergeStateStatus="BLOCKED"))
            desk = json.loads((run_dir(root) / "run-state.json").read_text())["stages"]["T10"]
        self.assertEqual(result["action"], "wait")
        self.assertEqual(desk["status"], "watching")


class ReplyTests(unittest.TestCase):
    def inbox(self, root: Path) -> None:
        watch._write(run_dir(root) / "pr-watch.json", {"pr": {"number": 5, "repo": "o/r", "node": "PR_1"}})
        watch._write(run_dir(root) / "pr-inbox.json", {"items": [
            {"id": "C1", "kind": "thread", "thread": "T_1", "author": "ana", "body": "Rename this"},
            {"id": "IC3", "kind": "comment", "author": "ana", "body": "Can you add a screenshot?\nThanks"},
        ], "ci": [{"id": "ci:abc:lint", "name": "lint", "required": True, "url": "https://circleci.com/x"},
                  {"id": "ci:abc:unit", "name": "unit", "required": True,
                   "url": "https://github.com/o/r/actions/runs/77/job/1"}]})

    def test_a_thread_reply_goes_to_its_thread_and_is_remembered(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            self.inbox(root)
            posted = {"addPullRequestReviewThreadReply": {"comment": {"id": "C2", "url": "u2"}}}
            with mock.patch.object(watch, "github_env", return_value={}), \
                    mock.patch.object(watch, "_graphql", return_value=posted) as call:
                result = watch.reply(root, "C1", body="Renamed in abc123.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)")
            state = watch.load_watch(run_dir(root))
        self.assertEqual(result["status"], "replied")
        self.assertEqual(call.call_args.kwargs["thread"], "T_1")
        self.assertNotIn("Claude", call.call_args.kwargs["body"])
        self.assertEqual(state["posted"], ["C2"])
        self.assertEqual(state["handled"]["C1"]["how"], "replied")

    def test_a_top_level_comment_is_answered_with_a_quote_and_a_mention(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            self.inbox(root)
            posted = {"addComment": {"commentEdge": {"node": {"id": "IC9", "url": "u"}}}}
            with mock.patch.object(watch, "github_env", return_value={}), \
                    mock.patch.object(watch, "_graphql", return_value=posted) as call:
                watch.reply(root, "IC3", body="Added to the description.")
        body = call.call_args.kwargs["body"]
        self.assertTrue(body.startswith("> Can you add a screenshot?\n\n@ana Added"))
        self.assertEqual(call.call_args.kwargs["subject"], "PR_1")

    def test_skip_marks_it_handled_without_posting(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            self.inbox(root)
            with mock.patch.object(watch, "_graphql") as call:
                watch.reply(root, "IC3", skip="thanks only")
            state = watch.load_watch(run_dir(root))
        call.assert_not_called()
        self.assertEqual(state["handled"]["IC3"]["reason"], "thanks only")

    def test_a_check_is_rerun_once_and_only_on_github_actions(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            self.inbox(root)
            ok = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(watch, "github_env", return_value={}), mock.patch.object(watch, "_gh", return_value=ok) as gh:
                watch.ci(root, "unit", "rerun")
                with self.assertRaises(ValueError):
                    watch.ci(root, "unit", "rerun")
                with self.assertRaises(ValueError) as raised:
                    watch.ci(root, "lint", "rerun")
            state = watch.load_watch(run_dir(root))
        self.assertEqual(gh.call_args_list[0].args[0][:4], ["run", "rerun", "77", "--failed"])
        self.assertIn("circleci", str(raised.exception))
        self.assertEqual(state["ci"]["ci:abc:unit"], "rerun")


class SyncTests(unittest.TestCase):
    def prepare(self, root: Path, base_change: str, path: str = "app/src/main/java/com/example/ProfileScreen.kt") -> Path:
        finished_run(root)
        remote = root.parent / f"{root.name}-remote.git"
        base = git(root, "rev-parse", "--abbrev-ref", "origin/HEAD").split("/", 1)[1]
        delivery.deliver(root, subject="D-1: Change", no_pr=True)
        watch._write(run_dir(root) / "pr-watch.json", {"pr": {"number": 5, "repo": "o/r", "base": base,
                                                              "head_branch": "user/D-1-change"}})
        other = root.parent / f"{root.name}-other"
        subprocess.run(["git", "clone", "-q", "-b", base, str(remote), str(other)], check=True)
        (other / path).parent.mkdir(parents=True, exist_ok=True)
        (other / path).write_text(base_change, encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "base moves on")
        git(other, "push", "-q", "origin", base)
        return remote

    def test_the_base_comes_in_as_a_merge_and_is_pushed(self) -> None:
        with AndroidProject() as root:
            remote = self.prepare(root, "class Other\n", "app/src/main/java/com/example/Other.kt")
            result, code = watch.sync(root)
            merged_in = (root / "app/src/main/java/com/example/Other.kt").is_file()
            pushed = git(remote, "log", "-1", "--format=%P", "user/D-1-change").split()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(len(pushed), 2)  # a merge commit, never a rewritten history
        self.assertTrue(merged_in)

    def test_a_conflict_is_left_open_and_continue_finishes_it(self) -> None:
        with AndroidProject() as root:
            remote = self.prepare(root, "package com.example\n\nfun ProfileScreen() = 42\n")
            result, code = watch.sync(root)
            self.assertEqual((code, result["status"]), (1, "conflict"), result)
            self.assertIn("app/src/main/java/com/example/ProfileScreen.kt", result["files"])
            again, code = watch.sync(root, resume=True)
            self.assertEqual((code, again["status"]), (1, "conflict"))  # markers still there
            path = root / "app/src/main/java/com/example/ProfileScreen.kt"
            path.write_text("package com.example\n\nfun ProfileScreen() = 42\n", encoding="utf-8")
            done, code = watch.sync(root, resume=True)
            pushed = git(remote, "log", "-1", "--format=%P", "user/D-1-change").split()
        self.assertEqual((code, done["status"]), (0, "synced"))
        self.assertEqual(len(pushed), 2)

    def test_uncommitted_work_is_never_merged_over(self) -> None:
        with AndroidProject() as root:
            self.prepare(root, "package com.example\n\nfun ProfileScreen() = 1\n")
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                watch.sync(root)
        self.assertIn("uncommitted", str(raised.exception))


class MergeTests(unittest.TestCase):
    def test_merge_refuses_when_not_ready_and_uses_the_checked_head(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            watch._write(run_dir(root) / "pr-watch.json", {"pr": {"number": 5, "repo": "o/r", "url": "u"},
                                                           "merge_methods": ["squash", "merge"]})
            with mock.patch.object(watch, "status", return_value={"action": "wait", "waiting_for": ["review from bob"]}):
                result, code = watch.merge(root)
            self.assertEqual((code, result["status"]), (1, "not_ready"))
            ok = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(watch, "status", return_value={"action": "merge", "local": {}}), \
                    mock.patch.object(watch, "github_env", return_value={}), \
                    mock.patch.object(watch, "_graphql", return_value={"repository": {"pullRequest": {"headRefOid": "abc"}}}), \
                    mock.patch.object(watch, "_gh", return_value=ok) as gh:
                result, code = watch.merge(root)
            state = watch.load_watch(run_dir(root))
        self.assertEqual((code, result["method"]), (0, "squash"))
        self.assertEqual(gh.call_args.args[0], ["pr", "merge", "5", "--squash", "--match-head-commit", "abc"])
        self.assertNotIn("--admin", gh.call_args.args[0])
        self.assertEqual(state["ended"], "merged")


class DeliverKeepBodyTests(unittest.TestCase):
    def test_a_feedback_round_pushes_without_touching_the_pr_description(self) -> None:
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "OPEN https://github.com/o/r/pull/5", "")

        with mock.patch("android_workflow.delivery.shutil.which", return_value="/bin/gh"), \
                mock.patch("android_workflow.watch.github_env", return_value={}), \
                mock.patch("android_workflow.delivery.subprocess.run", side_effect=fake_run):
            result = delivery.open_pull_request(Path("."), "github", "git@github.com:o/r.git", "b", "main", "s",
                                                Path("body.md"), timeout=5, keep_body=True)
        self.assertEqual(result, {"status": "updated", "url": "https://github.com/o/r/pull/5", "body": "kept"})
        self.assertFalse(any("edit" in call for call in calls))


if __name__ == "__main__":
    unittest.main()


class KeepDeviceTests(unittest.TestCase):
    def test_a_feedback_round_keeps_an_earlier_device_pass_only_when_there_was_one(self) -> None:
        from android_workflow.cli import finish_run, main, run
        from tests.test_workflow import approve_review, finish_verified, implement

        with AndroidProject() as root:
            run(root, {"id": "V-9", "title": "Change ProfileScreen button", "type": "chore"})
            implement(root)
            approve_review(root)
            with self.assertRaises(ValueError) as raised:
                finish_verified(root, keep_device="only a test changed")
            (run_dir(root) / "device-report.md").write_text(
                "# Status\n\nPASS\n\n# Device\n\nPixel 8\n\n# Scenarios\n\n" + "- AC1: ok.\n" * 12
                + "\n# Evidence\n\n- media/after/p.png\n\n# Not verified\n\n- None.\n", encoding="utf-8")
            after = run_dir(root) / "media/after"
            after.mkdir(parents=True)
            (after / "p.png").write_bytes(b"png")
            main(["log", "--target", str(root), "--stage", "T7", "--status", "completed"])
            implement(root, "fun empty() = 3\n")  # the PR feedback round: device verdict now stale
            approve_review(root)
            with self.assertRaises(ValueError):
                finish_verified(root)
            state = finish_verified(root, keep_device="renamed a private function")
            log = (run_dir(root) / "stage-log.md").read_text()
        self.assertIn("--keep-device needs an earlier device PASS", str(raised.exception))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["stages"]["T7"]["kept"], "renamed a private function")
        self.assertIn("earlier PASS kept: renamed a private function", log)


class AccountTests(unittest.TestCase):
    def test_the_pr_authors_account_is_used_even_when_another_is_active(self) -> None:
        tokens = {"work": "t-work", "personal": "t-personal"}

        def fake_gh(args, env=None, **kwargs):
            if args[:2] == ["auth", "status"]:
                return subprocess.CompletedProcess(args, 0, "account personal (keyring)\naccount work (keyring)\n", "")
            if args[:2] == ["auth", "token"]:
                return subprocess.CompletedProcess(args, 0, tokens[args[-1]] + "\n", "")
            return subprocess.CompletedProcess(args, 0, "o/r\n", "")  # both accounts can see the repository

        with AndroidProject() as root:
            finished_run(root)
            with mock.patch.object(watch, "_remote", return_value=("github.com", "o/r")), \
                    mock.patch.object(watch, "_gh", side_effect=fake_gh):
                as_author = watch.github_env(root, prefer="work")
                fallback = watch.github_env(root, prefer="someone-not-logged-in")
        self.assertEqual(as_author, {"GH_TOKEN": "t-work"})
        self.assertEqual(fallback, {"GH_TOKEN": "t-work"})  # remembered for this repository

    def test_two_word_roles_bind_live_tokens(self) -> None:
        from android_workflow.usage import _marker

        self.assertEqual(_marker("RUN=/x AW_STAGE=PR Watcher\n"), ("T10", None))
        self.assertEqual(_marker("AW_STAGE=Tech Lead "), ("T4L", None))
        self.assertEqual(_marker("AW_STAGE=Implementer/S2 "), ("T4", "S2"))
