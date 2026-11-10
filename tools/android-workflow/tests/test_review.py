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
        # approve_with_concerns: concerns ride along an approval; an unproven claim still comments
        self.assertEqual(review.verdict([concern, nit], ci_ok, {}, None, [], {}, True, True), ("APPROVE", []))
        self.assertEqual(review.verdict([concern], ci_ok, {}, None, [{"status": "unverified"}], {}, True, True)[0],
                         "COMMENT")
        # CI still running holds the approval, unless the change cannot affect the build
        pending = {"status": "pending"}
        self.assertEqual(review.verdict([], pending, {}, None, [], {}, True)[0], "COMMENT")
        self.assertEqual(review.verdict([], pending, {}, None, [], {}, True, ci_waived=True)[0], "APPROVE")

    def test_headline_follows_the_event(self) -> None:
        counts = {"blocking": 0, "concern": 2, "nit": 1}
        self.assertEqual(review.headline("APPROVE", counts, []),
                         "Looks good to me. 2 point(s) worth a look below, none blocking. 1 optional nit(s).")
        self.assertEqual(review.headline("COMMENT", counts, ["2 concern(s)"]), "A few points to look at. (2 concern(s))")
        self.assertEqual(review.headline("APPROVE", {"nit": 2}, []), "Looks good to me. 2 optional nit(s), none blocking.")

    def test_docs_only_changes(self) -> None:
        self.assertTrue(review.docs_only(["README.md", ".ai/skills/x/SKILL.md", "docs/a.txt"]))
        self.assertFalse(review.docs_only(["README.md", "app/build.gradle.kts"]))
        self.assertFalse(review.docs_only([]))
        self.assertTrue(review.ci_not_needed({"files": ["README.md"]}, {}))
        delta = {"files": ["A.kt", "README.md"], "delta_files": ["README.md"]}
        self.assertIsNone(review.ci_not_needed(delta, {"previous_head": "abc"}))
        self.assertIn("CI passed on abc", review.ci_not_needed(delta, {"previous_head": "abc", "ci_passed_heads": ["abc"]}))

    def test_manual_comments_are_what_the_developer_wrote_by_hand_after_the_post(self) -> None:
        state = {"posted": [{"at": 1793822000, "url": "https://g/pull/7#pullrequestreview-1"}],
                 "replies": {"handled": {"x": {"how": "replied", "reply": "c_reply"}}}}
        ours = {"url": "https://g/pull/7#pullrequestreview-1"}
        def node(cid: str, who: str, body: str = "text", stamp: str = "2026-11-04T20:47:39Z", review_url: dict | None = None):
            return {"id": cid, "author": {"login": who}, "body": body, "url": f"u/{cid}", "createdAt": stamp,
                    "pullRequestReview": review_url}
        pr = {"reviewThreads": {"nodes": [{"path": REPO, "line": 3, "comments": {"nodes": [
                  node("c_ours", "bob", review_url=ours),                    # our posted review
                  node("c_manual", "bob", "As a fast follow, aggregate these", review_url={"url": "https://g/pull/7#pullrequestreview-2"}),
                  node("c_reply", "bob"),                                     # a reply this workflow posted
                  node("c_alice", "alice"),                                   # someone else
                  node("c_old", "bob", stamp="2026-11-01T00:00:00Z")]}}]},   # before our review
              "reviews": {"nodes": [{"id": "r2", "author": {"login": "bob"}, "body": "", "url": "https://g/pull/7#pullrequestreview-2",
                                     "submittedAt": "2026-11-04T20:47:39Z"}]},
              "comments": {"nodes": [node("pc1", "bob", "Also: the summary endpoint")]}}
        found = review.manual_comments(pr, "bob", state)
        self.assertEqual([(i["id"], i["kind"]) for i in found], [("c_manual", "inline"), ("pc1", "comment")])
        self.assertEqual(review.manual_comments(pr, "bob", {**state, "manual_seen": ["c_manual", "pc1"]}), [])
        self.assertEqual(review.manual_comments(pr, "bob", {}), [])

    def test_others_threads_and_discussion_leave_ours_out(self) -> None:
        def note(login: str, body: str, kind: str = "User", **extra: str) -> dict:
            return {"author": {"__typename": kind, "login": login}, "body": body, "url": f"u/{body}", **extra}

        pr = {"reviewThreads": {"nodes": [
            {"id": "T1", "isResolved": True, "path": "A.kt", "line": 3, "comments": {"nodes": [note("carol", "old")]}},
            {"id": "T2", "isResolved": False, "path": "A.kt", "line": 4,
             "comments": {"nodes": [note("dave", "why?"), note("bob", "agreed")]}},
            {"id": "T3", "isResolved": False, "comments": {"nodes": [note("bob", "ours")]}}]},
            "comments": {"nodes": [note("ci[bot]", "coverage 80%", "Bot", createdAt="2026-01-02"),
                                   note("bob", "mine", createdAt="2026-01-01")]},
            "reviews": {"nodes": [note("carol", "LGTM", state="APPROVED", submittedAt="2026-01-01"),
                                  note("dave", "", state="COMMENTED")]}}
        threads = review.other_threads(pr, "bob")
        self.assertEqual([t["id"] for t in threads], ["T2", "T1"])  # open first, ours left out
        self.assertEqual(threads[0]["replies"], [{"author": "bob", "ours": True, "body": "agreed"}])
        talk = review.discussion(pr, "bob")
        self.assertEqual([(d["author"], d["kind"], d["bot"]) for d in talk],
                         [("carol", "review", False), ("ci[bot]", "comment", True)])

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


