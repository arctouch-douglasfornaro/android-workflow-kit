from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from android_workflow.cli import (
    TOOLKIT_FINGERPRINT_FILE,
    append_stage_log,
    build_project_config,
    consumer_modules,
    doc_covers,
    feature_docs_step,
    gate_commands,
    log_stage,
    run,
    toolkit_changes,
    toolkit_fingerprint,
)
from android_workflow.paths import run_dir


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class Project:
    def __enter__(self) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "settings.gradle.kts").write_text(
            'include(":app", ":core:domain", ":feature:other")\n', encoding="utf-8",
        )
        for module, dependency in (("app", ":core:domain"), ("feature/other", ":core:domain")):
            (root / module).mkdir(parents=True)
            (root / module / "build.gradle.kts").write_text(
                'plugins { id("com.android.library") }\n'
                f'dependencies {{ implementation(project("{dependency}")) }}\n',
                encoding="utf-8",
            )
        (root / "core/domain/src/main/java/com/example").mkdir(parents=True)
        (root / "core/domain/build.gradle.kts").write_text('plugins { id("com.android.library") }\n', encoding="utf-8")
        (root / "core/domain/src/main/java/com/example/Steps.kt").write_text(
            "package com.example\ndata class Steps(val steps: List<Step>)\nsealed interface Step\n",
            encoding="utf-8",
        )
        (root / "app/src/test/java/com/example").mkdir(parents=True)
        (root / "app/src/test/java/com/example/UseCaseTest.kt").write_text(
            "package com.example\nval fixture: Step? = null\n", encoding="utf-8",
        )
        (root / "feature/other/src/main/java/com/example").mkdir(parents=True)
        (root / "feature/other/src/main/java/com/example/Other.kt").write_text(
            "package com.example\nclass StepsOther\n", encoding="utf-8",
        )
        git(root, "init", "-q")
        git(root, "add", ".")
        return root

    def __exit__(self, *_: object) -> None:
        self.temp.cleanup()


CHANGED = ["core/domain/src/main/java/com/example/Steps.kt"]


class ConsumerModuleTests(unittest.TestCase):
    def test_a_dependent_module_that_references_a_changed_type_is_gated(self) -> None:
        with Project() as root:
            config = build_project_config(root)
            consumers, omitted = consumer_modules(root, config, [":core:domain"], CHANGED)

        self.assertEqual(consumers, [":app"])
        self.assertEqual(omitted, [])

    def test_consumers_get_compile_and_tests_but_not_lint(self) -> None:
        with Project() as root:
            config = build_project_config(root)
            commands = gate_commands(config, {"affected_modules": []}, False, [":core:domain"], consumers=[":app"])

        self.assertIn("./gradlew :app:compileDebugKotlin", commands)
        self.assertIn("./gradlew :app:testDebugUnitTest", commands)
        self.assertNotIn("./gradlew :app:lintDebug", commands)
        self.assertIn("./gradlew :core:domain:lintDebug", commands)

    def test_consumers_over_the_limit_are_reported_not_gated(self) -> None:
        with Project() as root:
            (root / "feature/other/src/main/java/com/example/Other.kt").write_text(
                "package com.example\nval step: Step? = null\n", encoding="utf-8",
            )
            git(root, "add", ".")
            config = build_project_config(root)
            config["quality_gates"]["max_consumer_modules"] = 1
            consumers, omitted = consumer_modules(root, config, [":core:domain"], CHANGED)

        self.assertEqual(consumers, [":app"])
        self.assertEqual(omitted, [":feature:other"])

    def test_test_only_changes_have_no_consumers(self) -> None:
        with Project() as root:
            config = build_project_config(root)
            consumers, _ = consumer_modules(
                root, config, [":app"], ["app/src/test/java/com/example/UseCaseTest.kt"],
            )

        self.assertEqual(consumers, [])



DOC = """---
# provenance
covers:
  - core/domain
  - feature/other
generated_from_commit: abc
---

# Domain
"""


