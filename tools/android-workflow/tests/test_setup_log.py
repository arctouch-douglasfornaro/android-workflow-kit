"""Setup is logged before the ticket's `start`: its record waits for that start, never the previous run."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ["ANDROID_WORKFLOW_NO_USAGE"] = "1"

import unittest

from android_workflow.cli import log_stage, open_pending_setup, pending_setup_path, read_json, run_dir
from test_live_team_emulator import GitProject, start


class SetupLogTests(unittest.TestCase):
    def test_setup_logged_before_start_joins_the_new_run_not_the_previous_one(self) -> None:
        with GitProject() as root:
            start(root, "OLD-1")
            old_log = (run_dir(root, "OLD-1") / "stage-log.md").read_text(encoding="utf-8")
            old_metrics = read_json(run_dir(root, "OLD-1") / "stage-metrics.json")
            open_pending_setup(root)  # what `CLI setup` does
            began = log_stage(root, "Setup", "started", "learning the project")
            done = log_stage(root, "Setup", "completed", "profile recorded", tokens=1200)
            start(root, "NEW-1")
            new_log = (run_dir(root, "NEW-1") / "stage-log.md").read_text(encoding="utf-8")
            new_metrics = read_json(run_dir(root, "NEW-1") / "stage-metrics.json")
            self.assertEqual((run_dir(root, "OLD-1") / "stage-log.md").read_text(encoding="utf-8"), old_log)
            self.assertEqual(read_json(run_dir(root, "OLD-1") / "stage-metrics.json"), old_metrics)
            self.assertFalse(pending_setup_path(root).exists())
        self.assertEqual([began["recorded"], done["recorded"]], ["pending", "pending"])
        self.assertNotIn("warnings", began)
        self.assertIn("learning the project", new_log)
        self.assertIn("profile recorded", new_log)
        self.assertEqual(new_metrics["stages"]["T0"]["tokens"], 1200)
        self.assertEqual(new_metrics["stages"]["T0"]["tokens_source"], "reported")

    def test_a_setup_still_working_at_start_closes_in_the_new_run(self) -> None:
        with GitProject() as root:
            open_pending_setup(root)  # no earlier run at all: this used to fail
            log_stage(root, "Setup", "started", "learning the project")
            start(root, "NEW-2")
            opened = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T0"]
            log_stage(root, "Setup", "completed", "profile recorded", tokens=50)
            closed = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T0"]
        self.assertIn("running_since", opened)
        self.assertNotIn("running_since", closed)
        self.assertEqual(closed["tokens"], 50)

    def test_setup_logs_after_start_go_to_the_current_run(self) -> None:
        with GitProject() as root:
            start(root, "CUR-1")
            result = log_stage(root, "Setup", "started", "again")
        self.assertNotIn("recorded", result)


if __name__ == "__main__":
    unittest.main()