def push_fix(project: "Project", path: str = REPO, text: str = "package com.example\n\nclass ProfileRepository\n") -> str:
    """The author pushes one more commit to the PR; returns the new head."""
    clone = project.root / f"author-{len(list(project.root.glob('author-*')))}"
    subprocess.run(["git", "clone", "-q", str(project.root / "origin.git"), str(clone)], check=True)
    git(clone, "fetch", "-q", "origin", "refs/pull/7/head")
    git(clone, "checkout", "-q", "FETCH_HEAD")
    (clone / path).parent.mkdir(parents=True, exist_ok=True)
    (clone / path).write_text(text, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "-c", "user.email=a@x", "-c", "user.name=A", "commit", "-qm", "fix")
    git(clone, "push", "-q", "origin", "HEAD:refs/pull/7/head")
    return git(clone, "rev-parse", "HEAD")


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
        self.assertNotIn("also", planner)  # no UI change: no build starts with the Planner
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
            # posted: the run only follows the PR now, and its clock stops at the post
            from android_workflow import office
            from android_workflow.cli import run_dir as current_run

            run_state = read_json(current_run(worktree) / "run-state.json")
            self.assertEqual(run_state["status"], "watching")
            self.assertIsInstance(run_state["watching_since"], int)
            self.assertFalse(office.run_is_live(current_run(worktree)))
            self.assertEqual(office.run_status(current_run(worktree), run_state),
                             ("watching", float(run_state["watching_since"])))

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

    def test_a_fix_with_a_suggestion_posts_githubs_suggested_change(self) -> None:
        findings = {"status": "changes_requested", "acceptance": [], "blocking": [
            {"id": "B1", "file": REPO, "line": 5, "problem": "cache() never expires", "fix": "return load()",
             "suggestion": {"line": 5, "replacement": "  fun cache() = load()\n"}}],
            "concerns": [
                {"id": "C1", "file": REPO, "line": 4, "problem": "two members", "fix": "merge them",
                 "suggestion": {"start_line": 4, "line": 5, "replacement": "  fun load() = 2 // ```"}},
                {"id": "C2", "file": REPO, "line": 3, "problem": "same code", "fix": "nothing",
                 "suggestion": {"line": 3, "replacement": "class ProfileRepository {"}},
                {"id": "C3", "file": REPO, "line": 6, "problem": "too far", "fix": "x",
                 "suggestion": {"start_line": 6, "line": 9, "replacement": "}"}}]}
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, findings)
            drafted = review.draft(worktree)
            value = read_json(review.agent_dir_for(worktree) / review.DRAFT_FILE)
            shown = (review.agent_dir_for(worktree) / review.DRAFT_MD).read_text()
            ask = review.next_action(worktree)
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh:
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 1, "html_url": "u"}), "")
                review.post(worktree, drafted["draft_id"])
                payload = json.loads(gh.call_args.kwargs["stdin"])
        by_finding = {c["finding"]: c for c in value["comments"]}
        self.assertEqual(drafted["suggestions"], 1)
        self.assertTrue(by_finding["B1"]["body"].endswith("```suggestion\n  fun cache() = load()\n```"))
        self.assertNotIn("start_line", by_finding["B1"])
        # C1 overlaps B1's line 5: stays a text fix; C2 changes nothing; C3 runs past the file
        self.assertEqual({d["finding"] for d in drafted["suggestions_as_text"]}, {"C1", "C2", "C3"})
        self.assertNotIn("```suggestion", by_finding["C1"]["body"])
        self.assertIn("1 with a suggested change", ask["question"])
        self.assertIn("· blocking · suggestion", shown)
        self.assertEqual(payload["comments"][0]["line"], 5)

    def test_a_multi_line_suggestion_spans_its_range_and_fences_backticks(self) -> None:
        findings = {"status": "approved", "acceptance": [], "blocking": [], "suggestions": [
            {"id": "S1", "file": REPO, "line": 4, "problem": "one member", "fix": "merge",
             "suggestion": {"start_line": 4, "line": 5, "replacement": "  fun load() = 2 // ```"}}]}
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, findings)
            drafted = review.draft(worktree)
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh:
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 1, "html_url": "u"}), "")
                review.post(worktree, drafted["draft_id"])
                comment = json.loads(gh.call_args.kwargs["stdin"])["comments"][0]
            edited = review.edit(worktree, set_text=[["C1", "nit: merge them"]])
            value = read_json(review.agent_dir_for(worktree) / review.DRAFT_FILE)
        self.assertEqual((comment["start_line"], comment["line"], comment["start_side"]), (4, 5, "RIGHT"))
        self.assertTrue(comment["body"].endswith("````suggestion\n  fun load() = 2 // ```\n````"))
        self.assertTrue(edited["draft_id"])
        self.assertNotIn("start_line", value["comments"][0])  # the developer's text dropped the suggestion

    def test_others_threads_back_a_finding_and_get_replies(self) -> None:
        threads = [{"id": "T_carol", "path": REPO, "line": 5, "resolved": False, "outdated": False, "author": "carol",
                    "comment": "cache() looks stale", "url": "https://x/t1", "replies": []},
                   {"id": "T_done", "path": REPO, "line": 4, "resolved": True, "outdated": False, "author": "dave",
                    "comment": "rename", "url": "https://x/t2", "replies": []}]
        findings = {"status": "approved", "acceptance": [], "blocking": [], "concerns": [
            {"id": "C1", "file": REPO, "line": 5, "problem": "cache() never expires after logout", "fix": "clear it",
             "thread": "T_carol"}],
            "replies": [{"thread": "T_carol", "stance": "agree", "body": "Agreed, and logout keeps it too."},
                        {"thread": "T_done", "stance": "agree", "body": "late"},
                        {"thread": "T_nope", "body": "x"}]}
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, findings)
            agent_dir = review.agent_dir_for(worktree)
            context = read_json(agent_dir / review.CONTEXT_FILE)
            (agent_dir / review.CONTEXT_FILE).write_text(json.dumps({**context, "other_threads": threads}))
            drafted = review.draft(worktree)
            value = read_json(agent_dir / review.DRAFT_FILE)
            shown = (agent_dir / review.DRAFT_MD).read_text()
            ask = review.next_action(worktree)
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh, \
                    mock.patch("android_workflow.watch._graphql") as graphql:
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 1, "html_url": "u"}), "")
                graphql.return_value = {"addPullRequestReviewThreadReply": {"comment": {"id": "c9", "url": "https://x/r"}}}
                posted = review.post(worktree, drafted["draft_id"])
                again = review.post(worktree, drafted["draft_id"])
            state = read_json(agent_dir / review.STATE_FILE)
        self.assertIn("_Also raised in [this thread](https://x/t1)", value["comments"][0]["body"])
        self.assertEqual([r["thread"] for r in value["thread_replies"]], ["T_carol"])
        self.assertEqual({d["reason"] for d in drafted["replies_dropped"]}, {"thread already resolved", "unknown thread"})
        self.assertIn("## Replies in others' threads (1)", shown)
        self.assertIn("1 reply(ies) in others' threads", ask["question"])
        self.assertEqual(graphql.call_count, 1)  # the retry posts nothing twice
        self.assertEqual(graphql.call_args.kwargs["thread"], "T_carol")
        self.assertEqual(posted["thread_replies"], [{"id": "R1", "url": "https://x/r"}])
        self.assertEqual(again["status"], "already_posted")
        self.assertEqual(len(state["thread_replies"]), 1)

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
            self.assertTrue((agent_dir / review.SPEC_FILE).exists())  # same PR text: the spec stands
            checks = review.next_action(worktree)  # no Planner again: straight to the delta review
            self.assertEqual(read_json(agent_dir / "run-state.json")["stages"]["T3"]["status"], "completed")
            self.assertIn("spec kept", (agent_dir / "stage-log.md").read_text())
        self.assertEqual(checks["run"][0]["mode"], "pr-delta")
        self.assertEqual(checks["run"][0]["tier"], "standard")  # a delta review runs on the standard tier

    def test_a_changed_pr_text_runs_the_planner_again(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, {"status": "approved", "blocking": []})
            state_path = review.agent_dir_for(worktree) / review.STATE_FILE
            state = read_json(state_path)
            state["posted"] = [{"head": project.head, "draft_id": "x"}]
            state_path.write_text(json.dumps(state))
            new_head = push_fix(project)
            result = project.start(fake_pr(new_head, body="Loads the profile once, and caches it."))
            agent_dir = Path(result["run"])
            self.assertFalse((agent_dir / review.SPEC_FILE).exists())
            planner = review.next_action(worktree)
        self.assertEqual((planner["next"], planner["agent"]), ("spawn", "aw-planner"))


    def test_addressed_threads_of_ours_are_resolved_after_the_post_with_the_same_ok(self) -> None:
        review_json = {"status": "approved", "blocking": [], "acceptance": [],
                       "threads": [{"id": "T_a", "addressed": True, "note": "cleared on logout now"},
                                   {"id": "T_b", "addressed": False, "note": "still cached"},
                                   {"id": "T_c", "addressed": True},                # already resolved
                                   {"id": "T_x", "addressed": True}]}               # not one of ours
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, review_json)
            agent_dir = review.agent_dir_for(worktree)
            context = read_json(agent_dir / review.CONTEXT_FILE)
            context["our_threads"] = [{"id": "T_a", "path": REPO, "line": 5, "resolved": False},
                                      {"id": "T_b", "path": REPO, "line": 4, "resolved": False},
                                      {"id": "T_c", "path": REPO, "line": 4, "resolved": True}]
            (agent_dir / review.CONTEXT_FILE).write_text(json.dumps(context))
            drafted = review.draft(worktree)
            value = read_json(agent_dir / review.DRAFT_FILE)
            self.assertEqual([(t["id"], t["thread"]) for t in value["resolve"]], [("T1", "T_a")])
            self.assertIn("Threads to resolve after posting (1)", (agent_dir / review.DRAFT_MD).read_text())
            kept = review.edit(worktree, keep_open=["T1"])
            self.assertNotEqual(kept["draft_id"], drafted["draft_id"])  # the OK covers what gets resolved
            self.assertEqual(kept["resolve"], 0)
            with self.assertRaises(ValueError):
                review.edit(worktree, keep_open=["T9"])
            redrafted = review.draft(worktree)
            self.assertEqual(redrafted["draft_id"], drafted["draft_id"])
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh, \
                    mock.patch("android_workflow.watch._graphql") as graphql:
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 99, "html_url": "u"}), "")
                posted = review.post(worktree, drafted["draft_id"])
                self.assertEqual((posted["status"], posted["resolved"]), ("posted", ["T_a"]))
                self.assertIn("resolveReviewThread", graphql.call_args.args[1])
                self.assertEqual(graphql.call_args.kwargs, {"thread": "T_a"})
                again = review.post(worktree, drafted["draft_id"])
            self.assertEqual((again["status"], again["resolved"]), ("already_posted", []))
            self.assertEqual((gh.call_count, graphql.call_count), (1, 1))  # nothing posted or resolved twice

    def test_a_failed_resolution_does_not_fail_the_post_and_a_retry_finishes_it(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.run_to_draft(project, {"status": "approved", "blocking": [], "acceptance": [],
                                                   "threads": [{"id": "T_a", "addressed": True}]})
            agent_dir = review.agent_dir_for(worktree)
            context = read_json(agent_dir / review.CONTEXT_FILE)
            context["our_threads"] = [{"id": "T_a", "path": REPO, "line": 5, "resolved": False}]
            (agent_dir / review.CONTEXT_FILE).write_text(json.dumps(context))
            drafted = review.draft(worktree)
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)), \
                    mock.patch("android_workflow.watch._gh") as gh, \
                    mock.patch("android_workflow.watch._graphql", side_effect=[RuntimeError("forbidden"), {}]):
                gh.return_value = subprocess.CompletedProcess([], 0, json.dumps({"id": 99, "html_url": "u"}), "")
                posted = review.post(worktree, drafted["draft_id"])
                self.assertEqual(posted["status"], "posted")
                self.assertEqual(posted["resolve_failed"][0]["thread"], "T_a")
                again = review.post(worktree, drafted["draft_id"])
            self.assertEqual(again["resolved"], ["T_a"])


