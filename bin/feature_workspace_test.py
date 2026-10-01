#!/usr/bin/env python3
"""Unit tests for feature_workspace.py.

Run: python3 ~/.ai/bin/feature_workspace_test.py

Covers branch-identity and start-point selection: getting either wrong silently attaches the run to
the wrong branch or orphans commits already pushed.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feature_workspace as fw  # noqa: E402

ISOLATED = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def run(cwd: str, *args: str, env=None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         env=env or ISOLATED)
    if res.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {res.stderr}")
    return res.stdout.strip()


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.origin = os.path.join(root, "origin.git")
        self.dir = os.path.join(root, "work")
        run(root, "init", "-q", "--bare", self.origin)
        run(root, "init", "-q", "-b", "master", self.dir)
        run(self.dir, "config", "user.email", "me@example.com")
        run(self.dir, "config", "user.name", "Me")
        run(self.dir, "remote", "add", "origin", self.origin)
        with open(os.path.join(self.dir, "a.txt"), "w") as f:
            f.write("base\n")
        run(self.dir, "add", "a.txt")
        run(self.dir, "commit", "-qm", "base")
        run(self.dir, "push", "-q", "-u", "origin", "master")

    def colleague_branch(self, name: str) -> None:
        run(self.dir, "checkout", "-q", "-b", name, "master")
        run(self.dir, "-c", "user.email=other@example.com", "-c", "user.name=Other",
            "commit", "-q", "--allow-empty", "-m", "theirs")
        run(self.dir, "push", "-q", "origin", name)
        run(self.dir, "checkout", "-q", "master")


class TestUsernameAttribution(RepoCase):
    def test_only_the_users_own_branches_are_counted(self) -> None:
        self.colleague_branch("colleague/AAA-1-x")
        run(self.dir, "checkout", "-q", "-b", "mine/BBB-1-y", "master")
        run(self.dir, "commit", "-q", "--allow-empty", "-m", "mine")
        run(self.dir, "checkout", "-q", "master")
        counts = fw.user_branch_prefixes(self.dir, "me@example.com")
        self.assertIn("mine", counts)
        self.assertNotIn("colleague", counts)

    def test_empty_identity_counts_nothing(self) -> None:
        """With no user.email the filter used to be skipped, so the most prolific OTHER engineer's
        prefix won and the run created a branch under their name, reported as `existing-branches`."""
        self.colleague_branch("colleague/AAA-1-x")
        self.colleague_branch("colleague/AAA-2-y")
        self.assertEqual({}, fw.user_branch_prefixes(self.dir, ""))
        self.assertEqual({}, fw.user_branch_prefixes(self.dir, None))

    def test_pick_username_falls_back_to_name_when_nothing_attributable(self) -> None:
        username, source = fw.pick_username({}, "", "Ada Lovelace")
        self.assertNotEqual("existing-branches", source)
        self.assertTrue(username)

    def test_pick_username_reports_existing_branches_only_when_it_used_them(self) -> None:
        username, source = fw.pick_username({"mine": 3}, "me@example.com", "Me")
        self.assertEqual(("mine", "existing-branches"), (username, source))


class TestStartPoint(RepoCase):
    def test_remote_only_branch_is_detected(self) -> None:
        run(self.dir, "checkout", "-q", "-b", "me/MF-99-thing", "master")
        run(self.dir, "commit", "-q", "--allow-empty", "-m", "remote work")
        run(self.dir, "push", "-q", "-u", "origin", "me/MF-99-thing")
        run(self.dir, "checkout", "-q", "master")
        run(self.dir, "branch", "-qD", "me/MF-99-thing")
        self.assertFalse(fw.local_branch_exists("me/MF-99-thing", self.dir))
        self.assertTrue(fw.remote_branch_exists("me/MF-99-thing", self.dir))

    def test_absent_branch_is_absent_on_both_sides(self) -> None:
        self.assertFalse(fw.local_branch_exists("me/MF-1-nope", self.dir))
        self.assertFalse(fw.remote_branch_exists("me/MF-1-nope", self.dir))

    def test_remote_only_branch_reuses_the_remote_tip(self) -> None:
        """Branching off base instead would orphan the pushed commits and make Phase 9's
        `push -u` a rejected non-fast-forward."""
        run(self.dir, "checkout", "-q", "-b", "me/MF-99-thing", "master")
        with open(os.path.join(self.dir, "w.txt"), "w") as f:
            f.write("work\n")
        run(self.dir, "add", "w.txt")
        run(self.dir, "commit", "-qm", "remote work")
        run(self.dir, "push", "-q", "-u", "origin", "me/MF-99-thing")
        remote_sha = run(self.dir, "rev-parse", "HEAD")
        run(self.dir, "checkout", "-q", "master")
        run(self.dir, "branch", "-qD", "me/MF-99-thing")

        rc = fw.main(["--ticket", "MF-99", "--title", "thing", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        self.assertEqual(remote_sha, run(self.dir, "rev-parse", "HEAD"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "w.txt")))

    def test_new_branch_starts_from_base(self) -> None:
        base_sha = run(self.dir, "rev-parse", "master")
        rc = fw.main(["--ticket", "MF-1", "--title", "fresh", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        self.assertEqual("me/MF-1-fresh", run(self.dir, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual(base_sha, run(self.dir, "rev-parse", "HEAD"))

    def test_an_earlier_branch_for_the_same_ticket_is_reported(self) -> None:
        run(self.dir, "branch", "me/MF-1-first-try")
        run(self.dir, "branch", "me/MF-10-other")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fw.main(["--ticket", "MF-1", "--title", "second try", "--username", "me",
                     "--repo-root", self.dir])
        self.assertIn("other_ticket_branches: me/MF-1-first-try\n", out.getvalue())

    def test_new_branch_starts_from_the_remote_base_not_a_stale_local_one(self) -> None:
        """The fetch advances remote refs only; a stale local base silently cuts the feature branch
        behind origin, and the missing commits surface much later as a conflict or a lost fix."""
        other = os.path.join(os.path.dirname(self.dir), "other")
        run(os.path.dirname(self.dir), "clone", "-q", self.origin, other)
        # The bare origin's HEAD may name a branch that was never pushed (git's default-branch name
        # differs from the seed's), so the clone checks nothing out. Pin it explicitly.
        run(other, "checkout", "-q", "-B", "master", "origin/master")
        run(other, "config", "user.email", "other@example.com")
        run(other, "config", "user.name", "Other")
        with open(os.path.join(other, "theirs.txt"), "w") as f:
            f.write("theirs\n")
        run(other, "add", "theirs.txt")
        run(other, "commit", "-qm", "colleague commit")
        run(other, "push", "-q", "origin", "master")

        stale_local = run(self.dir, "rev-parse", "master")
        rc = fw.main(["--ticket", "MF-7", "--title", "fresh", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        head = run(self.dir, "rev-parse", "HEAD")
        self.assertNotEqual(stale_local, head, "branched from the stale local base")
        self.assertEqual(run(self.dir, "rev-parse", "origin/master"), head)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "theirs.txt")))

    def test_start_point_falls_back_to_local_base_without_a_remote_ref(self) -> None:
        run(self.dir, "remote", "remove", "origin")
        rc = fw.main(["--ticket", "MF-8", "--title", "noremote", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        self.assertEqual("me/MF-8-noremote", run(self.dir, "rev-parse", "--abbrev-ref", "HEAD"))

    def test_existing_local_branch_is_reused_not_overwritten(self) -> None:
        run(self.dir, "checkout", "-q", "-b", "me/MF-2-local", "master")
        run(self.dir, "commit", "-q", "--allow-empty", "-m", "local work")
        local_sha = run(self.dir, "rev-parse", "HEAD")
        run(self.dir, "checkout", "-q", "master")
        rc = fw.main(["--ticket", "MF-2", "--title", "local", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        self.assertEqual(local_sha, run(self.dir, "rev-parse", "HEAD"))


class TestSyncWithRemote(RepoCase):
    """A local ref that exists is not necessarily current."""

    def _branch_pushed_from_elsewhere(self, branch: str) -> str:
        run(self.dir, "checkout", "-q", "-b", branch)
        run(self.dir, "push", "-q", "-u", "origin", branch)
        other = os.path.join(os.path.dirname(self.dir), "elsewhere")
        run(os.path.dirname(self.dir), "clone", "-q", self.origin, other)
        run(other, "config", "user.email", "other@example.com")
        run(other, "config", "user.name", "Other")
        run(other, "checkout", "-q", "-B", branch, f"origin/{branch}")
        with open(os.path.join(other, "pushed.txt"), "w") as f:
            f.write("pushed\n")
        run(other, "add", "pushed.txt")
        run(other, "commit", "-qm", "from elsewhere")
        run(other, "push", "-q", "origin", branch)
        run(self.dir, "checkout", "-q", "master")
        run(self.dir, "fetch", "-q", "origin")
        return other

    def test_stale_local_branch_is_fast_forwarded(self) -> None:
        """Checking out a stale ref and stopping leaves the pushed work absent from the tree and
        makes Phase 9's push a rejected non-fast-forward."""
        self._branch_pushed_from_elsewhere("me/MF-5-x")
        rc = fw.main(["--ticket", "MF-5", "--title", "x", "--username", "me",
                      "--repo-root", self.dir])
        self.assertEqual(0, rc)
        self.assertEqual(run(self.dir, "rev-parse", "origin/me/MF-5-x"),
                         run(self.dir, "rev-parse", "HEAD"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "pushed.txt")))

    def test_diverged_branch_is_reported_never_resolved(self) -> None:
        other = self._branch_pushed_from_elsewhere("me/MF-6-y")
        run(self.dir, "checkout", "-q", "me/MF-6-y")
        run(self.dir, "commit", "-q", "--allow-empty", "-m", "local only")
        local_tip = run(self.dir, "rev-parse", "HEAD")
        run(other, "commit", "-q", "--allow-empty", "-m", "remote only")
        run(other, "push", "-q", "origin", "me/MF-6-y")
        run(self.dir, "checkout", "-q", "master")
        run(self.dir, "fetch", "-q", "origin")

        self.assertEqual("diverged", fw.sync_with_remote("me/MF-6-y", self.dir))
        self.assertEqual(local_tip, run(self.dir, "rev-parse", "me/MF-6-y"),
                         "divergence must never be resolved automatically")

    def test_branch_ahead_of_remote_is_left_alone(self) -> None:
        run(self.dir, "checkout", "-q", "-b", "me/MF-7-z")
        run(self.dir, "push", "-q", "-u", "origin", "me/MF-7-z")
        run(self.dir, "commit", "-q", "--allow-empty", "-m", "unpushed")
        tip = run(self.dir, "rev-parse", "HEAD")
        self.assertEqual("ahead", fw.sync_with_remote("me/MF-7-z", self.dir))
        self.assertEqual(tip, run(self.dir, "rev-parse", "HEAD"))

    def test_unresolvable_ref_is_not_reported_as_current(self) -> None:
        """An empty rev-parse means the ref did not resolve, not that the branch is up to date --
        laundering it into `up-to-date` told the agent to proceed on a broken ref."""
        run(self.dir, "checkout", "-q", "-b", "me/MF-9-ghost")
        run(self.dir, "push", "-q", "-u", "origin", "me/MF-9-ghost")
        run(self.dir, "checkout", "-q", "master")
        # Local branch gone, remote-tracking ref still present: the state after someone deletes a
        # local branch without pruning, which is exactly when the agent must not be told "current".
        run(self.dir, "branch", "-qD", "me/MF-9-ghost")
        self.assertTrue(fw.remote_branch_exists("me/MF-9-ghost", self.dir))
        self.assertFalse(fw.local_branch_exists("me/MF-9-ghost", self.dir))
        self.assertEqual("unknown-ref", fw.sync_with_remote("me/MF-9-ghost", self.dir))

    def test_lowercase_ticket_is_upper_cased_not_turned_into_LOCAL(self) -> None:
        """`mf-2333` failed TICKET_RE and fell through to a `LOCAL-<MMDD>-` id, so the ticket
        vanished from the branch, the commit subject and the PR with no error."""
        rc = fw.main(["--ticket", "mf-2333", "--title", "thing", "--username", "me",
                      "--repo-root", self.dir, "--dry-run"])
        self.assertEqual(0, rc)
        self.assertTrue(fw.is_valid_ticket("MF-2333"))
        self.assertEqual("MF-2333-thing", fw.build_feature_id("MF-2333", "thing", None))

    def test_branch_without_a_remote_counterpart(self) -> None:
        run(self.dir, "checkout", "-q", "-b", "me/MF-8-local")
        self.assertEqual("no-remote", fw.sync_with_remote("me/MF-8-local", self.dir))


