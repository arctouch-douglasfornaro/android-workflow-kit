"""Android Review: review an open PR, decide by code, post one review only with the approved draft."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import review
from android_workflow.cli import log_stage, read_json

_TMP = Path(__file__).resolve().parent / "_tmp"
SCREEN = "app/src/main/java/com/example/ProfileScreen.kt"
REPO = "app/src/main/java/com/example/ProfileRepository.kt"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True, check=True).stdout.strip()


def make_origin(root: Path, files: dict[str, str]) -> tuple[Path, str, str]:
    """A main checkout whose `origin` holds `main` and `refs/pull/7/head` (the PR's commits)."""
    origin = root / "origin.git"
    main = root / "app"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "t@example.com")
    git(main, "config", "user.name", "T")
    (main / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
    (main / "app/src/main/java/com/example").mkdir(parents=True)
    (main / "app/build.gradle.kts").write_text('plugins { id("com.android.application") }\n', encoding="utf-8")
    (main / SCREEN).write_text("package com.example\n\nclass ProfileScreen\n", encoding="utf-8")
    (main / REPO).write_text("package com.example\n\nclass ProfileRepository {\n  fun load() = 1\n}\n", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "base")
    git(main, "remote", "add", "origin", str(origin))
    git(main, "push", "-q", "origin", "main")
    base = git(main, "rev-parse", "HEAD")
    git(main, "checkout", "-q", "-b", "feature")
    for path, text in files.items():
        (main / path).parent.mkdir(parents=True, exist_ok=True)
        (main / path).write_text(text, encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "change")
    head = git(main, "rev-parse", "HEAD")
    git(main, "push", "-q", "origin", "feature:refs/pull/7/head")
    git(main, "checkout", "-q", "main")
    git(main, "branch", "-q", "-D", "feature")
    return main, base, head


def fake_pr(head: str, body: str = "Loads the profile once.\n\n- [x] Tests added\n- [ ] Dark mode", **fields: object) -> dict:
    pr = {"number": 7, "url": "https://github.com/org/app/pull/7", "title": "APP-12 Load profile once", "body": body,
          "state": "OPEN", "isDraft": False, "author": {"login": "alice"}, "viewer": "bob",
          "baseRefName": "main", "headRefName": "alice/APP-12-load", "headRefOid": head,
          "files": {"totalCount": 1, "nodes": []}, "comments": {"nodes": []}, "reviewThreads": {"nodes": []},
          "commits": {"nodes": [{"commit": {"oid": head, "statusCheckRollup": {"contexts": {"nodes": [
              {"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS",
               "isRequired": True}]}}}}]}}
    pr.update(fields)
    return pr


class Project:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def __enter__(self) -> "Project":
        _TMP.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=_TMP)
        self.root = Path(self.temp.name)
        self.main, self.base, self.head = make_origin(self.root, self.files)
        self.patches = [
            mock.patch("android_workflow.watch._remote", return_value=("github.com", "org/app")),
            mock.patch("android_workflow.watch.github_env", return_value={}),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *_: object) -> None:
        for patch in self.patches:
            patch.stop()
        self.temp.cleanup()

    def start(self, pr: dict | None = None, ticket: bool = True) -> dict:
        with mock.patch.object(review, "fetch_pr", return_value=pr or fake_pr(self.head)), \
                mock.patch.object(review, "signed_media", return_value={}):
            result = review.start(self.main, "7")
        if ticket and result.get("status") == "ready":  # the orchestrator read the Jira ticket
            folder = Path(result["run"]) / review.TICKET_DIR
            folder.mkdir(exist_ok=True)
            (folder / "APP-12.md").write_text("# APP-12 Load profile once\n", encoding="utf-8")
        return result