class FeatureDocsTests(unittest.TestCase):
    def test_covers_reads_the_frontmatter_list(self) -> None:
        self.assertEqual(doc_covers(DOC), ["core/domain", "feature/other"])
        self.assertEqual(doc_covers("# No frontmatter\ncovers:\n  - x\n"), [])

    def test_a_covered_production_change_without_the_doc_fails(self) -> None:
        with Project() as root:
            (root / "core/domain/README.md").write_text(DOC, encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            step = feature_docs_step(root, {"feature_docs": {"glob": "*/*/README.md"}}, CHANGED)

        self.assertEqual(step["outcome"], "failed")
        self.assertIn("core/domain/README.md covers 1 changed file(s)", step["output_excerpt"][0])

    def test_updating_the_doc_passes(self) -> None:
        with Project() as root:
            (root / "core/domain/README.md").write_text(DOC, encoding="utf-8")
            step = feature_docs_step(
                root, {"feature_docs": {"glob": "*/*/README.md"}}, [*CHANGED, "core/domain/README.md"],
            )

        self.assertEqual(step["outcome"], "passed")

    def test_a_doc_edited_in_the_working_tree_counts_even_though_it_is_markdown(self) -> None:
        with Project() as root:
            (root / "core/domain/README.md").write_text(DOC, encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            (root / "core/domain/README.md").write_text(DOC + "\nEmpty state documented.\n", encoding="utf-8")
            # Only source files are passed in, as the gate does; the README edit must still be seen.
            step = feature_docs_step(root, {"feature_docs": {"glob": "*/*/README.md"}}, CHANGED)

        self.assertEqual(step["outcome"], "passed")

    def test_test_only_changes_do_not_need_the_doc(self) -> None:
        with Project() as root:
            (root / "core/domain/README.md").write_text(DOC, encoding="utf-8")
            step = feature_docs_step(
                root, {"feature_docs": {"glob": "*/*/README.md"}}, ["core/domain/src/test/java/StepsTest.kt"],
            )

        self.assertEqual(step["outcome"], "passed")

    def test_without_a_glob_the_check_is_off(self) -> None:
        with Project() as root:
            self.assertIsNone(feature_docs_step(root, {}, CHANGED))


class ToolkitIntegrityTests(unittest.TestCase):
    def test_an_unchanged_toolkit_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            (agent_dir / TOOLKIT_FINGERPRINT_FILE).write_text(
                json.dumps({"files": toolkit_fingerprint()}), encoding="utf-8",
            )

            self.assertEqual(toolkit_changes(agent_dir), [])

    def test_an_edited_toolkit_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            recorded = toolkit_fingerprint()
            edited = next(iter(recorded))
            recorded[edited] = "0" * 64
            (agent_dir / TOOLKIT_FINGERPRINT_FILE).write_text(json.dumps({"files": recorded}), encoding="utf-8")

            self.assertEqual(toolkit_changes(agent_dir), [edited])

    def test_a_run_without_a_fingerprint_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(toolkit_changes(Path(temp)), [])

    def test_start_records_the_fingerprint(self) -> None:
        with Project() as root:
            run(root, {"id": "APP-2", "title": "Change Steps", "type": "feature", "acceptance_criteria": ["a"]})

            self.assertTrue((run_dir(root, "APP-2") / TOOLKIT_FINGERPRINT_FILE).exists())


class StageLogTests(unittest.TestCase):
    def test_concurrent_identical_events_are_logged_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            agent_dir = Path(temp)
            threads = [
                threading.Thread(target=append_stage_log, args=(agent_dir, "T2", "completed", "cli", "12 candidatos"))
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            log = (agent_dir / "stage-log.md").read_text(encoding="utf-8")

        self.assertEqual(log.count("| Localizer | completed | cli | 12 candidatos |"), 1)

    def test_an_agent_stage_logged_without_tokens_warns(self) -> None:
        with Project() as root:
            run(root, {"id": "APP-3", "title": "Change Steps", "type": "feature", "acceptance_criteria": ["a"]})
            without = log_stage(root, "Planner", "completed", "plan ready")
            with_tokens = log_stage(root, "Reviewer", "completed", "approved", tokens=1200)

        self.assertIn("Planner logged without --tokens", without["warnings"][0])
        self.assertNotIn("warnings", with_tokens)


if __name__ == "__main__":
    unittest.main()