class ReviewModeRunTests(unittest.TestCase):
    def test_the_run_takes_the_pr_diff_as_change_set_and_implements_nothing(self) -> None:
        with Project(FlowTests.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            agent_dir = review.agent_dir_for(worktree)
            change_set = read_json(agent_dir / "change-set-map.json")
            log = (agent_dir / "stage-log.md").read_text()
            metrics = read_json(agent_dir / "stage-metrics.json")
            self.assertEqual([item["path"] for item in change_set["candidate_files"]], [REPO])
            self.assertEqual(change_set["source"], "pr_diff")
            self.assertIn("1 file(s) changed by the PR", log)  # no ticket-text guess
            self.assertLess(metrics["stages"]["T2"]["wall_time_seconds"], 5)
            self.assertNotIn("awaiting_host", log)
            self.assertNotIn("Implementer", log)
            self.assertFalse((agent_dir / "implementation-notes.md").exists())
            self.assertNotIn("T4", metrics["stages"])
            self.assertEqual(metrics["stages"]["T3"]["attempts"], 1)  # the Planner ran once, counted once


class QuietRoundTests(unittest.TestCase):
    CHANGE = FlowTests.CHANGE

    def approved_then_new_commits(self, project: Project, threads: list[dict] | None = None, ci: str = "pending") -> Path:
        """Round 1 approved and posted; the author pushes; round 2's delta review finds nothing new."""
        worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": [], "acceptance": []})
        agent_dir = review.agent_dir_for(worktree)
        state = read_json(agent_dir / review.STATE_FILE)
        state["posted"] = [{"head": project.head, "draft_id": "x", "event": "APPROVE", "url": "https://g/pull/7#pullrequestreview-1",
                            "at": 1793822000}]
        (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
        new_head = push_fix(project)
        checks = {"nodes": [{"commit": {"oid": new_head, "statusCheckRollup": {"contexts": {"nodes": [
            {"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS" if ci == "pending" else "COMPLETED",
             "conclusion": None if ci == "pending" else "SUCCESS", "isRequired": True}]}}}}]}
        pr = fake_pr(new_head, commits=checks)
        project.start(pr)
        self.assertEqual(review.next_action(worktree)["next"], "checks")
        (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": [], "concerns": [],
                                                           "suggestions": [], "threads": threads or []}))
        log_stage(worktree, "Reviewer", "completed", "delta", "host")
        with mock.patch.object(review, "fetch_pr", return_value=pr):
            review.ci(worktree)
        return worktree

    def test_nothing_new_after_our_approval_posts_nothing_and_does_not_wait_for_ci(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.approved_then_new_commits(project)
            done = review.next_action(worktree)
            state = read_json(review.agent_dir_for(worktree) / review.STATE_FILE)
        self.assertEqual(done["next"], "done")
        self.assertIn("no new review", done["why"])
        self.assertEqual((state["posted"][-1]["quiet"], state["posted"][-1]["event"]), (True, None))

    def test_addressed_threads_are_resolved_without_a_new_review_after_the_ok(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.approved_then_new_commits(project, threads=[{"id": "T_a", "addressed": True, "note": "fixed"}])
            agent_dir = review.agent_dir_for(worktree)
            context = read_json(agent_dir / review.CONTEXT_FILE)
            context["our_threads"] = [{"id": "T_a", "path": REPO, "line": 3, "resolved": False}]
            (agent_dir / review.CONTEXT_FILE).write_text(json.dumps(context))
            self.assertEqual(review.next_action(worktree)["next"], "draft")  # not wait_ci
            drafted = review.draft(worktree)
            self.assertTrue(drafted["quiet"])
            ask = review.next_action(worktree)
            self.assertEqual((ask["next"], ask["draft_id"]), ("ask_user", drafted["draft_id"]))
            self.assertIn("without a new review", ask["question"])
            self.assertIn("no new review is posted", (agent_dir / review.DRAFT_MD).read_text())
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(context["head"])), \
                    mock.patch("android_workflow.watch._gh") as gh, \
                    mock.patch("android_workflow.watch._graphql") as graphql:
                posted = review.post(worktree, drafted["draft_id"])
            gh.assert_not_called()  # no review POST
            self.assertEqual((posted["status"], posted["resolved"]), ("posted", ["T_a"]))
            self.assertEqual(graphql.call_args.kwargs, {"thread": "T_a"})
            self.assertEqual(review.next_action(worktree)["next"], "done")

    def test_the_developer_can_still_post_a_review(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.approved_then_new_commits(project, threads=[{"id": "T_a", "addressed": True}])
            agent_dir = review.agent_dir_for(worktree)
            context = read_json(agent_dir / review.CONTEXT_FILE)
            context["our_threads"] = [{"id": "T_a", "path": REPO, "line": 3, "resolved": False}]
            (agent_dir / review.CONTEXT_FILE).write_text(json.dumps(context))
            review.draft(worktree)
            edited = review.edit(worktree, event="APPROVE")
            value = read_json(agent_dir / review.DRAFT_FILE)
        self.assertEqual(edited["event"], "APPROVE")
        self.assertFalse(value.get("quiet"))
        self.assertTrue(value["body"].startswith("Looks good to me."))

    def test_a_new_finding_after_our_approval_is_a_normal_review(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = self.approved_then_new_commits(project, ci="passed")
            agent_dir = review.agent_dir_for(worktree)
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": [], "suggestions": [
                {"id": "S1", "file": REPO, "line": 3, "problem": "name"}]}))
            self.assertEqual(review.next_action(worktree)["next"], "draft")
            self.assertFalse(review.draft(worktree).get("quiet"))


