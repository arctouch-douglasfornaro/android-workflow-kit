import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import feature_setup as setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "settings.gradle.kts").write_text('include(":app")')
        (self.root / "build.gradle.kts").write_text('plugins { id("com.android.application") }')

    def test_first_run_persists_profile_and_probe_without_gradle(self):
        result = setup.ensure(self.root)
        self.assertTrue(result["created"])
        self.assertTrue(result["requires_explorer"])
        self.assertTrue(Path(result["probe"]).is_file())
        self.assertNotIn("<applicationId>", Path(result["profile"]).read_text())

    def test_accept_and_unchanged_run_do_not_probe(self):
        setup.ensure(self.root, no_probe=True)
        setup.ensure(self.root, accept=True)
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("unexpected probe")):
            self.assertFalse(setup.ensure(self.root)["requires_explorer"])

    def test_mtime_change_does_not_invalidate(self):
        before = setup.configuration_digest(self.root)
        os.utime(self.root / "build.gradle.kts", (1, 1))
        self.assertEqual(before, setup.configuration_digest(self.root))

    def test_module_configuration_change_invalidates_and_preserves_human_rows(self):
        setup.ensure(self.root, no_probe=True)
        profile = self.root / ".ai/project-profile.md"
        profile.write_text(profile.read_text() + "\nHuman fixture: 415\n")
        setup.ensure(self.root, accept=True)
        (self.root / "app").mkdir()
        (self.root / "app/build.gradle.kts").write_text('plugins { alias(libs.plugins.android) }')
        self.assertTrue(setup.ensure(self.root, no_probe=True)["requires_explorer"])
        self.assertIn("Human fixture: 415", profile.read_text())

    def test_accept_rejects_unprobed_configuration_change(self):
        setup.ensure(self.root, no_probe=True)
        (self.root / "gradle.properties").write_text("new=true")
        with self.assertRaises(ValueError):
            setup.ensure(self.root, accept=True)

    def test_no_probe_still_creates_profile(self):
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("unexpected probe")):
            self.assertTrue(Path(setup.ensure(self.root, no_probe=True)["profile"]).exists())

    def test_source_copy_only_when_configuration_matches(self):
        setup.ensure(self.root, no_probe=True)
        setup.ensure(self.root, accept=True)
        with tempfile.TemporaryDirectory() as target:
            target = Path(target)
            for name in ("settings.gradle.kts", "build.gradle.kts"):
                (target / name).write_text((self.root / name).read_text())
            result = setup.ensure(target, source=self.root, no_probe=True)
            self.assertTrue(result["copied_from_worktree_source"])
            self.assertFalse(result["requires_explorer"])

    def test_stale_source_not_copied(self):
        setup.ensure(self.root, no_probe=True)
        setup.ensure(self.root, accept=True)
        with tempfile.TemporaryDirectory() as target:
            target = Path(target)
            (target / "settings.gradle.kts").write_text('include(":different")')
            self.assertFalse(setup.ensure(target, source=self.root, no_probe=True)
                             ["copied_from_worktree_source"])

    def test_non_gradle_tree_rejected(self):
        (self.root / "settings.gradle.kts").unlink()
        with self.assertRaises(ValueError):
            setup.ensure(self.root)
        self.assertFalse((self.root / ".ai/project-profile.md").exists())

    def test_build_outputs_do_not_change_freshness(self):
        before = setup.configuration_digest(self.root)
        (self.root / "build").mkdir()
        (self.root / "build/build.gradle.kts").write_text("generated")
        self.assertEqual(before, setup.configuration_digest(self.root))

    def test_deleted_profile_cannot_inherit_complete_metadata(self):
        setup.ensure(self.root, no_probe=True)
        setup.ensure(self.root, accept=True)
        (self.root / ".ai/project-profile.md").unlink()
        self.assertTrue(setup.ensure(self.root, no_probe=True)["requires_explorer"])

    def test_human_profile_edits_require_reacceptance(self):
        setup.ensure(self.root, no_probe=True)
        setup.ensure(self.root, accept=True)
        profile = self.root / ".ai/project-profile.md"
        profile.write_text(profile.read_text() + "\nChanged install command\n")
        self.assertTrue(setup.ensure(self.root, no_probe=True)["requires_explorer"])

    def test_manifest_and_project_rules_invalidate_profile(self):
        for relative in ("app/src/main/AndroidManifest.xml", ".ai/rules/testing.md",
                         "build-logic/convention/src/main/kotlin/Android.kt"):
            with self.subTest(relative=relative):
                before = setup.configuration_digest(self.root)
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("new facts")
                self.assertNotEqual(before, setup.configuration_digest(self.root))

    def test_custom_named_included_convention_build_invalidates(self):
        (self.root / "settings.gradle.kts").write_text('includeBuild("build-conventions")')
        path = self.root / "build-conventions/src/main/kotlin/AndroidPlugin.kt"
        path.parent.mkdir(parents=True)
        path.write_text("old task")
        before = setup.configuration_digest(self.root)
        path.write_text("new task")
        self.assertNotEqual(before, setup.configuration_digest(self.root))


