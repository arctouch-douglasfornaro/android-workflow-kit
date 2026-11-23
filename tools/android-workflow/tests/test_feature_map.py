from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ANDROID_WORKFLOW_NO_WATCH", "1")

from android_workflow import feature_map
from android_workflow.cli import setup_spawns

MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application>
    <activity android:name=".set.SetPageActivity" />
    <activity
        android:name="com.example.learn.LearnActivity" />
    <activity android:name=".debug.DebugMenuActivity" />
  </application>
</manifest>
"""


class FeatureMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "app/src/main/res/navigation").mkdir(parents=True)
        (self.root / "app/src/main/AndroidManifest.xml").write_text(MANIFEST)
        (self.root / "app/src/main/res/navigation/home.xml").write_text("<navigation/>")
        (self.root / "app/src/test").mkdir(parents=True)
        (self.root / "app/src/test/AndroidManifest.xml").write_text(
            MANIFEST.replace("SetPageActivity", "TestOnlyActivity"))
        (self.root / "feature/folders/src/main/kotlin").mkdir(parents=True)
        (self.root / "feature/folders/src/main/kotlin/FoldersNav.kt").write_text(
            "fun NavGraphBuilder.folders() { composable(\"folders\") { } }\n")

    def write_map(self, surfaces: str, not_mapped: str = "") -> None:
        path = feature_map.map_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(feature_map.TEMPLATE.replace(
            "|---|---|---|---|---|---|---|\n", "|---|---|---|---|---|---|---|\n" + surfaces).replace(
            "| Entry | Why |\n|---|---|\n", "| Entry | Why |\n|---|---|\n" + not_mapped))

    def test_entry_points_are_release_activities_and_navigation_graphs(self) -> None:
        keys = {entry["key"] for entry in feature_map.entry_points(self.root)}
        self.assertEqual(keys, {"SetPageActivity", "LearnActivity", "DebugMenuActivity",
                                "app/src/main/res/navigation/home.xml",
                                "feature/folders/src/main/kotlin/FoldersNav.kt"})

    def test_no_map_is_missing(self) -> None:
        result = feature_map.status(self.root, self.root)
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["uncovered_count"], 5)

    def test_unmapped_entry_points_make_it_stale_and_not_mapped_globs_cover_them(self) -> None:
        self.write_map("| Set page | `SetPageActivity` | 1. Launch → tap a set | | | | `app/src/main/Set.kt:3` |\n")
        stale = feature_map.status(self.root, self.root)
        self.assertEqual(stale["status"], "stale")
        self.assertNotIn("SetPageActivity", stale["uncovered"])
        self.write_map(
            "| Set page | `SetPageActivity` | 1. Launch → tap a set | | | | `app/src/main/Set.kt:3` |\n"
            "| Learn | LearnActivity | Set page → Learn | quizlet://learn | | | |\n"
            "| Home | | Launch | | | | app/src/main/res/navigation/home.xml |\n",
            "| *Debug* | debug only |\n| feature/folders/** | not shipped |\n")
        result = feature_map.status(self.root, self.root)
        self.assertEqual(result["uncovered"], [])
        self.assertEqual(result["incomplete_rows"], ["Learn"])  # no Source
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["orphans"], ["Set page"])  # app/src/main/Set.kt does not exist

    def test_an_activity_name_inside_another_does_not_cover_it(self) -> None:
        self.write_map("| New set page | NewSetPageActivity | Launch | | | | x.kt |\n")
        self.assertIn("SetPageActivity", feature_map.status(self.root, self.root)["uncovered"])

    def test_learn_adds_and_replaces_rows_and_keeps_human_text(self) -> None:
        self.write_map("| Set page | SetPageActivity | old route | | | | a.kt |\n")
        path = feature_map.map_path(self.root)
        path.write_text(path.read_text() + "\n## Notes\n\nHuman note stays.\n")
        rows = self.root / "rows.md"
        rows.write_text("## Surfaces\n\n| Surface | Entry | How to reach | Source |\n|---|---|---|---|\n"
                        "| set page | SetPageActivity | Launch → Recents → tap set | a.kt:9 |\n"
                        "| Learn | LearnActivity | Set page → Learn | b.kt:1 |\n")
        result = feature_map.learn(self.root, rows)
        self.assertEqual((result["added"], result["replaced"]), (["Learn"], ["set page"]))
        text = path.read_text()
        surfaces, _ = feature_map.parse(text)
        self.assertEqual([row["How to reach"] for row in surfaces], ["Launch → Recents → tap set", "Set page → Learn"])
        self.assertIn("Human note stays.", text)

    def test_learn_refuses_logins_and_secrets(self) -> None:
        rows = self.root / "rows.md"
        rows.write_text("## Surfaces\n\n| Surface | How to reach | Prerequisites | Source |\n|---|---|---|---|\n"
                        "| Learn | Launch | log in as qa.user@example.com | b.kt |\n")
        with self.assertRaises(ValueError):
            feature_map.learn(self.root, rows)
        self.assertFalse(feature_map.map_path(self.root).exists())


class SetupSpawnTests(unittest.TestCase):
    def test_a_full_setup_absorbs_the_small_steps_and_the_map_runs_apart(self) -> None:
        spawns = setup_spawns([{"step": "profile", "blocking": True}, {"step": "app_id", "blocking": True},
                               {"step": "feature_map", "blocking": False}])
        self.assertEqual(spawns, [
            {"agent": "aw-setup", "steps": ["profile"], "tier": "strong", "blocking": True},
            {"agent": "aw-setup", "steps": ["feature_map"], "tier": "standard", "blocking": False},
        ])

    def test_small_steps_alone_use_the_standard_tier(self) -> None:
        spawns = setup_spawns([{"step": "formatter", "blocking": True}, {"step": "app_id", "blocking": True}])
        self.assertEqual(spawns, [{"agent": "aw-setup", "steps": ["formatter", "app_id"], "tier": "standard",
                                   "blocking": True}])

    def test_nothing_missing_spawns_nothing(self) -> None:
        self.assertEqual(setup_spawns([]), [])


if __name__ == "__main__":
    unittest.main()
