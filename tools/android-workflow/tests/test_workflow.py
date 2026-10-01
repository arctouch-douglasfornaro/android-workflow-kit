import json
import shutil
import tempfile
import unittest
from pathlib import Path

from android_workflow.cli import (
    bootstrap,
    build_project_config,
    configure,
    find_pr_template,
    finish_run,
    main,
    read_json,
    resume,
    route_for,
    run,
    strip_agent_attribution,
    source_fingerprint,
    validate_artifact,
)
from android_workflow.paths import KIT_ROOT, SKILL_DIR, cache_dir, run_dir


def artifacts(root: Path, ticket_id: str) -> Path:
    return run_dir(root, ticket_id)


def approve_review(root: Path) -> int:
    (run_dir(root) / "review.json").write_text(
        json.dumps({"schema_version": 1, "status": "approved", "blocking": [], "concerns": [], "suggestions": []}),
        encoding="utf-8",
    )
    return main(["log", "--target", str(root), "--stage", "T6", "--status", "completed", "--note", "approved"])


def pass_gate(root: Path) -> None:
    """A green gate for the current source, for tests about what happens after the gate."""
    path = run_dir(root) / "gate-report.json"
    report = read_json(path)
    report.update({"status": "passed", "fingerprint": source_fingerprint(root)})
    path.write_text(json.dumps(report), encoding="utf-8")


def finish_verified(root: Path, **kwargs: object) -> dict:
    pass_gate(root)
    return finish_run(root, **kwargs)


