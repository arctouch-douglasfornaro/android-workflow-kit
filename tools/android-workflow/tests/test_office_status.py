import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_workflow import office  # noqa: E402


class RunStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, value: dict) -> None:
        (self.run / name).write_text(json.dumps(value))

    def test_a_review_posted_before_the_status_existed_reads_as_watching_since_the_post(self) -> None:
        now = int(time.time())
        state = {"status": "running", "updated_at": now, "stages": {"T6": {"status": "completed"}}}
        self.write("run-state.json", state)
        self.write("pr-context.json", {"head": "abc"})
        self.write("review-state.json", {"posted": [{"head": "abc", "at": now - 60}]})
        self.assertEqual(office.run_status(self.run, state), ("watching", float(now - 60)))
        self.assertFalse(office.run_is_live(self.run))

    def test_a_working_agent_or_new_commits_keep_it_running(self) -> None:
        now = int(time.time())
        self.write("review-state.json", {"posted": [{"head": "abc", "at": now}]})
        self.write("pr-context.json", {"head": "new"})
        self.assertEqual(office.run_status(self.run, {"status": "running"}), ("running", None))
        self.write("pr-context.json", {"head": "abc"})
        working = {"status": "running", "stages": {"T10": {"status": "started"}}}
        self.assertEqual(office.run_status(self.run, working), ("running", None))

    def test_a_finished_run_stops_its_clock_when_it_finished(self) -> None:
        self.assertEqual(office.run_status(self.run, {"status": "completed", "finished_at": 100}), ("completed", 100.0))


class ActiveTimeTests(unittest.TestCase):
    def test_waiting_with_nobody_at_work_does_not_count(self) -> None:
        work = {"Orchestrator": [[0.0, None]], "Implementer": [[10.0, 70.0]], "Reviewer": [[50.0, 100.0]],
                "PR Watcher": [[1000.0, 1010.0]]}
        self.assertEqual(office._active(work), {"seconds": 100.0, "since": None})

    def test_an_agent_at_work_keeps_the_clock_open(self) -> None:
        work = {"Implementer": [[10.0, 20.0]], "Quality gate": [[100.0, None]], "Reviewer": [[110.0, 120.0]]}
        self.assertEqual(office._active(work), {"seconds": 10.0, "since": 100.0})

    def test_a_round_logged_only_at_its_end_ran_since_the_event_before(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "stage-log.md").write_text(
                "| 2026-11-09T13:53:09Z | PR Watcher | started | host | watching |\n"
                "| 2026-11-09T13:53:22Z | PR Watcher | wait | cli | checks running |\n"
                "| 2026-11-09T16:37:00Z | PR Watcher | sync | cli | conflict |\n"
                "| 2026-11-09T16:39:35Z | Implementer | completed | host | merge conflicts resolved |\n")
            work = office._work(run, {}, [{"role": "PR Watcher", "state": "waiting"}], 0.0)
            self.assertEqual(office._active(work)["seconds"], 13.0 + 155.0)


if __name__ == "__main__":
    unittest.main()
