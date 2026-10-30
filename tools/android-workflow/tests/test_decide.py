"""Decisions the run's files answer: the level and the next action, made by code, not the model."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import decide
from android_workflow.cli import main, read_json, source_fingerprint
from android_workflow.decide import choose_level
from android_workflow.paths import run_dir
from test_workflow import AndroidProject, fake_gradle_project, gradle_calls

SCREEN = "app/src/main/java/com/example/ProfileScreen.kt"
NO_DEVICE = {"status": "no_device", "reason": "no device connected"}


def cli(*args: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        main(list(args))
    text = out.getvalue()
    return json.loads(text[text.index("{"):]) if "{" in text else {}


def spec(**fields: object) -> dict:
    base = {"ticket": {"title": "Change ProfileScreen title", "description": ""}, "type": "chore", "surfaces": ["ui"],
            "risk": "low", "complexity": "low", "business_questions": [], "acceptance_criteria": []}
    return {**base, **fields}


CHANGE_SET = {"candidate_files": [{"path": SCREEN}, {"path": "app/src/test/java/com/example/ProfileScreenTest.kt"}]}


class LevelTests(unittest.TestCase):
    def test_a_low_risk_chore_allows_express_but_does_not_pick_it(self) -> None:
        chosen = choose_level(spec(), CHANGE_SET)
        self.assertEqual((chosen["level"], chosen["express_allowed"]), ("standard", True))

    def test_a_bug_never_allows_express(self) -> None:
        self.assertFalse(choose_level(spec(type="bug"), CHANGE_SET)["express_allowed"])

    def test_an_open_business_question_never_allows_express(self) -> None:
        self.assertFalse(choose_level(spec(business_questions=[{"id": "q"}]), CHANGE_SET)["express_allowed"])

    def test_medium_risk_never_allows_express(self) -> None:
        self.assertFalse(choose_level(spec(risk="medium"), CHANGE_SET)["express_allowed"])

    def test_risky_surfaces_and_words_are_full(self) -> None:
        self.assertEqual(choose_level(spec(surfaces=["room_migration"]), CHANGE_SET)["level"], "full")
        self.assertEqual(choose_level(spec(ticket={"title": "ProfileScreen subscription banner"}), CHANGE_SET)["level"], "full")
        self.assertEqual(choose_level(spec(risk="high"), CHANGE_SET)["level"], "full")

    def test_a_planner_cannot_set_a_level_below_the_rules(self) -> None:
        with self.assertRaises(ValueError):
            decide.apply_level(spec(surfaces=["lifecycle"]), explicit="standard", planner=True)
        with self.assertRaises(ValueError):
            decide.apply_level(spec(type="bug"), explicit="express", planner=True)
        self.assertEqual(decide.apply_level(spec(), explicit="express", planner=True)["level"], "express")

    def test_a_risky_finding_raises_a_chosen_level_and_a_bug_leaves_express(self) -> None:
        current = {**spec(), "level": "standard", "level_source": "orchestrator"}
        current["surfaces"] = ["room_migration"]
        self.assertEqual(decide.apply_level(current)["level"], "full")
        current = {**spec(), "level": "express", "level_source": "orchestrator", "type": "bug"}
        self.assertEqual(decide.apply_level(current)["level"], "standard")

    def test_the_developer_flag_is_never_overridden(self) -> None:
        current = {**spec(surfaces=["lifecycle"]), "level": "express", "level_source": "flag"}
        self.assertEqual(decide.apply_level(current)["level"], "express")

    def test_a_level_from_the_rules_is_raised_but_never_lowered(self) -> None:
        current = {**spec(surfaces=["lifecycle"])}
        decide.apply_level(current)
        self.assertEqual(current["level"], "full")
        current["surfaces"] = ["ui"]
        decide.apply_level(current)
        self.assertEqual(current["level"], "full")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = AndroidProject()
        self.root = self.project.__enter__()
        (self.root / SCREEN).write_text("package com.example\nclass ProfileScreen\n", encoding="utf-8")
        fake_gradle_project(self.root, {})
        self.target = str(self.root)
        self.device = mock.patch("android_workflow.emulator.status", return_value=NO_DEVICE)
        self.device.start()

    def tearDown(self) -> None:
        self.device.stop()
        self.project.__exit__(None, None, None)

    @property
    def rundir(self) -> Path:
        return run_dir(self.root)

    def start(self, *extra: str, title: str = "Change ProfileScreen title") -> dict:
        cli("start", "--target", self.target, "--id", "D-1", "--title", title, "--type", "chore", *extra)
        return read_json(self.rundir / "ticket-spec.json")

    def next(self, *extra: str) -> dict:
        return cli("next", "--target", self.target, *extra)

    def implement(self, body: str = "") -> None:
        (self.root / SCREEN).write_text(f"package com.example\nclass ProfileScreen {{ {body} }}\n", encoding="utf-8")
        (self.rundir / "implementation-notes.md").write_text("# Decisions\n\nDone.\n", encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Implementer", "--status", "completed", "--tokens", "1")

    def gate(self, status: str, steps: list | None = None) -> None:
        report = read_json(self.rundir / "gate-report.json")
        prior = list(report.get("prior_runs") or [])
        if report.get("status") == "failed":
            prior.append({"status": "failed", "steps": report.get("steps") or []})
        streak = (int(report.get("red_streak") or 0) + 1) if status == "failed" else 0
        report.update({"status": status, "fingerprint": source_fingerprint(self.root), "prior_runs": prior,
                       "red_streak": streak,
                       "steps": steps if steps is not None else [{"command": "./gradlew test", "outcome": status}]})
        (self.rundir / "gate-report.json").write_text(json.dumps(report), encoding="utf-8")

    def review(self, status: str, blocking: list | None = None) -> None:
        (self.rundir / "review.json").write_text(json.dumps({"schema_version": 1, "status": status, "blocking": blocking or [],
                                                         "concerns": [], "suggestions": []}), encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Reviewer", "--status", "completed", "--tokens", "1", "--note", status)

    def device_report(self, verdict: str, replayed: bool = True) -> None:
        (self.rundir / "device-report.md").write_text(f"# Status\n\n{verdict}\n\n# Device\n\nemulator\n" + "x" * 420,
                                                  encoding="utf-8")
        if replayed:  # what `CLI device replay` writes: a PASS stands on flows that replayed on this source
            (self.rundir / "replay.json").write_text(json.dumps({
                "status": "replayed", "fingerprint": source_fingerprint(self.root),
                "flows": [{"flow": "screen.yaml", "status": "passed"}]}), encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Device", "--status", "completed", "--tokens", "1", "--note", verdict)

    def test_the_level_is_written_at_start_and_a_flag_wins(self) -> None:
        self.assertEqual(self.start()["level"], "standard")
        self.assertEqual(self.start("--level", "express")["level_source"], "flag")

    def test_the_orchestrator_chooses_express_only_when_the_rules_allow_it(self) -> None:
        self.start()
        answer = self.next()
        self.assertEqual(answer["next"], "choose_level")
        self.assertNotIn("logged", answer)  # nothing started yet
        self.assertEqual(self.next("--level", "express")["next"], "update_spec")
        cli("start", "--target", self.target, "--id", "D-1", "--title", "Fix ProfileScreen", "--type", "bug",
            "--reproduction", "open it")
        with self.assertRaises(SystemExit):
            main(["next", "--target", self.target, "--level", "nope"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(["next", "--target", self.target, "--level", "express"])
        self.assertNotEqual(code, 0)
        self.assertIn("not allowed", out.getvalue())

    def test_standard_chosen_spawns_the_planner(self) -> None:
        self.start()
        self.assertEqual(self.next("--level", "standard")["agent"], "aw-planner")

    def test_express_records_the_ac_first_then_spawns_the_implementer(self) -> None:
        self.start("--level", "express")
        self.assertEqual(self.next()["next"], "update_spec")
        cli("update-spec", "--target", self.target, "--acceptance", "title is bigger", "--surfaces", "domain")
        answer = self.next()
        self.assertEqual((answer["next"], answer["agent"], answer["logged"]), ("spawn", "aw-implementer", "Implementer started"))
        self.assertEqual(self.next()["next"], "wait")

    def test_standard_spawns_the_planner_first(self) -> None:
        self.start("--risk", "medium", title="Make the profile title bigger")
        answer = self.next()
        self.assertEqual((answer["next"], answer["agent"]), ("spawn", "aw-planner"))
        self.assertEqual(self.next()["next"], "wait")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain")
        cli("log", "--target", self.target, "--stage", "Planner", "--status", "completed", "--note", "ready", "--tokens", "1")
        self.assertEqual(self.next()["agent"], "aw-implementer")

    def test_the_planner_level_survives_a_restart(self) -> None:
        self.start(title="Make the profile title bigger")
        cli("update-spec", "--target", self.target, "--level", "full", "--surfaces", "domain")
        self.assertEqual(self.start(title="Make the profile title bigger")["level"], "full")

    def test_update_spec_refuses_express_for_a_bug(self) -> None:
        self.start("--type", "bug", "--reproduction", "tap it")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(["update-spec", "--target", self.target, "--level", "express"])
        self.assertNotEqual(code, 0)

    def checked(self) -> None:
        self.start("--level", "express")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain")
        self.next()
        self.implement("val a = 1")

    def test_gate_and_reviewer_run_together_then_finish(self) -> None:
        self.checked()
        answer = self.next()
        self.assertEqual((answer["next"], answer["run"], answer["mode"]), ("checks", ["gate", "reviewer"], "full"))
        self.gate("passed")
        self.assertEqual(self.next()["next"], "wait")  # the Reviewer was logged as started
        self.review("approved")
        answer = self.next()
        self.assertEqual((answer["next"], answer["command"]), ("finish", "CLI finish --target TARGET"))

    def test_one_fix_round_carries_gate_and_review_findings(self) -> None:
        self.checked()
        self.next()
        self.gate("failed")
        self.review("changes_requested", [{"id": "R1", "file": SCREEN}])
        answer = self.next()
        self.assertEqual((answer["next"], answer["agent"], answer["ids"]), ("spawn", "aw-implementer", ["G1", "R1"]))
        self.assertEqual(sorted(answer["read"]), ["gate-report.json", "review.json"])

    def test_a_fix_round_that_changes_nothing_opens_a_draft_instead_of_looping(self) -> None:
        self.checked()
        self.gate("failed")
        self.review("approved")
        self.assertEqual(self.next()["mode"], "fix")
        cli("log", "--target", self.target, "--stage", "Implementer", "--status", "completed", "--tokens", "1")
        answer = self.next()
        self.assertIn("without changing the code", answer.get("draft", ""))

    def test_a_fix_round_that_edits_only_a_doc_goes_back_to_review(self) -> None:
        readme = self.root / "app/README.md"
        readme.write_text("# App\n\nPaging rule.\n", encoding="utf-8")
        for args in (("init", "-q"), ("add", "-A"), ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")):
            subprocess.run(["git", "-C", self.target, *args], check=True, capture_output=True)
        self.checked()
        readme.write_text("# App\n", encoding="utf-8")
        self.next()
        self.gate("passed")
        self.review("changes_requested", [{"id": "R1", "file": "app/README.md"}])
        self.assertEqual(self.next()["mode"], "fix")
        readme.write_text("# App\n\nPaging rule.\n", encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Implementer", "--status", "completed", "--tokens", "1")
        answer = self.next()
        self.assertNotIn("draft", answer)
        self.assertEqual((answer["next"], answer["mode"]), ("checks", "delta"))

    def test_a_green_gate_resets_the_red_count(self) -> None:
        self.checked()
        self.review("approved")
        for value in range(2):
            self.gate("failed")
            self.next()
            self.implement(f"val a = {value + 2}")
            self.review("approved")
        self.gate("passed")
        self.implement("val a = 9")
        self.review("approved")
        self.gate("failed")
        self.assertEqual(self.next()["mode"], "fix")  # one red after a green: a fix round, not a draft

    def test_a_pr_round_never_turns_into_a_draft(self) -> None:
        self.checked()
        self.gate("passed")
        self.review("approved")
        cli("finish", "--target", self.target)
        self.implement("val a = 5")  # a PR feedback round
        self.review("changes_requested", [{"id": "R1"}])
        self.gate("passed")
        self.next()
        self.implement("val a = 6")
        self.review("changes_requested", [{"id": "R1"}])
        self.gate("passed")
        answer = self.next()
        self.assertEqual(answer["next"], "ask_user")
        self.assertIn("push nothing", answer["question"])

    def test_a_third_red_gate_opens_a_draft(self) -> None:
        self.checked()
        self.review("approved")
        for round_number in range(3):
            self.gate("failed")
            if round_number < 2:
                self.assertEqual(self.next()["next"], "spawn")
                self.implement(f"val a = {round_number + 2}")
                self.review("approved")
        answer = self.next()
        self.assertEqual(answer["next"], "finish")
        self.assertIn("--draft", answer["command"])

    def test_a_review_that_still_blocks_after_one_fix_opens_a_draft(self) -> None:
        self.checked()
        self.gate("passed")
        self.review("changes_requested", [{"id": "R1"}])
        self.assertEqual(self.next()["ids"], ["R1"])
        self.implement("val a = 2")
        answer = self.next()
        self.assertEqual((answer["mode"], answer["tier"], answer["fixed"]), ("delta", "standard", ["R1"]))
        self.gate("passed")
        self.review("changes_requested", [{"id": "R1"}])
        self.assertIn("--draft", self.next()["command"])

    def test_a_new_blocker_found_by_the_delta_review_gets_one_more_round(self) -> None:
        self.checked()
        self.gate("passed")
        self.review("changes_requested", [{"id": "R1"}])
        self.assertEqual(self.next()["ids"], ["R1"])
        self.implement("val a = 2")
        self.next()
        self.gate("passed")
        self.review("changes_requested", [{"id": "R2"}])  # R1 is fixed; the delta found something else
        self.assertEqual(self.next()["ids"], ["R2"])
        self.implement("val a = 3")
        self.next()
        self.gate("passed")
        self.review("changes_requested", [{"id": "R3"}])  # never more than two fix rounds
        self.assertIn("--draft", self.next()["command"])

    def test_the_planner_questions_pause_the_run_and_the_answers_rerun_the_planner(self) -> None:
        self.start("--risk", "medium", title="Make the profile title bigger")
        self.assertEqual(self.next()["agent"], "aw-planner")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain",
            "--question", "Which label?|Which set?")
        cli("log", "--target", self.target, "--stage", "Planner", "--status", "completed", "--note", "questions", "--tokens", "1")
        answer = self.next()
        self.assertEqual((answer["next"], answer["question"]["question"]), ("ask_user", "Which label?"))
        cli("resume", "--target", self.target, "--question-id", answer["question"]["id"], "--answer", "Star")
        second = self.next()["question"]
        self.assertEqual(second["question"], "Which set?")
        state = cli("resume", "--target", self.target, "--question-id", second["id"], "--answer", "415")
        self.assertEqual(state["status"], "awaiting_host")
        answer = self.next()
        self.assertEqual((answer["agent"], [item["answer"] for item in answer["answers"]]), ("aw-planner", ["Star", "415"]))
        cli("log", "--target", self.target, "--stage", "Planner", "--status", "completed", "--note", "ready", "--tokens", "1")
        self.assertEqual(self.next()["agent"], "aw-implementer")

    def test_a_device_report_older_than_the_last_replay_is_refused(self) -> None:
        self.visual_run()
        with mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            self.next()
            report = self.rundir / "device-report.md"
            report.write_text("# Status\n\nPARTIAL\n\n# Device\n\nemulator\n" + "x" * 420, encoding="utf-8")
            (self.rundir / "replay.json").write_text(json.dumps({
                "status": "replayed", "fingerprint": source_fingerprint(self.root),
                "flows": [{"flow": "screen.yaml", "status": "passed"}]}), encoding="utf-8")
            os.utime(report, (report.stat().st_mtime - 120,) * 2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = main(["log", "--target", self.target, "--stage", "Device", "--status", "completed", "--note", "PARTIAL"])
            self.assertNotEqual(code, 0)
            self.assertIn("newer than device-report.md", out.getvalue())

    def test_a_formatting_only_change_keeps_the_approved_review(self) -> None:
        self.checked()
        self.review("approved")
        (self.root / SCREEN).write_text("package com.example\nclass ProfileScreen {  val a = 1  }\n", encoding="utf-8")
        self.gate("passed")
        answer = self.next()
        self.assertEqual(answer["next"], "finish")
        self.assertEqual(read_json(self.rundir / "run-state.json")["stages"]["T6"]["kept"], "formatting-only change")

    def test_a_real_change_after_approval_needs_a_delta_review(self) -> None:
        self.checked()
        self.review("approved")
        self.implement("val a = 2")
        self.gate("passed")
        answer = self.next()
        self.assertEqual((answer["next"], answer["run"], answer["mode"]), ("checks", ["reviewer"], "delta"))

    def visual_run(self) -> None:
        self.start("--level", "express")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "ui")
        self.next()
        self.implement("val a = 1")
        self.gate("passed")
        self.review("approved")

    def test_no_device_finishes_with_the_reason(self) -> None:
        self.visual_run()
        answer = self.next()
        self.assertEqual(answer["next"], "finish")
        self.assertIn('--skip-device "no device: no device connected"', answer["command"])
        self.assertIn("--no-device", self.next("--no-device")["command"])

    def test_a_connected_device_runs_the_device_agent_and_two_fails_open_a_draft(self) -> None:
        self.visual_run()
        with mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            answer = self.next()
            self.assertEqual((answer["agent"], answer["mode"], answer["serial"]), ("aw-device", "after", "emu-1"))
            self.device_report("FAIL")
            self.assertEqual(self.next()["ids"], ["D1"])
            self.implement("val a = 2")
            self.gate("passed")
            self.review("approved")
            self.assertEqual(self.next()["agent"], "aw-device")
            self.device_report("FAIL")
            self.assertIn("--draft", self.next()["command"])

    def test_a_device_pass_finishes_and_delivery_follows(self) -> None:
        self.visual_run()
        with mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            self.next()
            self.device_report("PASS")
            (self.rundir / "media/after").mkdir(parents=True, exist_ok=True)  # the after spawn made it
            (self.rundir / "media/after/screen.png").write_bytes(b"png")
            self.assertEqual(self.next()["command"], "CLI finish --target TARGET")
        cli("finish", "--target", self.target)
        self.assertEqual(self.next()["agent"], "aw-delivery")
        self.assertEqual(self.next()["next"], "wait")
        (self.rundir / "delivery.json").write_text(json.dumps({"pr": {"url": "u"}}), encoding="utf-8")
        self.assertEqual(self.next()["next"], "watch")
        self.assertEqual(self.next("--no-watch")["next"], "done")

    def test_a_dry_run_never_writes_the_run_state(self) -> None:
        self.checked()
        self.review("approved")
        (self.root / SCREEN).write_text("package com.example\nclass ProfileScreen {  val a = 1  }\n", encoding="utf-8")
        self.gate("passed")
        before = (self.rundir / "run-state.json").read_text()
        self.assertEqual(self.next("--dry-run")["next"], "finish")
        self.assertEqual((self.rundir / "run-state.json").read_text(), before)

    def test_keep_device_closes_a_device_stage_next_had_started(self) -> None:
        self.visual_run()
        with mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            self.next()
            self.device_report("PASS")
            (self.rundir / "media/after").mkdir(parents=True, exist_ok=True)  # the after spawn made it
            (self.rundir / "media/after/screen.png").write_bytes(b"png")
            cli("finish", "--target", self.target)
            self.implement("val a = 7")
            self.gate("passed")
            self.review("approved")
            self.assertEqual(self.next()["agent"], "aw-device")  # logged as started
            cli("finish", "--target", self.target, "--keep-device", "copy only")
            self.assertEqual(read_json(self.rundir / "run-state.json")["stages"]["T7"]["status"], "completed")

    def test_a_dry_run_records_and_logs_nothing(self) -> None:
        self.start("--level", "express")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain")
        self.assertEqual(self.next("--dry-run")["next"], "spawn")
        self.assertFalse((self.rundir / decide.DECISIONS_FILE).exists())
        self.assertEqual(self.next("--dry-run")["next"], "spawn")  # nothing was logged as started
        self.next()
        self.assertEqual(len((self.rundir / decide.DECISIONS_FILE).read_text().splitlines()), 1)

    def test_parallel_work_from_the_planner_spawns_the_tech_lead(self) -> None:
        self.start("--risk", "medium", title="Make the profile title bigger")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain",
            "--parallel-parts", "2", "--parallel-files", "5")
        cli("log", "--target", self.target, "--stage", "Planner", "--status", "completed", "--note", "ready", "--tokens", "1")
        answer = self.next()
        self.assertEqual((answer["agent"], answer["mode"]), ("aw-tech-lead", "split"))

    def test_a_small_parallel_plan_stays_with_one_implementer(self) -> None:
        self.start("--risk", "medium", title="Make the profile title bigger")
        cli("update-spec", "--target", self.target, "--acceptance", "a", "--surfaces", "domain",
            "--parallel-parts", "2", "--parallel-files", "3")
        cli("log", "--target", self.target, "--stage", "Planner", "--status", "completed", "--note", "ready", "--tokens", "1")
        self.assertEqual(self.next()["agent"], "aw-implementer")


if __name__ == "__main__":
    unittest.main()


FAKE_MAESTRO = """#!/bin/sh
# fake maestro: --device S test --test-output-dir DIR FLOW
out=""; flow=""
while [ $# -gt 0 ]; do
  case "$1" in --test-output-dir) out="$2"; shift;; *.yaml) flow="$1";; esac; shift