def implement(root: Path, body: str = "fun empty() = Unit\n") -> str:
    relative = "app/src/main/java/com/example/ProfileScreen.kt"
    (root / relative).write_text(f"package com.example\nclass ProfileScreen\n{body}", encoding="utf-8")
    (run_dir(root) / "implementation-notes.md").write_text(
        "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
        encoding="utf-8",
    )
    main(["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative])
    return relative

_TMP = Path(__file__).resolve().parent / "_tmp"


class AndroidProject:
    def __enter__(self) -> Path:
        _TMP.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=_TMP)
        root = Path(self.temp.name)
        (root / "settings.gradle.kts").write_text(
            'include(":app", ":core:domain")\n', encoding="utf-8"
        )
        (root / "gradle/wrapper").mkdir(parents=True)
        (root / "gradle/wrapper/gradle-wrapper.properties").write_text(
            "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10-bin.zip\n",
            encoding="utf-8",
        )
        (root / "app/src/main/java/com/example").mkdir(parents=True)
        (root / "app/src/test/java/com/example").mkdir(parents=True)
        (root / "app/build.gradle.kts").write_text(
            """
            plugins {
              id("com.android.application") version "8.7.0"
              id("org.jetbrains.kotlin.android") version "2.0.20"
            }
            android { compileSdk = 35; defaultConfig { minSdk = 24; targetSdk = 35 } }
            dependencies { implementation(project(":core:domain")) }
            """,
            encoding="utf-8",
        )
        (root / "core/domain").mkdir(parents=True)
        (root / "core/domain/build.gradle.kts").write_text("", encoding="utf-8")
        (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text(
            "package com.example\nclass ProfileScreen\n", encoding="utf-8"
        )
        (root / "app/src/test/java/com/example/ProfileScreenTest.kt").write_text(
            "package com.example\nclass ProfileScreenTest\n", encoding="utf-8"
        )
        return root

    def __exit__(self, *_: object) -> None:
        self.temp.cleanup()


class DetectionTests(unittest.TestCase):
    def test_detects_versions_modules_graph_and_tests(self) -> None:
        with AndroidProject() as root:
            config = build_project_config(root)

        detected = config["detected"]
        self.assertEqual(detected["versions"]["gradle"], "8.10")
        self.assertEqual(detected["versions"]["agp"], "8.7.0")
        self.assertEqual(detected["versions"]["kotlin"], "2.0.20")
        self.assertEqual(detected["modules"], [":app", ":core:domain"])
        self.assertEqual(detected["module_graph"][":app"], [":core:domain"])
        self.assertEqual(detected["tests"]["unit_roots"], ["app/src/test"])

    def test_overrides_replace_detected_defaults_without_losing_siblings(self) -> None:
        with AndroidProject() as root:
            overrides = root / "overrides.json"
            overrides.write_text(
                json.dumps({"commands": {"build": "./gradlew customBuild"}, "device": {"application_id": "com.example"}}),
                encoding="utf-8",
            )
            config = configure(root, overrides)

        self.assertEqual(config["commands"]["build"], "./gradlew customBuild")
        self.assertEqual(config["commands"]["unit_tests"], "./gradlew testDebugUnitTest")
        self.assertEqual(config["device"]["application_id"], "com.example")


class SchemaTests(unittest.TestCase):
    def test_missing_required_field_is_reported(self) -> None:
        schema = SKILL_DIR / "artifacts.schema.json"
        errors = validate_artifact("ticket-spec.json", {"schema_version": 1}, schema)
        self.assertIn("missing required field: ticket", errors)

class RoutingTests(unittest.TestCase):
    def test_plan_and_device_are_conditional(self) -> None:
        simple = route_for("feature", "low", "low", ["domain"])
        risky_ui = route_for("bug", "low", "low", ["ui"])
        self.assertNotIn("T3", simple)
        self.assertNotIn("T7", simple)
        self.assertIn("T3", risky_ui)
        self.assertIn("T7", risky_ui)

    def test_bug_without_reproduction_stops_in_plan(self) -> None:
        with AndroidProject() as root:
            state = run(root, {"id": "BUG-1", "title": "Profile crash", "type": "bug"})

        self.assertEqual(state["status"], "escalated")
        self.assertEqual(state["stages"]["T3"]["reason"], "bug_reproduction_missing")

    def test_run_localizes_once_and_stops_for_the_host(self) -> None:
        with AndroidProject() as root:
            state = run(
                root,
                {
                    "id": "APP-1",
                    "title": "Change ProfileScreen",
                    "type": "feature",
                    "acceptance_criteria": ["Profile is updated"],
                },
            )
            change_set = read_json(artifacts(root, "APP-1") / "change-set-map.json")
            log = (artifacts(root, "APP-1") / "stage-log.md").read_text(encoding="utf-8")
            schema = SKILL_DIR / "artifacts.schema.json"
            spec_errors = validate_artifact(
                "ticket-spec.json", read_json(artifacts(root, "APP-1") / "ticket-spec.json"), schema,
            )

        self.assertEqual(state["status"], "awaiting_host")
        self.assertEqual(state["current_stage"], "T4")
        self.assertEqual(state["stages"]["T4"]["reason"], "host_must_implement")
        self.assertEqual(change_set["candidate_files"][0]["path"], "app/src/main/java/com/example/ProfileScreen.kt")
        self.assertIn("| Implementer | awaiting_host |", log)
        self.assertNotIn("(T4)", log)
        self.assertEqual(spec_errors, [])

    def test_business_question_pauses_and_resume_uses_answer(self) -> None:
        with AndroidProject() as root:
            state = run(
                root,
                {
                    "id": "APP-2",
                    "title": "Apply eligibility",
                    "type": "feature",
                    "business_questions": [
                        {"id": "eligibility", "question": "What is the eligibility rule?"}
                    ],
                },
            )
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["pending_question"]["id"], "eligibility")

            resumed = resume(root, "eligibility", "Users with an active contract")
            persisted = read_json(artifacts(root, "APP-2") / "run-state.json")
            persisted_spec = read_json(artifacts(root, "APP-2") / "ticket-spec.json")

        self.assertEqual(resumed["status"], "awaiting_host")
        self.assertEqual(persisted["answers"]["eligibility"], "Users with an active contract")
        self.assertEqual(
            persisted_spec["business_questions"][0]["answer"], "Users with an active contract"
        )

class HostDrivenTests(unittest.TestCase):
    def test_finish_rejects_stub_without_source_diff(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-5", "title": "Change ProfileScreen", "type": "chore"})
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
        self.assertIn("Implementer incomplete", str(raised.exception))

    def test_host_implements_then_finish(self) -> None:
        with AndroidProject() as root:
            state = run(root, {"id": "APP-6", "title": "Change ProfileScreen", "type": "chore"})
            self.assertEqual(state["status"], "awaiting_host")
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen\nfun empty() = Unit\n", encoding="utf-8")
            (artifacts(root, "APP-6") / "implementation-notes.md").write_text(
                "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
                encoding="utf-8",
            )
            relative = "app/src/main/java/com/example/ProfileScreen.kt"
            log_code = main(
                ["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative]
            )
            approve_review(root)
            pass_gate(root)
            code = main(["finish", "--target", str(root)])
            finished = read_json(artifacts(root, "APP-6") / "run-state.json")
            log = (artifacts(root, "APP-6") / "stage-log.md").read_text(encoding="utf-8")
            pr_body = (artifacts(root, "APP-6") / "pr-description.md").read_text(encoding="utf-8")

        self.assertEqual(log_code, 0)
        self.assertEqual(code, 0)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["stages"]["T4"]["status"], "completed")
        self.assertIn("ProfileScreen.kt", "".join(finished["stages"]["T4"]["files"]))
        self.assertIn("| Implementer | completed |", log)
        self.assertNotRegex(log, r"\bT\d\b")
        self.assertIn("Change ProfileScreen", pr_body)
        self.assertNotIn("Verification not performed", pr_body)
        self.assertNotIn("Reviewer notes", pr_body)

    def test_every_host_points_at_the_source_of_truth(self) -> None:
        import tomllib

        roles = {}
        for path in sorted((SKILL_DIR / "agents").glob("aw-*.md")):
            header = path.read_text(encoding="utf-8").split("---\n")[1]
            meta = {}
            for line in header.splitlines():
                key, value = line.split(":", 1)
                value = value.strip()
                meta[key] = json.loads(value) if value.startswith('"') else value
            roles[meta["name"]] = meta
        self.assertEqual(set(roles), {"aw-setup", "aw-planner", "aw-implementer", "aw-reviewer", "aw-device", "aw-delivery"})
        models = {"strong": "opus", "standard": "sonnet", "fast": "haiku"}
        efforts = {"strong": "high", "standard": "medium", "fast": "low"}
        for host in (".claude", ".cursor", ".gemini"):
            self.assertEqual({path.stem for path in (KIT_ROOT / host / "agents").glob("*.md")}, set(roles), host)
        self.assertEqual({path.stem for path in (KIT_ROOT / ".codex/agents").glob("*.toml")}, set(roles))
        for name, meta in roles.items():
            source = f"~/.ai/skills/android-workflow/agents/{name}.md"
            for host in (".claude", ".cursor", ".gemini"):
                text = (KIT_ROOT / host / "agents" / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn(f"name: {name}\n", text)
                self.assertIn(f"description: {json.dumps(meta['description'], ensure_ascii=False)}\n", text, (host, name))
                self.assertIn(source, text)
            claude = (KIT_ROOT / ".claude/agents" / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn(f"model: {models[meta['tier']]}\n", claude)
            self.assertIn(f"tools: {meta['tools']}\n", claude)
            self.assertIn("kind: local", (KIT_ROOT / ".gemini/agents" / f"{name}.md").read_text(encoding="utf-8"))
            codex = tomllib.loads((KIT_ROOT / ".codex/agents" / f"{name}.toml").read_text(encoding="utf-8"))
            self.assertEqual((codex["name"], codex["description"]), (name, meta["description"]))
            self.assertEqual(codex["model_reasoning_effort"], efforts[meta["tier"]])
            self.assertIn(source, codex["developer_instructions"])
        for host in (".claude", ".codex", ".cursor", ".gemini", ".agents"):
            for skill in ("android-workflow", "device-driving"):
                link = KIT_ROOT / host / "skills" / skill
                self.assertTrue(link.is_symlink(), (host, skill))
                self.assertEqual(link.resolve(), (KIT_ROOT / "skills" / skill).resolve())
                self.assertTrue((link / "SKILL.md").is_file())

    def test_role_frontmatter_is_valid_yaml_scalars(self) -> None:
        for path in sorted((SKILL_DIR / "agents").glob("aw-*.md")):
            header = path.read_text(encoding="utf-8").split("---\n")[1]
            for line in header.splitlines():
                key, value = line.split(":", 1)
                value = value.strip()
                if ": " in value or value.startswith(("'", "{", "[")):
                    self.assertTrue(value.startswith('"'), (path.name, key))
                    json.loads(value)


class RunLayoutTests(unittest.TestCase):
    def test_bootstrap_writes_cache_and_gitignore(self) -> None:
        with AndroidProject() as root:
            bootstrap(root)
            ignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertTrue((cache_dir(root) / "env.json").exists())
            self.assertTrue((cache_dir(root) / "repo-map.json").exists())
            self.assertIn(".ai/workflow/", ignore)

    def test_second_ticket_keeps_first_run(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-1", "title": "One", "type": "chore"})
            run(root, {"id": "APP-2", "title": "Two", "type": "chore"})
            first = read_json(artifacts(root, "APP-1") / "ticket-spec.json")
            second = read_json(artifacts(root, "APP-2") / "ticket-spec.json")
            pointer = read_json(root / ".ai/workflow/current.json")
        self.assertEqual(first["ticket"]["id"], "APP-1")
        self.assertEqual(second["ticket"]["id"], "APP-2")
        self.assertEqual(pointer["ticket_id"], "APP-2")

    def test_clean_removes_one_ticket(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-1", "title": "One", "type": "chore"})
            run(root, {"id": "APP-2", "title": "Two", "type": "chore"})
            code = main(["clean", "--target", str(root), "--ticket", "APP-1"])
            self.assertEqual(code, 0)
            self.assertFalse(artifacts(root, "APP-1").exists())
            self.assertTrue((artifacts(root, "APP-2") / "ticket-spec.json").exists())

    def test_locate_skips_idea_and_weak_words(self) -> None:
        with AndroidProject() as root:
            (root / "generated").mkdir()
            (root / "generated/workspace.xml").write_text(
                "<project>reminder notification daily icon correct small</project>\n",
                encoding="utf-8",
            )
            (root / "app/src/main/java/com/example/Notifications.kt").write_text(
                "package com.example\nclass Notifications\n",
                encoding="utf-8",
            )
            run(
                root,
                {
                    "id": "N-1",
                    "title": "Smarter daily reminder notification + correct small icon",
                    "type": "chore",
                },
            )
            change_set = read_json(artifacts(root, "N-1") / "change-set-map.json")
            spec = read_json(artifacts(root, "N-1") / "ticket-spec.json")
            paths = [item["path"] for item in change_set["candidate_files"]]
            self.assertNotIn("generated/workspace.xml", paths)
            self.assertTrue(any("Notifications.kt" in path for path in paths))
            self.assertLessEqual(len(paths), 12)
            self.assertIn("notifications", spec["surfaces"])
            self.assertIn("T7", spec["route"])

    def test_finish_keeps_host_pr_body(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-7", "title": "Change ProfileScreen", "type": "chore"})
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen\nfun empty() = Unit\n", encoding="utf-8")
            folder = artifacts(root, "APP-7")
            (folder / "implementation-notes.md").write_text(
                "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
                encoding="utf-8",
            )
            (folder / "pr-description.md").write_text(
                "# Summary\n\nReal summary\n\n# Why\n\nBecause the daily copy was stale.\n\n"
                "# What changed\n\n- ReminderPlanner owns delivery copy.\n\n"
                "# Verification performed\n\n- Host tests.\n\n# Verification not performed\n\n- iOS device.\n\n"
                "# Visual evidence\n\nSee device-report.md.\n\n# Risks and assumptions\n\n- UTC daily roll.\n\n"
                "# Reviewer notes\n\n- Check timezone tests.\n",
                encoding="utf-8",
            )
            relative = "app/src/main/java/com/example/ProfileScreen.kt"
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative])
            approve_review(root)
            pass_gate(root)
            code = main(["finish", "--target", str(root)])
            body = (folder / "pr-description.md").read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("ReminderPlanner owns delivery copy", body)
            self.assertNotIn("See the diff and `implementation-notes.md`", body)

    def test_finish_keeps_short_host_body_without_invented_headings(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-10", "title": "Change ProfileScreen", "type": "chore"})
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen\nfun empty() = Unit\n", encoding="utf-8")
            folder = artifacts(root, "APP-10")
            (folder / "implementation-notes.md").write_text(
                "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
                encoding="utf-8",
            )
            (folder / "pr-description.md").write_text(
                "Empty profile shows guidance instead of a blank list.\n",
                encoding="utf-8",
            )
            relative = "app/src/main/java/com/example/ProfileScreen.kt"
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative])
            approve_review(root)
            pass_gate(root)
            code = main(["finish", "--target", str(root)])
            body = (folder / "pr-description.md").read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("Empty profile shows guidance", body)
            self.assertNotIn("# Summary", body)
            self.assertNotIn("Verification performed", body)

    def test_finish_keeps_repo_pr_template_headings(self) -> None:
        with AndroidProject() as root:
            (root / ".github").mkdir()
            (root / ".github/PULL_REQUEST_TEMPLATE.md").write_text(
                "## What\n\n\n## Test plan\n\n- [ ] Unit tests\n",
                encoding="utf-8",
            )
            run(root, {"id": "APP-11", "title": "Change ProfileScreen", "type": "chore"})
            spec = read_json(artifacts(root, "APP-11") / "ticket-spec.json")
            self.assertEqual(spec["pr_template"], ".github/PULL_REQUEST_TEMPLATE.md")
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen\nfun empty() = Unit\n", encoding="utf-8")
            folder = artifacts(root, "APP-11")
            (folder / "implementation-notes.md").write_text(
                "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
                encoding="utf-8",
            )
            (folder / "pr-description.md").write_text(
                "## What\n\nEmpty profile shows guidance.\n\n## Test plan\n\n- [x] Unit tests\n",
                encoding="utf-8",
            )
            relative = "app/src/main/java/com/example/ProfileScreen.kt"
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative])
            approve_review(root)
            pass_gate(root)
            code = main(["finish", "--target", str(root)])
            body = (folder / "pr-description.md").read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("## What", body)
            self.assertIn("## Test plan", body)
            self.assertNotIn("# Why", body)
            self.assertNotIn("Reviewer notes", body)

    def test_finds_github_pr_template(self) -> None:
        with AndroidProject() as root:
            (root / ".github").mkdir()
            (root / ".github/PULL_REQUEST_TEMPLATE.md").write_text("## What\n", encoding="utf-8")
            self.assertEqual(find_pr_template(root), ".github/PULL_REQUEST_TEMPLATE.md")

    def test_finish_strips_agent_attribution_from_host_pr_body(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "APP-9", "title": "Change ProfileScreen", "type": "chore"})
            screen = root / "app/src/main/java/com/example/ProfileScreen.kt"
            screen.write_text("package com.example\nclass ProfileScreen\nfun empty() = Unit\n", encoding="utf-8")
            folder = artifacts(root, "APP-9")
            (folder / "implementation-notes.md").write_text(
                "# Decisions\n\nEmpty state helper.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
                encoding="utf-8",
            )
            (folder / "pr-description.md").write_text(
                "# Summary\n\nReal summary\n\n# Why\n\nBecause the daily copy was stale.\n\n"
                "# What changed\n\n- ReminderPlanner owns delivery copy.\n\n"
                "# Verification performed\n\n- Host tests.\n\n# Verification not performed\n\n- iOS device.\n\n"
                "# Visual evidence\n\nSee device-report.md.\n\n# Risks and assumptions\n\n- UTC daily roll.\n\n"
                "# Reviewer notes\n\n- Check timezone tests.\n\n"
                "Made with Cloud Code\n"
                "Co-Authored-By: Cloud Code <cloudcode@google.com>\n"
                "🤖 Generated with Claude Code\n",
                encoding="utf-8",
            )
            relative = "app/src/main/java/com/example/ProfileScreen.kt"
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed", "--file", relative])
            approve_review(root)
            pass_gate(root)
            code = main(["finish", "--target", str(root)])
            body = (folder / "pr-description.md").read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("ReminderPlanner owns delivery copy", body)
            self.assertNotIn("Cloud Code", body)
            self.assertNotIn("Co-Authored-By", body)
            self.assertNotIn("Generated with", body)
            self.assertNotIn("Claude Code", body)


class AttributionTests(unittest.TestCase):
    def test_strips_agent_footers_keeps_product_prose(self) -> None:
        text = (
            "ReminderPlanner owns delivery copy.\n"
            "The enum file is generated with buildSrc.\n"
            "Made with Cloud Code\n"
            "Co-Authored-By: Gemini <gemini@google.com>\n"
            "Co-Authored-By: Jane Doe <jane@example.com>\n"
            "Generated with [Claude Code](https://claude.com/claude-code)\n"
        )
        out = strip_agent_attribution(text)
        self.assertIn("ReminderPlanner owns delivery copy", out)
        self.assertIn("generated with buildSrc", out)
        self.assertIn("Jane Doe", out)
        self.assertNotIn("Cloud Code", out)
        self.assertNotIn("Gemini", out)
        self.assertNotIn("Claude Code", out)


class EvidenceTests(unittest.TestCase):
    PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )

    def test_ingest_and_compare_before_with_after(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "NOTIF-1", "title": "Reminder", "type": "chore"})
            shot = root / "shade.png"
            shot.write_bytes(self.PNG)
            ingest = main(
                [
                    "evidence", "ingest", "--target", str(root),
                    "--phase", "before", "--name", "shade", "--file", str(shot),
                ]
            )
            after = main(
                [
                    "evidence", "ingest", "--target", str(root),
                    "--phase", "after", "--name", "shade", "--file", str(shot),
                ]
            )
            compare = main(
                ["evidence", "compare", "--target", str(root)]
            )
            before = artifacts(root, "NOTIF-1") / "media/before/shade.png"
            table = (artifacts(root, "NOTIF-1") / "media/compare.md").read_text(encoding="utf-8")
            self.assertEqual(ingest, 0)
            self.assertEqual(after, 0)
            self.assertEqual(compare, 0)
            self.assertTrue(before.is_file())
            self.assertIn("media/before/shade.png", table)
            self.assertIn("media/after/shade.png", table)

    def test_capture_writes_into_ticket_media(self) -> None:
        with AndroidProject() as root:
            fake = root / "fake-adb"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"PNG = bytes.fromhex({self.PNG.hex()!r})\n"
                "if 'devices' in sys.argv:\n"
                "    print('List of devices attached')\n"
                "    print('emulator-5554\\tdevice')\n"
                "    raise SystemExit(0)\n"
                "if 'screencap' in sys.argv:\n"
                "    sys.stdout.buffer.write(PNG)\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            overrides = root / "overrides.json"
            overrides.write_text(json.dumps({"tools": {"adb": str(fake)}}), encoding="utf-8")
            configure(root, overrides)
            run(root, {"id": "APP-8", "title": "Change ProfileScreen", "type": "chore"})
            code = main(
                [
                    "evidence", "capture", "--target", str(root),
                    "--phase", "before", "--name", "home",
                ]
            )
            shot = artifacts(root, "APP-8") / "media/before/home.png"
            self.assertEqual(code, 0)
            self.assertTrue(shot.is_file())
            self.assertTrue(shot.read_bytes().startswith(b"\x89PNG"))


