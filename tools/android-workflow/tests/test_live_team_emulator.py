"""Live office (clocks, file changes, watcher), the engineering team and the emulator helper."""

from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"

import argparse
import fcntl
import json
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import emulator, live, office, team
from android_workflow.cli import main, read_json, run
from android_workflow.paths import cache_dir, run_dir, workflow_root


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


class GitProject:
    """A small Android-shaped app in its own git repository (outside any other repository)."""

    def __enter__(self) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        (root / "settings.gradle.kts").write_text('include(":app", ":core:domain")\n', encoding="utf-8")
        (root / "app/src/main/java/com/example").mkdir(parents=True)
        (root / "app/build.gradle.kts").write_text('plugins { id("com.android.application") }\n', encoding="utf-8")
        (root / "core/domain").mkdir(parents=True)
        (root / "core/domain/build.gradle.kts").write_text("", encoding="utf-8")
        (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass ProfileScreen\n", encoding="utf-8")
        (root / "app/src/main/java/com/example/Old.kt").write_text("package com.example\nclass Old\n", encoding="utf-8")
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        return root

    def __exit__(self, *_: object) -> None:
        self.temp.cleanup()


def start(root: Path, ticket: str = "L-1") -> None:
    run(root, {"id": ticket, "title": "Change ProfileScreen", "type": "chore"})


def metrics(root: Path) -> dict:
    return read_json(run_dir(root) / "stage-metrics.json")


def state(root: Path) -> dict:
    return read_json(run_dir(root) / "run-state.json")


def log(root: Path, *args: str) -> int:
    return main(["log", "--target", str(root), *args])


class LiveClockTests(unittest.TestCase):
    def test_started_opens_a_clock_and_completed_measures_from_it(self) -> None:
        with GitProject() as root:
            start(root)
            log(root, "--stage", "Reviewer", "--status", "started", "--note", "reading")
            opened = metrics(root)["stages"]["T6"]
            data = office.collect(root)
            reviewer = {desk["role"]: desk for desk in data["desks"]}["Reviewer"]
            entry = metrics(root)["stages"]["T6"]
            entry["running_since"] -= 42  # the review took 42 seconds
            path = run_dir(root) / "stage-metrics.json"
            path.write_text(json.dumps({**metrics(root), "stages": {**metrics(root)["stages"], "T6": entry}}), encoding="utf-8")
            (run_dir(root) / "review.json").write_text(json.dumps(
                {"schema_version": 1, "status": "approved", "blocking": [], "concerns": [], "suggestions": []}), encoding="utf-8")
            log(root, "--stage", "Reviewer", "--status", "completed", "--note", "approved", "--tokens", "10")
            closed = metrics(root)["stages"]["T6"]
            after = {desk["role"]: desk for desk in office.collect(root)["desks"]}["Reviewer"]
        self.assertIsInstance(opened["running_since"], float)
        self.assertIsNone(opened["wall_time_seconds"])
        self.assertEqual(reviewer["state"], "working")
        self.assertEqual(reviewer["running_since"], opened["running_since"])
        self.assertNotIn("running_since", closed)
        self.assertGreaterEqual(closed["wall_time_seconds"], 42)
        self.assertLess(closed["wall_time_seconds"], 60)
        self.assertIsNone(after["running_since"])
        self.assertEqual(after["state"], "done")

    def test_a_second_round_adds_to_the_first(self) -> None:
        with GitProject() as root:
            start(root)
            log(root, "--stage", "Implementer", "--status", "completed", "--seconds", "30")
            log(root, "--stage", "Implementer", "--status", "started")
            log(root, "--stage", "Implementer", "--status", "completed", "--tokens", "5")
            entry = metrics(root)["stages"]["T4"]
        self.assertGreaterEqual(entry["wall_time_seconds"], 30)
        self.assertLess(entry["wall_time_seconds"], 40)
        self.assertNotIn("running_since", entry)

    def test_the_gate_shows_it_is_running_and_never_stays_running_after_a_crash(self) -> None:
        from android_workflow import cli

        with GitProject() as root:
            start(root)
            seen = {}

            def fake_gate(target: Path) -> dict:
                seen["state"] = state(target)["stages"]["T5"]["status"]
                seen["since"] = metrics(target)["stages"]["T5"].get("running_since")
                seen["page"] = office.collect(target)
                raise RuntimeError("gradle died")

            with mock.patch.object(cli, "run_quality_gate", fake_gate):
                with self.assertRaises(RuntimeError):
                    main(["gate", "--target", str(root)])
            after = state(root)["stages"]["T5"]
            log_text = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
            gate_after = metrics(root)["stages"]["T5"]
        self.assertEqual(seen["state"], "running")
        self.assertIsInstance(seen["since"], float)
        gate_desk = {desk["role"]: desk for desk in seen["page"]["desks"]}["Quality gate"]
        self.assertEqual(gate_desk["state"], "working")
        self.assertEqual(after["status"], "failed")
        self.assertNotIn("running_since", gate_after)
        self.assertIn("| Quality gate | started | cli | running the checks |", log_text)
        self.assertIn("| Quality gate | failed | cli | the gate did not finish |", log_text)

    def test_a_clock_left_open_by_a_crashed_round_starts_over(self) -> None:
        with GitProject() as root:
            start(root)
            log(root, "--stage", "Device", "--status", "started")
            path = run_dir(root) / "stage-metrics.json"
            value = metrics(root)
            value["stages"]["T7"]["running_since"] -= 3600
            path.write_text(json.dumps(value), encoding="utf-8")
            log(root, "--stage", "Device", "--status", "started")  # still open: the same stretch continues
            kept = metrics(root)["stages"]["T7"]["running_since"]
            spath = run_dir(root) / "run-state.json"
            st = state(root)
            st["stages"]["T7"]["status"] = "failed"  # the round died; a new one starts
            spath.write_text(json.dumps(st), encoding="utf-8")
            log(root, "--stage", "Device", "--status", "started")
            fresh = metrics(root)["stages"]["T7"]["running_since"]
        self.assertLess(kept, time.time() - 3000)
        self.assertGreater(fresh, time.time() - 60)

    def test_a_gate_killed_by_a_signal_is_not_shown_running(self) -> None:
        with GitProject() as root:
            start(root)
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            st = state(root)
            st["stages"]["T5"] = {"status": "running", "pid": dead.pid}
            (run_dir(root) / "run-state.json").write_text(json.dumps(st), encoding="utf-8")
            gate = {desk["role"]: desk for desk in office.collect(root)["desks"]}["Quality gate"]
            st["stages"]["T5"]["pid"] = os.getpid()
            (run_dir(root) / "run-state.json").write_text(json.dumps(st), encoding="utf-8")
            alive = {desk["role"]: desk for desk in office.collect(root)["desks"]}["Quality gate"]
        self.assertEqual(gate["state"], "failed")
        self.assertEqual(alive["state"], "working")

    def test_run_files_are_written_atomically(self) -> None:
        from android_workflow.cli import write_json

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.json"
            stop = threading.Event()
            bad = []

            def reader() -> None:
                while not stop.is_set():
                    try:
                        text = path.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    if text:
                        try:
                            json.loads(text)
                        except ValueError:
                            bad.append(text)

            thread = threading.Thread(target=reader)
            thread.start()
            for i in range(300):
                write_json(path, {"i": i, "pad": "x" * 5000})
            stop.set()
            thread.join()
            leftovers = [item.name for item in Path(folder).iterdir() if item.name != "x.json"]
        self.assertEqual(bad, [])
        self.assertEqual(leftovers, [])


class LiveChangesTests(unittest.TestCase):
    def test_changes_list_modified_new_and_deleted_files_newest_first(self) -> None:
        with GitProject() as root:
            start(root)
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text(screen.read_text(encoding="utf-8") + "fun a() = 1\nfun b() = 2\n", encoding="utf-8")
            os.utime(screen, (time.time() - 100, time.time() - 100))
            (root / "app/src/main/java/com/example/Old.kt").unlink()
            fresh = root / "core/domain/src/main/java/com/example/New.kt"
            fresh.parent.mkdir(parents=True)
            fresh.write_text("package com.example\nclass New\n", encoding="utf-8")
            changes = live.live_changes(root, full=True)
        by_path = {item["path"]: item for item in changes}
        self.assertEqual(by_path["app/src/main/java/com/example/ProfileScreen.kt"]["status"], "M")
        self.assertEqual((by_path["app/src/main/java/com/example/ProfileScreen.kt"]["added"],
                          by_path["app/src/main/java/com/example/ProfileScreen.kt"]["removed"]), (2, 0))
        self.assertEqual(by_path["app/src/main/java/com/example/Old.kt"]["status"], "D")
        self.assertIsNone(by_path["app/src/main/java/com/example/Old.kt"]["mtime"])
        self.assertEqual(by_path["core/domain/src/main/java/com/example/New.kt"]["status"], "??")
        self.assertEqual(by_path["core/domain/src/main/java/com/example/New.kt"]["added"], 2)
        self.assertEqual(changes[0]["path"], "core/domain/src/main/java/com/example/New.kt")  # newest edit first
        self.assertFalse(any(".ai" in item["path"].split("/") for item in changes))

    def test_new_files_are_found_cheaply_where_the_change_works_and_everywhere_now_and_then(self) -> None:
        with GitProject() as root:
            start(root)
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass P\n", encoding="utf-8")
            near = root / "app/src/main/java/com/example/EmptyState.kt"
            near.write_text("package com.example\nobject EmptyState\n", encoding="utf-8")
            far = root / "core/domain/src/main/java/com/example/Far.kt"
            far.parent.mkdir(parents=True)
            far.write_text("package com.example\n", encoding="utf-8")
            scoped = {item["path"] for item in live.live_changes(root)}
            live.live_changes(root, full=True)  # the watcher's occasional whole-repository scan
            after_full = {item["path"] for item in live.live_changes(root)}
            far.unlink()
            gone = {item["path"] for item in live.live_changes(root)}
        self.assertIn("app/src/main/java/com/example/EmptyState.kt", scoped)
        self.assertNotIn("core/domain/src/main/java/com/example/Far.kt", scoped)
        self.assertIn("core/domain/src/main/java/com/example/Far.kt", after_full)
        self.assertNotIn("core/domain/src/main/java/com/example/Far.kt", gone)

    def test_git_is_read_without_taking_the_index_lock(self) -> None:
        calls = []
        real = subprocess.run

        def spy(command, *args, **kwargs):
            calls.append(command)
            return real(command, *args, **kwargs)

        with GitProject() as root:
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("x\n", encoding="utf-8")
            with mock.patch.object(live.subprocess, "run", spy):
                live.live_changes(root)
                live.live_changes(root, full=True)
        git_calls = [command for command in calls if command[0] == "git"]
        self.assertTrue(git_calls)
        self.assertTrue(all("--no-optional-locks" in command for command in git_calls))

    def test_outside_a_git_repository_there_are_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(live.live_changes(Path(folder)), [])

    def test_the_page_gets_the_changes_and_a_data_script_while_the_run_is_live(self) -> None:
        with GitProject() as root:
            start(root)
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass P\n", encoding="utf-8")
            main(["office", "--target", str(root), "--no-open"])
            script = (workflow_root(root) / office.DATA_NAME).read_text(encoding="utf-8")
            html = (workflow_root(root) / office.OFFICE_NAME).read_text(encoding="utf-8")
            own_page = (run_dir(root) / office.OFFICE_NAME).read_text(encoding="utf-8")
        self.assertTrue(script.startswith("window.officeData && window.officeData({"))
        data = json.loads(re.search(r"window\.officeData\((.*)\);\n$", script, re.S).group(1))
        embedded = json.loads(re.search(r"let DATA = (.*);\n", html).group(1))
        own = json.loads(re.search(r"let DATA = (.*);\n", own_page).group(1))
        self.assertTrue(data["live"])
        self.assertEqual(data["live_src"], "office-data.js")
        self.assertEqual(embedded["live_src"], "office-data.js")
        self.assertIsNone(own["live_src"])  # a run's own page is a snapshot: it falls back to reloading
        self.assertEqual([item["path"] for item in data["changes"]], ["app/src/main/java/com/example/ProfileScreen.kt"])
        self.assertIn("mtime", data["artifacts"]["Planner"][0])
        self.assertIsNotNone(data["created_at"])
        self.assertFalse(data["totals_final"])

    def test_after_finish_the_run_stays_live_only_while_delivery_works(self) -> None:
        with GitProject() as root:
            start(root)
            path = run_dir(root) / "run-state.json"
            value = state(root)
            value["status"] = "completed"
            path.write_text(json.dumps(value), encoding="utf-8")
            done = office.run_is_live(run_dir(root))
            log(root, "--stage", "Delivery", "--status", "started", "--note", "opening the PR")
            delivering = office.run_is_live(run_dir(root))
            log(root, "--stage", "Delivery", "--status", "completed", "--tokens", "3")
            delivered = office.run_is_live(run_dir(root))
        self.assertFalse(done)
        self.assertTrue(delivering)
        self.assertFalse(delivered)


class WatcherTests(unittest.TestCase):
    def test_tests_and_opted_out_hosts_never_start_a_watcher(self) -> None:
        with GitProject() as root:
            start(root)
            with mock.patch.object(live.subprocess, "Popen") as popen:
                self.assertIsNone(live.ensure_watcher(root))
        popen.assert_not_called()

    def test_no_watcher_for_a_finished_run(self) -> None:
        with GitProject() as root:
            start(root)
            path = run_dir(root) / "run-state.json"
            path.write_text(json.dumps({**state(root), "status": "completed"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {live.DISABLE_ENV: ""}), mock.patch.object(live.subprocess, "Popen") as popen:
                self.assertIsNone(live.ensure_watcher(root))
        popen.assert_not_called()

    def test_watch_writes_the_data_script_and_stops_when_the_run_ends(self) -> None:
        with GitProject() as root:
            start(root)
            data_path = workflow_root(root) / office.DATA_NAME
            thread = threading.Thread(target=live.watch, args=(str(root), 0.05), daemon=True)
            thread.start()
            deadline = time.time() + 10
            while time.time() < deadline and not data_path.exists():
                time.sleep(0.05)
            changes = lambda: json.loads(re.search(r"officeData\((.*)\);\n$", data_path.read_text(encoding="utf-8"), re.S).group(1))["changes"]
            first = changes()
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass Q\n", encoding="utf-8")
            while time.time() < deadline and not changes():
                time.sleep(0.05)
            second = changes()
            record = json.loads((cache_dir(root) / "office-watch.json").read_text(encoding="utf-8"))
            path = run_dir(root) / "run-state.json"
            path.write_text(json.dumps({**state(root), "status": "completed"}), encoding="utf-8")
            thread.join(10)
            alive = thread.is_alive()
            record_left = (cache_dir(root) / "office-watch.json").exists()
        self.assertEqual(first, [])
        self.assertEqual([item["path"] for item in second], ["app/src/main/java/com/example/ProfileScreen.kt"])
        self.assertEqual(record["pid"], os.getpid())
        self.assertFalse(alive)
        self.assertFalse(record_left)

    def test_only_one_watcher_per_app(self) -> None:
        with GitProject() as root:
            start(root)
            lock = cache_dir(root) / "office-watch.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            with lock.open("a+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                began = time.time()
                live.watch(str(root), 0.05)  # another watcher holds the lock: returns at once
                took = time.time() - began
            wrote = (workflow_root(root) / office.DATA_NAME).exists()
        self.assertLess(took, 2)
        self.assertFalse(wrote)

    def test_watch_stops_when_clean_removes_the_folder(self) -> None:
        with GitProject() as root:
            start(root)
            thread = threading.Thread(target=live.watch, args=(str(root), 0.05), daemon=True)
            thread.start()
            time.sleep(0.4)
            main(["clean", "--target", str(root)])
            thread.join(10)
            alive = thread.is_alive()
        self.assertFalse(alive)


PLAN = {
    "schema_version": 1,
    "reason": "data and UI are independent",
    "contracts": ["ProfileRepository.load(): List<Profile>"],
    "slices": [
        {"id": "S1", "goal": "UI", "files": ["app/src/main/java/com/example/ProfileScreen.kt"]},
        {"id": "S2", "goal": "Data", "files": ["core/domain/src/main/java/com/example/Repo.kt"]},
    ],
}


def write_plan(root: Path, plan: dict) -> None:
    (run_dir(root) / "team-plan.json").write_text(json.dumps(plan), encoding="utf-8")


def cli_json(*args: str) -> tuple[dict, int]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args))
    return json.loads(out.getvalue()), code


class TeamPlanTests(unittest.TestCase):
    def test_a_valid_plan_is_ready_with_one_desk_per_slice(self) -> None:
        with GitProject() as root:
            start(root)
            write_plan(root, PLAN)
            result, code = cli_json("team", "--target", str(root))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([item["role"] for item in result["slices"]], ["Implementer", "Implementer 2"])

    def test_one_slice_means_one_implementer(self) -> None:
        with GitProject() as root:
            start(root)
            write_plan(root, {**PLAN, "slices": PLAN["slices"][:1]})
            result, code = cli_json("team", "--target", str(root))
        self.assertEqual((result["status"], code), ("single", 0))

    def test_invalid_plans_are_refused_with_the_reason(self) -> None:
        cases = {
            "one owner": [{"id": "S1", "goal": "a", "files": ["a/A.kt"]}, {"id": "S2", "goal": "b", "files": ["./a/A.kt"]}],
            "id 'S2'": [{"id": "S1", "goal": "a", "files": ["a/A.kt"]}, {"id": "S3", "goal": "b", "files": ["b/B.kt"]}],
            "at most 3": [{"id": f"S{i}", "goal": "x", "files": [f"m{i}/X.kt"]} for i in range(1, 5)],
            "relative to the app": [{"id": "S1", "goal": "a", "files": ["/etc/passwd"]}],
            "inside the app": [{"id": "S1", "goal": "a", "files": ["../other/A.kt"]}],
            "workflow files": [{"id": "S1", "goal": "a", "files": [".ai/workflow/x.kt"]}],
            "`goal`": [{"id": "S1", "files": ["a/A.kt"]}],
            "`files`": [{"id": "S1", "goal": "a", "files": []}],
        }
        with GitProject() as root:
            start(root)
            for expected, slices in cases.items():
                write_plan(root, {"schema_version": 1, "slices": slices})
                result, code = cli_json("team", "--target", str(root))
                self.assertEqual((result["status"], code), ("invalid", 1), expected)
                self.assertTrue(any(expected in error for error in result["errors"]), (expected, result["errors"]))
            (run_dir(root) / "team-plan.json").unlink()
            result, code = cli_json("team", "--target", str(root))
        self.assertEqual((result["status"], code), ("invalid", 1))

    def test_slice_ids_and_roles(self) -> None:
        self.assertEqual(team.normalize_slice("2"), "S2")
        self.assertEqual(team.normalize_slice("s3"), "S3")
        self.assertEqual(team.slice_role("S1"), "Implementer")
        self.assertEqual(team.slice_role("S3"), "Implementer 3")
        with self.assertRaises(ValueError):
            team.normalize_slice("S0")
        with self.assertRaises(ValueError):
            team.normalize_slice("lead")


class TeamRunTests(unittest.TestCase):
    def test_parallel_slices_keep_their_own_time_and_tokens_and_the_tech_lead_hands_off_to_the_gate(self) -> None:
        with GitProject() as root:
            start(root)
            log(root, "--stage", "Tech Lead", "--status", "started", "--note", "splitting")
            write_plan(root, PLAN)
            log(root, "--stage", "Tech Lead", "--status", "completed", "--tokens", "100")
            split_done = state(root)["current_stage"]
            log(root, "--stage", "Implementer", "--slice", "S1", "--status", "started")
            log(root, "--stage", "Implementer", "--slice", "S2", "--status", "started")
            log(root, "--stage", "Implementer", "--slice", "S1", "--status", "completed", "--tokens", "40",
                "--file", "app/src/main/java/com/example/ProfileScreen.kt")
            one_left = state(root)["stages"]["T4"]["status"]
            log(root, "--stage", "Implementer", "--slice", "S2", "--status", "completed", "--tokens", "60",
                "--file", "core/domain/src/main/java/com/example/Repo.kt")
            both_done = (state(root)["stages"]["T4"]["status"], state(root)["current_stage"])
            files = read_json(run_dir(root) / "t4-files.json")["files"]
            log(root, "--stage", "Tech Lead", "--status", "started", "--note", "integrating")
            log(root, "--stage", "Tech Lead", "--status", "completed", "--tokens", "50")
            final = state(root)
            stage = metrics(root)["stages"]
            desks = {desk["role"]: desk for desk in office.collect(root)["desks"]}
            log_text = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
        self.assertEqual(split_done, "T4")  # splitting is not the end of the implementation
        self.assertEqual(one_left, "started")
        self.assertEqual(both_done, ("integrating", "T4"))
        self.assertEqual(files, ["app/src/main/java/com/example/ProfileScreen.kt", "core/domain/src/main/java/com/example/Repo.kt"])
        self.assertEqual((final["stages"]["T4"]["status"], final["current_stage"], final["status"]), ("completed", "T5", "running"))
        self.assertEqual(stage["T4"]["tokens"], 100)
        self.assertEqual(stage["T4"]["slices"]["S1"]["tokens"], 40)
        self.assertEqual(stage["T4"]["slices"]["S2"]["attempts"], 1)
        self.assertEqual(stage["T4"]["attempts"], 1)  # one parallel window
        self.assertNotIn("running_since", stage["T4"])
        self.assertEqual(stage["T4L"]["tokens"], 150)
        self.assertEqual(set(desks) >= {"Tech Lead", "Implementer", "Implementer 2"}, True)
        self.assertNotIn("Implementer 3", desks)
        self.assertEqual(desks["Implementer 2"]["state"], "done")
        self.assertIn("| Implementer 2 | completed |", log_text)

    def test_parallel_logs_never_lose_an_update(self) -> None:
        tools = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(tools), "ANDROID_WORKFLOW_NO_WATCH": "1"}
        with GitProject() as root:
            start(root)
            write_plan(root, {**PLAN, "slices": PLAN["slices"] + [{"id": "S3", "goal": "x", "files": ["app/X.kt"]}]})
            for round_ in range(3):
                procs = [subprocess.Popen([sys.executable, "-m", "android_workflow.cli", "log", "--target", str(root),
                                           "--stage", "Implementer", "--slice", f"S{n}", "--status", "completed",
                                           "--tokens", "10", "--file", f"m{n}/F{round_}.kt"],
                                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for n in (1, 2, 3)]
                for proc in procs:
                    proc.wait(60)
            slices = state(root)["stages"]["T4"]["slices"]
            stage = metrics(root)["stages"]["T4"]
            files = read_json(run_dir(root) / "t4-files.json")["files"]
        self.assertEqual({name: item["status"] for name, item in slices.items()}, {"S1": "completed", "S2": "completed", "S3": "completed"})
        self.assertEqual(stage["tokens"], 90)
        self.assertEqual({name: item["attempts"] for name, item in stage["slices"].items()}, {"S1": 3, "S2": 3, "S3": 3})
        self.assertEqual(len(files), 9)

    def test_a_fix_round_after_integration_goes_back_to_the_gate(self) -> None:
        with GitProject() as root:
            start(root)
            write_plan(root, PLAN)
            for slice_id in ("S1", "S2"):
                log(root, "--stage", "Implementer", "--slice", slice_id, "--status", "completed", "--tokens", "1")
            log(root, "--stage", "Tech Lead", "--status", "completed", "--tokens", "1")
            value = state(root)
            value.update({"status": "escalated", "current_stage": "T5"})  # the gate failed in S2's file
            (run_dir(root) / "run-state.json").write_text(json.dumps(value), encoding="utf-8")
            log(root, "--stage", "Implementer", "--slice", "S2", "--status", "started", "--note", "fix G1")
            log(root, "--stage", "Implementer", "--slice", "S2", "--status", "completed", "--tokens", "1")
            after = state(root)
        self.assertEqual((after["stages"]["T4"]["status"], after["status"], after["current_stage"]), ("completed", "running", "T5"))

    def test_the_developers_earlier_work_is_never_called_unowned(self) -> None:
        with GitProject() as root:
            mine = root / "app/src/main/java/com/example/Old.kt"
            mine.write_text("package com.example\nclass Old // my work in progress\n", encoding="utf-8")
            (root / "notes.txt").write_text("scratch\n", encoding="utf-8")
            start(root)
            write_plan(root, PLAN)
            cli_json("team", "--target", str(root))  # accepting the plan records what was already changed
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass A\n", encoding="utf-8")
            report, _ = cli_json("team", "--target", str(root), "--check")
        self.assertEqual(report["preexisting"], ["app/src/main/java/com/example/Old.kt", "notes.txt"])
        self.assertEqual(report["unowned"], [])

    def test_slices_are_only_for_the_implementer(self) -> None:
        with GitProject() as root:
            start(root)
            code = log(root, "--stage", "Reviewer", "--slice", "S2", "--status", "started")
        self.assertEqual(code, 2)

    def test_no_team_desks_without_a_team(self) -> None:
        with GitProject() as root:
            start(root)
            roles = [desk["role"] for desk in office.collect(root)["desks"]]
        self.assertNotIn("Tech Lead", roles)
        self.assertNotIn("Implementer 2", roles)
        self.assertIn("Implementer", roles)

    def test_team_check_reports_who_touched_what(self) -> None:
        with GitProject() as root:
            start(root)
            write_plan(root, PLAN)
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass A\n", encoding="utf-8")
            repo = root / "core/domain/src/main/java/com/example/Repo.kt"
            repo.parent.mkdir(parents=True)
            repo.write_text("package com.example\nclass Repo\n", encoding="utf-8")
            (root / "app/src/main/java/com/example/Stray.kt").write_text("package com.example\n", encoding="utf-8")
            log(root, "--stage", "Implementer", "--slice", "S1", "--status", "completed", "--tokens", "1",
                "--file", "app/src/main/java/com/example/ProfileScreen.kt", "--file", "core/domain/src/main/java/com/example/Repo.kt")
            report, code = cli_json("team", "--target", str(root), "--check")
            saved = read_json(run_dir(root) / "team-report.json")
        self.assertEqual(code, 0)
        self.assertEqual(report, saved)
        self.assertEqual(report["status"], "slices_pending")
        self.assertEqual(report["pending"], ["S2"])
        self.assertEqual(report["unowned"], ["app/src/main/java/com/example/Stray.kt"])
        self.assertEqual(report["out_of_slice"], {"S1": ["core/domain/src/main/java/com/example/Repo.kt"]})
        self.assertEqual({item["id"]: item["changed"] for item in report["slices"]},
                         {"S1": ["app/src/main/java/com/example/ProfileScreen.kt"], "S2": ["core/domain/src/main/java/com/example/Repo.kt"]})


FAKE_EMULATOR = r'''#!{python}
import json, os, sys, time
state = os.environ["FAKE_SDK_STATE"]
avds = os.environ.get("FAKE_AVDS", "")
if sys.argv[1:] == ["-list-avds"]:
    print("INFO    | Storing crashdata in: /tmp/x")
    for name in filter(None, avds.split(",")):
        print(name)
    raise SystemExit(0)
args = sys.argv[1:]
port = args[args.index("-port") + 1]
with open(os.path.join(state, "launch.json"), "w") as handle:
    json.dump(args, handle)
with open(os.path.join(state, "devices"), "a") as handle:
    handle.write(f"emulator-{port}\tdevice\n")
while not os.path.exists(os.path.join(state, "kill")):
    time.sleep(0.05)
open(os.path.join(state, "devices"), "w").close()
'''

FAKE_ADB = r'''#!{python}
import os, sys
state = os.environ["FAKE_SDK_STATE"]
args = sys.argv[1:]
if args == ["devices"]:
    print("List of devices attached")
    path = os.path.join(state, "devices")
    if os.path.exists(path):
        print(open(path).read(), end="")
    raise SystemExit(0)
if args[:1] == ["-s"] and args[2:] == ["shell", "getprop", "sys.boot_completed"]:
    print("1" if os.path.exists(os.path.join(state, "booted")) else "")
    raise SystemExit(0)
if args[:1] == ["-s"] and args[2:] == ["emu", "kill"]:
    open(os.path.join(state, "kill"), "w").close()
    raise SystemExit(0)
raise SystemExit(1)
'''


class FakeSdk:
    def __init__(self, avds: str = "Pixel_9,Small_Phone", emulator_tool: bool = True) -> None:
        self.avds, self.emulator_tool = avds, emulator_tool

    def __enter__(self) -> tuple[Path, Path]:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        sdk, home, target, self.state = base / "sdk", base / "home", base / "app", base / "state"
        for folder in (sdk / "platform-tools", sdk / "emulator", home, target, self.state):
            folder.mkdir(parents=True)
        tools = [("platform-tools/adb", FAKE_ADB)] + ([("emulator/emulator", FAKE_EMULATOR)] if self.emulator_tool else [])
        for name, body in tools:
            path = sdk / name
            path.write_text(body.replace("{python}", sys.executable), encoding="utf-8")
            path.chmod(0o755)
        self.env = mock.patch.dict(os.environ, {
            "ANDROID_HOME": str(sdk), "ANDROID_SDK_ROOT": "", "HOME": str(home), "PATH": "/usr/bin:/bin",
            "FAKE_SDK_STATE": str(self.state), "FAKE_AVDS": self.avds,
        })
        self.env.start()
        return target, self.state

    def __exit__(self, *_: object) -> None:
        (self.state / "kill").touch()  # never leave a fake emulator behind
        time.sleep(0.2)
        self.env.stop()
        self.temp.cleanup()


def emulator_args(**values: object) -> argparse.Namespace:
    base = {"stop": False, "status": False, "wait": False, "avd": None, "headless": False, "timeout": None}
    return argparse.Namespace(**{**base, **values})


class EmulatorTests(unittest.TestCase):
    def test_a_connected_device_is_used_and_never_closed(self) -> None:
        with FakeSdk() as (target, state_dir):
            (state_dir / "devices").write_text("R5CT123\tdevice\n", encoding="utf-8")
            started, code = emulator.dispatch_emulator(target, emulator_args())
            stopped = emulator.stop(target)
            launched = (state_dir / "launch.json").exists()
        self.assertEqual((started["status"], code, started["serial"]), ("connected", 0, "R5CT123"))
        self.assertFalse(started["started_by_workflow"])
        self.assertFalse(launched)
        self.assertEqual(stopped["status"], "left_running")

    def test_no_device_starts_the_first_avd_waits_for_boot_and_stops_only_its_own(self) -> None:
        with FakeSdk() as (target, state_dir):
            booting, code = emulator.dispatch_emulator(target, emulator_args())
            deadline = time.time() + 10
            while time.time() < deadline and not (state_dir / "launch.json").exists():
                time.sleep(0.05)
            args = json.loads((state_dir / "launch.json").read_text(encoding="utf-8"))
            not_yet = emulator.status(target)["status"]
            (state_dir / "booted").touch()
            ready = emulator.wait(target, timeout=10, poll=0.05)
            again, _ = emulator.dispatch_emulator(target, emulator_args())
            stopped = emulator.stop(target, grace=10)
            record_left = emulator._record_path(target).exists()
            alive = emulator._pid_alive(booting["pid"])
        self.assertEqual((booting["status"], code), ("booting", 0))
        self.assertTrue(booting["started_by_workflow"])
        self.assertEqual(booting["avd"], "Pixel_9")
        self.assertEqual(booting["serial"], "emulator-5554")
        self.assertEqual(args[:4], ["-avd", "Pixel_9", "-port", "5554"])
        self.assertIn("-no-snapshot-save", args)
        self.assertNotIn("-no-window", args)
        self.assertEqual(not_yet, "booting")
        self.assertEqual((ready["status"], ready["serial"]), ("ready", "emulator-5554"))
        self.assertEqual(again["status"], "ready")  # a second call never starts another one
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(record_left)
        self.assertFalse(alive)

    def test_the_configured_avd_and_headless_mode_win(self) -> None:
        with FakeSdk() as (target, state_dir):
            (target / ".ai").mkdir()
            (target / ".ai/android-workflow.json").write_text(
                json.dumps({"device": {"avd": "Small_Phone", "emulator_headless": True}}), encoding="utf-8")
            started = emulator.start(target)
            deadline = time.time() + 10
            while time.time() < deadline and not (state_dir / "launch.json").exists():
                time.sleep(0.05)
            args = json.loads((state_dir / "launch.json").read_text(encoding="utf-8"))
            emulator.stop(target, grace=10)
        self.assertEqual(started["avd"], "Small_Phone")
        self.assertIn("-no-window", args)

    def test_without_an_avd_or_the_emulator_it_says_why_and_starts_nothing(self) -> None:
        with FakeSdk(avds="") as (target, state_dir):
            no_avd, code = emulator.dispatch_emulator(target, emulator_args())
        with FakeSdk(emulator_tool=False) as (target, state_dir):
            no_tool = emulator.start(target)
        self.assertEqual((no_avd["status"], code), ("unavailable", 1))
        self.assertIn("no Android Virtual Device", no_avd["reason"])
        self.assertEqual(no_tool["status"], "unavailable")
        self.assertIn("emulator was not found", no_tool["reason"])

    def test_a_device_that_is_not_ready_is_waited_for_never_doubled(self) -> None:
        with FakeSdk() as (target, state_dir):
            (state_dir / "devices").write_text("emulator-5554\toffline\n", encoding="utf-8")
            result = emulator.start(target)
            launched = (state_dir / "launch.json").exists()
            timed_out = emulator.wait(target, timeout=0.3, poll=0.05)
        self.assertEqual(result["status"], "booting")
        self.assertFalse(result["started_by_workflow"])
        self.assertFalse(launched)
        self.assertEqual(timed_out["status"], "timed_out")

    def test_two_runs_share_the_emulator_and_the_last_one_closes_it(self) -> None:
        with FakeSdk() as (first, state_dir):
            second = first.parent / "other-app"
            second.mkdir()
            for app, ticket in ((first, "A-1"), (second, "B-1")):
                (app / "settings.gradle.kts").write_text("", encoding="utf-8")
                start(app, ticket)
            booting = emulator.start(first)
            deadline = time.time() + 10
            while time.time() < deadline and not (state_dir / "launch.json").exists():
                time.sleep(0.05)
            (state_dir / "booted").touch()
            joined = emulator.start(second)
            first_done = emulator.stop(first)
            still = emulator._pid_alive(booting["pid"])
            last = emulator.stop(second, grace=10)
            gone = emulator._pid_alive(booting["pid"])
            launches = 1
        self.assertEqual(joined["status"], "ready")
        self.assertEqual(set(joined["users"]), {str(first), str(second)})
        self.assertEqual(first_done["status"], "left_running")
        self.assertTrue(still)
        self.assertEqual(last["status"], "stopped")
        self.assertFalse(gone)
        self.assertEqual(launches, 1)

    def test_an_unauthorized_phone_is_reported_at_once(self) -> None:
        with FakeSdk() as (target, state_dir):
            (state_dir / "devices").write_text("R5CT123\tunauthorized\n", encoding="utf-8")
            began = time.time()
            result, code = emulator.dispatch_emulator(target, emulator_args(wait=True))
            took = time.time() - began
            launched = (state_dir / "launch.json").exists()
        self.assertEqual((result["status"], code), ("unauthorized", 1))
        self.assertIn("USB debugging", result["reason"])
        self.assertLess(took, 5)
        self.assertFalse(launched)

    def test_an_emulator_that_dies_while_booting_is_reported(self) -> None:
        with FakeSdk() as (target, state_dir):
            started = emulator.start(target)
            deadline = time.time() + 10
            while time.time() < deadline and not (state_dir / "launch.json").exists():
                time.sleep(0.05)
            (state_dir / "kill").touch()
            result = emulator.wait(target, timeout=10, poll=0.05)
        self.assertEqual(started["status"], "booting")
        self.assertEqual(result["status"], "failed")
        self.assertIn("exited while booting", result["reason"])


if __name__ == "__main__":
    unittest.main()
