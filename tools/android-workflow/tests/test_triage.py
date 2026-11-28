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
        self.assertEqual(triage.classify("LT-12", self.CONFIG),
                         {"kind": "linear", "key": "LT-12", "sources": [{"id": "LT-12", "kind": "linear", "key": "LT-12"}]})
        self.assertEqual(triage.classify("APP-9", self.CONFIG)["kind"], "jira")
        self.assertEqual(triage.classify("APP-9 crash when saving", self.CONFIG)["kind"], "jira")
        self.assertEqual(triage.classify("https://linear.app/acme/issue/app-3/title", self.CONFIG)["key"], "APP-3")
        self.assertEqual(triage.classify("https://acme.atlassian.net/browse/LT-4", self.CONFIG)["kind"], "jira")

    def test_a_stack_trace_or_free_text(self) -> None:
        source = triage.classify(STACK, self.CONFIG)
        self.assertEqual(source["kind"], "stack")
        self.assertEqual(source["stack"]["exception"], "java.lang.IllegalStateException: note is null")
        self.assertEqual(source["stack"]["frames"], ["NoteViewModel.kt:42", "NoteScreen.kt:17"])
        self.assertEqual(triage.classify("The save button does nothing on the editor", self.CONFIG),
                         {"kind": "text", "key": None, "sources": []})
        self.assertTrue(triage.triage_id(source, STACK).endswith("-illegalstateexception"))

    def test_a_slack_thread_is_the_report(self) -> None:
        source = triage.classify("https://acme.slack.com/archives/C0ABC123/p1732800000123456", self.CONFIG)
        self.assertEqual(source["kind"], "slack")
        thread = source["sources"][0]
        self.assertEqual((thread["id"], thread["channel"], thread["ts"], thread["thread_ts"]),
                         ("slack-C0ABC123-1732800000123456", "C0ABC123", "1732800000.123456", "1732800000.123456"))
        reply = triage.classify("crash here <https://acme.slack.com/archives/C0ABC123/p1732800999000001"
                                "?thread_ts=1732800000.123456&cid=C0ABC123|link>", self.CONFIG)
        self.assertEqual((reply["kind"], reply["sources"][0]["thread_ts"]), ("slack", "1732800000.123456"))
        self.assertTrue(triage.triage_id(source, "https://acme.slack.com/archives/C0ABC123/p1732800000123456")
                        .endswith("-slack-c0abc123-1732800000123456"))

    def test_a_mix_lists_every_source(self) -> None:
        text = ("The deck crashes when a student opens it offline, QA saw it twice on Pixel 7 with the 9.12 build. "
                "Thread: https://acme.slack.com/archives/C01/p1732800000123456, also LT-55 and "
                "https://acme.sentry.io/issues/4242/. See https://github.com/acme/app/issues/9.")
        source = triage.classify(text, self.CONFIG)
        self.assertEqual(source["kind"], "text")
        self.assertEqual([item["kind"] for item in source["sources"]], ["slack", "sentry", "github", "linear"])
        self.assertEqual(source["sources"][-1]["id"], "LT-55")
        self.assertEqual(triage.classify("https://example.com/report/1 save fails", self.CONFIG)["kind"], "web")
        # A tracker link anywhere still makes the ticket the report, and its key is not listed twice.
        linked = triage.classify("see https://linear.app/acme/issue/LT-4/x (LT-4) and slack", self.CONFIG)
        self.assertEqual((linked["kind"], [item["id"] for item in linked["sources"]]), ("linear", ["LT-4"]))

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

        fetch = self.next()
        self.assertEqual((fetch["next"], fetch["sources"][0]["id"], fetch["sources"][0]["kind"]),
                         ("fetch_sources", "APP-77", "jira"))
        self.assertIn("--source APP-77", fetch["sources"][0]["how"])
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

    def test_a_slack_thread_reads_its_references_and_drafts_a_reply(self) -> None:
        from android_workflow.cli import log_stage

        started = triage.start(self.root, "https://acme.slack.com/archives/C0ABC123/p1732800000123456")
        self.assertEqual(started["source"], "slack")
        agent_dir = run_dir(self.root)
        fetch = self.next()
        self.assertEqual(fetch["next"], "fetch_sources")
        thread = fetch["sources"][0]
        self.assertEqual((thread["kind"], thread["channel"], thread["thread_ts"]), ("slack", "C0ABC123", "1732800000.123456"))
        self.assertIn("slack_read_thread", thread["how"])
        (agent_dir / "ticket").mkdir()
        (agent_dir / f"ticket/{thread['id']}.md").write_text(
            "reporter: the deck crashes offline, mail me at jane@example.com. Filed LT-55, "
            "crash at https://acme.sentry.io/issues/4242/ and a doc https://docs.google.com/d/1\n", encoding="utf-8")

        # One level deep: the ticket and the crash it names, not the doc.
        refs = self.next()
        self.assertEqual(refs["next"], "fetch_sources")
        self.assertEqual(sorted((item["kind"], item["depth"]) for item in refs["sources"]), [("linear", 1), ("sentry", 1)])
        sentry = next(item["id"] for item in refs["sources"] if item["kind"] == "sentry")
        self.assertEqual(triage.ticket(self.root, "no Sentry tool here", source=sentry)["source"], sentry)
        (agent_dir / "ticket/LT-55.md").write_text("Crash on offline deck open. See LT-56.\n", encoding="utf-8")

        spawn = self.next()
        self.assertEqual((spawn["agent"], spawn["mode"]), ("aw-device", "repro"))
        self.assertIn("RUN/ticket/LT-55.md", spawn["read"])
        self.assertNotIn("jane@example.com", (agent_dir / f"ticket/{thread['id']}.md").read_text(encoding="utf-8"))

        (agent_dir / triage.REPRO_FILE).write_text("## Status: NOT_REPRODUCED\n\nTried 3 times.\n", encoding="utf-8")
        log_stage(self.root, "Device", "flows_ready", "not reproduced", "host")
        self.assertEqual(self.next()["next"], "report")
        triage.report(self.root)
        write = self.next()
        self.assertEqual((write["next"], write["related_tickets"]), ("write_ticket", ["LT-55"]))
        (agent_dir / triage.TICKET_DRAFT).write_text("Not reproduced.\n", encoding="utf-8")
        ask = self.next()
        self.assertIn("LT-55", ask["question"])
        triage.posted(self.root, ask["draft_id"], "https://linear.app/acme/issue/LT-90", None)

        reply = self.next()
        self.assertEqual((reply["next"], reply["ticket_url"]), ("write_reply", "https://linear.app/acme/issue/LT-90"))
        (agent_dir / triage.REPLY_DRAFT).write_text("Could not reproduce it yet: LT-90.\n", encoding="utf-8")
        show = self.next()
        self.assertEqual((show["next"], show["show"], show["channel"], show["thread_ts"]),
                         ("ask_user", "RUN/slack-reply.md", "C0ABC123", "1732800000.123456"))
        self.assertEqual(triage.replied(self.root, "deadbeef0000", None, None)["reason"], "stale_draft")
        self.assertEqual(self.next()["next"], "ask_user")  # nothing posted on its own
        self.assertEqual(triage.replied(self.root, show["draft_id"], "https://acme.slack.com/x", None)["status"], "replied")
        done = self.next()
        self.assertEqual((done["next"], done["replied"]), ("done", "https://acme.slack.com/x"))

    def test_a_thin_report_asks_before_the_device(self) -> None:
        triage.start(self.root, "https://example.com/bug/7")
        fetch = self.next()
        self.assertEqual(fetch["sources"][0]["kind"], "web")
        triage.ticket(self.root, "behind a login", source=fetch["sources"][0]["id"])
        ask = self.next()
        self.assertEqual((ask["next"], sorted(ask["options"])), ("ask_user", ["describe", "go on"]))
        added = triage.add(self.root, "Tapping Save on the note editor does nothing, 9.12, free account, see APP-3")
        self.assertEqual(added["sources"], ["jira APP-3"])
        self.assertEqual(self.next()["next"], "fetch_sources")
        triage.ticket(self.root, "no Jira tool", source="APP-3")
        self.assertEqual(self.next()["agent"], "aw-device")

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
