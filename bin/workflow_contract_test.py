"""Static integration checks for the installed workflow's public contracts."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE_NAMES = ("designer", "implementer", "test-writer", "feature-reviewer",
              "device-pass", "project-explorer", "workspace-agent", "ship-agent", "pr-author")


class WorkflowContractTests(unittest.TestCase):
    def files(self):
        return [ROOT / "ANDROID.md", ROOT / "workflows/feature-workflow.md",
                ROOT / "workflows/workflow-setup.md",
                *sorted((ROOT / "workflows/feature-workflow").glob("*.md")),
                *(ROOT / "agents" / (name + ".md") for name in ROLE_NAMES)]

    def test_local_markdown_links_resolve(self):
        for file in self.files():
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", file.read_text()):
                if target.startswith(("https:", "http:", "#", "~")):
                    continue
                target = target.split("#")[0]
                self.assertTrue((file.parent / target).exists(), (file, target))

    def test_active_contracts_are_compact(self):
        self.assertLessEqual(len((ROOT / "workflows/feature-workflow.md").read_text().splitlines()), 180)
        for name in ROLE_NAMES:
            self.assertLessEqual(len((ROOT / "agents" / (name + ".md")).read_text().splitlines()), 75)

    def test_retired_execution_model_not_present(self):
        for file in self.files():
            text = file.read_text()
            self.assertNotIn("Only Claude Code has real subagents", text)
            self.assertNotIn("Delivery proceeds with manual reviewer checklist", text)
            self.assertNotIn("explora o código", text)
            self.assertNotIn("navegação no device", text)

    def test_positive_manual_receipt_examples_bind_start_fingerprint(self):
        for file in self.files():
            for line in file.read_text().splitlines():
                if line.startswith("python3 ") and "feature_state.py" in line:
                    self.assertIn("--repo-root REPO --base PINNED_BASE_SHA", line, (file, line))
                    if " record " in line and re.search(r"--status (PASS|NOT_REQUIRED)\b", line):
                        self.assertIn("--expected-fingerprint START_FP", line, (file, line))

    def test_setup_help_does_not_require_manual_bootstrap(self):
        text = (ROOT / "workflows/workflow-help.md").read_text()
        self.assertNotIn("run `/workflow-setup` first", text)


if __name__ == "__main__":
    unittest.main()
