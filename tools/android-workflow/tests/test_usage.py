"""Tokens while an agent works: read from the host's own transcript (Claude Code, Codex)."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The one office every app publishes to: a throwaway folder, never the developer's own.
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import contextlib
import io
import itertools
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from android_workflow import office, usage
from android_workflow.cli import main, read_json
from android_workflow.paths import run_dir
from test_live_team_emulator import PLAN, GitProject, metrics, office_script, start, write_plan

IDS = itertools.count(1)


def iso(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat().replace("+00:00", "Z")


def log(root: Path, *args: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["log", "--target", str(root), *args])
    return json.loads(out.getvalue() or "{}")


def claude_agent(home: Path, agent_type: str, prompt: str, requests: list[tuple[float, int]]) -> Path:
    """A Claude Code subagent transcript: the orchestrator's prompt, then one usage per request."""
    folder = home / "projects" / "-app" / "session" / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"agent-a{next(IDS)}.jsonl"
    path.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}), encoding="utf-8")
    lines = [{"type": "user", "timestamp": iso(requests[0][0] if requests else time.time()),
              "message": {"role": "user", "content": prompt}}]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    claude_requests(path, requests)
    return path


def claude_requests(path: Path, requests: list[tuple[float, int]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for when, tokens in requests:
            usage_ = {"input_tokens": 3, "cache_creation_input_tokens": 7, "cache_read_input_tokens": tokens - 30,
                      "output_tokens": 20}
            handle.write(json.dumps({"type": "assistant", "timestamp": iso(when),
                                     "message": {"id": f"m{next(IDS)}", "role": "assistant", "usage": usage_}}) + "\n")


def codex_agent(home: Path, cwd: Path, agent_path: str, requests: list[tuple[float, int]]) -> Path:
    """A Codex child thread: the forked history of its parent (ignored), then its own token counts."""
    now = datetime.now()
    folder = home / "sessions" / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{next(IDS)}.jsonl"
    spawn = {"parent_thread_id": "p", "depth": 1, "agent_path": agent_path, "agent_nickname": "Hooke", "agent_role": None}
    lines = [
        {"timestamp": iso(time.time()), "ordinal": 0, "type": "session_meta",
         "payload": {"cwd": str(cwd), "source": {"subagent": {"thread_spawn": spawn}}, "agent_path": agent_path,
                     "subagent_history_start_ordinal": 2}},
        # The parent's own usage, copied into the child as forked history: not the child's.
        {"timestamp": iso(time.time()), "ordinal": 1, "type": "event_msg",
         "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 999_999}}}},
    ]
    for ordinal, (when, tokens) in enumerate(requests, start=2):
        lines.append({"timestamp": iso(when), "ordinal": ordinal, "type": "event_msg",
                      "payload": {"type": "token_count", "info": {
                          "total_token_usage": {"total_tokens": tokens * 10},
                          "last_token_usage": {"total_tokens": tokens}}}})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


class HostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.homes = tempfile.TemporaryDirectory()
        base = Path(self.homes.name)
        self.claude, self.codex = base / "claude", base / "codex"
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude), "CODEX_HOME": str(self.codex)})
        self.env.start()
        os.environ.pop("ANDROID_WORKFLOW_NO_USAGE", None)
        usage._CACHE.clear()
        self.project = GitProject()
        self.root = self.project.__enter__()
        start(self.root)
        self.run = run_dir(self.root)

    def tearDown(self) -> None:
        self.project.__exit__(None, None, None)
        self.env.stop()
        self.homes.cleanup()
        usage._CACHE.clear()

    def desk(self, role: str) -> dict:
        return {desk["role"]: desk for desk in office.collect(self.root)["desks"]}[role]

    def prompt(self, extra: str = "") -> str:
        return f"TARGET={self.root}\nRUN={self.run}\nCLI=python3 ~/.ai/bin/android-workflow\nLevel: standard.{extra}"