class PureRuleTests(unittest.TestCase):
    def test_pr_reference(self) -> None:
        self.assertEqual(review.parse_pr("https://github.com/o/r/pull/22307/files"), 22307)
        self.assertEqual(review.parse_pr("#12"), 12)
        with self.assertRaises(ValueError):
            review.parse_pr("abc")

    def test_template_checklist_and_jira_keys(self) -> None:
        items = review.template_items("- [x] Tests added\n- [ ] Dark mode\n* [X] Screenshots")
        self.assertEqual([(i["text"], i["checked"]) for i in items],
                         [("Tests added", True), ("Dark mode", False), ("Screenshots", True)])
        self.assertEqual(review.jira_keys("APP-12 fix", "see MOB-3 and APP-12"), ["APP-12", "MOB-3"])

    def test_only_github_attachment_urls_are_media(self) -> None:
        body = ("![a](https://github.com/user-attachments/assets/1a2b-3c) "
                "<img src=\"https://evil.example.com/x.png\"> https://github.com/user-attachments/assets/1a2b-3c")
        self.assertEqual(review.media_urls(body), ["https://github.com/user-attachments/assets/1a2b-3c"])
        with self.assertRaises(ValueError):
            review._fetch("https://evil.example.com/x.png", "token", 10)

    def test_ci_is_trusted_only_when_required_checks_passed_on_the_head(self) -> None:
        context = {"head": "abc"}
        ok = {"name": "build", "outcome": "passed", "required": True}
        self.assertEqual(review.assess_ci(context, [ok], "abc")["status"], "passed")
        self.assertEqual(review.assess_ci(context, [ok], "old")["status"], "pending")
        self.assertEqual(review.assess_ci(context, [], "abc")["status"], "none")
        self.assertEqual(review.assess_ci(context, [{**ok, "outcome": "pending"}], "abc")["status"], "pending")
        failed = review.assess_ci(context, [ok, {"name": "lint", "outcome": "failed", "required": False}], "abc")
        self.assertEqual((failed["status"], failed["failed"]), ("failed", ["lint"]))
        skipped = review.assess_ci(context, [{"name": "x", "outcome": "pending", "required": False},
                                             {"name": "build", "outcome": "passed", "required": False}], "abc")
        self.assertEqual(skipped["status"], "pending")

    def test_verdict_rules(self) -> None:
        nit = {"severity": "nit"}
        concern = {"severity": "concern"}
        blocking = {"severity": "blocking"}
        ci_ok = {"status": "passed"}
        self.assertEqual(review.verdict([nit], ci_ok, {}, None, [], {}, True)[0], "APPROVE")
        self.assertEqual(review.verdict([concern], ci_ok, {}, None, [], {}, True)[0], "COMMENT")
        self.assertEqual(review.verdict([blocking, nit], ci_ok, {}, "pass", [], {}, True)[0], "REQUEST_CHANGES")
        self.assertEqual(review.verdict([], {"status": "failed", "failed": ["build"]}, {}, None, [], {}, True)[0],
                         "REQUEST_CHANGES")
        self.assertEqual(review.verdict([], ci_ok, {}, "fail", [], {}, True)[0], "REQUEST_CHANGES")
        self.assertEqual(review.verdict([], ci_ok, {}, "partial", [], {}, True)[0], "COMMENT")
        self.assertEqual(review.verdict([], ci_ok, {}, None, [{"status": "unverified"}], {}, True)[0], "COMMENT")
        self.assertEqual(review.verdict([], ci_ok, {}, None, [{"status": "unmet"}], {}, True)[0], "REQUEST_CHANGES")
        self.assertEqual(review.verdict([], ci_ok, {}, None, [], {}, False)[0], "COMMENT")
        self.assertEqual(review.verdict([blocking], ci_ok, {}, None, [], {"own_pr": True}, True)[0], "COMMENT")

    def test_comment_cap_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / ".ai").mkdir()
            (target / ".ai/android-workflow.json").write_text(json.dumps({"review": {"max_inline_comments": 40}}))
            self.assertEqual(review.review_config(target)["max_inline_comments"], 15)
            (target / ".ai/android-workflow.json").write_text("{}")
            self.assertEqual(review.review_config(target)["max_inline_comments"], 10)


