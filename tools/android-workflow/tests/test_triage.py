"""android-bug-triage: input detection, redaction and the `triage next` sequence, decided by code."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))
# The developer\'s own autonomy config never reaches a test.
os.environ["ANDROID_WORKFLOW_CONFIG"] = "/nonexistent/android-workflow-config.json"

import json
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import triage
from android_workflow.cli import load_state, read_json, write_json
from android_workflow.paths import run_dir
from test_workflow import AndroidProject

STACK = """FATAL EXCEPTION: main
Process: com.example, PID: 4242
java.lang.IllegalStateException: note is null
    at com.example.NoteViewModel.load(NoteViewModel.kt:42)
    at com.example.NoteScreen.onStart(NoteScreen.kt:17)
"""


class InputTests(unittest.TestCase):
    CONFIG = {"linear_prefixes": ["LT"]}

    def test_a_key_or_link_names_its_tracker(self) -> None:
        self.assertEqual(triage.classify("LT-12", self.CONFIG), {"kind": "linear", "key": "LT-12"})
        self.assertEqual(triage.classify("APP-9", self.CONFIG), {"kind": "jira", "key": "APP-9"})
        self.assertEqual(triage.classify("APP-9 crash when saving", self.CONFIG)["kind"], "jira")
        self.assertEqual(triage.classify("https://linear.app/acme/issue/app-3/title", self.CONFIG)["key"], "APP-3")
        self.assertEqual(triage.classify("https://acme.atlassian.net/browse/LT-4", self.CONFIG)["kind"], "jira")

    def test_a_stack_trace_or_free_text(self) -> None:
        source = triage.classify(STACK, self.CONFIG)
        self.assertEqual(source["kind"], "stack")
        self.assertEqual(source["stack"]["exception"], "java.lang.IllegalStateException: note is null")
        self.assertEqual(source["stack"]["frames"], ["NoteViewModel.kt:42", "NoteScreen.kt:17"])
        self.assertEqual(triage.classify("The save button does nothing on the editor", self.CONFIG),
                         {"kind": "text", "key": None})
        self.assertTrue(triage.triage_id(source, STACK).endswith("-illegalstateexception"))

    def test_redaction(self) -> None:
        text, count = triage.redact("user jane.doe@example.com, Authorization: Bearer abcdefghijklmnop1234, "
                                    "password=hunter22secret, eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl")
        self.assertEqual(count, 4)
        for secret in ("jane.doe@example.com", "abcdefghijklmnop1234", "hunter22secret", "eyJhbGciOiJI"):
            self.assertNotIn(secret, text)


class SequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = AndroidProject()
        self.root = self.project.__enter__()
        (self.root / ".ai").mkdir(exist_ok=True)
        (self.root / ".ai/project-profile.md").write_text("# Profile\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.project.__exit__()

    def next(self, build="passed", serial="emulator-5554"):
        with mock.patch("android_workflow.prebuild.current_status", return_value={"status": build}), \
                mock.patch("android_workflow.decide._device_for", return_value=(serial, None, "")):
            return triage.next_action(self.root)

    def test_from_a_tracker_key_to_the_offer_to_fix(self) -> None:
        started = triage.start(self.root, "APP-77")
        self.assertEqual((started["id"], started["source"]), ("APP-77", "jira"))
        agent_dir = run_dir(self.root)
        self.assertEqual(load_state(self.root)["mode"], "triage")
        self.assertEqual(read_json(agent_dir / triage.BUG_FILE)["key"], "APP-77")

        self.assertEqual(self.next()["next"], "fetch_ticket")
        (agent_dir / "ticket").mkdir()
        (agent_dir / "ticket/APP-77.md").write_text("Saving a note with an emoji title crashes.\n", encoding="utf-8")

        self.assertEqual(self.next(build="not_started")["next"], "prebuild")
        spawn = self.next()
        self.assertEqual((spawn["next"], spawn["agent"], spawn["mode"], spawn["serial"]),
                         ("spawn", "aw-device", "repro", "emulator-5554"))
        self.assertEqual(self.next()["next"], "wait")  # the Device stage is started

        (agent_dir / triage.REPRO_FILE).write_text("## Status: REPRODUCED\n\nCrashes on save.\n", encoding="utf-8")
        from android_workflow.cli import log_stage

        log_stage(self.root, "Device", "flows_ready", "repro done", "host")
        planner = self.next()
        self.assertEqual((planner["agent"], planner["mode"], planner["repro"]), ("aw-planner", "triage", "REPRODUCED"))
        self.assertIn("RUN/repro-report.md", planner["read"])

        (agent_dir / triage.CAUSE_FILE).write_text("Title encoder drops surrogate pairs (Encoder.kt:12).\n", encoding="utf-8")
        (agent_dir / "plan.md").write_text("## Objective\nFix it.\n", encoding="utf-8")
        spec = read_json(agent_dir / "ticket-spec.json")
        write_json(agent_dir / "ticket-spec.json", {**spec, "planner_updated": True,
                                                    "acceptance_criteria": ["Saving an emoji title keeps the note"]})
        self.assertEqual(self.next()["next"], "report")
        written = triage.report(self.root)
        self.assertEqual(written["result"], "REPRODUCED")
        report_text = (agent_dir / triage.REPORT_FILE).read_text(encoding="utf-8")
        self.assertIn("Encoder.kt:12", report_text)
        self.assertIn("Saving an emoji title keeps the note", report_text)

        write = self.next()
        self.assertEqual(write["next"], "write_ticket")
        self.assertIn("comment on APP-77", write["why"])
        (agent_dir / triage.TICKET_DRAFT).write_text("Reproduced. Reporter: jane@example.com\n", encoding="utf-8")
        ask = self.next()
        self.assertEqual(ask["next"], "ask_user")
        self.assertIn("1 email/token", ask["redacted"])
        self.assertNotIn("jane@example.com", (agent_dir / triage.TICKET_DRAFT).read_text(encoding="utf-8"))

        self.assertEqual(triage.posted(self.root, "deadbeef0000", None, None)["reason"], "stale_draft")
        self.assertEqual(triage.posted(self.root, ask["draft_id"], "https://linear.app/x", None)["status"], "posted")
        done = self.next()
        self.assertEqual((done["next"], done["offer"]), ("done", "/android-workflow APP-77"))

    def test_not_reproduced_skips_the_planner_and_offers_no_fix(self) -> None:
        triage.start(self.root, "The archive button does nothing on the note list")
        agent_dir = run_dir(self.root)
        self.assertEqual(self.next()["agent"], "aw-device")
        (agent_dir / triage.REPRO_FILE).write_text("## Status: NOT_REPRODUCED\n\nTried 3 times.\n", encoding="utf-8")
        from android_workflow.cli import log_stage

        log_stage(self.root, "Device", "flows_ready", "not reproduced", "host")
        self.assertEqual(self.next()["next"], "report")
        triage.report(self.root)
        write = self.next()
        self.assertEqual((write["next"], write["skill"]), ("write_ticket", "create-ticket"))
        self.assertEqual(triage.posted(self.root, None, None, "the developer will ask the reporter")["status"], "skipped")
        done = self.next()
        self.assertNotIn("offer", done)

    def test_no_device_goes_to_the_cause_from_the_code(self) -> None:
        triage.start(self.root, STACK, no_device=True)
        planner = self.next()
        self.assertEqual((planner["agent"], planner["mode"]), ("aw-planner", "triage"))
        self.assertTrue(planner["repro"].startswith("skipped"))
        self.assertTrue(read_json(run_dir(self.root) / triage.BUG_FILE)["stack"]["frames"])

    def test_a_blocked_device_asks_before_going_on(self) -> None:
        triage.start(self.root, "Crash when opening settings")
        agent_dir = run_dir(self.root)
        self.next()
        (agent_dir / triage.REPRO_FILE).write_text("## Status: BLOCKED\n\ninstall_failed\n", encoding="utf-8")
        ask = self.next()
        self.assertEqual(ask["next"], "ask_user")
        triage.repro(self.root, accept_blocked=True)
        self.assertEqual(self.next()["agent"], "aw-planner")

    def test_android_workflow_continues_the_triaged_run_at_the_implementer(self) -> None:
        from android_workflow import cli, decide

        triage.start(self.root, "APP-88")
        agent_dir = run_dir(self.root)
        (agent_dir / triage.CAUSE_FILE).write_text("Cause: Encoder.kt:12\n", encoding="utf-8")
        (agent_dir / "plan.md").write_text("## Objective\nFix it.\n## Steps\n1. x\n## Out of scope\nNone\n"
                                           "## Verification\nAC1 → unit\n", encoding="utf-8")
        args = cli.parse_args(["update-spec", "--target", str(self.root), "--acceptance", "Saving keeps the note",
                               "--proofs", "unit", "--surfaces", "domain",
                               "--reproduction", "from the code: Encoder.kt:12 drops surrogate pairs"])
        cli.update_spec(self.root, args)
        cli.log_stage(self.root, "Planner", "completed", "cause and plan", "host")
        # `/android-workflow APP-88` → `CLI start` again: the run resumes with the triage's plan.
        cli.run(self.root, {"id": "APP-88", "title": "Bug triage APP-88", "description": "", "type": "bug",
                            "acceptance_criteria": [], "surfaces": [], "business_questions": []})
        answer = decide.next_action(self.root, no_device=True, record=False)
        self.assertEqual(answer.get("agent"), "aw-implementer", answer)


if __name__ == "__main__":
    unittest.main()
