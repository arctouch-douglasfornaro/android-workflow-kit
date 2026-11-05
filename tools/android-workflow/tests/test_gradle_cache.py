import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_workflow import gradle_cache  # noqa: E402


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class SharedBuildCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.main, self.worktree = root / "main", root / "wt"
        self.main.mkdir()
        git(self.main, "init", "-q")
        (self.main / "settings.gradle").write_text("rootProject.name = 'x'\n")
        git(self.main, "add", ".")
        git(self.main, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
        git(self.main, "worktree", "add", "-q", str(self.worktree), "HEAD")
        self.home = mock.patch.object(Path, "home", return_value=root / "home")
        self.home.start()

    def tearDown(self) -> None:
        self.home.stop()
        self.tmp.cleanup()

    def test_a_gradle_run_in_a_worktree_points_at_the_main_checkout(self) -> None:
        parts, env = gradle_cache.wrap(["./gradlew", "assembleDebug"], self.worktree)
        self.assertEqual(parts[:3], ["./gradlew", "--init-script", str(gradle_cache.init_script())])
        self.assertEqual(parts[3:], ["assembleDebug"])
        self.assertEqual(env[gradle_cache.ROOT_ENV], str(self.main))
        self.assertIn("settingsEvaluated", gradle_cache.init_script().read_text())

    def test_the_main_checkout_another_program_or_the_switch_leave_the_command_alone(self) -> None:
        self.assertEqual(gradle_cache.wrap(["./gradlew", "help"], self.main), (["./gradlew", "help"], None))
        self.assertEqual(gradle_cache.wrap(["adb", "install"], self.worktree), (["adb", "install"], None))
        with mock.patch.dict(os.environ, {gradle_cache.ENV: "0"}):
            self.assertEqual(gradle_cache.wrap(["./gradlew", "help"], self.worktree), (["./gradlew", "help"], None))


if __name__ == "__main__":
    unittest.main()