class StartTests(unittest.TestCase):
    def test_start_checks_out_the_pr_head_in_a_worktree(self) -> None:
        with Project({REPO: "package com.example\n\nclass ProfileRepository {\n  fun load() = 2\n}\n"}) as project:
            result = project.start()
            worktree = Path(result["worktree_path"])
            context = read_json(Path(result["run"]) / review.CONTEXT_FILE)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), project.head)
            self.assertEqual(git(project.main, "rev-parse", "--abbrev-ref", "HEAD"), "main")
            self.assertEqual((context["base"], context["files"]), (project.base, [REPO]))
            self.assertEqual(context["jira"], ["APP-12"])
            self.assertFalse(context["ui_change"]["ui"])
            self.assertFalse(context["own_pr"])
            self.assertEqual(context["checks"][0]["outcome"], "passed")

    def test_a_compose_change_is_a_ui_change(self) -> None:
        with Project({REPO: "package com.example\n\n@Composable\nfun Profile() {}\n"}) as project:
            self.assertTrue(project.start()["ui_change"])

    def test_resources_and_screen_files_are_ui_changes(self) -> None:
        with Project({"app/src/main/res/values/strings.xml": "<resources/>\n"}) as project:
            context = read_json(Path(project.start()["run"]) / review.CONTEXT_FILE)
        self.assertEqual(context["ui_change"]["reasons"], ["app/src/main/res/values/strings.xml: UI resource"])

    def test_a_closed_pr_is_not_reviewed(self) -> None:
        with Project({REPO: "x\n"}) as project:
            self.assertEqual(project.start(fake_pr(project.head, state="MERGED"))["status"], "not_open")


def write_profile(worktree: Path) -> None:
    (worktree / ".ai").mkdir(exist_ok=True)
    (worktree / ".ai/project-profile.md").write_text("# Profile\n", encoding="utf-8")


