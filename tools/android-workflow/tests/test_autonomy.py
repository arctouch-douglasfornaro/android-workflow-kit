"""The autonomy policy: presets, the developer's and the project's files, the run's level."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import autonomy, decide
from android_workflow.delivery import pull_request_disclosures
from android_workflow.watch import watch_config


class AutonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / "app"
        (self.target / ".ai").mkdir(parents=True)
        self.user = self.root / "user.json"
        self.env = mock.patch.dict(os.environ, {"ANDROID_WORKFLOW_CONFIG": str(self.user)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def files(self, user: dict | None = None, project: dict | None = None) -> None:
        if user is not None:
            self.user.write_text(json.dumps({"autonomy": user}), encoding="utf-8")
        if project is not None:
            (self.target / ".ai" / "android-workflow.json").write_text(json.dumps({"autonomy": project}), encoding="utf-8")

    def test_no_file_is_balanced_the_behavior_before_levels(self) -> None:
        policy = autonomy.effective(self.target)
        self.assertEqual(policy["level"], "balanced")
        self.assertEqual((policy["partial_device"], policy["questions"], policy["merge"]), ("auto", "ask", "auto"))

    def test_the_project_wins_over_the_developer_and_a_key_wins_over_the_preset(self) -> None:
        self.files(user={"level": "autonomous", "open_pr": "ask"}, project={"level": "careful", "merge": "auto"})
        policy = autonomy.effective(self.target)
        self.assertEqual((policy["level"], policy["sources"]["level"]), ("careful", "project"))
        self.assertEqual((policy["open_pr"], policy["sources"]["open_pr"]), ("ask", "user"))
        self.assertEqual((policy["merge"], policy["sources"]["merge"]), ("auto", "project"))
        self.assertEqual(policy["questions"], "ask")

    def test_a_typo_is_ignored_and_reported(self) -> None:
        self.files(project={"level": "yolo", "questions": "never", "critical_words": ["Streak"]})
        policy = autonomy.effective(self.target)
        self.assertEqual((policy["level"], policy["questions"]), ("balanced", "ask"))
        self.assertEqual(len(policy["ignored"]), 2)
        self.assertEqual(policy["critical_words"], ["streak"])

    def test_critical_words_extend_the_built_in_ones(self) -> None:
        spec = {"title": "Reset the streak counter", "acceptance_criteria": []}
        self.assertIsNone(decide.critical(spec))
        self.assertIn("streak", decide.critical(spec, ["streak"]))

    def test_the_default_a_question_names(self) -> None:
        self.assertEqual(autonomy.take_default("Which label? (default: Star)"), "Star")
        self.assertIsNone(autonomy.take_default("Which label?"))

    def test_merge_follows_autonomy_unless_watch_sets_it(self) -> None:
        self.files(project={"level": "careful"})
        self.assertFalse(watch_config(self.target)["auto_merge"])
        (self.target / ".ai" / "android-workflow.json").write_text(
            json.dumps({"autonomy": {"level": "careful"}, "watch": {"auto_merge": True}}), encoding="utf-8")
        self.assertTrue(watch_config(self.target)["auto_merge"])

    def test_the_pr_states_what_was_assumed(self) -> None:
        state = {"assumed": [{"question": "Which label? (default: Star)", "answer": "Star"}]}
        lines = pull_request_disclosures({}, state, "body")
        self.assertIn("Assumed", lines[0])
        self.assertIn("→ Star", lines[1])


if __name__ == "__main__":
    unittest.main()
