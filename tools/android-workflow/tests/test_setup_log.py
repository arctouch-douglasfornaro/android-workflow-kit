"""Setup is logged before the ticket's `start`: its record waits for that start, never the previous run."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))
os.environ["ANDROID_WORKFLOW_NO_USAGE"] = "1"

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow.cli import log_stage, open_pending_setup, pending_setup_path, read_json, run_dir
from test_live_team_emulator import GitProject, git, start


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


PROFILE = """# Project profile

## Build commands
| Module | Plugin / variant | Compile | Unit tests | Lint | Evidence |
|---|---|---|---|---|---|
| :app | android app | ./gradlew :app:compileDebugKotlin | ./gradlew :app:testDebugUnitTest | ./gradlew :app:lintDebug | app/build.gradle.kts |

## Lint engine
| What | Value |
|---|---|
| Engine | none, checked |

## Tests
| What | Value |
|---|---|
| Libraries | junit4 |

## Code patterns
| Area | Pattern used here | Exemplar (path:line) |
|---|---|---|
| Screens | plain classes | app/src/main/java/com/example/ProfileScreen.kt |
"""


class SetupAdoptionTests(unittest.TestCase):
    """A worktree of a checkout already set up adopts that setup by itself: no Setup agent per worktree."""

    def setUp(self) -> None:
        from android_workflow import cli

        helper = Path(__file__).resolve().parents[3] / "bin" / "feature_setup.py"
        patcher = mock.patch.object(cli, "SETUP_HELPER", helper)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_up_main(self, root: Path) -> None:
        from android_workflow.cli import run_setup

        run_setup(root)
        (root / ".ai/project-profile.md").write_text(PROFILE, encoding="utf-8")
        (root / ".ai/android-workflow.json").write_text(json.dumps({
            "device": {"application_id": "com.example"}, "commands": {"format_check": "none"}}), encoding="utf-8")
        self.assertEqual(run_setup(root, accept=True)["status"], "ready")

    def test_a_worktree_with_the_same_build_files_adopts_the_main_checkouts_setup(self) -> None:
        from android_workflow.cli import run_setup

        with GitProject() as root, tempfile.TemporaryDirectory() as other:
            self.set_up_main(root)
            worktree = Path(other).resolve() / "wt"
            git(root, "worktree", "add", "-q", str(worktree), "-b", "wt")
            result = run_setup(worktree)  # no --source-repo: the main worktree is found
            copied = (worktree / ".ai/project-profile.md").read_text(encoding="utf-8")
            overrides = json.loads((worktree / ".ai/android-workflow.json").read_text(encoding="utf-8"))
            again = run_setup(worktree)
        self.assertEqual(result["status"], "ready", result["reasons"])
        self.assertEqual(result["adopted_setup"], {"adopted": True, "from": str(root.resolve())})
        self.assertEqual(copied, PROFILE)
        self.assertEqual(overrides["device"]["application_id"], "com.example")
        self.assertEqual(again["status"], "ready")
        self.assertNotIn("adopted_setup", again)  # set up now: nothing to adopt

    def test_a_worktree_whose_build_files_differ_still_needs_the_setup_agent(self) -> None:
        from android_workflow.cli import run_setup

        with GitProject() as root, tempfile.TemporaryDirectory() as other:
            self.set_up_main(root)
            worktree = Path(other).resolve() / "wt"
            git(root, "worktree", "add", "-q", str(worktree), "-b", "wt")
            (worktree / "app/build.gradle.kts").write_text('plugins { id("com.android.library") }\n', encoding="utf-8")
            result = run_setup(worktree, source_repo=root)
        self.assertEqual(result["status"], "needs_setup_agent")
        self.assertFalse(result["adopted_setup"]["adopted"])
        self.assertIn("app/build.gradle.kts", result["adopted_setup"]["reason"])


if __name__ == "__main__":
    unittest.main()