class FlowTests(unittest.TestCase):
    CHANGE = {REPO: "package com.example\n\nclass ProfileRepository {\n  fun load() = 2\n  fun cache() = 3\n}\n"}

    def run_to_draft(self, project: Project, review_json: dict) -> Path:
        result = project.start()
        worktree = Path(result["worktree_path"])
        self.assertEqual(review.next_action(worktree)["next"], "setup")
        write_profile(worktree)
        planner = review.next_action(worktree)
        self.assertEqual((planner["next"], planner["agent"], planner["mode"]), ("spawn", "aw-planner", "review"))
        agent_dir = Path(result["run"])
        spec = read_json(agent_dir / "ticket-spec.json")
        self.assertNotIn("T7", spec["route"])
        self.assertEqual(read_json(worktree / ".ai/workflow/_cache/repo-map.json")["base_commit"], project.base)
        self.assertEqual(review.next_action(worktree)["next"], "wait")
        (agent_dir / review.SPEC_FILE).write_text(json.dumps({
            "schema_version": 1, "status": "ready", "intent": "load once",
            "acceptance": [{"id": "AC1", "text": "Profile loads once", "source": "pr_body", "evidence": "test"},
                           {"id": "AC2", "text": "Tests added", "source": "template", "evidence": "test"}]}))
        log_stage(worktree, "Planner", "completed", "2 AC", "host")
        checks = review.next_action(worktree)
        self.assertEqual(checks["next"], "checks")
        self.assertEqual([item["agent"] for item in checks["run"]], ["aw-reviewer"])
        self.assertEqual(checks["commands"], ["CLI review ci --target TARGET"])
        self.assertNotIn("also", checks)  # no UI change: no base build, no device APK
        (agent_dir / "review.json").write_text(json.dumps(review_json))
        log_stage(worktree, "Reviewer", "completed", "done", "host")
        with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)):
            self.assertEqual(review.ci(worktree)["status"], "passed")
        self.assertEqual(review.next_action(worktree)["next"], "draft")
        return worktree

    def test_full_review_until_the_post(self) -> None:
        many_nits = [{"id": f"S{i}", "file": REPO, "line": 4, "problem": f"name {i}", "fix": "rename"} for i in range(1, 13)]
        review_json = {"schema_version": 1, "status": "approved", "blocking": [],
                       "concerns": [{"id": "C1", "file": f"{REPO}:5", "problem": "cache() is never invalidated"},
                                    {"id": "C2", "file": SCREEN, "line": 1, "problem": "off the diff"}],
                       "suggestions": many_nits,
                       "acceptance": [{"id": "AC1", "status": "met"}, {"id": "AC2", "status": "unverified"}]}
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, review_json)
            drafted = review.draft(worktree)
            agent_dir = review.agent_dir_for(worktree)
            value = read_json(agent_dir / review.DRAFT_FILE)
            self.assertEqual(drafted["event"], "COMMENT")
            self.assertEqual(len(value["comments"]), 10)
            self.assertEqual(value["comments"][0]["body"], "cache() is never invalidated")
            self.assertTrue(value["comments"][1]["body"].startswith("nit: "))
            self.assertIn("not verified on a device: no UI change", value["body"])
            self.assertIn("⚠️ not verified: Tests added _(PR template)_", value["body"])
            self.assertIn(f"`{SCREEN}:1` — off the diff", value["body"])  # SCREEN did not change: body, not inline
            self.assertEqual(drafted["in_body"], 4)
            ask = review.next_action(worktree)
            self.assertEqual((ask["next"], ask["draft_id"]), ("ask_user", drafted["draft_id"]))

            edited = review.edit(worktree, drop=["C2"], set_text=[["C3", "nit: shorter"]])
            self.assertNotEqual(edited["draft_id"], drafted["draft_id"])
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh:
                refused = review.post(worktree, drafted["draft_id"])
                self.assertEqual((refused["status"], refused["reason"]), ("refused", "draft_changed"))
                gh.assert_not_called()
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 99, "html_url": "u"}), "")
                posted = review.post(worktree, edited["draft_id"])
                payload = json.loads(gh.call_args.kwargs["stdin"])
            self.assertEqual(posted["status"], "posted")
            self.assertEqual((payload["event"], payload["commit_id"], len(payload["comments"])),
                             ("COMMENT", project.head, 9))
            self.assertEqual(payload["comments"][1]["body"], "nit: shorter")
            self.assertIn("/pulls/7/reviews", gh.call_args.args[0][3])
            self.assertEqual(review.next_action(worktree)["next"], "done")

    def test_a_moved_head_refuses_the_post(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, {"status": "approved", "blocking": [], "acceptance": []})
            drafted = review.draft(worktree)
            self.assertEqual(drafted["event"], "APPROVE")
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr("f" * 40)), \
                    mock.patch("android_workflow.watch._gh") as gh:
                refused = review.post(worktree, drafted["draft_id"])
            self.assertEqual(refused["reason"], "head_moved")
            gh.assert_not_called()

    def test_a_blocking_finding_requests_changes_and_flags_security(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, {"status": "changes_requested", "blocking": [
                {"id": "B1", "file": REPO, "line": 5, "problem": "logs the user's email", "fix": "drop it",
                 "security": True}], "acceptance": []})
            drafted = review.draft(worktree)
            value = read_json(review.agent_dir_for(worktree) / review.DRAFT_FILE)
        self.assertEqual(drafted["event"], "REQUEST_CHANGES")
        self.assertEqual(drafted["security"]["findings"], ["B1"])
        self.assertTrue(value["comments"][0]["body"].startswith("**Blocking:** logs the user's email"))

    def test_no_ci_runs_the_local_gate(self) -> None:
        with Project(self.CHANGE) as project:
            pr = fake_pr(project.head, commits={"nodes": []})
            result = project.start(pr)
            worktree = Path(result["worktree_path"])
            write_profile(worktree)
            review.next_action(worktree)
            agent_dir = Path(result["run"])
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": []}))
            log_stage(worktree, "Planner", "completed", "0 AC", "host")
            review.next_action(worktree)
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": []}))
            log_stage(worktree, "Reviewer", "completed", "done", "host")
            with mock.patch.object(review, "fetch_pr", return_value=pr):
                self.assertEqual(review.ci(worktree)["status"], "none")
            gate = review.next_action(worktree)
        self.assertEqual((gate["next"], gate["command"]), ("gate", "CLI gate --target TARGET"))

    def test_new_commits_start_a_delta_round(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, {"status": "approved", "blocking": []})
            state_path = review.agent_dir_for(worktree) / review.STATE_FILE
            state = read_json(state_path)
            state["posted"] = [{"head": project.head, "draft_id": "x"}]
            state_path.write_text(json.dumps(state))
            clone = project.root / "author"
            subprocess.run(["git", "clone", "-q", str(project.root / "origin.git"), str(clone)], check=True)
            git(clone, "fetch", "-q", "origin", "refs/pull/7/head")
            git(clone, "checkout", "-q", "FETCH_HEAD")
            (clone / REPO).write_text("package com.example\n\nclass ProfileRepository\n", encoding="utf-8")
            git(clone, "-c", "user.email=a@x", "-c", "user.name=A", "commit", "-qam", "fix")
            git(clone, "push", "-q", "origin", "HEAD:refs/pull/7/head")
            new_head = git(clone, "rev-parse", "HEAD")
            result = project.start(fake_pr(new_head))
            agent_dir = Path(result["run"])
            self.assertEqual((result["round"], result["head"]), (2, new_head))
            self.assertTrue((agent_dir / review.DELTA_FILE).read_text())
            self.assertFalse((agent_dir / "review.json").exists())
            review.next_action(worktree)  # the Planner again, on the new head
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": []}))
            log_stage(worktree, "Planner", "completed", "0 AC", "host")
            checks = review.next_action(worktree)
        self.assertEqual(checks["run"][0]["mode"], "pr-delta")


