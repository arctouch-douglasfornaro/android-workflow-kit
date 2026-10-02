from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from android_workflow.cli import finish_run, main, read_json, run
from android_workflow.delivery import deliver, pull_request_disclosures, stage_plan
from android_workflow.paths import run_dir
from android_workflow.prebuild import current_status, start_prebuild, wait_prebuild
from tests.test_workflow import AndroidProject, approve_review, fake_gradle_project, implement


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def with_remote(root: Path, remote: Path, branch: str = "user/D-1-change") -> str:
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    base = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", base)
    git(root, "fetch", "-q", "origin")
    git(root, "remote", "set-head", "origin", base)
    git(root, "checkout", "-q", "-b", branch)
    return base


BODY = "### What changed\n\nAdds the empty state helper.\n"


def finished_run(
    root: Path, ticket: str = "D-1", body: str = BODY, skip_device: str | None = None, extra: bool = False,
) -> None:
    fake_gradle_project(root, {})
    with_remote_path = root.parent / f"{root.name}-remote.git"
    with_remote(root, with_remote_path)
    run(root, {"id": ticket, "title": "Change ProfileScreen", "type": "chore"})
    implement(root)
    if extra:
        (root / "app/src/main/java/com/example/New.kt").write_text("class New\n", encoding="utf-8")
    assert main(["gate", "--target", str(root)]) == 0
    assert approve_review(root) == 0
    finish_run(root, skip_device=skip_device)
    (run_dir(root) / "pr-description.md").write_text(body, encoding="utf-8")


