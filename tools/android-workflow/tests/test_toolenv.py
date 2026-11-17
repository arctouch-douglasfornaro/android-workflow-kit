"""PATH and JAVA_HOME for a CLI run started by a shell that never read `.zshrc`."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import toolenv


def fake_jdk(home: Path, version: str) -> Path:
    (home / "bin").mkdir(parents=True)
    java = home / "bin" / "java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(java.stat().st_mode | stat.S_IXUSR)
    (home / "release").write_text(f'JAVA_VERSION="{version}"\n')
    return home


class ToolEnvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        (self.home / ".gradle").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        patches = [mock.patch.object(Path, "home", return_value=self.home),
                   mock.patch.object(toolenv, "EXTRA_PATH_DIRS", (str(self.root / "localbin"),))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_gh_outside_path_is_found_in_the_tool_folders(self) -> None:
        folder = self.root / "localbin"
        folder.mkdir()
        gh = folder / "gh"
        gh.write_text("#!/bin/sh\n")
        gh.chmod(0o755)
        self.assertEqual(toolenv.find_gh("/usr/bin:/bin"), str(gh))
        env = {"PATH": "/usr/bin:/bin", "JAVA_HOME": str(fake_jdk(self.root / "jdk", "21"))}
        changed = toolenv.prepare(None, env)
        self.assertEqual(env["PATH"], f"/usr/bin:/bin:{folder}")
        self.assertEqual(changed["path_added"], [str(folder)])
        self.assertNotIn("java_home", changed)

    def test_a_removed_java_home_is_replaced_by_gradles_jdk_of_the_daemon_version(self) -> None:
        jdks = self.home / ".gradle" / "jdks"
        fake_jdk(jdks / "vendor-17" / "jdk17" / "Contents" / "Home", "17.0.2")
        wanted = fake_jdk(jdks / "vendor-21" / "jdk21" / "Contents" / "Home", "21.0.11")
        target = self.root / "app"
        (target / "gradle").mkdir(parents=True)
        (target / "gradle" / "gradle-daemon-jvm.properties").write_text("toolchainVersion=21\n")
        env = {"PATH": "/usr/bin", "JAVA_HOME": str(self.root / "gone")}
        with mock.patch.object(toolenv, "system_homes", return_value=[]):
            changed = toolenv.prepare(target, env)
        self.assertEqual(env["JAVA_HOME"], str(wanted))
        self.assertTrue(env["PATH"].startswith(str(wanted / "bin")))
        self.assertEqual(changed["java_home_was"], str(self.root / "gone"))

    def test_org_gradle_java_home_wins(self) -> None:
        chosen = fake_jdk(self.root / "chosen", "21")
        (self.home / ".gradle" / "gradle.properties").write_text(f"org.gradle.java.home={chosen}\n")
        self.assertEqual(toolenv.find_java_home(None, None), str(chosen))

    def test_a_valid_java_home_is_kept(self) -> None:
        kept = fake_jdk(self.root / "kept", "17")
        self.assertEqual(toolenv.find_java_home(None, str(kept)), str(kept))


if __name__ == "__main__":
    unittest.main()
