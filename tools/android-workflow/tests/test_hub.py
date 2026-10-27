"""The one office: every app's runs in one index, worktrees under their repository, prune and clean."""

from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The one office every app publishes to: a throwaway folder, never the developer's own.
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import contextlib
import io
import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import hub
from android_workflow.cli import main, read_json, run
from test_live_team_emulator import GitProject, git, office_run, start


def quiet(*args: str) -> tuple[dict, int]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args))
    return (json.loads(out.getvalue()) if code == 0 else {}), code


def index() -> dict:
    text = (hub.hub_dir() / hub.INDEX_JS).read_text(encoding="utf-8")
    return json.loads(re.search(r"window\.officeIndex\((.*)\);\n$", text, re.S).group(1))


class HubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {hub.HOME_ENV: self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.home.cleanup)

    def test_every_app_and_run_is_in_one_index(self) -> None:
        with GitProject() as one, GitProject() as two:
            start(one, "A-1")
            quiet("office", "--target", str(one), "--no-open")
            start(two, "B-1")
            quiet("office", "--target", str(two), "--no-open")
            found = index()
            page = hub.page_path().read_text(encoding="utf-8")
        self.assertEqual({run["ticket"] for run in found["runs"]}, {"A-1", "B-1"})
        self.assertEqual(len(found["projects"]), 2)
        self.assertTrue(all(run["live"] for run in found["runs"]))  # each app's current run
        self.assertIn('"index_src": "index.js"', page)
        self.assertIn('id="projectPicker"', page)

    def test_two_tickets_going_in_one_checkout_are_both_live(self) -> None:
        with GitProject() as app:
            start(app, "A-1")
            quiet("log", "--target", str(app), "--stage", "Planner", "--status", "started")
            with self.assertRaisesRegex(ValueError, "A-1 is still running in this checkout"):
                start(app, "A-2")  # a second ticket would take A-1's run over
            # --take-over (its session was said to be gone): the app moves on while A-1's Planner still logs working
            run(app, {"id": "A-2", "title": "Change ProfileScreen", "type": "chore"}, take_over=True)
            quiet("office", "--target", str(app), "--no-open")
            found = {run["ticket"]: run for run in index()["runs"]}
        self.assertTrue(found["A-1"]["live"])
        self.assertTrue(found["A-2"]["live"])
        self.assertIn("Planner", found["A-1"]["working"])

    def test_a_run_left_behind_is_not_live_even_when_it_is_the_current_one(self) -> None:
        from android_workflow.paths import run_dir

        with GitProject() as app:
            start(app, "A-1")
            quiet("log", "--target", str(app), "--stage", "Device", "--status", "started")
            folder = run_dir(app)
            state = read_json(folder / "run-state.json")
            state["updated_at"] = int(time.time()) - 3 * 86400  # a session closed mid-run, days ago
            (folder / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
            metrics = read_json(folder / "stage-metrics.json")
            for entry in (metrics.get("stages") or {}).values():
                if entry.get("running_since"):
                    entry["running_since"] = time.time() - 3 * 86400
            (folder / "stage-metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
            quiet("office", "--target", str(app), "--no-open")
            found = index()["runs"][0]
        self.assertFalse(found["live"])

    def test_a_ticket_started_again_in_a_worktree_closes_its_copy_in_the_main_checkout(self) -> None:
        from android_workflow.paths import run_dir

        with GitProject() as app:
            start(app, "A-1")
            quiet("log", "--target", str(app), "--stage", "Planner", "--status", "started")
            worktree = app.parent / f"{app.name}-wt"
            git(app, "worktree", "add", "-q", str(worktree), "-b", "a-1")
            (worktree / "settings.gradle.kts").write_text("", encoding="utf-8")
            start(worktree, "A-1")
            left = read_json(run_dir(app) / "run-state.json")
            quiet("office", "--target", str(app), "--no-open")
            found = [run for run in index()["runs"] if run["ticket"] == "A-1" and not run.get("live")]
        self.assertEqual((left["status"], left["moved_to"]), ("moved", str(worktree.resolve())))
        self.assertEqual(left["stages"]["T3"]["status"], "moved")
        self.assertEqual(len(found), 1)  # the main checkout's copy is no longer shown running

    def test_the_workflow_picker_shows_a_short_title(self) -> None:
        page = hub.page_path()
        hub.publish_page()
        text = page.read_text(encoding="utf-8")
        self.assertIn("const shortTitle", text)
        self.assertIn("shortTitle(r.title)", text)

    def test_a_worktree_is_listed_under_its_repository(self) -> None:
        with GitProject() as main_repo, tempfile.TemporaryDirectory() as folder:
            tree = Path(folder).resolve() / "wt-W-2"
            git(main_repo, "worktree", "add", "-q", "--detach", str(tree))
            start(main_repo, "W-1")
            quiet("office", "--target", str(main_repo), "--no-open")
            start(tree, "W-2")
            quiet("office", "--target", str(tree), "--no-open")
            found = index()
            git(main_repo, "worktree", "remove", "--force", str(tree))
        runs = {run["ticket"]: run for run in found["runs"]}
        self.assertEqual(len(found["projects"]), 1)
        self.assertEqual(found["projects"][0]["name"], main_repo.name)
        self.assertEqual(runs["W-1"]["project"], runs["W-2"]["project"])
        self.assertIsNone(runs["W-1"]["worktree"])
        self.assertEqual(runs["W-2"]["worktree"], "wt-W-2")

    def test_clean_drops_the_apps_runs_from_the_office(self) -> None:
        with GitProject() as root:
            start(root, "C-1")
            start(root, "C-2")
            quiet("office", "--target", str(root), "--no-open")
            before = {run["ticket"] for run in index()["runs"]}
            quiet("clean", "--target", str(root), "--ticket", "C-1")
            after = {run["ticket"] for run in index()["runs"]}
            script_left = (hub.hub_dir() / hub.run_src(hub.run_key(root, "C-1"))).exists()
        self.assertEqual(before, {"C-1", "C-2"})
        self.assertEqual(after, {"C-2"})
        self.assertFalse(script_left)

    def test_a_removed_app_stays_listed_until_prune(self) -> None:
        with GitProject() as root:
            start(root, "G-1")
            quiet("office", "--target", str(root), "--no-open")
            key = hub.run_key(root, "G-1")
            gone_folder = Path(str(root))
        kept = index()
        result, code = quiet("office", "--prune", "--no-open")
        self.assertFalse(gone_folder.exists())
        self.assertEqual([(run["ticket"], run["gone"], run["live"]) for run in kept["runs"]], [("G-1", False, True)])
        self.assertEqual(code, 0)
        self.assertEqual(result["removed"], [key])
        self.assertEqual(index()["runs"], [])
        self.assertFalse((hub.hub_dir() / hub.run_src(key)).exists())

    def test_prune_days_drops_only_old_past_runs(self) -> None:
        with GitProject() as root:
            start(root, "D-1")
            state = root / ".ai/workflow/D-1/run-state.json"
            state.write_text(json.dumps({**read_json(state), "status": "completed", "updated_at": time.time() - 40 * 86400}),
                             encoding="utf-8")
            start(root, "D-2")
            quiet("office", "--target", str(root), "--no-open")
            result, _ = quiet("office", "--prune", "--days", "30", "--no-open")
            left = [run["ticket"] for run in index()["runs"]]
        self.assertEqual(result["removed"], [hub.run_key(root, "D-1")])
        self.assertEqual(left, ["D-2"])

    def test_start_status_and_office_print_the_link_that_opens_this_run(self) -> None:
        with GitProject() as root:
            started, _ = quiet("start", "--target", str(root), "--id", "U-1", "--title", "Change ProfileScreen", "--type", "chore")
            status, _ = quiet("status", "--target", str(root))
            opened, _ = quiet("office", "--target", str(root), "--no-open")
            wanted = f"{hub.page_path().resolve().as_uri()}#run={hub.run_key(root, 'U-1')}"
        self.assertTrue(wanted.startswith("file:///"))
        self.assertEqual([started["office_url"], status["office_url"], opened["office_url"]], [wanted] * 3)
        everything, _ = quiet("office", "--no-open")
        self.assertEqual(everything["office_url"], hub.page_path().resolve().as_uri())

    def test_start_prints_one_office_line_and_a_banner_after_the_json(self) -> None:
        with GitProject() as root:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["start", "--target", str(root), "--id", "M-1", "--title", "Change ProfileScreen", "--type", "chore"])
            logged, _ = quiet("log", "--target", str(root), "--stage", "Planner", "--status", "started")
        result = json.loads(out.getvalue())  # the banner never breaks the JSON
        self.assertEqual(code, 0)
        self.assertEqual(result["office_message"], f"🏢 **Office:** {result['office_url']}")
        self.assertEqual(list(result)[-2:], ["office_url", "office_message"])
        banner = err.getvalue().strip().splitlines()
        self.assertEqual(banner[1], "🏢  OFFICE  ·  M-1  ·  Cmd+click to open")
        self.assertEqual(banner[2].strip(), result["office_url"])
        self.assertNotIn("office_message", logged)

    def test_the_office_opens_without_an_app(self) -> None:
        result, code = quiet("office", "--no-open")
        self.assertEqual(code, 0)
        self.assertEqual(result["office"], str(hub.page_path()))
        self.assertTrue(hub.page_path().exists())
        _, bad = quiet("office", "--days", "3", "--no-open")
        self.assertEqual(bad, 2)

    def test_the_watcher_tick_updates_the_run_and_the_index(self) -> None:
        from android_workflow import office

        with GitProject() as root:
            start(root, "T-1")
            quiet("office", "--target", str(root), "--no-open")
            key, live = office.write_live_data(root)
            (root / "app/src/main/java/com/example/ProfileScreen.kt").write_text("package com.example\nclass Z\n", encoding="utf-8")
            again, _ = office.write_live_data(root, key, full_scan=True)
            data = office_run(root, "T-1")
        self.assertTrue(live)
        self.assertNotEqual(key, again)
        self.assertEqual([item["path"] for item in data["changes"]], ["app/src/main/java/com/example/ProfileScreen.kt"])


if __name__ == "__main__":
    unittest.main()