class VerificationTests(unittest.TestCase):
    def test_finish_requires_an_approved_review(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
        self.assertIn("review.json is not approved", str(raised.exception))

    def test_approved_review_with_blocking_findings_is_not_approved(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-2", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            (run_dir(root) / "review.json").write_text(
                json.dumps({"schema_version": 1, "status": "approved", "blocking": [{"id": "B1"}],
                            "concerns": [], "suggestions": []}),
                encoding="utf-8",
            )
            main(["log", "--target", str(root), "--stage", "T6", "--status", "completed"])
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
        self.assertIn("not approved", str(raised.exception))

    def test_edit_after_review_makes_it_stale(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-3", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            approve_review(root)
            implement(root, "fun empty() = 1\n")
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
        self.assertIn("review is missing or stale", str(raised.exception))

    def test_edit_after_green_gate_makes_it_stale(self) -> None:
        true = shutil.which("true") or "/usr/bin/true"
        with AndroidProject() as root:
            overrides = root / "overrides.json"
            overrides.write_text(json.dumps({
                "commands": {"build": true, "unit_tests": true, "android_lint": true},
                "module_commands": {"compile": true, "unit_tests": true, "android_lint": true},
            }), encoding="utf-8")
            configure(root, overrides)
            run(root, {"id": "V-4", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            self.assertEqual(main(["gate", "--target", str(root)]), 0)
            implement(root, "fun empty() = 2\n")
            approve_review(root)
            with self.assertRaises(ValueError) as raised:
                finish_run(root)
        self.assertIn("gate is stale", str(raised.exception))

    def test_required_device_blocks_finish_unless_skipped_with_reason(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-5", "title": "Change ProfileScreen button", "type": "chore"})
            implement(root)
            approve_review(root)
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
            state = finish_verified(root, skip_device="no device connected")
        self.assertIn("device-report.md is not PASS", str(raised.exception))
        self.assertEqual(state["stages"]["T7"]["reason"], "no device connected")

    def test_visual_ticket_needs_after_media(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-6", "title": "Change ProfileScreen button", "type": "chore"})
            implement(root)
            approve_review(root)
            (run_dir(root) / "device-report.md").write_text(
                "# Status\n\nPASS\n\n# Device\n\nPixel 8, API 35\n\n# Scenarios\n\n"
                + "- AC1: expected button, observed button.\n" * 12
                + "\n# Evidence\n\n- media/after/profile.png\n\n# Not verified\n\n- None.\n",
                encoding="utf-8",
            )
            main(["log", "--target", str(root), "--stage", "T7", "--status", "completed"])
            with self.assertRaises(ValueError) as raised:
                finish_verified(root)
            after = run_dir(root) / "media/after"
            after.mkdir(parents=True)
            (after / "profile.png").write_bytes(b"png")
            state = finish_verified(root)
        self.assertIn("no media/after evidence", str(raised.exception))
        self.assertEqual(state["status"], "completed")

    def test_unresolved_checks_refuse_finish_and_point_at_draft(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-7", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            approve_review(root)
            path = run_dir(root) / "gate-report.json"
            path.write_text(json.dumps({**read_json(path), "status": "failed"}), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                finish_run(root)
            state = finish_run(root, draft="gate still red after two fixes")
            log = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
        self.assertIn("--draft", str(raised.exception))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["draft"]["reason"], "gate still red after two fixes")
        self.assertIn("quality gate is failing; see gate-report.json", state["draft"]["issues"])
        self.assertFalse(state["stages"]["T8"]["ready_for_review"])
        self.assertIn("| Delivery | draft |", log)

    def test_draft_without_open_issues_is_a_normal_finish(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-8", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            approve_review(root)
            state = finish_verified(root, draft="just in case")
        self.assertIsNone(state["draft"])
        self.assertTrue(state["stages"]["T8"]["ready_for_review"])

    def test_draft_still_needs_the_implementation(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "V-9", "title": "Change ProfileScreen", "type": "chore"})
            with self.assertRaises(ValueError) as raised:
                finish_run(root, draft="stopped")
        self.assertIn("Implementer incomplete", str(raised.exception))

    def test_surfaces_are_inferred_from_english_and_portuguese(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "S-1", "title": "Add a retry button to the profile screen"})
            run(root, {"id": "S-2", "title": "Adicionar botão na tela de perfil"})
            english = read_json(artifacts(root, "S-1") / "ticket-spec.json")
            portuguese = read_json(artifacts(root, "S-2") / "ticket-spec.json")
        for spec in (english, portuguese):
            self.assertIn("ui", spec["surfaces"])
            self.assertIn("T7", spec["route"])

    def test_update_spec_recomputes_route(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "U-1", "title": "Change ProfileScreen", "type": "chore"})
            code = main(["update-spec", "--target", str(root), "--surfaces", "navigation",
                         "--acceptance", "Back returns to Home|Title is shown", "--risk", "medium"])
            spec = read_json(run_dir(root) / "ticket-spec.json")
        self.assertEqual(code, 0)
        self.assertIn("T7", spec["route"])
        self.assertIn("T3", spec["route"])
        self.assertEqual(len(spec["acceptance_criteria"]), 2)

    def test_gate_uses_modules_the_implementer_touched(self) -> None:
        from android_workflow.cli import gate_commands, gate_modules

        with AndroidProject() as root:
            run(root, {"id": "G-1", "title": "Change ProfileScreen", "type": "chore"})
            domain = root / "core/domain/src/main/java/Rule.kt"
            domain.parent.mkdir(parents=True)
            domain.write_text("class Rule\n", encoding="utf-8")
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed",
                  "--file", "core/domain/src/main/java/Rule.kt"])
            change_set = read_json(run_dir(root) / "change-set-map.json")
            modules = gate_modules(root, change_set)
            config = read_json(cache_dir(root) / "project-config.json")
            commands = gate_commands(config, change_set, True, modules)
        self.assertEqual(modules, [":core:domain"])
        self.assertTrue(commands[0].endswith(":core:domain:compileDebugKotlin"))
        self.assertTrue(commands[-1].endswith(":core:domain:lintDebug"))
        self.assertFalse(any("assembleDebug" in command for command in commands))


class SetupTests(unittest.TestCase):
    def test_formatter_is_found_in_version_catalog_and_reaches_the_gate(self) -> None:
        from android_workflow.cli import gate_commands

        with AndroidProject() as root:
            (root / "gradle/libs.versions.toml").write_text(
                '[plugins]\nkotlinter = { id = "org.jmailen.kotlinter", version = "4.4.1" }\n', encoding="utf-8"
            )
            config = configure(root)
            commands = gate_commands(config, {"affected_modules": [":app"], "public_api_changed": False}, False)
        self.assertEqual(config["commands"]["format_check"], "gradle lintKotlin")
        self.assertEqual(config["commands"]["format_apply"], "gradle formatKotlin")
        self.assertEqual(commands[0], "gradle lintKotlin")

    def test_formatter_in_convention_plugin_source(self) -> None:
        from android_workflow.cli import detect_quality_tools

        with AndroidProject() as root:
            plugin = root / "build-logic/convention/src/main/kotlin/QualityPlugin.kt"
            plugin.parent.mkdir(parents=True)
            plugin.write_text('pluginManager.apply("com.diffplug.spotless")\n'
                              'pluginManager.apply("io.gitlab.arturbosch.detekt")\n', encoding="utf-8")
            quality = detect_quality_tools(root)
        self.assertEqual(quality["formatter"], "spotless")
        self.assertTrue(quality["format_check"].endswith("spotlessCheck"))
        self.assertTrue(quality["detekt"].endswith("detekt"))

    def test_versions_resolve_catalog_aliases_and_sdk_constants(self) -> None:
        from android_workflow.cli import detect_versions

        with AndroidProject() as root:
            (root / "gradle/libs.versions.toml").write_text(
                '[versions]\nandroidGradlePluginVersion = "9.4.0"\nkotlinVersion = "2.4.20"\n', encoding="utf-8"
            )
            (root / "build.gradle.kts").write_text('extra["COMPILE_SDK_VERSION"] = 37\n', encoding="utf-8")
            (root / "app/build.gradle.kts").write_text(
                "android { compileSdk = COMPILE_SDK_VERSION; defaultConfig { minSdk = MIN_SDK } }\n", encoding="utf-8"
            )
            (root / "gradle.properties").write_text("MIN_SDK=26\n", encoding="utf-8")
            versions = detect_versions(root)
        self.assertEqual(versions["agp"], "9.4.0")
        self.assertEqual(versions["kotlin"], "2.4.20")
        self.assertEqual(versions["compile_sdk"], "37")
        self.assertEqual(versions["min_sdk"], "26")

    def test_jvm_modules_use_variantless_tasks(self) -> None:
        from android_workflow.cli import gate_commands

        with AndroidProject() as root:
            (root / "core/domain/build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
            config = configure(root)
            commands = gate_commands(config, {"affected_modules": [":app", ":core:domain"],
                                              "public_api_changed": False}, False)
        self.assertEqual(config["jvm_modules"], [":core:domain"])
        self.assertIn("./gradlew :core:domain:compileKotlin", commands)
        self.assertIn("./gradlew :core:domain:test", commands)
        self.assertIn("./gradlew :app:compileDebugKotlin", commands)
        self.assertFalse(any(":core:domain:lintDebug" in command for command in commands))

    def test_jvm_convention_plugin_alias_is_jvm(self) -> None:
        from android_workflow.cli import detect_jvm_modules

        with AndroidProject() as root:
            (root / "core/domain/build.gradle.kts").write_text(
                "plugins {\n    alias(libs.plugins.acme.jvm.library)\n}\n", encoding="utf-8"
            )
            (root / "app/build.gradle.kts").write_text(
                "plugins {\n    alias(libs.plugins.acme.android.application)\n}\n", encoding="utf-8"
            )
            self.assertEqual(detect_jvm_modules(root, [":app", ":core:domain"]), [":core:domain"])

    def test_module_with_apply_from_stays_android(self) -> None:
        from android_workflow.cli import detect_jvm_modules

        with AndroidProject() as root:
            (root / "core/domain/build.gradle").write_text(
                "apply plugin: 'kotlin'\napply from: '../gradle/android-library.gradle'\n", encoding="utf-8"
            )
            self.assertEqual(detect_jvm_modules(root, [":core:domain"]), [])

    def test_mentioning_ktlint_is_not_a_ktlint_plugin(self) -> None:
        from android_workflow.cli import detect_quality_tools

        with AndroidProject() as root:
            (root / "build.gradle.kts").write_text("// TODO: add ktlint later\n", encoding="utf-8")
            quality = detect_quality_tools(root)
        self.assertIsNone(quality["format_check"])

    def test_setup_reports_flavors_and_empty_profile_until_resolved(self) -> None:
        from android_workflow.cli import run_setup

        with AndroidProject() as root:
            (root / "app/build.gradle.kts").write_text(
                'android { productFlavors { create("prod") { }; create("staging") { } } }\n', encoding="utf-8"
            )
            result = run_setup(root)
            joined = " ".join(result["reasons"])
            with self.assertRaises(ValueError):
                run_setup(root, accept=True)
        self.assertEqual(result["status"], "needs_setup_agent")
        self.assertIn("prod, staging", joined)
        self.assertIn("Code patterns", joined)

    def test_durable_overrides_survive_clean_and_redetection(self) -> None:
        with AndroidProject() as root:
            overrides = root / "o.json"
            overrides.write_text(json.dumps({"module_commands": {"unit_tests": "./gradlew {module}:testProdDebugUnitTest"}}),
                                 encoding="utf-8")
            configure(root, overrides)
            main(["clean", "--target", str(root), "--all"])
            run(root, {"id": "D-1", "title": "Change ProfileScreen", "type": "chore"})
            config = read_json(cache_dir(root) / "project-config.json")
        self.assertEqual(config["module_commands"]["unit_tests"], "./gradlew {module}:testProdDebugUnitTest")

    def test_run_files_are_excluded_without_touching_gitignore(self) -> None:
        import subprocess

        from android_workflow.paths import ensure_gitignore

        with AndroidProject() as root:
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            ensure_gitignore(root)
            exclude = (root / ".git/info/exclude").read_text(encoding="utf-8")
            status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True,
                                    capture_output=True, check=True).stdout
        self.assertIn(".ai/workflow/", exclude)
        self.assertIn(".ai/project-profile.md", exclude)
        self.assertFalse((root / ".gitignore").exists())
        self.assertNotIn(".gitignore", status)


FAKE_GRADLEW = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

cwd = Path.cwd()
config = json.loads((cwd / "fake-gradle.json").read_text()) if (cwd / "fake-gradle.json").exists() else {}
with (cwd / "gradle-calls.log").open("a") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\n")
tasks = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
for task in tasks:
    if task in config.get("missing", []):
        name = task.rpartition(":")[2]
        print("FAILURE: Build failed with an exception.\n\n* What went wrong:")
        print(f"Cannot locate tasks that match '{task}' as task '{name}' not found in project '{task.rpartition(':')[0]}'.")
        print("\n* Try:\n> Run gradle tasks\n\nBUILD FAILED in 1s")
        sys.exit(1)
blocks = []
for task in tasks:
    kind = config.get("tasks", {}).get(task, "pass")
    if kind == "pass":
        print(f"> Task {task}")
        continue
    if kind == "baseline_continue":
        baseline = cwd / task.strip(":").split(":")[0] / "lint-baseline.xml"
        baseline.write_text("<issues/>\n")
        print(f"> Task {task}")
        continue
    print(f"> Task {task} FAILED")
    if kind == "baseline":
        baseline = cwd / task.strip(":").split(":")[0] / "lint-baseline.xml"
        baseline.write_text("<issues/>\n")
        print(f"Created baseline file {baseline}")
        print("Also breaking the build in case this was not intentional.")
        blocks.append((task, ["> Aborting build since new baseline file was created"]))
    elif kind == "lint_inline":
        issues = config.get("lint_issues", [])
        blocks.append((task, [
            "> Lint found errors in the project; aborting build.",
            *issues,
        ]))
    elif kind == "lint":
        report = cwd / "build" / (task.replace(":", "_") + "-lint.txt")
        report.parent.mkdir(parents=True, exist_ok=True)
        issues = config.get("lint_issues", [])
        report.write_text("".join(f"{cwd}/{issue}\n" for issue in issues) + config.get("lint_summary", ""))
        blocks.append((task, [
            "> Lint found errors in the project; aborting build.",
            f"  Lint found {len(issues)} errors. First failure:",
            "  The full lint text report is located at:",
            f"    {report}",
        ]))
    else:
        blocks.append((task, ["> There were failing tests."]))
if not blocks:
    print("\nBUILD SUCCESSFUL in 1s")
    sys.exit(0)
print(f"\nFAILURE: Build completed with {len(blocks)} failures.")
for index, (task, lines) in enumerate(blocks, 1):
    print(f"\n{index}: Task failed with an exception.\n-----------\n* What went wrong:")
    print(f"Execution failed for task '{task}'.")
    print("\n".join(lines))
    print("\n* Try:\n> Run with --stacktrace option to get the stack trace.")
    print("=" * 78)
print("\nBUILD FAILED in 2s")
sys.exit(1)
"""


def fake_gradle_project(root: Path, fake: dict, formatter: bool = False) -> None:
    import os
    import subprocess

    gradlew = root / "gradlew"
    gradlew.write_text(FAKE_GRADLEW, encoding="utf-8")
    os.chmod(gradlew, 0o755)
    (root / "fake-gradle.json").write_text(json.dumps(fake), encoding="utf-8")
    (root / ".gitignore").write_text("build/\ngradle-calls.log\n", encoding="utf-8")
    overrides = {"commands": {"format_check": "./gradlew lintKotlin" if formatter else ""}}
    (root / ".ai").mkdir(exist_ok=True)
    (root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], check=True)


def gradle_calls(root: Path) -> list[str]:
    path = root / "gradle-calls.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class BatchedGateTests(unittest.TestCase):
    def gate(self, root: Path, fake: dict, formatter: bool = False, working_fake: dict | None = None) -> dict:
        fake_gradle_project(root, fake, formatter)
        run(root, {"id": "B-1", "title": "Change ProfileScreen", "type": "chore"})
        implement(root)
        if working_fake is not None:
            (root / "fake-gradle.json").write_text(json.dumps(working_fake), encoding="utf-8")
        self.code = main(["gate", "--target", str(root)])
        return read_json(run_dir(root) / "gate-report.json")

    def test_gradle_tasks_share_one_continue_invocation(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {})
            calls = gradle_calls(root)
        self.assertEqual(self.code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(calls), 1)
        self.assertIn("--continue", calls[0])
        for task in (":app:compileDebugKotlin", ":app:testDebugUnitTest", ":app:lintDebug"):
            self.assertIn(task, calls[0])
        self.assertTrue(all(step["outcome"] == "passed" for step in report["steps"]))
        self.assertIsInstance(report["duration_seconds"], float)

    def test_each_command_gets_its_own_outcome(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:testDebugUnitTest": "fail"}})
        outcomes = {step["command"]: step["outcome"] for step in report["steps"]}
        self.assertEqual(self.code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(outcomes["./gradlew :app:testDebugUnitTest"], "failed")
        self.assertEqual(outcomes["./gradlew :app:compileDebugKotlin"], "passed")
        self.assertEqual(outcomes["./gradlew :app:lintDebug"], "passed")
        failed = next(step for step in report["steps"] if step["outcome"] == "failed")
        self.assertIn("Execution failed for task ':app:testDebugUnitTest'.", failed["output_excerpt"])

    def test_missing_lint_baseline_is_unverified_and_fails_the_gate(self) -> None:
        from android_workflow.cli import source_changes

        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:lintDebug": "baseline"}})
            baseline_exists = (root / "app/lint-baseline.xml").exists()
            changes = source_changes(root)
        lint = next(step for step in report["steps"] if step["command"].endswith(":app:lintDebug"))
        self.assertEqual(self.code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(lint["outcome"], "unverified")
        self.assertFalse(baseline_exists)
        self.assertNotIn("app/lint-baseline.xml", changes)
        self.assertTrue(any("lint baseline" in warning for warning in report["warnings"]))

    def test_baseline_written_without_abort_is_still_unverified(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:lintDebug": "baseline_continue"}})
            baseline_exists = (root / "app/lint-baseline.xml").exists()
        lint = next(step for step in report["steps"] if step["command"].endswith(":app:lintDebug"))
        self.assertEqual(self.code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(lint["outcome"], "unverified")
        self.assertFalse(baseline_exists)

    def test_tracked_baseline_is_never_deleted(self) -> None:
        import subprocess

        with AndroidProject() as root:
            (root / "app/lint-baseline.xml").write_text("<issues/>\n", encoding="utf-8")
            fake_gradle_project(root, {"tasks": {":app:lintDebug": "baseline"}})
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            run(root, {"id": "B-3", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            main(["gate", "--target", str(root)])
            baseline_exists = (root / "app/lint-baseline.xml").exists()
        self.assertTrue(baseline_exists)

    def test_new_untracked_baseline_is_not_a_source_change(self) -> None:
        from android_workflow.cli import source_changes

        with AndroidProject() as root:
            fake_gradle_project(root, {})
            run(root, {"id": "B-2", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            (root / "core/domain/lint-baseline.xml").write_text("<issues/>\n", encoding="utf-8")
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed",
                  "--file", "core/domain/lint-baseline.xml"])
            changes = source_changes(root)
        self.assertEqual(changes, ["app/src/main/java/com/example/ProfileScreen.kt"])

    def lint_step(self, report: dict) -> dict:
        return next(step for step in report["steps"] if step["command"].endswith(":app:lintDebug"))

    def test_lint_findings_only_in_untouched_files_are_waived_and_recorded(self) -> None:
        issue = "core/domain/src/Old.kt:3: Error: The result of blockingGet is not used [CheckResult]"
        fake = {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [issue]}
        with AndroidProject() as root:
            report = self.gate(root, fake)
            cache_exists = (root / ".git/android-workflow/base-gate.json").exists()
            base_log = (run_dir(root) / "gate-base.log").exists()
            self.assertEqual(len(subprocess_lines(root, "worktree", "list")), 1)
            log = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
        lint = self.lint_step(report)
        self.assertEqual(self.code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(lint["outcome"], "waived")
        self.assertEqual(lint["waiver"]["count"], 1)
        self.assertEqual(lint["waiver"]["files"], ["core/domain/src/Old.kt"])
        self.assertEqual(report["waivers"][0]["command"], "./gradlew :app:lintDebug")
        self.assertTrue(any("waived" in warning for warning in report["warnings"]))
        self.assertIn("waived", log)
        self.assertFalse(base_log)
        self.assertFalse(cache_exists)

    def test_inline_lint_finding_in_untouched_file_is_waived_without_report_path(self) -> None:
        issue = "core/domain/src/Old.kt:3: Error: Old problem [OldId]"
        fake = {"tasks": {":app:lintDebug": "lint_inline"}, "lint_issues": [issue]}
        with AndroidProject() as root:
            report = self.gate(root, fake)
        lint = self.lint_step(report)
        self.assertEqual(self.code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(lint["outcome"], "waived")
        self.assertEqual(lint["waiver"]["files"], ["core/domain/src/Old.kt"])

    def test_a_waived_gate_lets_finish_through(self) -> None:
        issue = "core/domain/src/Old.kt:3: Error: Old problem [OldId]"
        with AndroidProject() as root:
            self.gate(root, {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [issue]})
            self.assertEqual(approve_review(root), 0)
            state = finish_run(root, skip_device="no device")
        self.assertEqual(state["status"], "completed")

    def test_lint_scope_all_restores_the_strict_policy(self) -> None:
        issue = "core/domain/src/Old.kt:3: Error: Old problem [OldId]"
        with AndroidProject() as root:
            fake_gradle_project(root, {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [issue]})
            (root / ".ai/android-workflow.json").write_text(
                json.dumps({"quality_gates": {"lint_scope": "all"}}), encoding="utf-8")
            run(root, {"id": "B-9", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            code = main(["gate", "--target", str(root)])
            report = read_json(run_dir(root) / "gate-report.json")
        self.assertEqual(code, 1)
        self.assertEqual(self.lint_step(report)["outcome"], "failed")

    def test_findings_inside_and_outside_the_change_still_fail(self) -> None:
        old = "core/domain/src/Old.kt:3: Error: Old problem [OldId]"
        mine = "app/src/main/java/com/example/ProfileScreen.kt:9: Error: New problem [NewId]"
        fake = {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [old, mine]}
        with AndroidProject() as root:
            report = self.gate(root, fake)
        lint = self.lint_step(report)
        self.assertEqual(self.code, 1)
        self.assertEqual(lint["outcome"], "failed")
        self.assertEqual(lint["diagnostic_count"], 2)
        self.assertEqual(lint["in_scope_findings"], 1)
        self.assertEqual(report["waivers"], [])

    def test_a_lint_failure_with_no_readable_findings_is_never_waived(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:lintDebug": "lint"}, "lint_issues": []})
        self.assertEqual(self.code, 1)
        self.assertEqual(self.lint_step(report)["outcome"], "failed")

    def test_lint_summary_larger_than_the_parsed_findings_is_never_waived(self) -> None:
        issue = "core/domain/src/Old.kt:3: Error: Old problem [OldId]"
        fake = {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [issue], "lint_summary": "5 errors, 0 warnings\n"}
        with AndroidProject() as root:
            report = self.gate(root, fake)
        self.assertEqual(self.code, 1)
        self.assertEqual(self.lint_step(report)["outcome"], "failed")

    def test_gate_logs_are_kept_per_run(self) -> None:
        with AndroidProject() as root:
            first = self.gate(root, {"tasks": {":app:testDebugUnitTest": "fail"}})
            (root / "fake-gradle.json").write_text("{}", encoding="utf-8")
            main(["gate", "--target", str(root)])
            second = read_json(run_dir(root) / "gate-report.json")
            logs = sorted(path.name for path in run_dir(root).glob("gate-run*.log"))
        self.assertEqual((first["run"], second["run"]), (1, 2))
        self.assertEqual(len(logs), 2)
        self.assertNotEqual(first["steps"][0]["log"], second["steps"][0]["log"])
        self.assertEqual(second["prior_runs"][0]["steps"][0]["log"], first["steps"][0]["log"])

    def test_finding_in_a_changed_file_fails_without_base_comparison(self) -> None:
        issue = "app/src/main/java/com/example/ProfileScreen.kt:3: Error: Bad [SomeId]"
        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:lintDebug": "lint"}, "lint_issues": [issue]})
            base_log = (run_dir(root) / "gate-base.log").exists()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(base_log)

    def test_formatter_is_scoped_and_falls_back_when_a_module_lacks_it(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {"missing": [":app:lintKotlin"]}, formatter=True)
            calls = gradle_calls(root)
        self.assertEqual(self.code, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn(":app:lintKotlin", calls[0].split())
        self.assertIn("lintKotlin", calls[1].split())
        self.assertNotIn(":app:lintKotlin", calls[1].split())
        self.assertIn(":app:compileDebugKotlin", calls[2].split())
        self.assertTrue(any("project-wide" in warning for warning in report["warnings"]))

    def test_format_check_runs_alone_first_when_nothing_can_fix_it(self) -> None:
        with AndroidProject() as root:
            self.gate(root, {}, formatter=True)
            calls = gradle_calls(root)
        self.assertEqual(len(calls), 2)
        self.assertIn(":app:lintKotlin", calls[0].split())
        self.assertNotIn(":app:compileDebugKotlin", calls[0].split())
        self.assertIn(":app:compileDebugKotlin", calls[1].split())
        self.assertNotIn("lintKotlin", calls[1].split())

    def test_a_failed_format_check_skips_the_slow_tasks(self) -> None:
        with AndroidProject() as root:
            report = self.gate(root, {"tasks": {":app:lintKotlin": "fail"}}, formatter=True)
            calls = gradle_calls(root)
        self.assertEqual(self.code, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual([step["command"] for step in report["steps"]], ["./gradlew :app:lintKotlin"])

    def test_formatter_that_can_fix_runs_before_the_check_in_the_touched_module(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {}, formatter=True)
            overrides = json.loads((root / ".ai/android-workflow.json").read_text(encoding="utf-8"))
            overrides["commands"]["format_apply"] = "./gradlew formatKotlin"
            (root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")
            run(root, {"id": "B-7", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            code = main(["gate", "--target", str(root)])
            calls = gradle_calls(root)
            report = read_json(run_dir(root) / "gate-report.json")
        self.assertEqual(code, 0)
        self.assertIn(":app:formatKotlin", calls[0].split())
        self.assertNotIn("lintKotlin", calls[0].split())
        self.assertIn(":app:lintKotlin", calls[1].split())
        self.assertIn(":app:compileDebugKotlin", calls[1].split())
        self.assertEqual(report["auto_format"]["formatted"], [])

    def test_auto_format_keeps_touched_files_and_restores_the_rest(self) -> None:
        import os
        import subprocess

        script = (
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
            "with open('gradle-calls.log', 'a') as h: h.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if any('formatKotlin' in a for a in sys.argv):\n"
            "    for name in ('app/src/main/java/com/example/ProfileScreen.kt', 'core/domain/Other.kt'):\n"
            "        p = Path(name); p.write_text(p.read_text() + '// formatted\\n')\n"
            "print('BUILD SUCCESSFUL')\n"
        )
        with AndroidProject() as root:
            (root / "core/domain/Other.kt").write_text("class Other\n", encoding="utf-8")
            fake_gradle_project(root, {}, formatter=True)
            gradlew = root / "gradlew"
            gradlew.write_text(script, encoding="utf-8")
            os.chmod(gradlew, 0o755)
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-qm", "script"], check=True)
            overrides = json.loads((root / ".ai/android-workflow.json").read_text(encoding="utf-8"))
            overrides["commands"]["format_apply"] = "./gradlew formatKotlin"
            (root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")
            run(root, {"id": "B-8", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            main(["gate", "--target", str(root)])
            report = read_json(run_dir(root) / "gate-report.json")
            mine = (root / "app/src/main/java/com/example/ProfileScreen.kt").read_text(encoding="utf-8")
            other = (root / "core/domain/Other.kt").read_text(encoding="utf-8")
        self.assertEqual(report["auto_format"]["formatted"], ["app/src/main/java/com/example/ProfileScreen.kt"])
        self.assertEqual(report["auto_format"]["restored_outside_change"], ["core/domain/Other.kt"])
        self.assertIn("// formatted", mine)
        self.assertNotIn("// formatted", other)

    def test_auto_format_can_be_turned_off(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {}, formatter=True)
            (root / ".ai/android-workflow.json").write_text(json.dumps({
                "commands": {"format_check": "./gradlew lintKotlin", "format_apply": "./gradlew formatKotlin"},
                "quality_gates": {"auto_format": False},
            }), encoding="utf-8")
            run(root, {"id": "B-6", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            main(["gate", "--target", str(root)])
            calls = gradle_calls(root)
        self.assertFalse(any("formatKotlin" in call for call in calls))


def subprocess_lines(root: Path, *args: str) -> list[str]:
    import subprocess

    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


class RunStateTests(unittest.TestCase):
    def test_host_logged_stages_move_an_escalated_run_forward(self) -> None:
        with AndroidProject() as root:
            fake_gradle_project(root, {"tasks": {":app:testDebugUnitTest": "fail"}})
            run(root, {"id": "S-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            main(["gate", "--target", str(root)])
            self.assertEqual(read_json(run_dir(root) / "run-state.json")["status"], "escalated")
            main(["log", "--target", str(root), "--stage", "Quality gate", "--status", "completed", "--note", "waived"])
            after_gate = read_json(run_dir(root) / "run-state.json")
            main(["log", "--target", str(root), "--stage", "Delivery", "--status", "completed", "--note", "PR"])
            after_delivery = read_json(run_dir(root) / "run-state.json")
        self.assertEqual(after_gate["status"], "running")
        self.assertEqual(after_gate["current_stage"], "T6")
        self.assertEqual(after_delivery["status"], "completed")
        self.assertIsNone(after_delivery["current_stage"])

    def test_wait_seconds_are_kept_apart_from_work_time(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "S-2", "title": "Change ProfileScreen", "type": "chore"})
            main(["log", "--target", str(root), "--stage", "Reviewer", "--status", "completed",
                  "--seconds", "100", "--wait-seconds", "70"])
            entry = read_json(run_dir(root) / "stage-metrics.json")["stages"]["T6"]
        self.assertEqual(entry["wall_time_seconds"], 30)
        self.assertEqual(entry["human_wait_seconds"], 70)


class RoleNameTests(unittest.TestCase):
    def test_log_accepts_role_and_agent_names(self) -> None:
        from android_workflow.cli import resolve_stage

        self.assertEqual(resolve_stage("Implementer"), ("T4", "Implementer"))
        self.assertEqual(resolve_stage("aw-implementer"), ("T4", "Implementer"))
        self.assertEqual(resolve_stage("quality-gate"), ("T5", "Quality gate"))
        self.assertEqual(resolve_stage("reviewer"), ("T6", "Reviewer"))
        self.assertEqual(resolve_stage("T7"), ("T7", "Device"))
        for retired in ("Architect", "aw-tester"):
            with self.assertRaises(ValueError):
                resolve_stage(retired)

    def test_output_and_log_use_role_names(self) -> None:
        import contextlib
        import io

        with AndroidProject() as root:
            run(root, {"id": "R-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["log", "--target", str(root), "--stage", "aw-implementer", "--status", "completed",
                             "--note", "implementer: 2 tests"])
            printed = json.loads(out.getvalue())
            state = read_json(run_dir(root) / "run-state.json")
            log = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertEqual(printed["current_stage"], "Quality gate")
        self.assertIn("Implementer", printed["stages"])
        self.assertNotIn("T4", printed["stages"])
        self.assertIn("T4", state["stages"])
        self.assertIn("| Implementer | completed | host | implementer: 2 tests |", log)


class TelemetryTests(unittest.TestCase):
    def test_host_stages_accumulate_time_and_tokens_and_totals_are_recorded(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "M-1", "title": "Change ProfileScreen", "type": "chore"})
            implement(root)
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed",
                  "--tokens", "100", "--seconds", "5"])
            main(["log", "--target", str(root), "--stage", "T4", "--status", "completed",
                  "--note", "fix round", "--tokens", "50", "--seconds", "2.5"])
            approve_review(root)
            finish_verified(root)
            metrics = read_json(run_dir(root) / "stage-metrics.json")
        stages = metrics["stages"]
        self.assertEqual(stages["T4"]["tokens"], 150)
        self.assertGreaterEqual(stages["T4"]["wall_time_seconds"], 7.5)
        self.assertEqual(stages["T4"]["attempts"], 3)
        self.assertIsNotNone(stages["T0"]["wall_time_seconds"])
        self.assertIsNotNone(stages["T1"]["wall_time_seconds"])
        self.assertIsNotNone(stages["T6"]["wall_time_seconds"])
        self.assertIsNone(stages["T6"]["tokens"])
        self.assertIsInstance(stages["T8"]["wall_time_seconds"], float)
        self.assertEqual(metrics["totals"]["tokens"], 150)
        self.assertIsNotNone(metrics["totals"]["wall_time_seconds"])

    def test_a_new_start_resets_metrics(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "M-2", "title": "Change ProfileScreen", "type": "chore"})
            main(["log", "--target", str(root), "--stage", "T6", "--status", "completed", "--tokens", "9"])
            run(root, {"id": "M-2", "title": "Change ProfileScreen", "type": "chore"})
            metrics = read_json(run_dir(root) / "stage-metrics.json")
        self.assertNotIn("T6", metrics["stages"])

    def test_repeated_start_does_not_duplicate_log_rows(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "M-3", "title": "Change ProfileScreen", "type": "chore"})
            run(root, {"id": "M-3", "title": "Change ProfileScreen", "type": "chore"})
            log = (run_dir(root) / "stage-log.md").read_text(encoding="utf-8")
        self.assertEqual(log.count("| Localizer |"), 1)
        self.assertEqual(log.count("| Implementer | awaiting_host |"), 1)

    def test_clean_stale_removes_only_idle_unfinished_runs(self) -> None:
        with AndroidProject() as root:
            run(root, {"id": "C-1", "title": "Change ProfileScreen", "type": "chore"})
            state_path = run_dir(root, "C-1") / "run-state.json"
            state = read_json(state_path)
            state["updated_at"] -= 48 * 3600
            state_path.write_text(json.dumps(state), encoding="utf-8")
            run(root, {"id": "C-2", "title": "Change ProfileScreen", "type": "chore"})
            code = main(["clean", "--target", str(root), "--stale"])
            remaining = {path.name for path in (root / ".ai/workflow").iterdir()}
        self.assertEqual(code, 0)
        self.assertNotIn("C-1", remaining)
        self.assertIn("C-2", remaining)


if __name__ == "__main__":
    unittest.main()