class DeliverTests(unittest.TestCase):
    def test_commits_pushes_and_never_ships_workflow_files(self) -> None:
        with AndroidProject() as root:
            finished_run(root, extra=True)
            (root / "local.properties").write_text("sdk.dir=/x\n", encoding="utf-8")
            (root / "notes.txt").write_text("scratch\n", encoding="utf-8")
            result = deliver(root, subject="D-1: Add the empty state helper", no_pr=True)
            remote = root.parent / f"{root.name}-remote.git"
            pushed = subprocess.run(
                ["git", "-C", str(remote), "log", "-1", "--format=%s", "user/D-1-change"],
                text=True, capture_output=True, check=True,
            ).stdout.strip()
            committed = git(root, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertTrue(result["committed"] and result["pushed"])
        self.assertEqual(pushed, "D-1: Add the empty state helper")
        self.assertIn("app/src/main/java/com/example/ProfileScreen.kt", committed)
        self.assertIn("app/src/main/java/com/example/New.kt", committed)
        self.assertNotIn("local.properties", committed)
        self.assertFalse(any(name.startswith(".ai/") for name in committed))
        self.assertFalse(any("notes.txt" in name for name in committed))
        self.assertTrue(any("notes.txt" in warning for warning in result["warnings"]))

    def test_unfinished_run_is_refused(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            run(root, {"id": "D-2", "title": "Change ProfileScreen", "type": "chore"})
            with self.assertRaises(ValueError) as raised:
                deliver(root)
        self.assertIn("run `finish` first", str(raised.exception))

    def test_edit_after_finish_is_refused(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text(screen.read_text(encoding="utf-8") + "fun another() = 1\n", encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                deliver(root, no_pr=True)
        self.assertIn("source changed after `finish`", str(raised.exception))

    def test_base_branch_is_never_delivered(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            base = git(root, "rev-parse", "--abbrev-ref", "origin/HEAD").split("/", 1)[1]
            git(root, "checkout", "-q", base)
            with self.assertRaises(ValueError) as raised:
                deliver(root, no_pr=True)
        self.assertIn("base branch", str(raised.exception))

    def test_stub_pr_body_is_refused(self) -> None:
        with AndroidProject() as root:
            finished_run(root, body="")
            with self.assertRaises(ValueError) as raised:
                deliver(root, no_pr=True)
        self.assertIn("pr-description.md", str(raised.exception))

    def test_tool_attribution_is_stripped_from_commit_and_body(self) -> None:
        body = BODY + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
        with AndroidProject() as root:
            finished_run(root, body=body)
            deliver(
                root, subject="D-1: Add helper\n\nCo-Authored-By: Claude <noreply@anthropic.com>", no_pr=True,
            )
            message = git(root, "log", "-1", "--format=%B")
            text = (run_dir(root) / "pr-description.md").read_text(encoding="utf-8")
        self.assertNotIn("Claude", message)
        self.assertNotIn("Claude", text)
        self.assertIn("empty state helper", text)

    def test_no_push_stops_after_the_commit(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            result = deliver(root, no_push=True)
            remote = root.parent / f"{root.name}-remote.git"
            refs = subprocess.run(["git", "-C", str(remote), "branch", "--list"], text=True, capture_output=True).stdout
        self.assertTrue(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertNotIn("user/D-1-change", refs)

    def test_rerun_after_a_partial_delivery_reuses_the_commit(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            first = deliver(root, no_push=True)
            second = deliver(root, no_pr=True)
        self.assertEqual(first["sha"], second["sha"])
        self.assertFalse(second["committed"])
        self.assertTrue(second["pushed"])

    def test_other_remote_hosts_get_a_compare_url_instead_of_a_pr(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            git(root, "remote", "set-url", "origin", str(root.parent / f"{root.name}-remote.git"))
            result = deliver(root)
        self.assertEqual(result["pr"]["status"], "manual")

    def test_disclosures_cover_waived_lint_and_skipped_device(self) -> None:
        gate = {"waivers": [{"count": 4, "file_count": 2}, {"count": 3, "file_count": 1}]}
        state = {"stages": {"T7": {"status": "skipped", "reason": "no emulator"}}}
        lines = pull_request_disclosures(gate, state, BODY)
        self.assertEqual(len(lines), 2)
        self.assertIn("7 finding(s) in 3 file(s)", lines[0])
        self.assertIn("no emulator", lines[1])
        self.assertEqual(pull_request_disclosures(gate, state, BODY + "Lint: pre-existing, untouched. Device: none."), [])

    def test_draft_lists_what_is_not_ready(self) -> None:
        state = {"draft": {"reason": "review still blocking", "issues": ["review.json is not approved"]}}
        lines = pull_request_disclosures({}, state, BODY)
        self.assertIn("**Not ready for review:** review still blocking.", lines)
        self.assertIn("- review.json is not approved", lines)
        self.assertEqual(pull_request_disclosures({}, {"draft": None}, BODY), [])

    def test_draft_run_opens_a_draft_pull_request(self) -> None:
        from unittest import mock

        from android_workflow.delivery import open_pull_request

        with AndroidProject() as root:
            bin_dir = root / "fakebin"
            bin_dir.mkdir()
            calls = root / "gh-calls.txt"
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                f"echo \"$@\" >> {calls}\n"
                "[ \"$2\" = view ] && exit 1\n"
                "echo https://github.com/o/r/pull/7\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            body = root / "body.md"
            body.write_text(BODY, encoding="utf-8")
            with mock.patch.dict("os.environ", {"PATH": f"{bin_dir}:/usr/bin:/bin"}):
                draft = open_pull_request(root, "github", "git@github.com:o/r.git", "b", "main", "S", body, 30, draft=True)
                ready = open_pull_request(root, "github", "git@github.com:o/r.git", "b", "main", "S", body, 30)
            created = [line for line in calls.read_text(encoding="utf-8").splitlines() if line.startswith("pr create")]
        self.assertEqual((draft["status"], draft["draft"]), ("created", True))
        self.assertFalse(ready["draft"])
        self.assertTrue(created[0].endswith("--draft"))
        self.assertFalse(created[1].endswith("--draft"))

    def test_waived_lint_is_disclosed_in_the_body_that_ships(self) -> None:
        with AndroidProject() as root:
            finished_run(root)
            report = read_json(run_dir(root) / "gate-report.json")
            report["waivers"] = [{"command": "x", "count": 5, "file_count": 2}]
            (run_dir(root) / "gate-report.json").write_text(json.dumps(report), encoding="utf-8")
            result = deliver(root, no_push=True)
            text = (run_dir(root) / "pr-description.md").read_text(encoding="utf-8")
        self.assertEqual(len(result["disclosures_added"]), 1)
        self.assertIn("5 finding(s) in 2 file(s)", text)

    def test_stage_plan_excludes_generated_and_secret_files(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            (root / "app/build/outputs").mkdir(parents=True)
            (root / "app/build/outputs/app.apk").write_text("x", encoding="utf-8")
            (root / "release.jks").write_text("x", encoding="utf-8")
            (root / "core/domain/lint-baseline.xml").write_text("<issues/>", encoding="utf-8")
            (root / "app/src/main/java/com/example/Added.kt").write_text("class Added\n", encoding="utf-8")
            include, left_out = stage_plan(root)
        self.assertEqual(include, ["app/src/main/java/com/example/Added.kt"])
        self.assertEqual(left_out, [])


class PrebuildTests(unittest.TestCase):
    def prepared(self, root: Path, command: str, surfaces: str = "ui") -> None:
        fake_gradle_project(root, {})
        (root / ".ai/android-workflow.json").write_text(
            json.dumps({"commands": {"build": command}}), encoding="utf-8")
        run(root, {"id": "P-1", "title": "Change the button", "type": "chore", "surfaces": [surfaces]})

    def test_builds_the_base_in_the_background_and_records_the_apk(self) -> None:
        with AndroidProject() as root:
            script = root / "build.sh"
            script.write_text(
                "#!/bin/sh\nsleep 1\nmkdir -p app/build/outputs/apk/debug\n"
                "echo apk > app/build/outputs/apk/debug/app-debug.apk\n", encoding="utf-8")
            script.chmod(0o755)
            self.prepared(root, str(script))
            started = start_prebuild(root)
            self.assertEqual(started["status"], "running")
            finished = wait_prebuild(root, timeout=60, poll=0.2)
        self.assertEqual(finished["status"], "passed")
        self.assertTrue(finished["apks"][0]["path"].endswith("app-debug.apk"))

    def test_source_edited_during_the_build_taints_it(self) -> None:
        with AndroidProject() as root:
            script = root / "build.sh"
            script.write_text("#!/bin/sh\nsleep 1\n", encoding="utf-8")
            script.chmod(0o755)
            self.prepared(root, str(script))
            start_prebuild(root)
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("class Edited\n", encoding="utf-8")
            finished = wait_prebuild(root, timeout=60, poll=0.2)
        self.assertEqual(finished["status"], "tainted")

    def test_a_failing_build_is_reported(self) -> None:
        with AndroidProject() as root:
            self.prepared(root, "/usr/bin/false")
            start_prebuild(root)
            finished = wait_prebuild(root, timeout=60, poll=0.2)
        self.assertEqual(finished["status"], "failed")

    def test_skipped_when_the_route_has_no_device_stage(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            run(root, {"id": "P-2", "title": "Rename a function", "type": "chore"})
            result = start_prebuild(root)
        self.assertEqual(result["status"], "skipped")

    def test_skipped_once_the_source_has_changed(self) -> None:
        with AndroidProject() as root:
            self.prepared(root, "/usr/bin/true")
            implement(root)
            result = start_prebuild(root)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("source already changed", result["reason"])

    def test_dead_worker_is_not_left_running(self) -> None:
        with AndroidProject() as root:
            self.prepared(root, "/usr/bin/true")
            from android_workflow.prebuild import write_record

            write_record(root, {"status": "running", "pid": 999999, "log": "x"})
            status = current_status(root)
        self.assertEqual(status["status"], "failed")

    def test_cli_status_reports_not_started(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {})
            run(root, {"id": "P-3", "title": "Change the button", "type": "chore"})
            self.assertEqual(main(["prebuild", "--target", str(root), "--status"]), 0)
            time.sleep(0)


if __name__ == "__main__":
    unittest.main()