class ClaudeCodeTests(HostTests):
    def test_a_working_agent_shows_the_tokens_of_its_latest_request(self) -> None:
        log(self.root, "--stage", "Reviewer", "--status", "started")
        now = time.time()
        claude_agent(self.claude, "aw-reviewer", self.prompt(), [(now + 1, 40_000), (now + 2, 52_000)])
        claude_agent(self.claude, "aw-reviewer", "RUN=/elsewhere/.ai/workflow/X-9", [(now + 3, 7_000)])  # another run
        reviewer = self.desk("Reviewer")
        totals = office.collect(self.root)["totals"]
        self.assertEqual(reviewer["tokens"], 52_000)
        self.assertEqual(reviewer["tokens_live"], "claude")
        self.assertEqual(totals["tokens"], 52_000)
        self.assertTrue(totals["tokens_live"])

    def test_the_count_follows_the_transcript_as_it_grows(self) -> None:
        log(self.root, "--stage", "Planner", "--status", "started")
        now = time.time()
        path = claude_agent(self.claude, "aw-planner", self.prompt(), [(now + 1, 30_000)])
        first = self.desk("Planner")["tokens"]
        claude_requests(path, [(now + 2, 45_000)])
        self.assertEqual((first, self.desk("Planner")["tokens"]), (30_000, 45_000))

    def test_a_round_adds_to_the_rounds_already_logged_and_ignores_older_requests(self) -> None:
        log(self.root, "--stage", "Reviewer", "--status", "started")
        log(self.root, "--stage", "Reviewer", "--status", "completed", "--tokens", "60000")
        time.sleep(0.01)
        log(self.root, "--stage", "Reviewer", "--status", "started", "--note", "delta review")
        since = metrics(self.root)["stages"]["T6"]["running_since"]
        claude_agent(self.claude, "aw-reviewer", self.prompt(), [(since - 30, 60_000)])  # the first round
        claude_agent(self.claude, "aw-reviewer", self.prompt(), [(since + 1, 20_000)])
        self.assertEqual(self.desk("Reviewer")["tokens"], 80_000)

    def test_a_prompt_that_names_the_run_from_target_binds_too(self) -> None:
        log(self.root, "--stage", "Device", "--status", "started")
        now = time.time()
        prompt = f"TARGET={self.root}\nRUN=TARGET/.ai/workflow/{self.run.name}/\nLevel: standard."
        claude_agent(self.claude, "aw-device", prompt, [(now + 1, 33_000)])
        self.assertEqual(self.desk("Device")["tokens"], 33_000)

    def test_completed_without_tokens_reads_them_and_the_reported_total_does_not_add_twice(self) -> None:
        log(self.root, "--stage", "Reviewer", "--status", "started")
        now = time.time()
        claude_agent(self.claude, "aw-reviewer", self.prompt(), [(now + 1, 48_000)])
        (self.run / "review.json").write_text(json.dumps(
            {"schema_version": 1, "status": "approved", "blocking": [], "concerns": [], "suggestions": []}), encoding="utf-8")
        own = log(self.root, "--stage", "Reviewer", "--status", "completed", "--note", "approved")
        filled = metrics(self.root)["stages"]["T6"]
        log(self.root, "--stage", "Reviewer", "--status", "completed", "--tokens", "48900")
        merged = metrics(self.root)["stages"]["T6"]
        reviewer = self.desk("Reviewer")
        self.assertNotIn("warnings", own)
        self.assertEqual((filled["tokens"], filled["tokens_source"]), (48_000, "claude"))
        self.assertEqual((merged["tokens"], merged["tokens_source"]), (48_900, "reported"))
        self.assertEqual(reviewer["state"], "done")
        self.assertNotIn("tokens_live", reviewer)

    def test_a_finished_run_reopened_adds_the_working_agent_to_its_total(self) -> None:
        path = self.run / "stage-metrics.json"
        value = read_json(path)
        value["totals"] = {"wall_time_seconds": 100, "tokens": 90_000}
        path.write_text(json.dumps(value), encoding="utf-8")
        log(self.root, "--stage", "Delivery", "--status", "started")
        claude_agent(self.claude, "aw-delivery", self.prompt(), [(time.time() + 1, 20_000)])
        totals = office.collect(self.root)["totals"]
        self.assertEqual((totals["tokens"], totals["tokens_live"]), (110_000, True))

    def test_without_a_transcript_nothing_is_invented(self) -> None:
        log(self.root, "--stage", "Planner", "--status", "started")
        planner = self.desk("Planner")
        result = log(self.root, "--stage", "Planner", "--status", "completed")
        self.assertIsNone(planner["tokens"])
        self.assertIsNone(office.collect(self.root)["totals"]["tokens"])
        self.assertIn("logged without --tokens", " ".join(result["warnings"]))
        self.assertIsNone(metrics(self.root)["stages"]["T3"]["tokens"])

    def test_the_switch_turns_it_off(self) -> None:
        log(self.root, "--stage", "Planner", "--status", "started")
        claude_agent(self.claude, "aw-planner", self.prompt(), [(time.time() + 1, 30_000)])
        with mock.patch.dict(os.environ, {"ANDROID_WORKFLOW_NO_USAGE": "1"}):
            self.assertIsNone(self.desk("Planner")["tokens"])