class DraftEditTests(unittest.TestCase):
    def test_an_event_override_rewrites_the_headline_and_is_logged(self) -> None:
        with Project(FlowTests.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": [], "acceptance": [
                {"id": "AC1", "status": "unverified"}], "concerns": [{"id": "C1", "file": REPO, "line": 5, "problem": "p"}]})
            agent_dir = review.agent_dir_for(worktree)
            drafted = review.draft(worktree)
            self.assertEqual(drafted["event"], "COMMENT")
            self.assertTrue(read_json(agent_dir / review.DRAFT_FILE)["body"].startswith("A few points to look at."))
            review.edit(worktree, event="APPROVE", set_text=[["C1", "shorter"]])
            value = read_json(agent_dir / review.DRAFT_FILE)
            self.assertTrue(value["body"].startswith("Looks good to me. 1 point(s) worth a look below"))
            review.edit(worktree, body="Approving, thanks!")
            edits = [json.loads(line)["edit"] for line in (agent_dir / "review-decisions.jsonl").read_text().splitlines()
                     if "edit" in json.loads(line)]
        self.assertEqual((edits[0]["event"]["from"], edits[0]["event"]["to"]), ("COMMENT", "APPROVE"))
        self.assertIn("1 claim(s) without evidence", edits[0]["event"]["computed"])
        self.assertEqual((edits[0]["rewritten"][0]["before"], edits[0]["rewritten"][0]["after"]), ("p", "shorter"))
        self.assertIn("+Approving, thanks!", edits[1]["body"]["diff"])

    def test_a_docs_only_pr_does_not_wait_for_ci(self) -> None:
        with Project({"README.md": "# Benchmarks\n"}) as project:
            pending = {"nodes": [{"commit": {"oid": project.head, "statusCheckRollup": {"contexts": {"nodes": [
                {"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS", "conclusion": None, "isRequired": True}]}}}}]}
            pr = fake_pr(project.head, commits=pending)
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
                self.assertEqual(review.ci(worktree)["status"], "pending")
            self.assertEqual(review.next_action(worktree)["next"], "draft")
            drafted = review.draft(worktree)
            body = read_json(agent_dir / review.DRAFT_FILE)["body"]
        self.assertEqual(drafted["event"], "APPROVE")
        self.assertIn("not waited for: only documentation changed", body)


