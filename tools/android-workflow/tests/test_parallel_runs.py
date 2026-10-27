"""Runs side by side on one machine, the base build, the device proof, the partial verdict and the PR checklist."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import decide, locks
from android_workflow.cli import read_json, source_fingerprint
from android_workflow.paths import cache_dir
from android_workflow.delivery import normalize_checklist
from android_workflow.evidence import archive_stale_after, load_manifest, record_item
from android_workflow.flows import evidence_names, recipe, scaffold
from android_workflow.paths import STATE_HOME_ENV, run_dir
import test_decide
from test_decide import cli



def own_tests(cls: type) -> type:
    """A test_decide subclass for its harness only: the inherited tests run in test_decide, not again here."""
    for name in dir(cls.__mro__[1]):
        if name.startswith("test_") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


class StateHome:
    """A fresh machine-wide state folder: no slot or lease from another test leaks in."""

    def __enter__(self) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {STATE_HOME_ENV: self.temp.name})
        self.env.start()
        return Path(self.temp.name)

    def __exit__(self, *_: object) -> None:
        self.env.stop()
        self.temp.cleanup()


class BuildSlotTests(unittest.TestCase):
    def test_a_build_waits_while_every_slot_is_taken(self) -> None:
        with StateHome(), mock.patch.dict(os.environ, {locks.SLOTS_ENV: "1"}):
            order: list[str] = []
            waited: list[list] = []
            first_in = threading.Event()

            def first() -> None:
                with locks.build_slot("first"):
                    first_in.set()
                    time.sleep(0.6)
                    order.append("first done")

            thread = threading.Thread(target=first)
            thread.start()
            first_in.wait(5)
            with locks.build_slot("second", on_wait=waited.append, poll=0.05) as slot:
                order.append("second in")
            thread.join()
        self.assertEqual(order, ["first done", "second in"])
        self.assertEqual(waited[0][0]["label"], "first")
        self.assertGreater(slot["waited_seconds"], 0.2)

    def test_two_slots_run_two_builds_at_once(self) -> None:
        with StateHome(), mock.patch.dict(os.environ, {locks.SLOTS_ENV: "2"}):
            with locks.build_slot("a") as one, locks.build_slot("b", poll=0.05) as two:
                holders = {item["label"] for item in locks.slot_holders()}
        self.assertEqual({one["slot"], two["slot"]}, {0, 1})
        self.assertEqual(holders, {"a", "b"})


class DeviceLeaseTests(unittest.TestCase):
    def run_folder(self, base: Path, name: str, device_started: bool) -> Path:
        folder = base / name
        folder.mkdir()
        (folder / "run-state.json").write_text(json.dumps(
            {"stages": {"T7": {"status": "started" if device_started else "completed"}}}), encoding="utf-8")
        return folder

    def test_a_device_another_run_is_on_is_not_free(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", True), self.run_folder(home, "B-1", True)
            self.assertIsNotNone(locks.acquire("emu-1", a))
            self.assertIsNone(locks.acquire("emu-1", b))
            self.assertEqual(locks.free_serials(["emu-1", "emu-2"], b), ["emu-2"])
            self.assertEqual(locks.free_serials(["emu-1"], a), ["emu-1"])  # its own lease

    def test_the_lease_lasts_until_the_run_finishes(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", True), self.run_folder(home, "B-1", True)
            locks.acquire("emu-1", a)
            (a / "run-state.json").write_text(json.dumps({"stages": {"T7": {"status": "completed"}}}), encoding="utf-8")
            self.assertEqual(locks.free_serials(["emu-1"], b), [])  # between rounds it is still A's device
            (a / "run-state.json").write_text(json.dumps({"status": "completed", "stages": {}}), encoding="utf-8")
            self.assertEqual(locks.free_serials(["emu-1"], b), ["emu-1"])

    def test_a_run_left_idle_lets_its_device_go(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", True), self.run_folder(home, "B-1", True)
            locks.acquire("emu-1", a)
            old = time.time() - locks.LEASE_IDLE_SECONDS - 60
            (a / "run-state.json").write_text(json.dumps({"updated_at": old, "stages": {}}), encoding="utf-8")
            with locks._leases() as live:
                live["emu-1"]["since"] = old
            self.assertEqual(locks.free_serials(["emu-1"], b), ["emu-1"])

    def test_a_command_still_on_the_device_keeps_it_after_the_run_lets_go(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", True), self.run_folder(home, "B-1", True)
            locks.acquire("emu-1", a)
            locks.acquire("emu-1", a, pid=os.getpid())  # an install still running
            self.assertEqual(locks.release(a), [])  # the stage ended under it: the device is not given back yet
            (a / "run-state.json").write_text(json.dumps({"status": "completed", "stages": {}}), encoding="utf-8")
            self.assertIsNone(locks.acquire("emu-1", b))
            locks.release(a, serial="emu-1", pid=os.getpid())  # the install exits
            self.assertIsNotNone(locks.acquire("emu-1", b))

    def test_a_command_lease_dies_with_its_process(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", False), self.run_folder(home, "B-1", True)
            locks.acquire("emu-1", a, mode="command", pid=999999)
            self.assertEqual(locks.free_serials(["emu-1"], b), ["emu-1"])

    def test_a_replay_waits_for_the_other_run_and_gives_up_after_its_timeout(self) -> None:
        with StateHome() as home:
            a, b = self.run_folder(home, "A-1", True), self.run_folder(home, "B-1", True)
            locks.acquire("emu-1", a)
            told: list = []
            with locks.device_lease("emu-1", b, on_wait=told.append, poll=0.05, timeout=0.2) as held:
                self.assertFalse(held)
            self.assertEqual(told[0]["run"], str(a))


@own_tests
class DecideDeviceTests(test_decide.RunTests):
    """`next` with another run on the device, the late before and the partial verdict."""

    def connected(self, serial: str = "emu-1"):
        return mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": serial,
                                                                             "devices": [serial]})

    def test_another_runs_device_makes_this_run_wait_for_it(self) -> None:
        with StateHome() as home:
            other = home / "OTHER-1"
            other.mkdir()
            (other / "run-state.json").write_text(json.dumps({"stages": {"T7": {"status": "started"}}}), encoding="utf-8")
            locks.acquire("emu-1", other)
            self.visual_run()
            with self.connected():
                answer = self.next()
        self.assertEqual(answer["next"], "wait_device")
        self.assertIn("OTHER-1", answer["why"])
        self.assertEqual(answer["command"], "CLI emulator --target TARGET --wait-free")

    def test_a_booting_emulator_is_waited_for_before_it_is_leased(self) -> None:
        with StateHome():
            self.visual_run()
            with mock.patch("android_workflow.emulator.status", return_value={"status": "booting", "serial": "emu-1"}):
                answer = self.next()
                lease = locks.lease_of("emu-1")
        self.assertEqual((answer["next"], answer["command"]), ("wait_device", "CLI emulator --target TARGET --wait"))
        self.assertIsNone(lease)

    def test_a_returned_implementer_whose_completed_was_never_logged_is_named(self) -> None:
        with StateHome():
            self.start("--level", "express")
            test_decide.cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "ui")
            self.assertEqual(self.next()["agent"], "aw-implementer")
            waiting = self.next()
            (self.root / test_decide.SCREEN).write_text("package com.example\nclass ProfileScreen { val a = 1 }\n",
                                                        encoding="utf-8")
            (self.rundir / "implementation-notes.md").write_text("# Decisions\n\nDone.\n", encoding="utf-8")
            stuck = self.next()
        self.assertNotIn("if_returned", waiting)
        self.assertEqual(stuck["next"], "wait")
        self.assertIn("--stage Implementer --status completed", stuck["if_returned"])

    def test_the_base_build_starts_only_for_a_change_with_something_to_see(self) -> None:
        with StateHome():
            ui = self.start("--level", "standard", "--surfaces", "ui")
            visual = self.next()
        self.assertIn("T7", ui["route"])
        self.assertIn("CLI prebuild --target TARGET", visual["also"])
        self.tearDown()
        self.setUp()
        with StateHome():
            audio = self.start("--level", "standard", "--surfaces", "audio")
            silent = self.next()
        self.assertIn("T7", audio["route"])
        self.assertIn("CLI emulator --target TARGET", silent["also"])
        self.assertNotIn("CLI prebuild --target TARGET", silent["also"])

    def test_the_device_spawn_leases_the_device_to_this_run(self) -> None:
        with StateHome():
            self.visual_run()
            with self.connected():
                answer = self.next()
                lease = locks.lease_of("emu-1")
        self.assertEqual((answer["agent"], answer["serial"]), ("aw-device", "emu-1"))
        self.assertEqual(lease["run"], str(self.rundir))
        self.assertEqual(answer["scaffold"], "CLI device scaffold --target TARGET")

    def test_a_pass_without_replayed_flows_is_refused(self) -> None:
        with StateHome():
            self.visual_run()
            with self.connected():
                self.next()
                self.device_report("PASS", replayed=False)
        state = read_json(self.rundir / "run-state.json")
        self.assertNotEqual(state["stages"]["T7"].get("verdict"), "pass")

    def test_maestro_unavailable_with_a_reason_lets_a_pass_through(self) -> None:
        with StateHome():
            self.visual_run()
            with self.connected():
                self.next()
                (self.rundir / "device-report.md").write_text(
                    "# Status\n\nPASS\n\n# Device\n\nemulator\n\nMaestro: unavailable — the driver crashes on API 37\n"
                    + "x" * 420, encoding="utf-8")
                cli("log", "--target", self.target, "--stage", "Device", "--status", "completed", "--tokens", "1")
        self.assertEqual(read_json(self.rundir / "run-state.json")["stages"]["T7"]["verdict"], "pass")

    def test_the_late_before_replays_the_flows_on_the_base_apk(self) -> None:
        with StateHome():
            self.visual_run()
            flows = self.rundir / "media/flows"
            flows.mkdir(parents=True)
            (flows / "screen.yaml").write_text("- takeScreenshot: screen\n", encoding="utf-8")
            passed = {"status": "passed", "apks": [{"path": "/x/base.apk"}]}
            with self.connected(), mock.patch("android_workflow.prebuild.current_status", return_value=passed):
                self.next()
                self.device_report("PASS")
                (self.rundir / "media/after/screen.png").write_bytes(b"png")
                answer = self.next()
                (self.rundir / "replay-before.json").write_text("{}", encoding="utf-8")
                done = self.next()
        self.assertEqual(answer["next"], "replay_before")
        self.assertIn("--phase before --install-base", answer["command"])
        self.assertEqual(done["next"], "finish")

    def test_a_late_before_is_tried_once_when_the_base_build_failed(self) -> None:
        with StateHome():
            self.visual_run()
            flows = self.rundir / "media/flows"
            flows.mkdir(parents=True)
            (flows / "screen.yaml").write_text("- takeScreenshot: screen\n", encoding="utf-8")
            with self.connected(), mock.patch("android_workflow.prebuild.current_status", return_value={"status": "failed"}):
                self.next()
                self.device_report("PASS")
                (self.rundir / "media/after/screen.png").write_bytes(b"png")
                answer = self.next()
        self.assertEqual(answer["next"], "finish")
        self.assertIn("late: skipped", read_json(self.rundir / "run-state.json")["device_before"])

    def test_anomalies_make_a_pass_partial_and_the_developer_decides(self) -> None:
        with StateHome():
            self.visual_run()
            with self.connected():
                self.next()
                (self.rundir / "device-report.md").write_text(
                    "# Status\n\nPASS\n\n# Device\n\nemulator\n\n# Anomalies\n\n- a swipe after 1/1 showed 1/10\n"
                    + "x" * 420, encoding="utf-8")
                (self.rundir / "replay.json").write_text(json.dumps({
                    "fingerprint": source_fingerprint(self.root), "flows": [{"status": "passed"}]}), encoding="utf-8")
                cli("log", "--target", self.target, "--stage", "Device", "--status", "completed", "--tokens", "1")
                (self.rundir / "media/after/screen.png").write_bytes(b"png")
                answer = self.next()
            refused = cli("finish", "--target", self.target)
            accepted = cli("finish", "--target", self.target, "--accept-partial", "the 1/10 swipe")
        self.assertEqual(answer["next"], "ask_user")
        self.assertEqual(set(answer["options"]), {"ready", "draft", "again"})
        self.assertNotEqual(refused.get("status"), "completed")
        self.assertEqual(accepted["status"], "completed")
        self.assertEqual(accepted["device_partial"], "the 1/10 swipe")

    def test_none_under_anomalies_is_still_a_pass(self) -> None:
        with StateHome():
            self.visual_run()
            with self.connected():
                self.next()
                (self.rundir / "device-report.md").write_text(
                    "# Status\n\nPASS (all AC)\n\n# Device\n\n" + "x" * 420 + "\n\n# Anomalies\n\n- None.\n", encoding="utf-8")
        from android_workflow.cli import recorded_verdict

        self.assertEqual(recorded_verdict(self.rundir, "T7"), "pass")


@own_tests
class MediaRoundTests(test_decide.RunTests):
    def test_a_new_source_moves_the_earlier_after_captures_out_of_the_pr(self) -> None:
        self.visual_run()
        after = self.rundir / "media/after"
        archive_stale_after(self.root, "fp-1")
        (after / "old.png").write_bytes(b"old")
        os.utime(after / "old.png", (time.time() - 60, time.time() - 60))
        record_item(self.root, {"name": "old", "phase": "after", "kind": "screenshot", "path": "media/after/old.png"})
        (after / "new.png").write_bytes(b"new")  # captured in this round
        same = archive_stale_after(self.root, "fp-1", before=time.time())
        moved = archive_stale_after(self.root, "fp-2", before=time.time())
        left = sorted(path.name for path in after.iterdir() if not path.name.startswith("."))
        manifest = load_manifest(self.root)
        self.assertIsNone(same)
        self.assertTrue(moved.endswith("history/fp-1/after"))
        self.assertTrue((Path(moved) / "old.png").is_file())
        self.assertEqual(left, ["new.png"])
        self.assertEqual([item["name"] for item in manifest["items"]], [])
        self.assertEqual(manifest["history"][0]["items"][0]["name"], "old")


TEMPLATE = """### Traceability
Addresses []