class TestBootstrapWorktree(RepoCase):
    def add_submodule(self) -> str:
        sub_origin = os.path.join(os.path.dirname(self.dir), "sub.git")
        seed = os.path.join(os.path.dirname(self.dir), "seed")
        run(os.path.dirname(self.dir), "init", "-q", "--bare", "-b", "master", sub_origin)
        run(os.path.dirname(self.dir), "init", "-q", "-b", "master", seed)
        with open(os.path.join(seed, "config.json"), "w") as f:
            f.write("{}\n")
        run(seed, "add", "-A")
        run(seed, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "seed")
        run(seed, "remote", "add", "origin", sub_origin)
        run(seed, "push", "-q", "origin", "master")
        env = {**ISOLATED, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
               "GIT_CONFIG_VALUE_0": "always"}
        os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="protocol.file.allow",
                          GIT_CONFIG_VALUE_0="always")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")])
        run(self.dir, "submodule", "add", "-q", sub_origin, "shared/config", env=env)
        run(self.dir, "commit", "-qm", "add submodule", env=env)
        run(self.dir, "push", "-q", "origin", "master", env=env)
        return sub_origin

    def worktree(self) -> str:
        path = os.path.join(os.path.dirname(self.dir), "wt")
        run(self.dir, "worktree", "add", "-q", path, "-b", "me/T-1-x")
        return path

    def test_project_without_submodules_or_local_files_gets_an_empty_report(self) -> None:
        report = fw.bootstrap_worktree(self.dir, self.worktree())
        self.assertEqual({"submodules": {}, "copied": [], "ignored_root_files_not_copied": []}, report)

    def test_initialised_submodule_is_initialised_in_the_worktree(self) -> None:
        self.add_submodule()
        run(self.dir, "submodule", "update", "--init", env={**ISOLATED, "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"})
        path = self.worktree()
        self.assertFalse(os.path.exists(os.path.join(path, "shared/config/config.json")))
        report = fw.bootstrap_worktree(self.dir, path)
        self.assertEqual("initialised", report["submodules"]["shared/config"])
        self.assertTrue(os.path.isfile(os.path.join(path, "shared/config/config.json")))

    def test_submodule_the_main_checkout_never_initialised_is_left_alone(self) -> None:
        self.add_submodule()
        run(self.dir, "submodule", "deinit", "-f", "--all")
        report = fw.bootstrap_worktree(self.dir, self.worktree())
        self.assertEqual({}, report["submodules"])

    def test_failed_init_falls_back_to_copying_files_at_the_same_commit(self) -> None:
        sub_origin = self.add_submodule()
        run(self.dir, "submodule", "update", "--init", env={**ISOLATED, "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"})
        path = self.worktree()
        shutil.rmtree(sub_origin)
        os.environ["GIT_CONFIG_COUNT"] = "0"
        report = fw.bootstrap_worktree(self.dir, path)
        self.assertIn("copied files", report["submodules"]["shared/config"])
        self.assertTrue(os.path.isfile(os.path.join(path, "shared/config/config.json")))
        self.assertFalse(os.path.exists(os.path.join(path, "shared/config/.git")))

    def test_local_properties_is_copied_but_a_tracked_one_is_not(self) -> None:
        with open(os.path.join(self.dir, "local.properties"), "w") as f:
            f.write("sdk.dir=/sdk\n")
        with open(os.path.join(self.dir, ".gitignore"), "w") as f:
            f.write("local.properties\nkeystore.properties\n")
        with open(os.path.join(self.dir, "keystore.properties"), "w") as f:
            f.write("secret\n")
        report = fw.bootstrap_worktree(self.dir, self.worktree())
        self.assertEqual(["local.properties"], report["copied"])
        self.assertEqual(["keystore.properties"], report["ignored_root_files_not_copied"])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.dir), "wt", "keystore.properties")))

    def test_main_creates_the_worktree_and_reports_the_bootstrap(self) -> None:
        with open(os.path.join(self.dir, "local.properties"), "w") as f:
            f.write("sdk.dir=/sdk\n")
        with open(os.path.join(self.dir, ".gitignore"), "w") as f:
            f.write("local.properties\n")
        target = os.path.join(os.path.dirname(self.dir), "wt-created")
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fw.main(["--ticket", "T-2", "--title", "thing", "--username", "me",
                          "--repo-root", self.dir, "--worktree", target])
        self.assertEqual(0, rc)
        self.assertIn("bootstrap_copied: local.properties", out.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(target, "local.properties")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