class DeviceTests(unittest.TestCase):
    def test_a_ui_change_routes_through_the_device(self) -> None:
        with Project({SCREEN: "package com.example\n\n@Composable\nfun ProfileScreen() {}\n"}) as project:
            result = project.start()
            worktree = Path(result["worktree_path"])
            agent_dir = Path(result["run"])
            write_profile(worktree)
            planner = review.next_action(worktree)
            # both APKs build while the Planner and the Reviewer read, not after them
            self.assertEqual(planner["also"], ["CLI prebuild --target TARGET", "CLI device build --target TARGET"])
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



class CarryOverTests(unittest.TestCase):
    """A delta round reuses what the new commits cannot have changed: the device PASS and the media review."""
    UI = {SCREEN: "package com.example\n\n@Composable\nfun ProfileScreen() {}\n"}
    MEDIA = [{"id": "M1", "url": "https://github.com/user-attachments/assets/1", "status": "downloaded",
              "kind": "image", "path": "M1.png", "bytes": 10, "sha256": "aa"}]

    def round_one_with_device_pass(self, project: Project) -> tuple[Path, Path]:
        with mock.patch.object(review, "download_media", return_value=self.MEDIA):
            result = project.start(fake_pr(project.head, body="Shows the profile ![s](https://github.com/user-attachments/assets/1)"))
        worktree, agent_dir = Path(result["worktree_path"]), Path(result["run"])
        write_profile(worktree)
        review.next_action(worktree)
        (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": [
            {"id": "AC1", "text": "Profile shows", "evidence": "device"}]}))
        log_stage(worktree, "Planner", "completed", "1 AC", "host")
        review.next_action(worktree)
        (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": [],
                                                           "acceptance": [{"id": "AC1", "status": "unverified"}]}))
        log_stage(worktree, "Reviewer", "completed", "done", "host")
        (agent_dir / review.MEDIA_FILE).write_text(json.dumps({"findings": [], "acceptance": []}))
        (agent_dir / "device-report.md").write_text("# Status\n\nPASS\n\n# Scenarios\n\n- AC1 profile: PASS. ok\n")
        run_state = read_json(agent_dir / "run-state.json")
        run_state.setdefault("stages", {})["T7"] = {"status": "completed"}
        (agent_dir / "run-state.json").write_text(json.dumps(run_state))
        state = read_json(agent_dir / review.STATE_FILE)
        state["posted"] = [{"head": project.head, "draft_id": "x", "event": "APPROVE"}]
        (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
        return worktree, agent_dir

    def delta(self, project: Project, new_head: str) -> dict:
        with mock.patch.object(review, "download_media", return_value=self.MEDIA):
            return project.start(fake_pr(new_head, body="Shows the profile ![s](https://github.com/user-attachments/assets/1)"))

    def test_device_pass_and_media_review_carry_over_when_no_ui_changed(self) -> None:
        with Project(self.UI) as project:
            worktree, agent_dir = self.round_one_with_device_pass(project)
            result = self.delta(project, push_fix(project))
            self.assertIn("carried over", result["device_carried"])
            self.assertTrue(result["media"]["review_kept"])
            self.assertTrue((agent_dir / review.MEDIA_FILE).is_file())
            checks = review.next_action(worktree)
            self.assertEqual([item["agent"] for item in checks["run"]], ["aw-reviewer"])  # no aw-media again
            self.assertNotIn("also", checks)  # no APK builds: the device does not run
            self.assertEqual(read_json(agent_dir / "run-state.json")["stages"]["T7"]["status"], "skipped")
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": [],
                                                               "acceptance": [{"id": "AC1", "status": "unverified"}]}))
            log_stage(worktree, "Reviewer", "completed", "done", "host")
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(result["head"])):
                review.ci(worktree)
            self.assertEqual(review.next_action(worktree)["next"], "done")  # nothing new after our approval
            drafted = review.draft(worktree, allow_quiet=False)
            body = read_json(agent_dir / review.DRAFT_FILE)["body"]
        self.assertEqual(drafted["event"], "APPROVE")  # AC1 still proved by the carried device run
        self.assertIn(f"PASS on {project.head[:7]} carried over", body)

    def test_a_new_ui_change_or_new_media_runs_them_again(self) -> None:
        with Project(self.UI) as project:
            worktree, agent_dir = self.round_one_with_device_pass(project)
            self.MEDIA = [{**self.MEDIA[0], "sha256": "bb"}]
            result = self.delta(project, push_fix(project, SCREEN,
                                                  "package com.example\n\n@Composable\nfun ProfileScreen(x: Int) {}\n"))
            self.assertIsNone(result["device_carried"])
            self.assertNotIn("review_kept", result["media"])
            self.assertFalse((agent_dir / review.MEDIA_FILE).exists())
            checks = review.next_action(worktree)
        self.assertEqual(sorted(item["agent"] for item in checks["run"]), ["aw-media", "aw-reviewer"])
        self.assertEqual(checks["also"], ["CLI prebuild --target TARGET", "CLI device build --target TARGET"])



