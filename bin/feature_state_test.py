#!/usr/bin/env python3
"""Isolated regression tests: PYTHONDONTWRITEBYTECODE=1 python3 feature_state_test.py.

Commits only inside TemporaryDirectory, identity supplied through environment.
No git config writes, remote pushes, hook bypasses or third-party dependencies.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
import feature_state as fs


class FeatureStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(
            HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "config"),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Feature State Test", GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Feature State Test", GIT_COMMITTER_EMAIL="test@example.invalid",
        )
        self.environment = mock.patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "-b", "main")
        self.write("build.gradle.kts", "// root\n")
        self.write("app/build.gradle.kts", "// app\n")
        self.write("app/src/main/A.kt", "class A\n")
        self.write("app/src/main/B.kt", "class B\n")
        self.write("old/build.gradle", "// deleted module\n")
        self.write("old/src/Old.kt", "class Old\n")
        self.write(".gitignore", "out/\n")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-b", "feature")
        self.directory = self.repo / ".ai/workflow/test"
        self.directory.mkdir(parents=True)

    def git(self, *args, check=True, root=None):
        completed = subprocess.run(
            ["git", "--no-pager", "-C", str(root or self.repo), *args],
            capture_output=True, text=True, check=check,
        )
        return completed.stdout

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def snap(self, base=None):
        return fs.snapshot(self.repo, base or self.base)

    def evidence(self, name="review.md", text="Reviewed source and call sites."):
        target = self.directory / name
        target.write_text(text)
        return target

    def gate(self, code="print('tests passed')", phase="gate"):
        return fs.run(self.repo, self.directory, self.base, phase, phase + ".log",
                      [sys.executable, "-c", code])

    def approve(self):
        self.assertEqual("PASS", self.gate()["status"])
        expected = self.snap()["fingerprint"]
        fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                  expected_fingerprint=expected)
        fs.record(self.repo, self.directory, self.base, "device", "NOT_REQUIRED",
                  self.evidence("device.md", "No UI changes."), "Source-only helper",
                  expected_fingerprint=expected)

    def test_surface_committed_staged_unstaged_untracked_deleted_both_rename_sides(self):
        self.git("mv", "app/src/main/A.kt", "app/src/main/Renamed.kt")
        self.git("commit", "-m", "rename")
        self.git("mv", "app/src/main/B.kt", "app/src/main/Staged.kt")
        self.write("app/src/main/Staged.kt", "unstaged content\n")
        self.write("app/src/main/new\nodd name.kt", "class New\n")
        shutil.rmtree(self.repo / "old")
        snap = self.snap()
        self.assertEqual({
            "app/src/main/A.kt", "app/src/main/Renamed.kt", "app/src/main/B.kt",
            "app/src/main/Staged.kt", "app/src/main/new\nodd name.kt",
            "old/build.gradle", "old/src/Old.kt",
        }, set(snap["paths"]))
        self.assertEqual(["app", "old"], snap["modules"])
        self.assertEqual("deleted", snap["working_overrides"]["old/src/Old.kt"]["mode"])
        self.assertEqual(self.base, snap["base_commit"])
        self.assertEqual("feature", snap["branch"])

    def test_deleted_committed_build_directory_uses_merge_base_module(self):
        self.git("rm", "-r", "old")
        self.git("commit", "-m", "remove old module")
        self.assertIn("old", self.snap()["modules"])

    def test_actual_merge_base_not_base_tip_drives_surface(self):
        self.git("checkout", "main")
        self.write("main-only.txt", "not on feature\n")
        self.git("add", ".")
        self.git("commit", "-m", "main only")
        tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "feature")
        snap = self.snap("main")
        self.assertEqual(self.base, snap["merge_base"])
        self.assertEqual(tip, snap["base_commit"])
        self.assertEqual([], snap["paths"])

    def test_deterministic_and_does_not_mutate_index(self):
        index = self.repo / ".git/index"
        before = index.read_bytes()
        self.assertEqual(self.snap(), self.snap())
        self.assertEqual(before, index.read_bytes())

    def test_precise_exclusions_not_whole_ai_tree(self):
        initial = self.snap()
        self.write(".ai/workflow/arbitrary/test.txt", "artifact\n")
        for path in fs.EXCLUDED:
            self.write(path, "local profile\n")
        self.write("out/generated", "ignored untracked build output\n")
        self.assertEqual(initial, self.snap())
        self.write(".ai/skills/feature.md", "real source\n")
        self.write(".ai/project-profile.team.json", "not excluded\n")
        self.assertEqual([".ai/project-profile.team.json", ".ai/skills/feature.md"],
                         self.snap()["paths"])

    def test_unchanged_tracked_inputs_and_restored_stat_invalidate(self):
        initial = self.snap()["fingerprint"]
        path = self.repo / "app/src/main/A.kt"
        info = path.stat()
        path.write_text("class Z\n")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertNotEqual(initial, self.snap()["fingerprint"])

    def test_assume_unchanged_and_skip_worktree_do_not_hide_changes(self):
        self.snap()  # Populate cache before index flags and hidden edits.
        self.git("update-index", "--assume-unchanged", "app/src/main/A.kt")
        self.git("update-index", "--skip-worktree", "app/src/main/B.kt")
        self.write("app/src/main/A.kt", "class ChangedA\n")
        self.write("app/src/main/B.kt", "class ChangedB\n")
        self.assertEqual(["app/src/main/A.kt", "app/src/main/B.kt"], self.snap()["paths"])

    def test_warm_cache_skips_regular_file_reads_and_matches_uncached(self):
        uncached = fs.snapshot(self.repo, self.base, use_cache=False)
        self.assertFalse((self.repo / fs.CACHE_PATH).exists())
        self.assertEqual(uncached, self.snap())
        real_open = os.open
        reads = []

        def watch(path, flags, *args, **kwargs):
            if flags & os.O_NOFOLLOW and not flags & os.O_CREAT:
                reads.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(fs.os, "open", side_effect=watch):
            warm = self.snap()
        self.assertEqual([], reads)
        self.assertEqual(uncached, warm)
        cache = json.loads((self.repo / fs.CACHE_PATH).read_bytes())
        self.assertEqual(str(self.repo), cache["repo_root"])
        self.assertEqual("sha1", cache["blob_algorithm"])
        self.assertIn(str(self.repo / "app/src/main/A.kt"), cache["files"])

    def test_cache_restored_mtime_hidden_edit_invalidates_via_ctime(self):
        before = self.snap()
        path = self.repo / "app/src/main/A.kt"
        cached = json.loads((self.repo / fs.CACHE_PATH).read_bytes())["files"][str(path)]
        self.git("update-index", "--assume-unchanged", "app/src/main/A.kt")
        info = path.stat()
        path.write_text("class Z\n")  # Same size, restored mtime; ctime must differ.
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(cached["stat"][4], path.stat().st_mtime_ns)
        self.assertNotEqual(cached["stat"][5], path.stat().st_ctime_ns)
        after = self.snap()
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        self.assertEqual(after, fs.snapshot(self.repo, self.base, use_cache=False))

    def test_each_stat_identity_field_invalidates_cache_entry(self):
        self.snap()
        path = "app/src/main/A.kt"
        cache = json.loads((self.repo / fs.CACHE_PATH).read_bytes())["files"]
        expected = fs._working(self.repo, path, "sha1")
        for field in range(6):
            with self.subTest(field=field):
                altered = dict(cache[str(self.repo / path)])
                altered["stat"] = altered["stat"][:]
                altered["stat"][field] += 1
                altered.update(sha256="0" * 64, oid="0" * 40)
                updated = {}
                actual = fs._working(self.repo, path, "sha1",
                                     {str(self.repo / path): altered}, updated)
                self.assertEqual(expected, actual)
                self.assertNotEqual(altered, updated[str(self.repo / path)])

    def test_stale_cache_replacement_deletion_and_symlink_never_reused(self):
        self.snap()
        path = self.repo / "app/src/main/A.kt"
        original = path.stat()
        replacement = self.write("replacement", "class Z\n")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        os.replace(replacement, path)
        self.assertEqual(self.snap(), fs.snapshot(self.repo, self.base, use_cache=False))
        path.unlink()
        deleted = self.snap()
        self.assertEqual({"mode": "deleted"}, deleted["working_overrides"]["app/src/main/A.kt"])
        path.symlink_to("target")
        linked = self.snap()
        cache = json.loads((self.repo / fs.CACHE_PATH).read_bytes())
        self.assertNotIn(str(path), cache["files"])
        path.unlink()
        path.symlink_to("other")
        changed = self.snap()
        self.assertNotEqual(linked["content_fingerprint"], changed["content_fingerprint"])
        self.assertEqual(changed, fs.snapshot(self.repo, self.base, use_cache=False))

    def test_corrupt_incompatible_or_foreign_cache_is_a_miss(self):
        expected = self.snap()
        path = self.repo / fs.CACHE_PATH
        good = json.loads(path.read_bytes())
        bad_entries = {key: None for key in good["files"]}
        variants = [
            b"broken json", b"[]",
            fs._json(dict(good, version=-1)).encode(),
            fs._json(dict(good, repo_root="/another/repo")).encode(),
            fs._json(dict(good, blob_algorithm="sha256")).encode(),
            fs._json(dict(good, content_algorithm="sha1")).encode(),
            fs._json(dict(good, files=bad_entries)).encode(),
        ]
        for data in variants:
            with self.subTest(data=data[:60]):
                path.write_bytes(data)
                self.assertEqual(expected, self.snap())

    def test_cache_symlink_and_failed_atomic_write_do_not_change_source_identity(self):
        expected = self.snap()
        path = self.repo / fs.CACHE_PATH
        path.unlink()
        outside = self.home / "outside-cache"
        outside.write_text("untouched")
        path.symlink_to(outside)
        self.assertEqual(expected, self.snap())
        self.assertEqual("untouched", outside.read_text())
        path.unlink()
        self.snap()
        old = path.read_bytes()
        with mock.patch.object(fs.os, "replace", side_effect=OSError("cache unavailable")):
            self.assertEqual(expected, self.snap())
        self.assertEqual(old, path.read_bytes())
        self.assertEqual([], list(path.parent.glob(".feature-state-*")))

    def test_no_cache_cli_and_api_leave_cache_untouched(self):
        self.snap()
        path = self.repo / fs.CACHE_PATH
        path.write_text("intentionally invalid")
        result = fs.snapshot(self.repo, self.base, use_cache=False)
        code, cli_result = self.cli("--no-cache", "snapshot")
        self.assertEqual(0, code)
        self.assertEqual(result, cli_result)
        self.assertEqual("intentionally invalid", path.read_text())
        path.unlink()
        self.approve()
        ready = fs.verify_ready(self.repo, self.directory, use_cache=False)
        self.assertEqual(result["content_fingerprint"], ready["content_fingerprint"])

    def test_working_bytes_win_over_staged_bytes(self):
        path = "app/src/main/A.kt"
        self.write(path, "staged\n")
        self.git("add", path)
        self.write(path, "working\n")
        first = self.snap()
        self.assertEqual(fs._digest(b"working\n"), first["working_overrides"][path]["sha256"])
        self.write(path, "different staged\n")
        self.git("add", path)
        self.write(path, "working\n")
        self.assertEqual(first["fingerprint"], self.snap()["fingerprint"])
        self.write(path, "changed working\n")
        self.assertNotEqual(first["fingerprint"], self.snap()["fingerprint"])

    def test_modes_are_bound(self):
        original = self.snap()["fingerprint"]
        (self.repo / "app/src/main/A.kt").chmod(0o755)
        self.assertNotEqual(original, self.snap()["fingerprint"])
        self.assertEqual("100755", self.snap()["working_overrides"]["app/src/main/A.kt"]["mode"])

    def test_symlink_hashes_link_not_external_referent(self):
        outside = self.home / "external"
        outside.write_text("private\n")
        link = self.repo / "link"
        link.symlink_to(outside)
        initial = self.snap()
        self.assertEqual("120000", initial["working_overrides"]["link"]["mode"])
        outside.write_text("changed private\n")
        self.assertEqual(initial, self.snap())
        link.unlink()
        link.symlink_to("missing-target")
        self.assertNotEqual(initial["fingerprint"], self.snap()["fingerprint"])

    def test_symlink_parent_and_special_file_rejected(self):
        shutil.rmtree(self.repo / "app/src")
        (self.repo / "app/src").symlink_to(self.home)
        with self.assertRaisesRegex(ValueError, "symlink parent"):
            self.snap()
        (self.repo / "app/src").unlink()
        if hasattr(os, "mkfifo"):
            (self.repo / "old/src/Old.kt").unlink()
            os.mkfifo(self.repo / "old/src/Old.kt")
            with self.assertRaisesRegex(ValueError, "unsupported source"):
                self.snap()

    def test_uninitialized_gitlink_rejected_with_setup_instruction(self):
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.base + ",vendor")
        with self.assertRaisesRegex(ValueError, "submodule update --init --recursive"):
            self.snap()
        (self.repo / "vendor").mkdir()
        with self.assertRaisesRegex(ValueError, "uninitialized"):
            self.snap()

    def submodule(self, parent=None, name="vendor"):
        """Build a local gitlink fixture without config writes or network transport."""
        parent = parent or self.repo
        child = parent / name
        child.mkdir()
        self.git("init", "-b", "main", root=child)
        (child / "source.txt").write_text("module source\n")
        self.git("add", ".", root=child)
        self.git("commit", "-m", "module baseline", root=child)
        (parent / ".gitmodules").write_text(
            '[submodule "' + name + '"]\n\tpath = ' + name + '\n\turl = ./fixture\n')
        head = self.git("rev-parse", "HEAD", root=child).strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + head + "," + name,
                 root=parent)
        self.git("add", ".gitmodules", root=parent)
        self.git("commit", "-m", "register module", root=parent)
        return child

    def test_clean_initialized_submodule_gitfile_and_approvals(self):
        child = self.submodule()
        # Normal initialized submodules use a .git file rather than a directory.
        gitdir = self.home / "module-gitdir"
        shutil.move(child / ".git", gitdir)
        (child / ".git").write_text("gitdir: " + str(gitdir) + "\n")
        head = self.git("rev-parse", "HEAD", root=child).strip()
        parent_index = (self.repo / ".git/index").read_bytes()
        child_index = (gitdir / "index").read_bytes()
        snap = self.snap("HEAD")
        self.assertEqual({"vendor": head}, snap["submodules"])
        self.assertNotIn("vendor", snap["paths"])
        self.assertEqual(snap, self.snap("HEAD"))
        self.assertEqual(parent_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(child_index, (gitdir / "index").read_bytes())
        self.approve()
        self.assertEqual("PASS", fs.verify_ready(self.repo, self.directory)["status"])

    def test_dirty_submodule_worktree_index_and_untracked_rejected(self):
        child = self.submodule()
        for kind in ("worktree", "index", "untracked"):
            with self.subTest(kind=kind):
                path = child / ("new.txt" if kind == "untracked" else "source.txt")
                path.write_text("dirty\n")
                if kind == "index":
                    self.git("add", "source.txt", root=child)
                    (child / "source.txt").write_text("module source\n")
                with self.assertRaisesRegex(ValueError, "dirty submodule"):
                    self.snap()
                if kind == "untracked":
                    path.unlink()
                else:
                    self.git("restore", "--source=HEAD", "--staged", "--worktree",
                             "source.txt", root=child)

    def test_clean_submodule_changed_actual_head_bound_before_and_after_commit(self):
        child = self.submodule()
        self.approve()
        before = self.snap()
        (child / "source.txt").write_text("new committed source\n")
        self.git("add", ".", root=child)
        self.git("commit", "-m", "new module head", root=child)
        head = self.git("rev-parse", "HEAD", root=child).strip()
        after = self.snap()
        self.assertIn("vendor", after["paths"])
        self.assertEqual({"mode": "160000", "head": head}, after["working_overrides"]["vendor"])
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        self.assertNotEqual(before["content_fingerprint"], after["content_fingerprint"])
        with self.assertRaisesRegex(ValueError, "stale fingerprint"):
            fs.verify_ready(self.repo, self.directory)
        self.git("add", "vendor")
        self.git("commit", "-m", "advance module")
        self.assertEqual(after["content_fingerprint"], self.snap()["content_fingerprint"])

    def test_nested_submodules_clean_dirty_absent_and_changed_head(self):
        child = self.submodule()
        nested = self.submodule(parent=child, name="nested")
        self.git("add", "vendor")
        self.git("commit", "-m", "register nested module")
        self.snap()
        (nested / "new.txt").write_text("dirty nested\n")
        with self.assertRaisesRegex(ValueError, "dirty submodule.*nested"):
            self.snap()
        (nested / "new.txt").unlink()
        self.git("commit", "--allow-empty", "-m", "advance nested HEAD", root=nested)
        with self.assertRaisesRegex(ValueError, "dirty submodule"):
            self.snap()
        shutil.rmtree(nested)
        with self.assertRaisesRegex(ValueError, "uninitialized.*nested"):
            self.snap()

    def test_submodule_git_errors_not_treated_as_clean(self):
        child = self.submodule()
        (child / ".git/HEAD").write_text("invalid head\n")
        with self.assertRaisesRegex(RuntimeError, "submodule.*submodule update"):
            self.snap()

    def test_nested_submodule_hidden_edits_rejected_with_warm_parent_cache(self):
        child = self.submodule()
        nested = self.submodule(parent=child, name="nested")
        self.git("add", "vendor")
        self.git("commit", "-m", "nested fixture")
        self.approve()
        for target in (child, nested):
            for flag in ("assume-unchanged", "skip-worktree"):
                with self.subTest(target=target.name, flag=flag):
                    self.snap()
                    source = target / "source.txt"
                    info = source.stat()
                    self.git("update-index", "--" + flag, "source.txt", root=target)
                    source.write_text("hidden source\n")
                    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
                    self.assertEqual("", self.git(
                        "status", "--porcelain", "--untracked-files=all",
                        "--ignore-submodules=none", root=target))
                    with self.assertRaisesRegex(ValueError, "dirty submodule.*source.txt"):
                        self.snap()
                    with self.assertRaisesRegex(ValueError, "dirty submodule"):
                        fs.verify_ready(self.repo, self.directory)
                    self.git("update-index", "--no-" + flag, "source.txt", root=target)
                    self.git("restore", "source.txt", root=target)

    def test_submodule_cache_lives_only_in_parent_and_reuses_raw_hashes(self):
        child = self.submodule()
        nested = self.submodule(parent=child, name="nested")
        self.git("add", "vendor")
        self.git("commit", "-m", "nested fixture")
        uncached = fs.snapshot(self.repo, self.base, use_cache=False)
        self.assertEqual(uncached, self.snap())
        cache = json.loads((self.repo / fs.CACHE_PATH).read_bytes())["files"]
        for target in (child, nested):
            self.assertIn(str(target / "source.txt"), cache)
            self.assertFalse((target / ".ai").exists())
        real_open = os.open
        reads = []

        def watch(path, flags, *args, **kwargs):
            if flags & os.O_NOFOLLOW and not flags & os.O_CREAT:
                reads.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(fs.os, "open", side_effect=watch):
            self.assertEqual(uncached, self.snap())
        self.assertEqual([], reads)
        for target in (child, nested):
            self.assertEqual("", self.git("status", "--porcelain", root=target))

    def test_submodule_profile_workflow_and_tracked_ignored_inputs_are_not_excluded(self):
        child = self.submodule()
        paths = (".ai/project-profile.md", ".ai/workflow/source.txt", "out/tracked.txt")
        (child / ".gitignore").write_text("out/\n")
        for name in paths:
            path = child / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("tracked input\n")
        self.git("add", "-f", ".", root=child)
        self.git("commit", "-m", "tracked dependencies", root=child)
        self.git("add", "vendor")
        self.git("commit", "-m", "update dependency")
        self.snap()
        for name in paths:
            with self.subTest(path=name):
                self.git("update-index", "--assume-unchanged", name, root=child)
                (child / name).write_text("hidden edit\n")
                self.assertEqual("", self.git("status", "--porcelain", root=child))
                with self.assertRaisesRegex(ValueError, "tracked bytes/mode differ"):
                    self.snap()
                self.git("update-index", "--no-assume-unchanged", name, root=child)
                self.git("restore", name, root=child)

    def test_hidden_submodule_modes_deletions_and_symlink_changes_rejected(self):
        child = self.submodule()
        (child / "link").symlink_to("source.txt")
        self.git("add", "link", root=child)
        self.git("commit", "-m", "dependency link", root=child)
        self.git("add", "vendor")
        self.git("commit", "-m", "update dependency")
        self.snap()
        for kind in ("mode", "deleted", "symlink"):
            with self.subTest(kind=kind):
                name = "link" if kind == "symlink" else "source.txt"
                path = child / name
                self.git("update-index", "--skip-worktree", name, root=child)
                if kind == "mode":
                    path.chmod(0o755)
                elif kind == "deleted":
                    path.unlink()
                else:
                    path.unlink()
                    path.symlink_to("other")
                self.assertEqual("", self.git("status", "--porcelain", root=child))
                with self.assertRaisesRegex(ValueError, "tracked bytes/mode differ"):
                    self.snap()
                self.git("update-index", "--no-skip-worktree", name, root=child)
                self.git("restore", name, root=child)

    def test_deleted_submodule_content_stable_across_commit_and_base(self):
        child = self.submodule()
        before = self.snap()
        self.git("update-index", "--force-remove", "vendor")
        shutil.rmtree(child)
        deleted = self.snap()
        self.assertEqual({"mode": "deleted"}, deleted["working_overrides"]["vendor"])
        self.assertNotEqual(before["content_fingerprint"], deleted["content_fingerprint"])
        self.git("commit", "-m", "remove module")
        self.assertEqual(deleted["content_fingerprint"], self.snap("HEAD")["content_fingerprint"])

    def test_content_identity_survives_commit_base_branch_root_changes(self):
        self.write("new.txt", "new source\n")
        self.write("app/src/main/A.kt", "updated\n")
        (self.repo / "app/src/main/B.kt").unlink()
        before = self.snap()
        self.approve()
        ready = fs.verify_ready(self.repo, self.directory)
        self.assertEqual(before["content_fingerprint"], ready["content_fingerprint"])
        self.git("add", "new.txt", "app")
        self.git("commit", "-m", "ship working content")
        self.git("checkout", "-b", "shipped")
        after = self.snap("HEAD")
        self.assertEqual(before["content_fingerprint"], after["content_fingerprint"])
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        with self.assertRaisesRegex(ValueError, "stale fingerprint"):
            fs.verify_ready(self.repo, self.directory)
        copy = self.home / "copy"
        shutil.copytree(self.repo, copy)
        self.assertEqual(after["content_fingerprint"],
                         fs.snapshot(copy, "HEAD")["content_fingerprint"])

    def test_content_identity_detects_hook_edits_modes_links_and_newly_tracked_ignored(self):
        before = self.snap()["content_fingerprint"]
        self.write("app/src/main/A.kt", "hook modification\n")
        self.assertNotEqual(before, self.snap()["content_fingerprint"])
        self.git("restore", "app/src/main/A.kt")
        (self.repo / "app/src/main/A.kt").chmod(0o755)
        self.assertNotEqual(before, self.snap()["content_fingerprint"])
        (self.repo / "app/src/main/A.kt").chmod(0o644)
        link = self.repo / "link"
        link.symlink_to("target")
        linked = self.snap()["content_fingerprint"]
        link.unlink()
        link.symlink_to("other")
        self.assertNotEqual(linked, self.snap()["content_fingerprint"])
        link.unlink()
        self.write("out/input", "formerly ignored\n")
        self.assertEqual(before, self.snap()["content_fingerprint"])
        self.git("add", "-f", "out/input")
        tracked = self.snap()["content_fingerprint"]
        self.assertNotEqual(before, tracked)
        self.git("commit", "-m", "track ignored input")
        self.assertEqual(tracked, self.snap()["content_fingerprint"])

    def test_conflicts_rejected(self):
        self.git("checkout", "main")
        self.write("app/src/main/A.kt", "main conflict\n")
        self.git("add", ".")
        self.git("commit", "-m", "main change")
        self.git("checkout", "feature")
        self.write("app/src/main/A.kt", "feature conflict\n")
        self.git("add", ".")
        self.git("commit", "-m", "feature change")
        self.git("merge", "main", check=False)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.snap()

    def test_conflicts_in_excluded_profile_still_rejected(self):
        self.write(".ai/project-profile.md", "initial\n")
        self.git("add", ".")
        self.git("commit", "-m", "track profile")
        self.git("branch", "profile-other")
        self.write(".ai/project-profile.md", "feature\n")
        self.git("add", ".")
        self.git("commit", "-m", "feature profile")
        self.git("checkout", "profile-other")
        self.write(".ai/project-profile.md", "other\n")
        self.git("add", ".")
        self.git("commit", "-m", "other profile")
        self.git("merge", "feature", check=False)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.snap()

    def test_tracked_ignored_file_and_unstaged_rename_are_included(self):
        self.write("out/input.txt", "tracked despite ignore\n")
        self.git("add", "-f", "out/input.txt")
        self.git("commit", "-m", "tracked input")
        first = self.snap()["fingerprint"]
        self.write("out/input.txt", "edited\n")
        (self.repo / "app/src/main/A.kt").rename(self.repo / "app/src/main/Moved.kt")
        changed = self.snap()
        self.assertNotEqual(first, changed["fingerprint"])
        self.assertTrue({"out/input.txt", "app/src/main/A.kt", "app/src/main/Moved.kt"}
                        <= set(changed["paths"]))

    def test_git_errors_and_wrong_root_fail_closed(self):
        with self.assertRaises(RuntimeError):
            self.snap("not-a-ref")
        with self.assertRaises(RuntimeError):
            fs.snapshot(self.home, "HEAD")
        with self.assertRaises(ValueError):
            fs.snapshot(self.repo / "app", self.base)

    def test_root_branch_head_and_base_are_bound(self):
        first = self.snap()["fingerprint"]
        self.git("checkout", "-b", "another")
        self.assertNotEqual(first, self.snap()["fingerprint"])
        self.git("checkout", "feature")
        self.git("commit", "--allow-empty", "-m", "new head")
        second = self.snap()["fingerprint"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, self.snap("HEAD")["fingerprint"])
        copy = self.home / "copy"
        shutil.copytree(self.repo, copy)
        self.assertNotEqual(second, fs.snapshot(copy, self.base)["fingerprint"])

    def test_widening_build_resources_schema_di_including_removed_annotations(self):
        for path in ("settings.gradle.kts", "gradle/libs.versions.toml",
                     "app/src/main/res/values/strings.xml", "app/schemas/1.json",
                     "app/src/main/di/Bindings.kt"):
            self.write(path, "changed\n")
        reasons = "\n".join(self.snap()["widening_reasons"])
        for category in ("build/settings", "resources/manifest", "schema/migration",
                         "dependency-injection"):
            self.assertIn(category, reasons)
        self.write("app/src/main/A.kt", "@Inject class A\n")
        self.git("add", ".")
        self.git("commit", "-m", "DI baseline")
        base = self.git("rev-parse", "HEAD").strip()
        self.write("app/src/main/A.kt", "class A\n")
        snap = self.snap(base)
        self.assertTrue(snap["widening_required"])
        self.assertIn("dependency-injection", snap["widening_reasons"][0])

    def test_ordinary_source_change_does_not_require_widening(self):
        self.write("app/src/main/A.kt", "class Other\n")
        self.assertFalse(self.snap()["widening_required"])

    def test_all_required_and_selected_phase_check(self):
        with self.assertRaisesRegex(ValueError, "missing phase"):
            fs.verify_ready(self.repo, self.directory)
        self.assertEqual("PASS", self.gate()["status"])
        self.assertEqual("PASS", fs.check(self.repo, self.directory, phases=("gate",))["status"])
        with self.assertRaisesRegex(ValueError, "review"):
            fs.verify_ready(self.repo, self.directory)
        self.approve()
        self.assertEqual("PASS", fs.verify_ready(self.repo, self.directory)["status"])

    def test_stale_approval_and_evidence_tampering(self):
        self.approve()
        self.evidence(text="silently replaced review")
        with self.assertRaisesRegex(ValueError, "evidence content changed"):
            fs.verify_ready(self.repo, self.directory)
        self.approve()
        self.write("app/src/main/A.kt", "unreviewed\n")
        with self.assertRaisesRegex(ValueError, "stale fingerprint"):
            fs.verify_ready(self.repo, self.directory)

    def test_manual_gate_pass_and_gate_review_skip_refused(self):
        evidence = self.evidence()
        for phase, status, reason in (
                ("gate", "PASS", None), ("gate", "NOT_REQUIRED", "skip"),
                ("review", "NOT_REQUIRED", "skip"), ("device", "NOT_REQUIRED", " ")):
            with self.subTest(phase=phase, status=status):
                with self.assertRaises(ValueError):
                    fs.record(self.repo, self.directory, self.base, phase, status, evidence, reason)

    def test_manual_device_pass_and_review_trust_boundary(self):
        self.gate()
        expected = self.snap()["fingerprint"]
        receipt = fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                            expected_fingerprint=expected)
        self.assertIn("not independently verified", receipt["trust_boundary"])
        fs.record(self.repo, self.directory, self.base, "device", "PASS", self.evidence("device.md"),
                  expected_fingerprint=expected)
        self.assertEqual("PASS", fs.verify_ready(self.repo, self.directory)["status"])

    def test_positive_manual_records_require_start_fingerprint(self):
        self.approve()
        state_path = self.directory / fs.STATE_FILE
        before = state_path.read_bytes()
        for phase, status in (("review", "PASS"), ("device", "PASS"), ("device", "NOT_REQUIRED")):
            with self.subTest(phase=phase, status=status):
                with self.assertRaisesRegex(ValueError, "requires --expected-fingerprint"):
                    fs.record(self.repo, self.directory, self.base, phase, status,
                              self.evidence("manual.md"), "No UI")
                self.assertEqual(before, state_path.read_bytes())

    def test_stale_manual_work_cannot_approve_new_source_with_fresh_prerequisites(self):
        self.approve()
        started = self.snap()["fingerprint"]
        self.write("app/src/main/A.kt", "new source\n")
        self.gate()
        current = self.snap()["fingerprint"]
        with self.assertRaisesRegex(ValueError, "expected fingerprint differs"):
            fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                      expected_fingerprint=started)
        fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                  expected_fingerprint=current)
        for status in ("PASS", "NOT_REQUIRED"):
            with self.assertRaisesRegex(ValueError, "expected fingerprint differs"):
                fs.record(self.repo, self.directory, self.base, "device", status,
                          self.evidence("device.md"), "No UI", expected_fingerprint=started)

    def test_manual_phase_prerequisites_missing_failed_stale_and_tampered(self):
        expected = self.snap()["fingerprint"]
        with self.assertRaisesRegex(ValueError, "missing phase: gate"):
            fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                      expected_fingerprint=expected)
        self.gate()
        for status in ("PASS", "NOT_REQUIRED"):
            with self.assertRaisesRegex(ValueError, "missing phase: review"):
                fs.record(self.repo, self.directory, self.base, "device", status,
                          self.evidence("device.md"), "No UI", expected_fingerprint=expected)
        self.approve()
        fs.record(self.repo, self.directory, self.base, "gate", "FAIL", self.evidence("failed.md"))
        with self.assertRaisesRegex(ValueError, "phase not approved: gate"):
            fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                      expected_fingerprint=expected)
        self.approve()
        self.write("app/src/main/A.kt", "unverified\n")
        with self.assertRaisesRegex(ValueError, "stale fingerprint: gate"):
            fs.record(self.repo, self.directory, self.base, "review", "PASS", self.evidence(),
                      expected_fingerprint=self.snap()["fingerprint"])
        self.approve()
        self.evidence(text="tampered review")
        with self.assertRaisesRegex(ValueError, "evidence content changed: review"):
            fs.record(self.repo, self.directory, self.base, "device", "PASS",
                      self.evidence("device.md"), expected_fingerprint=self.snap()["fingerprint"])

    def test_device_run_requires_prerequisites_before_executing(self):
        result = self.gate("from pathlib import Path; Path('must-not-exist').touch()", phase="device")
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("missing phase: gate", result["reason"])
        self.assertFalse((self.repo / "must-not-exist").exists())

    def test_device_run_cannot_revoke_review_and_pass(self):
        self.approve()
        result = self.gate(
            "from pathlib import Path; Path('.ai/workflow/test/review.md').write_text('tampered')",
            phase="device")
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("evidence content changed: review", result["reason"])

    def test_selected_phase_check_also_validates_prerequisites_and_old_manual_receipts(self):
        self.approve()
        path = self.directory / fs.STATE_FILE
        state = json.loads(path.read_bytes())
        del state["phases"]["review"]["expected_fingerprint"]
        path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "lacks matching expected fingerprint"):
            fs.check(self.repo, self.directory, phases=("device",))
        self.approve()
        fs.record(self.repo, self.directory, self.base, "gate", "BLOCKED", self.evidence("blocked.md"))
        with self.assertRaisesRegex(ValueError, "phase not approved: gate"):
            fs.check(self.repo, self.directory, phases=("review",))

    def test_manual_record_uses_one_snapshot_for_prerequisite_checks(self):
        self.gate()
        expected = self.snap()["fingerprint"]
        with mock.patch.object(fs, "snapshot", wraps=fs.snapshot) as spy:
            receipt = fs.record(self.repo, self.directory, self.base, "review", "PASS",
                                self.evidence(), expected_fingerprint=expected)
        self.assertEqual(1, spy.call_count)
        self.assertEqual(expected, receipt["expected_fingerprint"])

    def test_failed_and_blocked_records_revoke_approval(self):
        for status in ("FAIL", "BLOCKED"):
            self.approve()
            fs.record(self.repo, self.directory, self.base, "review", status, self.evidence())
            with self.assertRaisesRegex(ValueError, "not approved"):
                fs.verify_ready(self.repo, self.directory)

    def test_run_failure_overwrites_pass_and_captures_stderr(self):
        self.approve()
        receipt = self.gate("import sys; print('failure', file=sys.stderr); sys.exit(7)")
        self.assertEqual("FAIL", receipt["status"])
        self.assertEqual(7, receipt["exit_code"])
        self.assertGreaterEqual(receipt["duration_seconds"], 0)
        self.assertIn("failure", (self.directory / "gate.log").read_text())
        with self.assertRaisesRegex(ValueError, "not approved"):
            fs.verify_ready(self.repo, self.directory)

    def test_run_mutates_source_blocks_and_records_endpoints(self):
        receipt = self.gate("from pathlib import Path; Path('new-source').write_text('new')")
        self.assertEqual("BLOCKED", receipt["status"])
        self.assertEqual(0, receipt["exit_code"])
        self.assertNotEqual(receipt["before"], receipt["after"])

    def test_run_executable_missing_and_invalid_post_snapshot_revoke_pass(self):
        self.approve()
        result = fs.run(self.repo, self.directory, self.base, "gate", "gate.log",
                        [str(self.home / "missing-executable")])
        self.assertEqual("BLOCKED", result["status"])
        with self.assertRaises(ValueError):
            fs.verify_ready(self.repo, self.directory)
        self.approve()
        result = self.gate("import os; os.unlink('app/src/main/A.kt'); os.mkfifo('app/src/main/A.kt')")
        self.assertEqual("BLOCKED", result["status"])
        state = json.loads((self.directory / fs.STATE_FILE).read_text())
        self.assertEqual("BLOCKED", state["phases"]["gate"]["status"])

    def test_run_invalid_pre_snapshot_replaces_pass(self):
        self.approve()
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.base + ",vendor")
        self.assertEqual("BLOCKED", self.gate()["status"])
        state = json.loads((self.directory / fs.STATE_FILE).read_text())
        self.assertEqual("BLOCKED", state["phases"]["gate"]["status"])

    def test_run_device_and_command_cwd_and_log_hash(self):
        self.approve()
        result = self.gate("from pathlib import Path; print(Path.cwd())", phase="device")
        self.assertEqual("PASS", result["status"])
        self.assertIn(str(self.repo), (self.directory / "device.log").read_text())
        self.assertEqual("PASS", fs.check(self.repo, self.directory, phases=("device",))["status"])
        (self.directory / "device.log").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "evidence content changed"):
            fs.check(self.repo, self.directory, phases=("device",))

    def test_pinned_base_and_explicit_base_mismatch(self):
        self.approve()
        self.git("checkout", "main")
        self.git("commit", "--allow-empty", "-m", "advance main")
        self.git("checkout", "feature")
        self.assertEqual(self.base, fs.verify_ready(self.repo, self.directory)["base_commit"])
        with self.assertRaisesRegex(ValueError, "differs from recorded"):
            fs.verify_ready(self.repo, self.directory, "main")
        self.assertEqual("PASS", fs.verify_ready(self.repo, self.directory, self.base)["status"])

    def test_evidence_containment_symlinks_and_empty_files(self):
        self.gate()
        expected = self.snap()["fingerprint"]
        self.evidence()
        (self.directory / "escape").symlink_to(self.home, target_is_directory=True)
        (self.directory / "link").symlink_to(self.directory / "review.md")
        (self.directory / "empty").touch()
        for path in ("../review.md", self.repo / "build.gradle.kts", "escape/file",
                     "link", "empty", fs.STATE_FILE, "missing"):
            with self.subTest(path=path):
                with self.assertRaises((ValueError, OSError)):
                    fs.record(self.repo, self.directory, self.base, "review", "PASS", path,
                              expected_fingerprint=expected)
        with self.assertRaises(ValueError):
            fs.record(self.repo, self.repo / "state", self.base, "review", "PASS", "review.md",
                      expected_fingerprint=expected)
        with self.assertRaises(ValueError):
            fs.run(self.repo, self.directory, self.base, "gate", "../bad.log", ["true"])

    def test_state_repository_binding_and_malformed_state(self):
        self.approve()
        copy = self.home / "other"
        shutil.copytree(self.repo, copy)
        with self.assertRaisesRegex(ValueError, "binding"):
            fs.verify_ready(copy, self.directory)
        (self.directory / fs.STATE_FILE).write_text("not json")
        with self.assertRaises(ValueError):
            fs.verify_ready(self.repo, self.directory)

    def test_atomic_save_preserves_prior_file_on_replace_failure(self):
        self.approve()
        path = self.directory / fs.STATE_FILE
        before = path.read_bytes()
        with mock.patch.object(fs.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                fs._atomic(path, b"new")
        self.assertEqual(before, path.read_bytes())
        self.assertEqual([], list(self.directory.glob(".feature-state-*")))

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = fs.main(["--repo-root", str(self.repo), "--base", self.base, *args])
        return code, json.loads(output.getvalue())

    def test_cli_global_options_json_and_nonzero_failures(self):
        code, output = self.cli("snapshot")
        self.assertEqual(0, code)
        self.assertEqual(self.snap()["fingerprint"], output["fingerprint"])
        code, output = self.cli("run", "--run-dir", str(self.directory), "--phase", "gate",
                                "--log", "gate.log", "--", sys.executable, "-c", "print('ok')")
        self.assertEqual(0, code)
        self.assertEqual("gate.log", output["evidence"])
        self.assertIn("duration_seconds", output)
        self.assertNotIn("snapshot_before", output)
        self.assertNotIn("snapshot_after", output)
        persisted = json.loads((self.directory / fs.STATE_FILE).read_bytes())["phases"]["gate"]
        self.assertIn("snapshot_before", persisted)
        self.assertIn("snapshot_after", persisted)
        code, output = self.cli("check", "--run-dir", str(self.directory), "--phase", "gate")
        self.assertEqual(0, code)
        code, output = self.cli("check", "--run-dir", str(self.directory))
        self.assertEqual(1, code)
        self.assertIn("review", output["error"])
        code, output = self.cli("record", "--run-dir", str(self.directory), "--phase", "gate",
                                "--status", "PASS", "--evidence", "gate.log")
        self.assertEqual(1, code)
        code, output = self.cli("run", "--run-dir", str(self.directory), "--phase", "gate",
                                "--log", "gate.log", "--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(1, code)
        self.assertEqual(3, output["exit_code"])
        code, output = self.cli("record")
        self.assertEqual(1, code)
        self.assertEqual("BLOCKED", output["status"])

    def test_cli_record_expected_fingerprint_and_compact_summary(self):
        self.gate()
        self.evidence()
        args = ("record", "--run-dir", str(self.directory), "--phase", "review",
                "--status", "PASS", "--evidence", "review.md")
        code, result = self.cli(*args)
        self.assertEqual(1, code)
        self.assertIn("--expected-fingerprint", result["error"])
        code, result = self.cli(*args, "--expected-fingerprint", self.snap()["fingerprint"])
        self.assertEqual(0, code)
        self.assertEqual("review.md", result["evidence"])
        self.assertNotIn("trust_boundary", result)
        code, result = self.cli("check", "--run-dir", str(self.directory), "--phase", "review")
        self.assertEqual(0, code)
        self.assertEqual(["gate", "review"], result["phases"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