class DeviceTests(unittest.TestCase):
    def test_a_ui_change_routes_through_the_device(self) -> None:
        with Project({SCREEN: "package com.example\n\n@Composable\nfun ProfileScreen() {}\n"}) as project:
            result = project.start()
            worktree = Path(result["worktree_path"])
            agent_dir = Path(result["run"])
            write_profile(worktree)
            review.next_action(worktree)
            self.assertIn("T7", read_json(agent_dir / "ticket-spec.json")["route"])
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": []}))
            log_stage(worktree, "Planner", "completed", "0 AC", "host")
            checks = review.next_action(worktree)
            self.assertEqual(checks["also"], ["CLI prebuild --target TARGET", "CLI device build --target TARGET"])
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": []}))
            log_stage(worktree, "Reviewer", "completed", "done", "host")
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)):
                review.ci(worktree)
            serial = ("emulator-5554", None, "")
            with mock.patch("android_workflow.decide._device_for", return_value=serial):
                spawn = review.next_action(worktree)
            self.assertEqual((spawn["agent"], spawn["mode"], spawn["serial"]), ("aw-device", "after", "emulator-5554"))
            self.assertEqual(review.next_action(worktree)["next"], "wait")

    def test_no_device_says_so_in_the_review(self) -> None:
        with Project({SCREEN: "package com.example\n\n@Composable\nfun ProfileScreen() {}\n"}) as project:
            result = project.start()
            worktree = Path(result["worktree_path"])
            agent_dir = Path(result["run"])
            write_profile(worktree)
            review.next_action(worktree)
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": []}))
            log_stage(worktree, "Planner", "completed", "0 AC", "host")
            review.next_action(worktree)
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": []}))
            log_stage(worktree, "Reviewer", "completed", "done", "host")
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)):
                review.ci(worktree)
            with mock.patch("android_workflow.decide._device_for", return_value=(None, None, "no SDK")):
                self.assertEqual(review.next_action(worktree)["next"], "draft")
            drafted = review.draft(worktree)
            body = read_json(agent_dir / review.DRAFT_FILE)["body"]
        self.assertEqual(drafted["event"], "APPROVE")
        self.assertIn("not verified on a device: no device: no SDK", body)

    def test_the_reviewer_can_require_the_device_never_remove_it(self) -> None:
        with Project({REPO: "package com.example\n\nclass ProfileRepository\n"}) as project:
            result = project.start()
            worktree = Path(result["worktree_path"])
            write_profile(worktree)
            review.next_action(worktree)
            review.require_device(worktree, "UI state of ProfileViewModel changes")
            spec = read_json(Path(result["run"]) / "ticket-spec.json")
        self.assertIn("T7", spec["route"])
        self.assertIn("ui", spec["surfaces"])