### Pre-merge checklist
- [ ] Link the related Jira issue in the `Traceability` section
- [ ] Check if recomposition count is reasonable <!-- Delete if not a UI related task -->
- [ ] Strings have translator context <!-- Delete if no strings were added or changed -->

- [ ] Add Test Plan to Jira issue
"""


class ChecklistTests(unittest.TestCase):
    def test_boxes_go_back_to_the_template_and_the_ticket_link_is_ticked(self) -> None:
        body = TEMPLATE.replace("Addresses []", "Addresses [APP-1]").replace("- [ ] Check", "- [x] Check") \
            .replace("- [ ] Add Test", "- [x] Add Test")
        out, notes = normalize_checklist(body, TEMPLATE, "APP-1", True, ["app/src/main/res/values/strings.xml"])
        self.assertIn("- [x] Link the related Jira issue", out)
        self.assertIn("- [ ] Check if recomposition", out)
        self.assertIn("- [ ] Add Test Plan", out)
        self.assertIn("- [ ] Strings have", out)
        self.assertIn("unticked: add test plan to jira issue", notes)

    def test_conditional_items_that_do_not_apply_are_removed(self) -> None:
        body = TEMPLATE.replace("Addresses []", "Addresses [APP-1]") \
            .replace(" <!-- Delete if not a UI related task -->", "")  # the author dropped the comment
        out, _ = normalize_checklist(body, TEMPLATE, "APP-1", False, ["app/src/main/java/A.kt"])
        self.assertNotIn("Strings have", out)
        self.assertNotIn("recomposition", out)

    def test_no_ticket_in_the_body_leaves_the_link_box_alone(self) -> None:
        out, _ = normalize_checklist(TEMPLATE, TEMPLATE, "APP-1", True, [])
        self.assertIn("- [ ] Link the related Jira issue", out)


PLAN = """# Plan

