from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("ANDROID_WORKFLOW_NO_WATCH", "1")

from android_workflow import review_rules
from android_workflow.decide import gate_failures

RULE = """id: runcatching-in-coroutine
language: kotlin
severity: {severity}
message: runCatching also catches CancellationException
rule:
  pattern: runCatching {{ $$$ }}
  inside:
    kind: function_declaration
    regex: '^suspend '
    stopBy: end
"""

OLD = """class Repo {
    suspend fun old() = runCatching { load() }
}
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True).stdout


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "app"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        (self.root / "src").mkdir()
        (self.root / "src/Repo.kt").write_text(OLD)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD").strip()
        self.home = Path(self.tmp.name) / "home-rules"
        patcher = mock.patch.dict(os.environ, {"AW_REVIEW_RULES_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def rule(self, severity: str = "warning", folder: Path | None = None) -> Path:
        folder = folder or self.root / review_rules.RULES_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "runcatching-in-coroutine.yml"
        path.write_text(RULE.format(severity=severity))
        return path


class AddedLinesTests(RepoCase):
    def test_only_the_lines_the_change_adds_and_untracked_files_whole(self) -> None:
        (self.root / "src/Repo.kt").write_text(OLD[:-2] + "    fun more() = 1\n}\n")
        (self.root / "src/New.kt").write_text("class New\nclass Two\n")
        (self.root / "notes.md").write_text("not kotlin\n")
        added = review_rules.added_lines(self.root, self.base)
        self.assertEqual(added["src/Repo.kt"], {3})
        self.assertEqual(added["src/New.kt"], {1, 2})
        self.assertNotIn("notes.md", added)


class CheckTests(RepoCase):
    def test_no_rules_skips(self) -> None:
        self.assertEqual(review_rules.check(self.root, self.base)["status"], "skipped")

    def test_no_ast_grep_skips_and_never_fails(self) -> None:
        self.rule("error")
        (self.root / "src/New.kt").write_text("suspend fun x() = runCatching { 1 }\n")
        with mock.patch.object(review_rules, "binary", return_value=None):
            result = review_rules.check(self.root, self.base)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("ast-grep", result["reason"])

    def test_a_broken_check_is_skipped_in_the_gate(self) -> None:
        with mock.patch.object(review_rules, "check", side_effect=RuntimeError("boom")):
            self.assertEqual(review_rules.check_safely(self.root, self.base)["status"], "skipped")

    def test_app_rule_overrides_the_global_one_of_the_same_name(self) -> None:
        self.rule("warning", self.home)
        app = self.rule("error")
        self.assertEqual([path.resolve() for path in review_rules.rule_files(self.root)], [app.resolve()])
        self.assertEqual([row["scope"] for row in review_rules.listing(self.root)], ["app"])
        self.assertEqual(review_rules.listing(self.root)[0]["mode"], "block")


@unittest.skipUnless(shutil.which("ast-grep"), "ast-grep is not installed")
class ScanTests(RepoCase):
    def test_warn_reports_only_new_code(self) -> None:
        self.rule("warning")
        (self.root / "src/Repo.kt").write_text(OLD[:-2] + "    suspend fun fresh() = runCatching { load() }\n}\n")
        result = review_rules.check(self.root, self.base)
        self.assertEqual(result["status"], "passed")
        self.assertEqual([(f["file"], f["line"], f["mode"]) for f in result["findings"]], [("src/Repo.kt", 3, "warn")])

    def test_block_fails_and_untouched_code_never_trips(self) -> None:
        self.rule("error")
        (self.root / "src/Other.kt").write_text("fun plain() = runCatching { 1 }\n")
        self.assertEqual(review_rules.check(self.root, self.base)["findings"], [])
        (self.root / "src/New.kt").write_text("suspend fun x() = runCatching { 1 }\n")
        result = review_rules.check(self.root, self.base)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["findings"][0]["mode"], "block")

    def test_a_broken_rule_is_an_error_not_a_failure(self) -> None:
        folder = self.root / review_rules.RULES_DIR
        folder.mkdir(parents=True)
        (folder / "bad.yml").write_text("id: bad\nlanguage: kotlin\nrule:\n  nonsense: [\n")
        (self.root / "src/New.kt").write_text("class New\n")
        result = review_rules.check(self.root, self.base)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["errors"])

    def test_test_counts_matches_over_the_app(self) -> None:
        result = review_rules.test(self.root, self.rule())
        self.assertEqual((result["matches"], result["files"], result["examples"]), (1, 1, ["src/Repo.kt:2"]))


class PatternTests(RepoCase):
    def write_review(self, run: str, review: dict, archived: bool = False) -> None:
        folder = self.root / ".ai/workflow" / ("_archive" if archived else "") / run
        folder.mkdir(parents=True)
        (folder / "review.json").write_text(json.dumps(review))

    def test_terms_shared_by_runs_become_patterns(self) -> None:
        self.write_review("LT-1", {"concerns": ["`runCatching` swallows CancellationException in load()"]})
        self.write_review("PR-2", {"blocking": [{"id": "B1", "file": "a/Vm.kt", "line": 4,
                                                 "problem": "runCatching around a suspend call hides cancel"}]},
                          archived=True)
        self.write_review("LT-3", {"concerns": [{"file": "a/Vm.kt:9", "problem": "Double tap sends exit twice"}],
                                   "suggestions": ["Rename the helper"]})
        self.rule()
        result = review_rules.patterns(self.root, 2)
        self.assertEqual((result["reviews"], result["findings"]), (3, 4))
        by_term = {term: group for group in result["patterns"] for term in group["terms"]}
        self.assertEqual(by_term["runCatching"]["runs"], ["LT-1", "PR-2"])
        self.assertEqual(by_term["runCatching"]["covered_by"], ["runcatching-in-coroutine"])
        self.assertEqual(by_term["Vm.kt"]["runs"], ["LT-3", "PR-2"])
        self.assertNotIn("CancellationException", by_term)  # one run only
        self.assertEqual(review_rules.patterns(self.root, 3)["patterns"], [])

    def test_ids_and_plain_words_are_not_terms(self) -> None:
        self.assertEqual(review_rules.terms("AC1 and C2 fail; the Device must rerun", None), set())


class GateTests(unittest.TestCase):
    def test_a_blocking_rule_is_its_own_gate_failure(self) -> None:
        gate = {"status": "failed", "steps": [], "review_rules": {"status": "failed"}}
        self.assertEqual(gate_failures(gate), ["G-rules"])


if __name__ == "__main__":
    unittest.main()