class SignalTests(unittest.TestCase):
    CHANGE = FlowTests.CHANGE

    def test_the_linked_ticket_is_read_before_the_planner(self) -> None:
        with Project(self.CHANGE) as project:
            result = project.start(ticket=False)
            worktree = Path(result["worktree_path"])
            write_profile(worktree)
            fetch = review.next_action(worktree)
            self.assertEqual((fetch["next"], fetch["keys"]), ("fetch_ticket", ["APP-12"]))
            ticket_file = Path(result["run"]) / review.TICKET_DIR / "APP-12.md"
            ticket_file.parent.mkdir(exist_ok=True)
            ticket_file.write_text("# APP-12\n", encoding="utf-8")
            planner = review.next_action(worktree)
        self.assertEqual(planner["agent"], "aw-planner")
        self.assertIn("RUN/ticket/APP-12.md", planner["read"])

    def test_no_jira_tool_skips_the_ticket(self) -> None:
        with Project(self.CHANGE) as project:
            result = project.start(ticket=False)
            worktree = Path(result["worktree_path"])
            write_profile(worktree)
            review.ticket(worktree, "no Jira tool on this host")
            self.assertEqual(review.next_action(worktree)["agent"], "aw-planner")

    def test_a_copied_file_gets_a_divergence_diff(self) -> None:
        body = "".join(f"  fun step{i}() = {i}\n" for i in range(30))
        original = f"package com.example\n\nclass ProfileRepository {{\n{body}}}\n"
        legacy = "app/src/main/java/com/example/LegacyProfileRepository.kt"
        _TMP.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=_TMP) as folder:
            repo = Path(folder) / "repo"
            (repo / Path(REPO).parent).mkdir(parents=True)
            git(repo, "init", "-q")
            (repo / REPO).write_text(original, encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "-c", "user.email=t@x", "-c", "user.name=T", "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            (repo / REPO).write_text("package com.example\n\nclass ProfileRepository\n", encoding="utf-8")
            (repo / legacy).write_text(original.replace("class ProfileRepository", "class LegacyProfileRepository")
                                       .replace("step3() = 3", "step3() = 4"), encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "-c", "user.email=t@x", "-c", "user.name=T", "commit", "-qm", "split")
            head = git(repo, "rev-parse", "HEAD")
            out = Path(folder) / "run"
            out.mkdir()
            copies = review.copied_code(repo, out, base, head)
            diff = (out / "copy-diff/LegacyProfileRepository.diff").read_text()
        self.assertEqual(len(copies), 1)
        self.assertEqual((copies[0]["from"], copies[0]["diff"]), (REPO, "RUN/copy-diff/LegacyProfileRepository.diff"))
        self.assertIn("+  fun step3() = 4", diff)

    def test_device_results_reconcile_the_acceptance(self) -> None:
        report = "# Status\n\nPARTIAL\n\n# Scenarios\n\n- AC1 legacy: PASS. ok\n- AC2 sdui (flag): FAIL. broken\n" \
                 "\n# Not verified\n\n- Audio output.\n- Swipe.\n"
        self.assertEqual(review.device_scenarios(report), {"AC1": "pass", "AC2": "fail"})
        self.assertEqual(review.device_not_verified(report), ["Audio output.", "Swipe."])
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / "review.json").write_text(json.dumps({"acceptance": [
                {"id": "AC1", "status": "unverified"}, {"id": "AC2", "status": "unverified"},
                {"id": "AC3", "status": "unverified"}, {"id": "AC4", "status": "unverified"}]}))
            (run / review.SPEC_FILE).write_text(json.dumps({"acceptance": [
                {"id": "AC1", "text": "a", "evidence": "device"}, {"id": "AC2", "text": "b", "evidence": "device"},
                {"id": "AC3", "text": "c", "evidence": "media"}, {"id": "AC4", "text": "d", "evidence": "code"}]}))
            context = {"media": [{"id": "M1", "status": "not_downloaded"}]}
            rows = {r["id"]: r["status"] for r in review._acceptance(run, context, report)}
        self.assertEqual(rows, {"AC1": "met", "AC2": "unmet", "AC3": "unchecked", "AC4": "unverified"})

    def test_nits_alone_approve(self) -> None:
        nit = [{"id": "S1", "severity": "nit", "problem": "rename", "path": REPO, "line": 4}]
        event, reasons = review.verdict(nit, {"status": "passed"}, {}, "pass",
                                        [{"id": "AC1", "status": "met"}, {"id": "AC2", "status": "unchecked"}],
                                        {"own_pr": False}, True)
        self.assertEqual((event, reasons), ("APPROVE", []))

    def test_signed_attachment_urls_come_from_the_rendered_body(self) -> None:
        uuid = "82b230b0-8514-4826-bb90-c561cf364d99"
        rendered = json.dumps({"body_html": f'<video src="https://private-user-images.githubusercontent.com/1/2-{uuid}.mov?jwt=a&amp;b=c"></video>'})
        replies = [subprocess.CompletedProcess([], 0, rendered, ""), subprocess.CompletedProcess([], 0, "[]", "")]
        with mock.patch("android_workflow.watch._gh", side_effect=replies):
            signed = review.signed_media({}, "org/app", 7)
        self.assertEqual(signed, {uuid: f"https://private-user-images.githubusercontent.com/1/2-{uuid}.mov?jwt=a&b=c"})


