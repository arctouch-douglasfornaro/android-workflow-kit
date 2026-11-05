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


if __name__ == "__main__":
    unittest.main()