class CodexTests(HostTests):
    def test_a_child_thread_in_the_app_counts_and_its_forked_history_does_not(self) -> None:
        log(self.root, "--stage", "Implementer", "--status", "started")
        now = time.time()
        codex_agent(self.codex, self.root, "/root/aw_implementer", [(now + 1, 90_000), (now + 2, 120_000)])
        codex_agent(self.codex, Path("/somewhere/else"), "/root/aw_implementer", [(now + 3, 5_000)])
        implementer = self.desk("Implementer")
        self.assertEqual((implementer["tokens"], implementer["tokens_live"]), (120_000, "codex"))

    def test_a_name_without_the_prefix_still_names_the_role(self) -> None:
        log(self.root, "--stage", "Reviewer", "--status", "started")
        codex_agent(self.codex, self.root, "/root/reviewer_delta", [(time.time() + 1, 34_000)])
        self.assertEqual(self.desk("Reviewer")["tokens"], 34_000)

    def test_team_slices_are_told_apart_by_name(self) -> None:
        log(self.root, "--stage", "Tech Lead", "--status", "started")
        write_plan(self.root, PLAN)
        log(self.root, "--stage", "Tech Lead", "--status", "completed", "--tokens", "100")
        log(self.root, "--stage", "Implementer", "--slice", "S1", "--status", "started")
        log(self.root, "--stage", "Implementer", "--slice", "S2", "--status", "started")
        now = time.time()
        codex_agent(self.codex, self.root, "/root/aw_implementer_s1", [(now + 1, 41_000)])
        codex_agent(self.codex, self.root, "/root/aw_implementer_s2", [(now + 1, 62_000)])
        data = office.collect(self.root)
        desks = {desk["role"]: desk for desk in data["desks"]}
        filled = log(self.root, "--stage", "Implementer", "--slice", "S2", "--status", "completed")
        slice_two = metrics(self.root)["stages"]["T4"]["slices"]["S2"]
        log(self.root, "--stage", "Implementer", "--slice", "S2", "--status", "completed", "--tokens", "62500")
        reported = metrics(self.root)["stages"]["T4"]["slices"]["S2"]
        self.assertEqual((desks["Implementer"]["tokens"], desks["Implementer 2"]["tokens"]), (41_000, 62_000))
        self.assertEqual(data["totals"]["tokens"], 100 + 41_000 + 62_000)
        self.assertNotIn("warnings", filled)
        self.assertEqual((slice_two["tokens"], slice_two["tokens_source"]), (62_000, "codex"))
        self.assertEqual((reported["tokens"], reported["tokens_source"]), (62_500, "reported"))

    def test_the_watcher_rewrites_the_page_when_the_count_grows(self) -> None:
        log(self.root, "--stage", "Implementer", "--status", "started")
        now = time.time()
        path = codex_agent(self.codex, self.root, "/root/aw_implementer", [(now + 1, 10_000)])
        key, _ = office.write_live_data(self.root)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": iso(now + 2), "ordinal": 9, "type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {"total_tokens": 25_000}}}}) + "\n")
        again, _ = office.write_live_data(self.root, key)
        written = office_script(self.root).read_text(encoding="utf-8")
        self.assertNotEqual(key, again)
        self.assertIn('"tokens": 25000', written)


if __name__ == "__main__":
    unittest.main()