done
name=$(basename "$flow" .yaml)
if grep -q FAILME "$flow"; then echo "Assertion is false"; exit 1; fi
if grep -q FLAKY "$flow" && [ ! -f "$flow.seen" ]; then touch "$flow.seen"; echo "flaky"; exit 1; fi
mkdir -p "$out/2026/$name/takeScreenshot"
declared=$(grep -E '^ *- *(takeScreenshot|startRecording):' "$flow" | sed 's/^[^:]*: *//')
if [ -n "$declared" ]; then
  for d in $declared; do printf 'shot-%s' "$(basename "$d")" > "$out/2026/$name/takeScreenshot/$(basename "$d").png"; done
  printf 'junk' > "$out/2026/$name/takeScreenshot/not-declared.png"
else
  printf 'after-%s' "$name" > "$out/2026/$name/takeScreenshot/$name.png"
fi
"""


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = AndroidProject()
        self.root = self.project.__enter__()
        fake_gradle_project(self.root, {})
        cli("start", "--target", str(self.root), "--id", "R-1", "--title", "Change ProfileScreen", "--level", "express")
        self.bin = self.root / "fakebin"
        self.bin.mkdir()
        (self.bin / "maestro").write_text(FAKE_MAESTRO, encoding="utf-8")
        os.chmod(self.bin / "maestro", 0o755)
        self.path = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.path.start()
        self.flows = run_dir(self.root) / "media/flows"
        self.flows.mkdir(parents=True)
        (run_dir(self.root) / "media/before").mkdir(parents=True)

    def tearDown(self) -> None:
        self.path.stop()
        self.project.__exit__(None, None, None)

    def replay(self) -> tuple[dict, int]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["device", "replay", "--target", str(self.root), "--serial", "emu-1"])
        text = out.getvalue()
        return json.loads(text[text.index("{"):]), code

    def test_no_saved_flow_sends_the_agent_to_drive_by_hand(self) -> None:
        result, code = self.replay()
        self.assertEqual((result["status"], code), ("no_flows", 1))

    def test_every_flow_is_replayed_and_filed_under_after_with_its_before_name(self) -> None:
        (self.flows / "profile.yaml").write_text("appId: x\n---\n- launchApp\n", encoding="utf-8")
        (run_dir(self.root) / "media/before/profile.png").write_bytes(b"before")
        result, code = self.replay()
        self.assertEqual((result["status"], code), ("replayed", 0))
        self.assertEqual(result["flows"][0]["captured"], ["profile"])
        self.assertEqual((run_dir(self.root) / "media/after/profile.png").read_bytes(), b"after-profile")
        self.assertEqual(result["missing_from_after"], [])
        self.assertIn("profile", (run_dir(self.root) / "media/compare.md").read_text())
        self.assertTrue(any("installDebug" in call for call in gradle_calls(self.root)))

    def test_a_failed_flow_ingests_nothing_and_an_old_after_capture_is_not_kept(self) -> None:
        (run_dir(self.root) / "media/before/b.png").write_bytes(b"before")
        (run_dir(self.root) / "media/after").mkdir(parents=True)
        (run_dir(self.root) / "media/after/b.png").write_bytes(b"stale")
        (self.flows / "b.yaml").write_text("- FAILME\n", encoding="utf-8")
        result, _ = self.replay()
        self.assertEqual(result["status"], "failed")
        self.assertFalse((run_dir(self.root) / "media/after/b.png").exists())
        self.assertEqual(result["missing_from_after"], ["b"])

    def test_a_flow_that_captures_no_before_name_is_not_a_pass(self) -> None:
        (run_dir(self.root) / "media/before/other.png").write_bytes(b"before")
        (self.flows / "profile.yaml").write_text("- launchApp\n", encoding="utf-8")
        result, _ = self.replay()
        self.assertEqual(result["flows"][0]["status"], "failed")
        self.assertFalse((run_dir(self.root) / "media/after/profile.png").exists())

    def test_an_unchanged_flow_that_failed_twice_is_skipped_until_it_is_edited(self) -> None:
        (self.flows / "_open.yaml").write_text("- openLink: https://x/1\n- launchApp\n", encoding="utf-8")
        (self.flows / "b.yaml").write_text("- runFlow: _open.yaml\n- FAILME\n", encoding="utf-8")
        first, _ = self.replay()
        self.assertIn("launchApp after openLink", first["flows"][0]["hints"][0])
        second, _ = self.replay()
        third, _ = self.replay()
        self.assertEqual([r["flows"][0].get("failed_streak") for r in (first, second, third)], [1, 2, 2])
        self.assertTrue(third["flows"][0]["skipped"])
        (self.flows / "_open.yaml").write_text("- openLink: https://x/1\n", encoding="utf-8")  # a helper edit reruns it
        fourth, _ = self.replay()
        self.assertNotIn("skipped", fourth["flows"][0])

    def test_a_failing_flow_is_reported_with_its_log(self) -> None:
        (self.flows / "a.yaml").write_text("- launchApp\n", encoding="utf-8")
        (self.flows / "b.yaml").write_text("- FAILME\n", encoding="utf-8")
        result, _ = self.replay()
        self.assertEqual(result["status"], "partial")
        failed = next(item for item in result["flows"] if item["flow"] == "b.yaml")
        self.assertIn("Assertion is false", failed["log_tail"])

    def test_next_offers_the_replay_when_flows_exist(self) -> None:
        (self.flows / "a.yaml").write_text("- launchApp\n", encoding="utf-8")
        self.assertIn("device replay", decide._replay_hint(run_dir(self.root)))

    def test_helpers_are_not_flows_and_a_script_replaces_the_flow_it_drives(self) -> None:
        from android_workflow.replay import declared_names, flows

        (self.flows / "_nav.yaml").write_text("- launchApp\n", encoding="utf-8")
        (self.flows / "happy.yaml").write_text("- takeScreenshot: media/flows/x-happy\n- startRecording: x-clip\n", encoding="utf-8")
        (self.flows / "error.yaml").write_text("- takeScreenshot: x-error\n", encoding="utf-8")
        (self.flows / "error.sh").write_text("exit 0\n", encoding="utf-8")
        self.assertEqual([path.name for path in flows(self.root)], ["error.sh", "happy.yaml"])
        self.assertEqual(declared_names(self.flows / "happy.yaml"), ["x-clip", "x-happy"])
        self.assertEqual(declared_names(self.flows / "error.sh"), ["x-error"])

    def test_a_flow_files_what_it_declares_under_that_name_with_no_before_capture(self) -> None:
        (self.flows / "happy.yaml").write_text("- launchApp\n- takeScreenshot: media/flows/x-happy\n", encoding="utf-8")
        result, code = self.replay()
        self.assertEqual((result["status"], code), ("replayed", 0))
        self.assertEqual(result["flows"][0]["captured"], ["x-happy"])
        self.assertEqual((run_dir(self.root) / "media/after/x-happy.png").read_bytes(), b"shot-x-happy")
        self.assertFalse((run_dir(self.root) / "media/after/not-declared.png").exists())
        self.assertIn("seconds", result["flows"][0])
        self.assertIn("install_seconds", result)
        self.assertFalse(result["over_budget"])

    def test_a_flaky_flow_is_rerun_once(self) -> None:
        (self.flows / "a.yaml").write_text("- FLAKY\n- takeScreenshot: a-shot\n", encoding="utf-8")
        result, _ = self.replay()
        self.assertEqual((result["status"], result["flows"][0]["attempts"]), ("replayed", 2))

    def test_the_before_phase_runs_on_the_installed_build_and_files_under_before(self) -> None:
        (self.flows / "happy.yaml").write_text("- takeScreenshot: x-happy\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["device", "replay", "--target", str(self.root), "--serial", "emu-1", "--phase", "before"])
        text = out.getvalue()
        result = json.loads(text[text.index("{"):])
        self.assertEqual((result["status"], result["phase"], code), ("replayed", "before", 0))
        self.assertTrue((run_dir(self.root) / "media/before/x-happy.png").is_file())
        self.assertFalse((run_dir(self.root) / "media/after").exists())
        self.assertFalse(any("installDebug" in call for call in gradle_calls(self.root)))
        self.assertTrue((run_dir(self.root) / "replay-before.json").is_file())

    def test_a_script_runs_with_the_device_and_the_flow_in_its_environment(self) -> None:
        (self.flows / "err.yaml").write_text("- takeScreenshot: x-err\n", encoding="utf-8")
        (self.flows / "err.sh").write_text(
            '#!/bin/sh\n"$AW_MAESTRO" --device "$AW_SERIAL" test --test-output-dir "$AW_OUT" "$AW_FLOW"\n', encoding="utf-8")
        os.chmod(self.flows / "err.sh", 0o755)
        result, _ = self.replay()
        self.assertEqual((result["status"], result["flows"][0]["flow"], result["flows"][0]["captured"]),
                         ("replayed", "err.sh", ["x-err"]))

    def before(self, **fields: object) -> dict | None:
        agent_dir = run_dir(self.root)
        with mock.patch("android_workflow.prebuild.current_status", return_value={"status": "passed"}), \
                mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            return decide._device_before(self.root, agent_dir, {"route": ["T7"], "surfaces": ["navigation"], **fields}, {},
                                         False, record=False)

    def test_the_before_run_writes_flows_even_when_nothing_is_visual(self) -> None:
        answer = self.before(visual=False)
        self.assertEqual((answer["agent"], answer["mode"], answer["capture"]), ("aw-device", "before", False))
        self.assertEqual(answer["budget_seconds"], decide.BEFORE_BUDGET_SECONDS)
        self.assertTrue(self.before(visual=True)["capture"])

    def test_the_before_run_is_skipped_once_flows_are_saved(self) -> None:
        (self.flows / "a.yaml").write_text("- launchApp\n", encoding="utf-8")
        self.assertIsNone(self.before(visual=True))

