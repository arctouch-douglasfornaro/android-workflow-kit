"""Android skills: the CLI picks what each agent reads, from the ticket and the code, without being asked."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"

import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from android_workflow import office, skills
from android_workflow.cli import main, read_json
from android_workflow.paths import KIT_ROOT, run_dir
from test_live_team_emulator import GitProject

COMPOSE = "package com.example\nimport androidx.compose.runtime.Composable\n@Composable fun ProfileScreen() {}\n"


def start(root: Path, title: str, *extra: str) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        main(["start", "--target", str(root), "--id", "K-1", "--title", title, "--type", "feature", *extra])


def chosen(root: Path, role: str = "implementer") -> list[str]:
    return [item["name"] for item in read_json(run_dir(root) / "skills.json")[role]]


def project_skill(root: Path, name: str, description: str, body: str = "Real content.\n", pointer: bool = True,
                  extra: str = "") -> None:
    real = root / ".ai/skills" / name / "SKILL.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(f"# {name}\n\n{body}", encoding="utf-8")
    if pointer:
        wrapper = root / ".claude/skills" / name / "SKILL.md"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n> The full skill content lives in "
                           f"the tool-agnostic copy: [`.ai/skills/{name}/SKILL.md`](../../../.ai/skills/{name}/SKILL.md)\n",
                           encoding="utf-8")


class KitSkillTests(unittest.TestCase):
    def test_the_catalog_matches_the_skill_folders_and_every_skill_has_frontmatter(self) -> None:
        catalog = json.loads((KIT_ROOT / "skills/android/catalog.json").read_text(encoding="utf-8"))["skills"]
        folders = {path.name for path in (KIT_ROOT / "skills/android").iterdir() if path.is_dir()}
        self.assertEqual(set(catalog), folders)
        for name in folders:
            text = (KIT_ROOT / "skills/android" / name / "SKILL.md").read_text(encoding="utf-8")
            meta = skills._frontmatter(text)
            self.assertEqual(meta.get("name"), name)
            self.assertTrue(meta.get("description"), name)
            for retired in ("/workflow-setup", "00-capabilities", "Phase 0", "device-pass"):
                self.assertNotIn(retired, text, (name, retired))
            self.assertNotRegex(text.lower(), r"quizlet|assembly", name)  # the kit is public and app-neutral

    def test_every_host_links_the_kit_android_skills(self) -> None:
        for host in (".claude", ".codex", ".cursor", ".gemini", ".agents"):
            for name in json.loads((KIT_ROOT / "skills/android/catalog.json").read_text(encoding="utf-8"))["skills"]:
                link = KIT_ROOT / host / "skills" / name
                self.assertTrue(link.is_symlink(), (host, name))
                self.assertEqual(link.resolve(), (KIT_ROOT / "skills/android" / name).resolve())


class SelectionTests(unittest.TestCase):
    def test_a_compose_ticket_gets_the_compose_skills_without_anyone_asking(self) -> None:
        with GitProject() as root:
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(COMPOSE, encoding="utf-8")
            start(root, "Show empty state on ProfileScreen", "--surfaces", "ui")
            picked = read_json(run_dir(root) / "skills.json")
        names = [item["name"] for item in picked["implementer"]]
        self.assertIn("compose-android", names)
        self.assertIn("android-screenshots", names)
        self.assertNotIn("android-cli", names)  # a Device skill
        compose = next(item for item in picked["implementer"] if item["name"] == "compose-android")
        self.assertEqual(compose["source"], "kit")
        self.assertTrue(Path(compose["path"]).is_file())
        self.assertTrue(compose["why"])
        self.assertIn("android-screenshots", [item["name"] for item in picked["device"]])

    def test_jank_in_a_compose_list_adds_the_performance_skills(self) -> None:
        with GitProject() as root:
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(
                COMPOSE + "@Composable fun Rows() { LazyColumn {} }\n", encoding="utf-8")
            start(root, "Fix jank when scrolling ProfileScreen")
            names = chosen(root)
        self.assertIn("compose-performance", names)
        self.assertIn("android-performance", names)

    def test_the_apps_own_skills_win_and_process_skills_are_left_out(self) -> None:
        with GitProject() as root:
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(COMPOSE, encoding="utf-8")
            project_skill(root, "compose-rules", "Compose conventions for this app. Use when writing Compose UI.")
            project_skill(root, "unit-testing", "Unit testing conventions. Use when writing tests.")
            project_skill(root, "assembly-modal-dialog", "Using AssemblyModalDialog for confirmations.")
            project_skill(root, "jira-ticket-creator", "Creates a Jira ticket.")
            project_skill(root, "weekly-report", "A weekly report.", extra="disable-model-invocation: true\n")
            start(root, "Add a confirmation dialog to ProfileScreen")
            picked = read_json(run_dir(root) / "skills.json")
        names = [item["name"] for item in picked["implementer"]]
        self.assertEqual(set(names[:3]), {"assembly-modal-dialog", "compose-rules", "unit-testing"})  # the app's own first
        self.assertNotIn("compose-android", names)  # compose-rules covers Compose for this app
        self.assertNotIn("jira-ticket-creator", names)
        self.assertNotIn("weekly-report", names)
        rules = next(item for item in picked["implementer"] if item["name"] == "compose-rules")
        self.assertEqual((rules["source"], rules["path"]), ("project", ".ai/skills/compose-rules/SKILL.md"))
        self.assertIn("Compose conventions for this app", rules["description"])
        self.assertIn("dialog", " ".join(next(i for i in picked["implementer"] if i["name"] == "assembly-modal-dialog")["why"]))

    def test_the_code_an_implementer_already_changed_refreshes_the_list(self) -> None:
        with GitProject() as root:
            project_skill(root, "localization-strings", "Adding Android strings so every locale can translate them.")
            start(root, "Tweak ProfileScreen")
            before = chosen(root)
            values = root / "app/src/main/res/values/strings.xml"
            values.parent.mkdir(parents=True)
            values.write_text("<resources><string name=\"empty\">Nothing yet</string></resources>\n", encoding="utf-8")
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(COMPOSE, encoding="utf-8")
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "started"])
            after = chosen(root)
        self.assertNotIn("localization-strings", before)
        self.assertIn("localization-strings", after)
        self.assertIn("compose-android", after)

    def test_a_failing_format_step_brings_the_formatter_skill(self) -> None:
        with GitProject() as root:
            start(root, "Tweak ProfileScreen")
            gate = run_dir(root) / "gate-report.json"
            report = read_json(gate)
            report["steps"] = [{"command": "./gradlew :app:lintKotlin", "outcome": "failed"}]
            gate.write_text(json.dumps(report), encoding="utf-8")
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "started", "--note", "fix round"])
            names = chosen(root)
        self.assertIn("ktlint-fixer", names)

    def test_at_most_the_configured_number_and_the_planner_can_correct_the_list(self) -> None:
        with GitProject() as root:
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(
                COMPOSE + "@Composable fun Rows() { LazyColumn {} }\nval x = WindowInsets.safeDrawing\n", encoding="utf-8")
            (root / ".ai").mkdir(exist_ok=True)
            (root / ".ai/android-workflow.json").write_text(json.dumps({"skills": {"max": 2}}), encoding="utf-8")
            start(root, "Fix jank and insets on ProfileScreen navigation", "--surfaces", "ui")
            limited = chosen(root)
            main(["skills", "--target", str(root), "--drop", limited[0], "--add", "android-cli"])
            corrected = chosen(root)
            main(["log", "--target", str(root), "--stage", "Implementer", "--status", "started"])
            kept = chosen(root)
            code = main(["skills", "--target", str(root), "--add", "no-such-skill"])
        self.assertEqual(len(limited), 2)
        self.assertNotIn(limited[0], corrected)
        self.assertIn("android-cli", corrected)  # the Planner's choice, even outside the limit
        self.assertEqual(corrected, kept)  # a refresh keeps the manual choices
        self.assertEqual(code, 2)

    def test_the_implementer_panel_shows_the_skills(self) -> None:
        with GitProject() as root:
            start(root, "Tweak ProfileScreen")
            data = office.collect(root)
            html = office.PAGE
        self.assertIn("skills.json", [item["name"] for item in data["artifacts"]["Implementer"]])
        self.assertIn('"skills.json": k =>', html)

    def test_a_skill_problem_never_fails_start(self) -> None:
        with GitProject() as root:
            broken = root / ".ai/skills/broken/SKILL.md"
            broken.parent.mkdir(parents=True)
            broken.write_bytes(b"\xff\xfe---\nname: [\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["start", "--target", str(root), "--id", "K-2", "--title", "Tweak ProfileScreen", "--type", "feature"])
            exists = (run_dir(root) / "skills.json").exists()
        self.assertEqual(code, 0)
        self.assertTrue(exists)


if __name__ == "__main__":
    unittest.main()