class StructuralRefreshTests(unittest.TestCase):
    CATALOG = (
        '[versions]\ncompose = "1.7.0"\nkotlin = "2.0.20"\n\n[libraries]\n'
        'core = { module = "androidx.core:core-ktx", version.ref = "compose" }\n'
        'okhttp = "com.squareup.okhttp3:okhttp:4.12.0"\n\n[plugins]\n'
        'kotlinter = { id = "org.jmailen.kotlinter", version = "4.4.1" }\n'
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "main"
        self.root.mkdir()
        (self.root / "settings.gradle.kts").write_text('include(":app")')
        (self.root / "build.gradle.kts").write_text(
            'plugins { id("com.android.application") version "8.5.0" }\n'
            'dependencies { implementation("androidx.room:room-ktx:2.6.1") }')
        (self.root / "gradle").mkdir()
        (self.root / "gradle/libs.versions.toml").write_text(self.CATALOG)
        (self.root / "app/src").mkdir(parents=True)
        (self.root / "app/src/Screen.kt").write_text("class Screen\n")
        setup.ensure(self.root, no_probe=True)
        profile = self.root / ".ai/project-profile.md"
        profile.write_text(profile.read_text() + "\nExemplar: `app/src/Screen.kt:1`\n")
        setup.ensure(self.root, accept=True)

    def bump_versions(self, root=None):
        root = root or self.root
        (root / "gradle/libs.versions.toml").write_text(
            self.CATALOG.replace("1.7.0", "1.8.0").replace("4.12.0", "4.12.1").replace("4.4.1", "4.5.0"))
        (root / "build.gradle.kts").write_text(
            'plugins { id("com.android.application") version "8.7.0" }\n'
            'dependencies { implementation("androidx.room:room-ktx:2.7.0") }')

    def test_version_bumps_do_not_change_the_structural_digest(self):
        exact, structural = setup.digests(self.root)
        self.bump_versions()
        new_exact, new_structural = setup.digests(self.root)
        self.assertNotEqual(exact, new_exact)
        self.assertEqual(structural, new_structural)

    def test_new_plugin_or_module_changes_the_structural_digest(self):
        _, structural = setup.digests(self.root)
        (self.root / "gradle/libs.versions.toml").write_text(
            self.CATALOG + 'detekt = { id = "io.gitlab.arturbosch.detekt", version = "1.0" }\n')
        self.assertNotEqual(structural, setup.digests(self.root)[1])
        (self.root / "gradle/libs.versions.toml").write_text(self.CATALOG)
        (self.root / "settings.gradle.kts").write_text('include(":app", ":feature")')
        self.assertNotEqual(structural, setup.digests(self.root)[1])

    def test_version_only_change_refreshes_without_an_agent(self):
        self.bump_versions()
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("unexpected probe")):
            result = setup.ensure(self.root)
        self.assertFalse(result["requires_explorer"])
        self.assertTrue(result["refreshed_without_agent"])
        self.assertEqual(setup.load_metadata(self.root)["configuration"], setup.configuration_digest(self.root))

    def test_structural_change_still_needs_the_agent(self):
        (self.root / "gradle/libs.versions.toml").write_text(
            self.CATALOG + 'detekt = { id = "io.gitlab.arturbosch.detekt" }\n')
        result = setup.ensure(self.root, no_probe=True)
        self.assertTrue(result["requires_explorer"])
        self.assertFalse(result["refreshed_without_agent"])

    def test_a_cited_file_that_disappeared_needs_the_agent(self):
        self.bump_versions()
        (self.root / "app/src/Screen.kt").unlink()
        result = setup.ensure(self.root, no_probe=True)
        self.assertTrue(result["requires_explorer"])

    def test_a_moved_line_does_not_need_the_agent(self):
        self.bump_versions()
        (self.root / "app/src/Screen.kt").write_text("// header\nclass Screen\n")
        self.assertFalse(setup.ensure(self.root, no_probe=True)["requires_explorer"])

    def test_force_still_reprobes(self):
        self.bump_versions()
        self.assertTrue(setup.ensure(self.root, no_probe=True, force=True)["requires_explorer"])

    def test_worktree_with_only_version_drift_inherits_the_profile(self):
        worktree = Path(self.tmp.name) / "wt"
        (worktree / "gradle").mkdir(parents=True)
        (worktree / "app/src").mkdir(parents=True)
        for name in ("settings.gradle.kts", "build.gradle.kts", "gradle/libs.versions.toml", "app/src/Screen.kt"):
            (worktree / name).write_text((self.root / name).read_text())
        self.bump_versions(worktree)
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("unexpected probe")):
            result = setup.ensure(worktree, source=self.root)
        self.assertTrue(result["copied_from_worktree_source"])
        self.assertFalse(result["requires_explorer"])

    def test_untouched_minimal_profile_in_a_worktree_is_replaced_by_the_source_profile(self):
        worktree = Path(self.tmp.name) / "wt"
        (worktree / "gradle").mkdir(parents=True)
        (worktree / "app/src").mkdir(parents=True)
        for name in ("settings.gradle.kts", "build.gradle.kts", "gradle/libs.versions.toml", "app/src/Screen.kt"):
            (worktree / name).write_text((self.root / name).read_text())
        setup.ensure(worktree, no_probe=True)
        result = setup.ensure(worktree, source=self.root, no_probe=True)
        self.assertTrue(result["copied_from_worktree_source"])
        self.assertIn("Exemplar", (worktree / ".ai/project-profile.md").read_text())

    def test_worktree_with_a_structural_difference_is_not_copied(self):
        worktree = Path(self.tmp.name) / "wt"
        (worktree / "gradle").mkdir(parents=True)
        (worktree / "app/src").mkdir(parents=True)
        for name in ("build.gradle.kts", "gradle/libs.versions.toml", "app/src/Screen.kt"):
            (worktree / name).write_text((self.root / name).read_text())
        (worktree / "settings.gradle.kts").write_text('include(":app", ":other")')
        result = setup.ensure(worktree, source=self.root, no_probe=True)
        self.assertFalse(result["copied_from_worktree_source"])

    def test_wrapper_version_bump_is_not_structural(self):
        wrapper = self.root / "gradle/wrapper/gradle-wrapper.properties"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10-bin.zip\n")
        _, structural = setup.digests(self.root)
        wrapper.write_text("distributionUrl=https\\://services.gradle.org/distributions/gradle-8.11-bin.zip\n")
        self.assertEqual(structural, setup.digests(self.root)[1])


if __name__ == "__main__":
    unittest.main()
