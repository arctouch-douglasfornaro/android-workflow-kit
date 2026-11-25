import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_eval


class SkillEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {"ANDROID_WORKFLOW_STATE_HOME": self.tmp.name,
                                          "ANDROID_WORKFLOW_NO_USAGE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_case_has_a_prompt_rubric_and_known_checks(self):
        kinds = {"json_equals", "json_contains", "file_exists", "file_absent", "regex", "not_regex", "cli",
                 "unchanged", "sections_unchanged", "no_secrets", "map_row", "not_mapped", "mutation"}
        for case in skill_eval.cases().values():
            with self.subTest(case=case["id"]):
                self.assertTrue(Path(case["_dir"], case["prompt_file"]).is_file())
                self.assertTrue(case["rubric"])
                self.assertLessEqual({check["kind"] for check in case["checks"]}, kinds)
                self.assertTrue((skill_eval.EVALS / "fixtures" / case["fixture"]).is_dir())

    def test_the_workspace_and_prompt_never_say_eval(self):
        prepared = skill_eval.prepare("setup-app-id")
        relative = Path(prepared["workdir"]).relative_to(self.tmp.name).as_posix()
        self.assertNotRegex(relative, r"(?i)eval|test|grade|bench")
        self.assertNotRegex(prepared["prompt"].replace(self.tmp.name, ""), r"(?i)\beval|grad(e|ing)|rubric")
        self.assertNotIn(str(skill_eval.EVALS), prepared["prompt"])
        self.assertNotRegex(Path(prepared["workdir"]).joinpath(".ai/project-profile.md").read_text(), r"(?i)\beval")

    def test_checks_score_what_the_agent_wrote_and_record_keeps_the_delta(self):
        prepared = skill_eval.prepare("setup-app-id")
        first = skill_eval.check(prepared["id"])
        self.assertFalse(next(r for r in first["results"] if r["id"] == "app_id_recorded")["passed"])
        self.assertLess(first["score"], 10)
        with self.assertRaisesRegex(ValueError, "no judge score"):
            skill_eval.record(prepared["id"])  # a failed or skipped judge never records a checks-only score
        skill_eval.record(prepared["id"], checks_only=True)

        again = skill_eval.prepare("setup-app-id")
        workdir = Path(again["workdir"])
        overrides = workdir / ".ai/android-workflow.json"
        data = json.loads(overrides.read_text())
        data["device"] = {"app_module": ":app", "application_id": "com.notes.app.debug", "launch_activity": ".MainActivity"}
        overrides.write_text(json.dumps(data))
        skill_eval.run_cli(["setup", "--target", str(workdir), "--accept"], workdir)
        second = skill_eval.check(again["id"])
        self.assertEqual(second["score"], 10, second["results"])
        entry = skill_eval.record(again["id"], checks_only=True)
        self.assertGreater(entry["delta"], 0)
        row = next(r for r in skill_eval.report()["cases"] if r["case"] == "setup-app-id")
        self.assertEqual(row["score"], 10)

    def test_a_profile_rewrite_outside_the_step_fails_the_scope_check(self):
        prepared = skill_eval.prepare("setup-app-id")
        profile = Path(prepared["workdir"]) / ".ai/project-profile.md"
        profile.write_text(profile.read_text().replace("JUnit4", "JUnit5"))
        results = {r["id"]: r for r in skill_eval.check(prepared["id"])["results"]}
        self.assertFalse(results["profile_scope"]["passed"])
        self.assertIn("Tests", results["profile_scope"]["detail"])

    def test_map_checks_read_the_learned_rows(self):
        prepared = skill_eval.prepare("setup-feature-map")
        workdir = Path(prepared["workdir"])
        source = Path(skill_eval.cases()["planner-map-recipe"]["_dir"]) / "feature-map.md"
        (workdir / ".ai/feature-map.md").write_text(source.read_text())
        results = {r["id"]: r for r in skill_eval.check(prepared["id"])["results"]}
        for name in ("map_ok", "detail_deep_link", "detail_selector", "archive_route", "settings_route",
                     "gallery_not_mapped", "profile_untouched"):
            self.assertTrue(results[name]["passed"], (name, results[name]))

    def test_judge_scores_are_weighted_and_unknown_ids_ignored(self):
        prepared = skill_eval.prepare("setup-app-id")
        skill_eval.check(prepared["id"])
        verdict = {"scores": [{"id": "evidence", "score": 10, "reason": "plugin"},
                              {"id": "scope", "score": 4, "reason": "rewrote Tests"},
                              {"id": "made_up", "score": 0, "reason": "x"}], "summary": "ok"}

        def fake_codex(command, **_):
            Path(command[command.index("-o") + 1]).write_text(json.dumps(verdict))
            return skill_eval.subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(skill_eval.subprocess, "run", side_effect=fake_codex):
            result = skill_eval.judge(prepared["id"])
        self.assertEqual(result["score"], round((10 * 3 + 4 * 3) / 6, 2))
        self.assertEqual(result["missing"], ["reply"])


    def test_a_codex_agent_is_judged_by_claude_and_compared_with_its_own_host(self):
        prepared = skill_eval.prepare("setup-app-id")
        calls = []

        def fake(command, **_):
            calls.append(command)
            if command[0] == "codex":
                Path(command[command.index("-o") + 1]).write_text("STATUS: DONE")
                return skill_eval.subprocess.CompletedProcess(command, 0, "", "")
            verdict = {"scores": [{"id": "evidence", "score": 8, "reason": "x"}], "summary": "ok"}
            return skill_eval.subprocess.CompletedProcess(command, 0, json.dumps(verdict), "")

        with patch.object(skill_eval.subprocess, "run", side_effect=fake):
            ran = skill_eval.run_agent(prepared["id"])
            with self.assertRaises(ValueError):
                skill_eval.judge(prepared["id"], "codex")
            judged = skill_eval.judge(prepared["id"])
        self.assertEqual((ran["host"], ran["model"]), ("codex", skill_eval.CODEX_TIERS[prepared["tier"] or "standard"][0]))
        self.assertIn("agents/aw-setup.md", calls[0][-1])
        self.assertTrue(calls[0][-1].endswith(prepared["prompt"]))
        self.assertEqual((calls[-1][0], judged["score"]), ("claude", 8))
        claude_run = skill_eval.prepare("setup-app-id")
        skill_eval.record(claude_run["id"], checks_only=True)
        entry = skill_eval.record(prepared["id"])
        self.assertEqual((entry["host"], entry["previous"]), ("codex", None))  # Claude's run is another host
        rows = [row for row in skill_eval.report()["cases"] if row["case"] == "setup-app-id"]
        self.assertEqual(sorted(row["host"] for row in rows), ["claude", "codex"])
        self.assertEqual([row["host"] for row in skill_eval.report("codex")["cases"] if row["case"] == "setup-app-id"], ["codex"])


if __name__ == "__main__":
    unittest.main()