Navigation recipe (debug build):
- Preconditions: logged in.
1. launchApp, tap `"Flashcards"`, wait for id `flashcards_content`.
2. Swipe every card → id `flashcards_summary`. Capture `sdfc-summary-browse`.
3. Wifi off, tap the row → error with Retry. Capture `start-round-error-retry`.
Evidence: `study-gfxinfo` (frame stats).

Test plan: unit tests.
"""


@own_tests
class ScaffoldTests(test_decide.RunTests):
    def test_the_recipe_becomes_one_flow_per_evidence_name(self) -> None:
        lines = recipe(PLAN)
        self.assertEqual(evidence_names(lines), ["sdfc-summary-browse", "start-round-error-retry"])
        self.assertFalse(any("Test plan" in line for line in lines))
        self.start("--level", "express")
        (self.rundir / "plan.md").write_text(PLAN, encoding="utf-8")
        result = scaffold(self.root, {"device": {"application_id": "com.example.debug"}})
        again = scaffold(self.root, {})
        flows = self.rundir / "media/flows"
        nav = (flows / "_nav.yaml").read_text(encoding="utf-8")
        browse = (flows / "sdfc-summary-browse.yaml").read_text(encoding="utf-8")
        self.assertEqual(sorted(result["written"]), ["_nav.yaml", "sdfc-summary-browse.yaml",
                                                     "start-round-error-retry.sh", "start-round-error-retry.yaml"])
        self.assertIn("appId: com.example.debug", nav)
        self.assertIn("# TODO 2. Swipe every card", nav)
        self.assertIn("- runFlow: _nav.yaml", browse)
        self.assertIn("- takeScreenshot: sdfc-summary-browse", browse)
        self.assertTrue(os.access(flows / "start-round-error-retry.sh", os.X_OK))
        self.assertEqual(again["written"], [])  # never overwrites a flow the agent filled in

    def test_an_audio_ticket_gets_a_logcat_flow_that_keeps_only_the_matching_lines(self) -> None:
        self.start("--level", "express")
        spec = read_json(self.rundir / "ticket-spec.json")
        (self.rundir / "ticket-spec.json").write_text(json.dumps({**spec, "surfaces": ["audio"]}), encoding="utf-8")
        result = scaffold(self.root, {"device": {"event_log_source": "EventLogInterceptorManager"}})
        script = (self.rundir / "media/flows" / f"{result['evidence_names'][0]}.sh").read_text(encoding="utf-8")
        self.assertIn("PATTERN='EventLogInterceptorManager.*TODO_event_name'", script)
        self.assertIn("*TODO*) echo", script)  # an unfilled pattern fails instead of matching every event
        self.assertIn('logcat -d | grep -E "$PATTERN"', script)
        self.assertNotIn("logcat -d >", script)  # never the full log

    def test_an_ac_about_audio_keeps_the_device_stage(self) -> None:
        self.start("--level", "express")
        test_decide.cli("update-spec", "--target", str(self.root), "--surfaces", "",
                        "--acceptance", "Opening a card plays its audio once|One autoplay event is logged")
        spec = read_json(self.rundir / "ticket-spec.json")
        self.assertEqual(spec["surfaces_from_ac"], ["audio", "analytics"])
        self.assertIn("T7", spec["route"])


@own_tests
class TokenTests(test_decide.RunTests):
    def test_the_transcript_wins_over_a_reported_estimate(self) -> None:
        self.start("--level", "express")
        cli("log", "--target", self.target, "--stage", "Reviewer", "--status", "started")
        measured = {"tokens": 41234, "host": "claude", "processed": 900000}
        with mock.patch("android_workflow.cli.measured_round", return_value=measured):
            cli("log", "--target", self.target, "--stage", "Reviewer", "--status", "completed", "--tokens", "60000")
        entry = read_json(self.rundir / "stage-metrics.json")["stages"]["T6"]
        self.assertEqual((entry["tokens"], entry["tokens_source"]), (41234, "claude"))
        self.assertEqual(entry["tokens_reported"], 60000)
        self.assertEqual(entry["processed_tokens"], 900000)

    def test_a_reported_figure_stays_when_no_transcript_names_the_run(self) -> None:
        self.start("--level", "express")
        with mock.patch("android_workflow.cli.measured_round", return_value=None):
            cli("log", "--target", self.target, "--stage", "Reviewer", "--status", "completed", "--tokens", "500")
        entry = read_json(self.rundir / "stage-metrics.json")["stages"]["T6"]
        self.assertEqual((entry["tokens"], entry["tokens_source"]), (500, "reported"))


@own_tests
class LateBeforeReplayTests(test_decide.ReplayTests):
    def test_the_late_before_installs_the_cached_base_apk_then_files_under_before(self) -> None:
        apk = self.root / "base.apk"
        apk.write_bytes(b"base")
        (self.bin / "adb").write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/adb-calls.log"\necho Success\n', encoding="utf-8")
        os.chmod(self.bin / "adb", 0o755)
        (self.flows / "happy.yaml").write_text("- takeScreenshot: x-happy\n", encoding="utf-8")
        passed = {"status": "passed", "apks": [{"path": str(apk)}]}
        with StateHome(), mock.patch("android_workflow.prebuild.current_status", return_value=passed):
            result = test_decide.cli("device", "replay", "--target", str(self.root), "--serial", "emu-1",
                                     "--phase", "before", "--install-base")
        calls = (self.bin / "adb-calls.log").read_text(encoding="utf-8")
        self.assertEqual((result["status"], result["base_apk"]), ("replayed", str(apk)))
        self.assertIn(f"-s emu-1 install -r -d {apk}", calls)
        self.assertTrue((run_dir(self.root) / "media/before/x-happy.png").is_file())

    def test_a_before_replay_after_the_after_round_never_runs_on_the_new_build(self) -> None:
        (self.flows / "happy.yaml").write_text("- takeScreenshot: x-happy\n", encoding="utf-8")
        (run_dir(self.root) / "replay.json").write_text("{}", encoding="utf-8")  # an after build is installed
        with StateHome():
            result = test_decide.cli("device", "replay", "--target", str(self.root), "--serial", "emu-1", "--phase", "before")
        self.assertEqual(result["status"], "no_base_apk")


@own_tests
class DeviceBuildTests(test_decide.ReplayTests):
    """The after APK is built before the device is held, then only `adb install`ed on it."""

    def with_apk_build(self) -> Path:
        script = self.root / "make-apk.sh"
        leases = Path(os.environ[STATE_HOME_ENV]) / "locks" / "device-leases.json"
        script.write_text("#!/bin/sh\n"
                          f"cat '{leases}' > leases-during-build.json 2>/dev/null || echo '{{}}' > leases-during-build.json\n"
                          "echo build >> build-calls.log\n"
                          "mkdir -p app/build/outputs/apk/debug\n"
                          "printf 'apk' > app/build/outputs/apk/debug/app-debug.apk\n", encoding="utf-8")
        script.chmod(0o755)
        config_path = cache_dir(self.root) / "project-config.json"
        config = read_json(config_path)
        config["commands"]["build"] = "./make-apk.sh"
        config.setdefault("device", {})["app_module"] = ":app"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (self.bin / "adb").write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/adb-calls.log"\necho Success\n',
                                      encoding="utf-8")
        os.chmod(self.bin / "adb", 0o755)
        return self.root / "app/build/outputs/apk/debug/app-debug.apk"

    def test_the_apk_is_built_with_no_device_held_and_adb_installed_once_per_source(self) -> None:
        (self.flows / "profile.yaml").write_text("- takeScreenshot: profile\n", encoding="utf-8")
        with StateHome():
            apk = self.with_apk_build()
            first, code = self.replay()
            during = read_json(self.root / "leases-during-build.json")
            again, _ = self.replay()  # same source: the APK is reused, not built again
        calls = (self.bin / "adb-calls.log").read_text(encoding="utf-8")
        self.assertEqual((first["status"], code), ("replayed", 0))
        self.assertNotIn("emu-1", during)  # the build ran before the device was leased
        self.assertIn(f"-s emu-1 install -r {apk}", calls)
        self.assertFalse(any("installDebug" in call for call in test_decide.gradle_calls(self.root)))
        self.assertEqual(first["apk"], str(apk))
        self.assertIn("build_seconds", first)
        self.assertFalse(first["over_budget"])
        self.assertEqual((self.root / "build-calls.log").read_text(encoding="utf-8").count("build"), 1)
        self.assertEqual(again["build_seconds"], 0.0)

    def test_a_failed_build_never_takes_the_device(self) -> None:
        (self.flows / "profile.yaml").write_text("- takeScreenshot: profile\n", encoding="utf-8")
        with StateHome():
            self.with_apk_build()
            (self.root / "make-apk.sh").write_text("#!/bin/sh\necho 'e: Broken.kt:1 boom'\nexit 1\n", encoding="utf-8")
            result, code = self.replay()
            lease = locks.lease_of("emu-1")
        self.assertEqual((result["status"], code), ("install_failed", 1))
        self.assertIn("boom", result["reason"])
        self.assertIsNone(lease)
        self.assertFalse((self.bin / "adb-calls.log").exists())

    def test_a_logcat_flow_passes_on_its_event_count(self) -> None:
        (self.flows / "autoplay-events.yaml").write_text("- launchApp\n", encoding="utf-8")
        (self.flows / "autoplay-events.sh").write_text(
            '#!/bin/sh\nprintf "I EventLog: autoplay_audio\\n" > "$AW_OUT/autoplay-events-events.txt"\n', encoding="utf-8")
        os.chmod(self.flows / "autoplay-events.sh", 0o755)
        with StateHome():
            result, code = self.replay()
        self.assertEqual(result["flows"][0]["status"], "passed")
        self.assertEqual(result["flows"][0]["events"]["matches"], 1)


class WaitFreeTests(unittest.TestCase):
    def test_waits_until_the_other_run_leaves_the_device(self) -> None:
        from android_workflow import emulator

        with StateHome() as home:
            mine, other = home / "MINE-1", home / "OTHER-1"
            for folder, started in ((mine, False), (other, True)):
                folder.mkdir()
                (folder / "run-state.json").write_text(
                    json.dumps({"stages": {"T7": {"status": "started" if started else "completed"}}}), encoding="utf-8")
            locks.acquire("emu-1", other)

            def leave() -> None:
                time.sleep(0.3)
                (other / "run-state.json").write_text(json.dumps({"status": "completed", "stages": {}}),
                                                      encoding="utf-8")

            threading.Thread(target=leave).start()
            with mock.patch.object(emulator, "status", return_value={"status": "connected", "serial": "emu-1"}), \
                    mock.patch.object(emulator, "adb_path", return_value="adb"), \
                    mock.patch.object(emulator, "_booted", return_value=True), \
                    mock.patch("android_workflow.paths.run_dir", return_value=mine):
                result = emulator.wait_free(home, timeout=5, poll=0.05)
        self.assertEqual((result["status"], result["serial"]), ("free", "emu-1"))


if __name__ == "__main__":
    unittest.main()