def thread(thread_id: str, *comments: tuple[str, str, str], resolved: bool = False) -> dict:
    return {"id": thread_id, "isResolved": resolved, "isOutdated": False, "path": REPO, "line": 4,
            "comments": {"nodes": [{"id": cid, "author": {"__typename": "User", "login": who}, "body": body,
                                    "url": f"u/{cid}", "createdAt": "2026-11-04T12:00:00Z"} for cid, who, body in comments]}}


class ReplyWatchTests(unittest.TestCase):
    CHANGE = FlowTests.CHANGE

    def test_the_inbox_holds_answers_to_our_threads_and_mentions_only(self) -> None:
        pr = fake_pr("a" * 40, reviewThreads={"nodes": [
            thread("T1", ("c1", "bob", "cache() is never invalidated"), ("c2", "alice", "It is, on logout. See X.")),
            thread("T2", ("c3", "bob", "nit: name"), ("c4", "alice", "ok"), ("c5", "bob", "thanks")),  # we spoke last
            thread("T3", ("c6", "carol", "other reviewer"), ("c7", "alice", "done")),                  # not ours
            thread("T4", ("c8", "bob", "x"), ("c9", "alice", "y"), resolved=True),
            thread("T5", ("c10", "bob", "x"), ("c11", "cursor[bot]", "bot")),
        ]}, comments={"nodes": [
            {"id": "p1", "author": {"login": "alice"}, "body": "@bob could you look again?", "createdAt": "2026-11-04T12:00:00Z"},
            {"id": "p2", "author": {"login": "alice"}, "body": "@bob old", "createdAt": "2020-01-01T00:00:00Z"},
            {"id": "p3", "author": {"login": "alice"}, "body": "no mention", "createdAt": "2026-11-04T12:00:00Z"},
        ]})
        items = review.reply_inbox(pr, "bob", handled={"c99"}, since=1_700_000_000)
        self.assertEqual([(i["id"], i["comment"], i["kind"]) for i in items],
                         [("R1", "c2", "thread"), ("R2", "p1", "comment")])
        self.assertEqual(items[0]["conversation"][0], {"author": "bob", "ours": True, "body": "cache() is never invalidated"})
        self.assertEqual(review.reply_inbox(pr, "bob", handled={"c2", "p1", "p2"}, since=None), [])

    def test_replies_are_posted_only_with_the_approved_batch_and_never_twice(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            agent_dir = review.agent_dir_for(worktree)
            state = read_json(agent_dir / review.STATE_FILE)
            state["posted"] = [{"head": project.head, "draft_id": "x", "at": 1_700_000_000}]
            (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
            pr = fake_pr(project.head, id="PR_node", reviewThreads={"nodes": [
                thread("T1", ("c1", "bob", "cache() is never invalidated"), ("c2", "alice", "Why? It is cleared on logout.")),
                thread("T2", ("c3", "bob", "nit: name"), ("c4", "alice", "👍"))]})
            self.assertEqual(review.next_action(worktree)["next"], "done")
            with mock.patch.object(review, "fetch_pr", return_value=pr):
                waited = review.wait(worktree, timeout=0)
            self.assertEqual((waited["status"], waited["count"]), ("replies", 2))
            with self.assertRaises(ValueError):  # the agent has not proposed anything yet
                review.replies(worktree)
            (agent_dir / review.REPLIES_FILE).write_text(json.dumps({
                "replies": [{"id": "R1", "body": "Right, logout clears it; but a token refresh does not (Repo.kt:40)."}],
                "skipped": [{"id": "R2", "reason": "an emoji"}]}))
            drafted = review.replies(worktree)
            self.assertEqual((drafted["replies"], drafted["skipped"], drafted["ask_user"]), (1, 1, []))
            self.assertIn("token refresh", (agent_dir / review.REPLIES_MD).read_text())
            edited = review.replies(worktree, set_text=[["R1", "Makes sense, thanks."]])
            self.assertNotEqual(edited["draft_id"], drafted["draft_id"])
            with mock.patch("android_workflow.watch._graphql") as graphql:
                refused = review.reply(worktree, drafted["draft_id"])
                self.assertEqual(refused["reason"], "draft_changed")
                graphql.assert_not_called()
                graphql.return_value = {"addPullRequestReviewThreadReply": {"comment": {"id": "c5", "url": "u/c5"}}}
                posted = review.reply(worktree, edited["draft_id"])
                self.assertEqual(posted["status"], "posted")
                self.assertEqual(graphql.call_args.kwargs, {"thread": "T1", "body": "Makes sense, thanks."})
                again = review.reply(worktree, edited["draft_id"])
                self.assertEqual(graphql.call_count, 1)  # already handled: nothing posted twice
            self.assertEqual(again["posted"], [])
            with mock.patch.object(review, "fetch_pr", return_value=pr):
                self.assertEqual(review.wait(worktree, timeout=0)["status"], "timed_out")  # both handled
            self.assertFalse((agent_dir / review.INBOX_FILE).exists())

    def test_new_commits_come_before_replies(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            pr = fake_pr("f" * 40, reviewThreads={"nodes": [thread("T1", ("c1", "bob", "x"), ("c2", "alice", "fixed"))]})
            with mock.patch.object(review, "fetch_pr", return_value=pr):
                waited = review.wait(worktree, timeout=0)
        self.assertEqual((waited["status"], waited["replies_pending"]), ("new_commits", 1))


class SkillContractTests(unittest.TestCase):
    def test_the_skill_names_every_review_command_the_cli_has(self) -> None:
        from android_workflow.paths import KIT_ROOT

        text = (KIT_ROOT / "skills/android-review/SKILL.md").read_text(encoding="utf-8")
        for action in ("start", "next", "ci", "draft", "edit", "post", "answer", "device", "ticket", "wait", "replies", "reply"):
            self.assertIn(f"CLI review {action}", text)
        self.assertIn("Nothing reaches GitHub before the developer's OK", text)


if __name__ == "__main__":
    unittest.main()