class TriageTests(unittest.TestCase):
    """Cheap decisions from the diff: the Reviewer's tier and whether the attached media is read at all."""

    def test_a_small_diff_with_no_risky_area_gets_the_standard_reviewer(self) -> None:
        with Project(FlowTests.CHANGE) as project:
            result = project.start()
            self.assertEqual(result["reviewer_tier"], "standard")
            worktree, agent_dir = Path(result["worktree_path"]), Path(result["run"])
            write_profile(worktree)
            review.next_action(worktree)
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": []}))
            log_stage(worktree, "Planner", "completed", "0 AC", "host")
            checks = review.next_action(worktree)
        self.assertEqual((checks["run"][0]["mode"], checks["run"][0]["tier"]), ("pr", "standard"))

    def test_a_risky_area_or_a_big_diff_keeps_the_strong_reviewer(self) -> None:
        risky = {REPO: "package com.example\n\n@Module\nclass ProfileRepository {\n  @Provides fun load() = 2\n}\n"}
        with Project(risky) as project:
            context = read_json(Path(project.start()["run"]) / review.CONTEXT_FILE)
        self.assertEqual(context["reviewer"]["tier"], "strong")
        self.assertIn("@Module", context["reviewer"]["reason"])
        big = {REPO: "package com.example\n\nclass ProfileRepository {\n" + "".join(f"  fun f{i}() = {i}\n" for i in range(200)) + "}\n"}
        with Project(big) as project:
            self.assertEqual(project.start()["reviewer_tier"], "strong")

    def test_tests_of_a_screen_are_no_ui_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            files = ["app/src/test/java/com/example/ProfileScreenTest.kt",
                     "app/src/androidTest/java/com/example/ProfileScreenUiTest.kt"]
            self.assertFalse(review.ui_change(Path(folder), "a", "b", files)["ui"])
            self.assertTrue(review.ui_change(Path(folder), "a", "b", [SCREEN])["ui"])

    def test_media_is_read_only_when_it_can_prove_something(self) -> None:
        media = [{"id": "M1", "url": "https://github.com/user-attachments/assets/1", "status": "downloaded",
                  "kind": "image", "path": "M1.png", "bytes": 10, "sha256": "aa"}]
        with Project(FlowTests.CHANGE) as project:
            with mock.patch.object(review, "download_media", return_value=media):
                result = project.start()
            worktree, agent_dir = Path(result["worktree_path"]), Path(result["run"])
            write_profile(worktree)
            review.next_action(worktree)
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": [
                {"id": "AC1", "text": "loads once", "evidence": "test"}]}))
            log_stage(worktree, "Planner", "completed", "1 AC", "host")
            checks = review.next_action(worktree)
            self.assertEqual([item["agent"] for item in checks["run"]], ["aw-reviewer"])  # no UI, no media AC
            self.assertIn("no UI change", read_json(agent_dir / review.STATE_FILE)["media_skipped"])
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": [
                {"id": "AC1", "text": "event logged, see the screenshot", "evidence": "media"}]}))
            checks = review.next_action(worktree)
        self.assertEqual([item["agent"] for item in checks["run"]], ["aw-media"])



