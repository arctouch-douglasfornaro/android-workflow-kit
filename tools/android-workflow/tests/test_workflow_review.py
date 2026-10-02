"""Improvements from the workflow review: a faster gate, device checks, cheaper reviews, cleaner logs."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"

import contextlib
import io
import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import cli, office
from android_workflow.cli import gradle_invocation, gradle_task_args, main, read_json, run
from android_workflow.paths import run_dir
from test_live_team_emulator import GitProject
from test_workflow import AndroidProject, approve_review, fake_gradle_project, gradle_calls, pass_gate


def quiet(*args: str) -> tuple[dict, int]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args))
    return json.loads(out.getvalue() or "{}"), code


def notes(root: Path) -> None:
    (run_dir(root) / "implementation-notes.md").write_text(
        "# Decisions\n\nDone.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n", encoding="utf-8")


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = AndroidProject()
        self.root = self.project.__enter__()
        root = self.root
        domain = root / "core/domain/src/main/java/com/example"
        domain.mkdir(parents=True)
        (domain / "Steps.kt").write_text("package com.example\nclass Steps\n", encoding="utf-8")
        tests = root / "app/src/test/java/com/example"
        (tests / "StepsTest.kt").write_text("package com.example\nclass StepsTest { @Test fun t() { Steps() } }\n", encoding="utf-8")
        (tests / "FakeSteps.kt").write_text("package com.example\nclass FakeSteps { val s = Steps() }\n", encoding="utf-8")
        (tests / "OtherTest.kt").write_text("package com.example\nclass OtherTest\n", encoding="utf-8")
        fake_gradle_project(root, {})
        run(root, {"id": "G-1", "title": "Change Steps", "type": "chore"})

    def tearDown(self) -> None:
        self.project.__exit__(None, None, None)

    def gate(self) -> tuple[dict, list[str]]:
        notes(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["gate", "--target", str(self.root)])
        return read_json(run_dir(self.root) / "gate-report.json"), gradle_calls(self.root)

    def test_a_consumer_runs_only_the_tests_that_use_the_changed_declaration(self) -> None:
        (self.root / "core/domain/src/main/java/com/example/Steps.kt").write_text(
            "package com.example\nclass Steps { fun count() = 1 }\n", encoding="utf-8")
        report, calls = self.gate()
        batch = next(call for call in calls if "testDebugUnitTest" in call or ":app:" in call)
        self.assertIn(":app", report["consumer_modules"])
        self.assertIn(":app:testDebugUnitTest --tests com.example.StepsTest", batch)
        self.assertNotIn("OtherTest", batch)
        self.assertNotIn("FakeSteps", batch)  # a fake is not a test class
        self.assertIn("gate-test-filters.init.gradle", batch)  # a stale filter never fails the gate
        self.assertEqual(report["scope"]["consumer_tests"][":app"], ["com.example.StepsTest"])

    def test_every_consumer_test_runs_when_the_project_asks_for_it(self) -> None:
        overrides = self.root / ".ai/android-workflow.json"
        overrides.write_text(json.dumps({**read_json(overrides), "quality_gates": {"consumer_tests": "all"}}), encoding="utf-8")
        import shutil

        shutil.rmtree(self.root / ".ai/workflow/_cache")  # the project config is cached per commit
        with contextlib.redirect_stdout(io.StringIO()):
            run(self.root, {"id": "G-1", "title": "Change Steps", "type": "chore"})
        (self.root / "core/domain/src/main/java/com/example/Steps.kt").write_text(
            "package com.example\nclass Steps { fun count() = 1 }\n", encoding="utf-8")
        _, calls = self.gate()
        batch = next(call for call in calls if ":app:" in call)
        self.assertIn(":app:testDebugUnitTest", batch)
        self.assertNotIn("--tests", batch)

    def test_a_later_round_keeps_the_task_list_of_earlier_rounds(self) -> None:
        steps = self.root / "core/domain/src/main/java/com/example/Steps.kt"
        steps.write_text("package com.example\nclass Steps { fun count() = 1 }\n", encoding="utf-8")
        self.gate()
        first = gradle_calls(self.root)[-1]
        steps.write_text("package com.example\nclass Steps\n", encoding="utf-8")  # the fix moved elsewhere
        (self.root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(
            "package com.example\nclass ProfileScreen { fun x() = 2 }\n", encoding="utf-8")
        report, calls = self.gate()
        second = calls[-1]
        for task in [part for part in first.split() if part.startswith(":")]:
            self.assertIn(task, second)  # same tasks, same configuration cache entry
        self.assertIn(":core:domain", report["scope"]["modules"])


class GradleArgsTests(unittest.TestCase):
    def test_a_test_filter_stays_with_its_task(self) -> None:
        parsed = gradle_task_args("./gradlew :app:testDebugUnitTest --tests com.a.FooTest --tests com.a.BarTest :app:lintDebug")
        self.assertEqual(parsed, ("./gradlew", [(":app:testDebugUnitTest", ["--tests", "com.a.FooTest", "--tests", "com.a.BarTest"]),
                                                (":app:lintDebug", [])]))
        self.assertEqual(gradle_invocation("./gradlew :a:test --tests X"), ("./gradlew", [":a:test"]))
        self.assertIsNone(gradle_invocation("./gradlew :a:test --offline"))
        self.assertIsNone(gradle_invocation("./gradlew --tests X"))


class DeviceCheckTests(unittest.TestCase):
    def finish_project(self, root: Path) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            main(["start", "--target", str(root), "--id", "D-1", "--title", "Change ProfileScreen", "--type", "feature",
                  "--surfaces", "ui"])
        (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass P\n", encoding="utf-8")
        notes(root)
        main(["log", "--target", str(root), "--stage", "Implementer", "--status", "completed", "--tokens", "1"])
        approve_review(root)
        pass_gate(root)

    def test_skipping_the_device_is_refused_while_one_is_connected(self) -> None:
        with GitProject() as root:
            self.finish_project(root)
            with mock.patch.object(cli, "connected_device", return_value="emulator-5554"):
                _, refused = quiet("finish", "--target", str(root), "--skip-device", "no device")
                state_after_refusal = read_json(run_dir(root) / "run-state.json")["status"]
                _, allowed = quiet("finish", "--target", str(root), "--skip-device", "developer passed --no-device", "--no-device")
        self.assertEqual(refused, 2)
        self.assertNotEqual(state_after_refusal, "completed")
        self.assertEqual(allowed, 0)

    def test_no_device_alone_skips_the_device(self) -> None:
        with GitProject() as root:
            self.finish_project(root)
            with mock.patch.object(cli, "connected_device", return_value="emulator-5554"):
                result, code = quiet("finish", "--target", str(root), "--no-device")
        self.assertEqual(code, 0)
        self.assertEqual(result["stages"]["Device"]["status"], "skipped")

    def test_with_no_device_connected_the_skip_is_accepted(self) -> None:
        with GitProject() as root:
            self.finish_project(root)
            with mock.patch.object(cli, "connected_device", return_value=None):
                _, code = quiet("finish", "--target", str(root), "--skip-device", "no device")
        self.assertEqual(code, 0)

    def test_a_draft_may_still_skip_a_device_it_could_not_use(self) -> None:
        with GitProject() as root:
            self.finish_project(root)
            with mock.patch.object(cli, "connected_device", return_value="emulator-5554"):
                _, code = quiet("finish", "--target", str(root), "--skip-device", "login wall", "--draft", "device blocked by login")
        self.assertEqual(code, 0)

    def test_the_implementer_waits_only_for_a_base_build_already_running(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "P-1", "title": "Change ProfileScreen", "type": "chore"})
            result, code = quiet("prebuild", "--target", str(root), "--wait", "--if-running")
            started = (run_dir(root) / "prebuild.json").exists()
        self.assertEqual(code, 0)
        self.assertTrue(result["may_edit"])
        self.assertFalse(started)  # it never starts a build


class LogTests(unittest.TestCase):
    def test_the_same_round_logged_twice_counts_once(self) -> None:
        with GitProject() as root:
            run(root, {"id": "L-9", "title": "Change ProfileScreen", "type": "chore"})
            for args in (["--status", "started"], ["--status", "completed", "--note", "approved"],
                         ["--status", "completed", "--note", "approved, 0 blocking", "--tokens", "500"]):
                main(["log", "--target", str(root), "--stage", "Reviewer", *args])
            once = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T6"]
            main(["log", "--target", str(root), "--stage", "Reviewer", "--status", "completed", "--tokens", "500"])
            repeated = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T6"]
            main(["log", "--target", str(root), "--stage", "Reviewer", "--status", "started"])
            main(["log", "--target", str(root), "--stage", "Reviewer", "--status", "completed", "--tokens", "200"])
            two = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T6"]
        self.assertEqual((once["attempts"], once["tokens"]), (1, 500))
        self.assertEqual((repeated["attempts"], repeated["tokens"]), (1, 500))
        self.assertEqual((two["attempts"], two["tokens"]), (2, 700))

    def test_a_second_round_after_other_work_is_not_merged(self) -> None:
        with GitProject() as root:
            run(root, {"id": "L-8", "title": "Change ProfileScreen", "type": "chore"})
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "completed", "--tokens", "10"])
            main(["log", "--target", str(root), "--stage", "Quality gate", "--status", "completed", "--note", "failed"])
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "completed", "--tokens", "7"])
            entry = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T4"]
        self.assertEqual((entry["attempts"], entry["tokens"]), (2, 17))

    def test_a_restart_keeps_the_plan_the_planner_wrote(self) -> None:
        with GitProject() as root:
            run(root, {"id": "R-2", "title": "Change ProfileScreen", "type": "feature", "complexity": "medium"})
            main(["update-spec", "--target", str(root), "--surfaces", "ui", "--complexity", "medium"])
            plan = run_dir(root) / "plan.md"
            plan.write_text("# Objective\n\nThe Planner's plan.\n", encoding="utf-8")
            run(root, {"id": "R-2", "title": "Change ProfileScreen", "type": "feature"})
            kept = plan.read_text(encoding="utf-8")
        self.assertIn("The Planner's plan.", kept)

    def test_a_new_start_keeps_the_planner_decisions_and_the_earlier_metrics(self) -> None:
        with GitProject() as root:
            run(root, {"id": "R-1", "title": "Change ProfileScreen", "type": "feature"})
            main(["update-spec", "--target", str(root), "--surfaces", "ui,navigation", "--acceptance", "shows a|hides b",
                  "--complexity", "medium"])
            planned = read_json(run_dir(root) / "ticket-spec.json")
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "completed", "--tokens", "42"])
            run(root, {"id": "R-1", "title": "Change ProfileScreen", "type": "feature"})
            again = read_json(run_dir(root) / "ticket-spec.json")
            metrics = read_json(run_dir(root) / "stage-metrics.json")
        self.assertEqual(again["surfaces"], ["ui", "navigation"])
        self.assertEqual(again["acceptance_criteria"], ["shows a", "hides b"])
        self.assertEqual(again["complexity"], "medium")
        self.assertEqual(again["route"], planned["route"])
        self.assertIsNone(metrics["stages"]["T4"]["tokens"])  # this run starts at zero…
        self.assertEqual(metrics["earlier_runs"][-1]["stages"]["T4"]["tokens"], 42)  # …the earlier one is kept

    def test_a_changed_toolkit_is_reported_when_an_agent_starts_not_at_the_gate(self) -> None:
        with GitProject() as root:
            run(root, {"id": "T-1", "title": "Change ProfileScreen", "type": "chore"})
            path = run_dir(root) / cli.TOOLKIT_FINGERPRINT_FILE
            recorded = read_json(path)
            first = sorted(recorded["files"])[0]
            recorded["files"][first] = "0" * 64
            path.write_text(json.dumps(recorded), encoding="utf-8")
            result, _ = quiet("log", "--target", str(root), "--stage", "Implementer", "--status", "started")
        self.assertTrue(any("toolkit changed" in warning for warning in result.get("warnings", [])))

    def test_a_review_records_what_it_saw_without_touching_the_index(self) -> None:
        with GitProject() as root:
            run(root, {"id": "V-1", "title": "Change ProfileScreen", "type": "chore"})
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen { val a = 1 }\n", encoding="utf-8")
            (root / "app/src/main/java/com/example/New.kt").write_text("package com.example\nclass New\n", encoding="utf-8")
            status_before = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True).stdout
            approve_review(root)
            tree = read_json(run_dir(root) / "run-state.json")["stages"]["T6"]["reviewed_tree"]
            status_after = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True).stdout
            nothing_new, _ = quiet("delta", "--target", str(root))
            screen.write_text("package com.example\nclass ProfileScreen { val a = 2 }\n", encoding="utf-8")
            (root / "app/src/main/java/com/example/Newer.kt").write_text("package com.example\nclass Newer\n", encoding="utf-8")
            delta, _ = quiet("delta", "--target", str(root))
            patch = Path(delta["diff"]).read_text(encoding="utf-8")
        self.assertEqual(status_before, status_after)
        self.assertTrue(tree)
        self.assertEqual(nothing_new["files"], [])
        self.assertEqual(delta["files"], ["app/src/main/java/com/example/Newer.kt", "app/src/main/java/com/example/ProfileScreen.kt"])
        self.assertIn("+class ProfileScreen { val a = 2 }", patch)
        self.assertNotIn("New.kt", patch)  # already reviewed


class OfficePageTests(unittest.TestCase):
    def test_a_working_agents_card_is_always_highlighted_and_diffs_open_per_file(self) -> None:
        page = office.PAGE
        self.assertIn(".filebtn.busy", page)
        self.assertIn('marks.busy ? " busy" : ""', page)  # in the card's own HTML: a refresh never drops it
        self.assertIn("function diffView", page)
        self.assertIn("OPEN_DIFFS", page)  # an open diff stays open when the page updates

    def test_the_toolkit_folder_is_never_a_target(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".ai/tools/android-workflow/android_workflow").mkdir(parents=True)
            (home / ".ai/tools/android-workflow/android_workflow/cli.py").write_text("", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _, code = quiet("office", "--target", str(home), "--no-open")
            written = (home / ".ai" / "workflow").exists()
        self.assertEqual(code, 2)
        self.assertIn("is not an Android app", err.getvalue())
        self.assertFalse(written)


class RunPageTests(unittest.TestCase):
    def test_a_live_runs_own_page_sends_the_reader_to_the_live_page(self) -> None:
        import re

        def page_data(path: Path) -> dict:
            return json.loads(re.search(r"let DATA = (.*);\n", path.read_text(encoding="utf-8")).group(1))

        with GitProject() as root:
            run(root, {"id": "P-1", "title": "Change ProfileScreen", "type": "chore"})
            main(["office", "--target", str(root), "--no-open"])
            live_page = page_data(run_dir(root, "P-1") / "office.html")
            state = run_dir(root, "P-1") / "run-state.json"
            state.write_text(json.dumps({**read_json(state), "status": "completed"}), encoding="utf-8")
            run(root, {"id": "P-2", "title": "Another change", "type": "chore"})
            main(["office", "--target", str(root), "--no-open"])
            past_page = page_data(run_dir(root, "P-1") / "office.html")
        self.assertEqual(live_page["redirect"], "../office.html")
        self.assertNotIn("redirect", past_page)  # once another run is current it is P-1's own snapshot again
        self.assertEqual(past_page["ticket"], "P-1")


class ReopenedRunTests(unittest.TestCase):
    def test_the_office_follows_a_run_reopened_after_finish(self) -> None:
        with GitProject() as root:
            run(root, {"id": "O-9", "title": "Change ProfileScreen", "type": "chore"})
            path = run_dir(root) / "run-state.json"
            path.write_text(json.dumps({**read_json(path), "status": "completed"}), encoding="utf-8")
            finished = office.run_is_live(run_dir(root))
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "started", "--note", "one more change"])
            reopened = office.run_is_live(run_dir(root))
            metrics_path = run_dir(root) / "stage-metrics.json"
            metrics = read_json(metrics_path)
            metrics["stages"]["T4"]["running_since"] = time.time() - 5 * 3600
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            abandoned = office.run_is_live(run_dir(root))
        self.assertFalse(finished)
        self.assertTrue(reopened)
        self.assertFalse(abandoned)


if __name__ == "__main__":
    unittest.main()