class RenderRouteTests(unittest.TestCase):
    """Static UI the Planner proves with `evidence: render` is rendered on the JVM, not driven on a device."""

    def test_static_ui_is_proved_by_renders_and_the_draft_says_so(self) -> None:
        from android_workflow import render
        from android_workflow.cli import source_fingerprint

        with Project({SCREEN: "package com.example\n\n@Composable\nfun ProfileScreen() {}\n"}) as project:
            result = project.start()
            worktree, agent_dir = Path(result["worktree_path"]), Path(result["run"])
            write_profile(worktree)
            overrides = read_json(worktree / ".ai/android-workflow.json") if (worktree / ".ai/android-workflow.json").is_file() else {}
            overrides["render"] = {"modules": {":app": {"ok": True, "seconds": 40}}}
            (worktree / ".ai/android-workflow.json").write_text(json.dumps(overrides))
            review.next_action(worktree)
            (agent_dir / review.SPEC_FILE).write_text(json.dumps({"status": "ready", "acceptance": [
                {"id": "AC1", "text": "The title reads Profile", "source": "pr_body", "evidence": "render"}]}))
            log_stage(worktree, "Planner", "completed", "1 AC", "host")
            checks = review.next_action(worktree)
            self.assertNotIn("also", checks)  # no APK builds: nothing is installed
            (agent_dir / "review.json").write_text(json.dumps({"status": "approved", "blocking": [],
                                                               "acceptance": [{"id": "AC1", "status": "unverified"}]}))
            log_stage(worktree, "Reviewer", "completed", "done", "host")
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)):
                review.ci(worktree)
            spawn = review.next_action(worktree)
            self.assertEqual((spawn["agent"], spawn["mode"], spawn["ac"]), ("aw-media", "render", ["AC1"]))
            (agent_dir / "media/render").mkdir(parents=True, exist_ok=True)
            (agent_dir / "media/render/title.png").write_bytes(b"png")
            (agent_dir / render.REPORT_FILE).write_text(json.dumps({
                "status": "passed", "module": ":app", "seconds": 41, "fingerprint": source_fingerprint(worktree),
                "captures": [{"name": "title", "status": "rendered", "path": str(agent_dir / "media/render/title.png")}]}))
            (agent_dir / render.REVIEW_FILE).write_text(json.dumps({"status": "pass", "acceptance": [
                {"id": "AC1", "text": "The title reads Profile", "status": "met", "captures": ["title"]}]}))
            self.assertEqual(render.verdict(worktree)["device_report"], "written")
            self.assertEqual(review.next_action(worktree)["next"], "draft")
            drafted = review.draft(worktree)
            body = read_json(agent_dir / review.DRAFT_FILE)["body"]
        self.assertEqual(drafted["event"], "APPROVE")
        self.assertIn("not run — PASS from JVM renders", body)
        self.assertIn("_(in a JVM render)_", body)


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

    def test_a_settled_thread_we_opened_is_resolved_after_its_reply(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            agent_dir = review.agent_dir_for(worktree)
            state = read_json(agent_dir / review.STATE_FILE)
            state["posted"] = [{"head": project.head, "draft_id": "x", "at": 1_700_000_000}]
            (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
            pr = fake_pr(project.head, id="PR_node", reviewThreads={"nodes": [
                thread("T1", ("c1", "bob", "cache() is never invalidated"), ("c2", "alice", "Cleared on logout, X.kt:42")),
                thread("T2", ("c3", "carol", "naming?"), ("c4", "bob", "agree"), ("c5", "alice", "renamed")),
                thread("T3", ("c6", "bob", "nit: name"), ("c7", "alice", "done"))]})
            with mock.patch.object(review, "fetch_pr", return_value=pr):
                self.assertEqual(review.wait(worktree, timeout=0)["count"], 3)
            inbox = read_json(agent_dir / review.INBOX_FILE)
            self.assertEqual([i["opened_by_us"] for i in inbox["items"]], [True, False, True])
            (agent_dir / review.REPLIES_FILE).write_text(json.dumps({
                "replies": [{"id": "R1", "body": "Right, X.kt:42 clears it."}],
                "skipped": [{"id": "R2", "reason": "not our thread"}, {"id": "R3", "reason": "done, the head shows it"}],
                "resolve": ["R1", "R2", "R3"]}))  # R2: carol's thread, never ours to resolve
            drafted = review.replies(worktree)
            self.assertEqual(drafted["resolve"], 2)
            self.assertIn("Then resolve the thread", (agent_dir / review.REPLIES_MD).read_text())
            kept = review.replies(worktree, keep_open=["R3"])
            self.assertNotEqual(kept["draft_id"], drafted["draft_id"])
            with self.assertRaises(ValueError):
                review.replies(worktree, keep_open=["R2"])
            with mock.patch("android_workflow.watch._graphql") as graphql:
                graphql.return_value = {"addPullRequestReviewThreadReply": {"comment": {"id": "c8", "url": "u/c8"}}}
                posted = review.reply(worktree, kept["draft_id"])
            self.assertEqual(posted["resolved"], ["T1"])
            self.assertEqual([c.kwargs for c in graphql.call_args_list],
                             [{"thread": "T1", "body": "Right, X.kt:42 clears it."}, {"thread": "T1"}])

    def test_the_watch_hands_over_after_watch_max_hours_like_our_own_prs(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            agent_dir = review.agent_dir_for(worktree)
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head)):
                self.assertEqual(review.wait(worktree, timeout=0)["status"], "timed_out")
                state = read_json(agent_dir / review.STATE_FILE)
                state["watch"]["started_at"] -= 73 * 3600  # default watch.max_hours: 72
                (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
                waited = review.wait(worktree, timeout=0)
        self.assertEqual((waited["status"], waited["reason"]), ("needs_user", "watched for 72 hours"))

    def test_a_merged_pr_ends_the_run_and_manual_comments_are_recorded(self) -> None:
        with Project(self.CHANGE) as project:
            worktree = FlowTests().run_to_draft(project, {"status": "approved", "blocking": []})
            agent_dir = review.agent_dir_for(worktree)
            state = read_json(agent_dir / review.STATE_FILE)
            state["posted"] = [{"head": project.head, "draft_id": "x", "event": "APPROVE", "at": 1793822000,
                                "url": "https://g/pull/7#pullrequestreview-1"}]
            (agent_dir / review.STATE_FILE).write_text(json.dumps(state))
            manual = {"id": "pc1", "author": {"login": "bob"}, "body": "fast follow: aggregate use case",
                      "url": "u/pc1", "createdAt": "2026-11-04T20:47:39Z"}
            with mock.patch.object(review, "fetch_pr", return_value=fake_pr(project.head, state="MERGED",
                                                                           comments={"nodes": [manual]})):
                waited = review.wait(worktree, timeout=0)
            decisions = (agent_dir / "review-decisions.jsonl").read_text()
            run_state = read_json(agent_dir / "run-state.json")
        self.assertEqual(waited["status"], "merged")
        self.assertEqual((run_state["status"], run_state["outcome"]), ("completed", "merged"))
        self.assertIn('"missed"', decisions)
        self.assertIn("aggregate use case", decisions)

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
