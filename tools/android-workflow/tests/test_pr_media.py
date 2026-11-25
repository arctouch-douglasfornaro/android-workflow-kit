from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
# Machine-wide state (build slots, device leases, base checkouts): a throwaway folder, never the developer's.
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))
# The developer\'s own autonomy config never reaches a test.
os.environ["ANDROID_WORKFLOW_CONFIG"] = "/nonexistent/android-workflow-config.json"
os.environ["ANDROID_WORKFLOW_NO_OPEN"] = "1"

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import pr_media
from android_workflow.delivery import deliver
from android_workflow.paths import run_dir
from tests.test_delivery_prebuild import finished_run
from tests.test_workflow import AndroidProject

# The shape of a real project template: a Before/After table with placeholder rows, tips in comments.
TEMPLATE = """### Traceability
Addresses [D-1]

### What has changed in this PR and why?
Adds the summary.

### Screenshots (for UI changes)

Description | Before | After
---|---|---
light_theme | Image_Before | Image_After
dark_theme | Image_Before | Image_After
tablet | Image_Before | Image_After

<!-- TIP: Place a video within a table
<video src="video url here"/>
-->

### Notes
Keep me.
"""


def media(root: Path, files: dict[str, str]) -> Path:
    agent_dir = root
    for relative in files:
        path = agent_dir / "media" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return agent_dir


class FillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="aw-media-"))

    def test_template_table_gets_one_row_per_capture_and_the_rest_is_untouched(self) -> None:
        agent_dir = media(self.dir, {"before/summary.png": "", "after/summary.png": "", "after/flow.mp4": ""})
        text, info = pr_media.apply(agent_dir, TEMPLATE, {"surfaces": ["ui"]})
        self.assertIn("summary | `before/summary.png` | `after/summary.png`", text)
        self.assertIn("flow | — | `after/flow.mp4` (video)", text)
        self.assertNotIn("Image_Before", text)
        self.assertIn("Description | Before | After\n---|---|---\n", text)
        self.assertIn("<!-- TIP: Place a video", text)
        self.assertIn("### Notes\nKeep me.", text)
        self.assertIn("Adds the summary.", text)
        self.assertEqual(info["rows"], 2)
        self.assertEqual(info["files"][0], str(agent_dir / "media/after/flow.mp4"))
        self.assertEqual(info["unpaired"], ["flow (after only)"])

    def test_filling_twice_gives_the_same_body(self) -> None:
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": ""})
        once, _ = pr_media.apply(agent_dir, TEMPLATE, {"surfaces": ["ui"]})
        twice, _ = pr_media.apply(agent_dir, once, {"surfaces": ["ui"]})
        self.assertEqual(once, twice)

    def test_a_row_a_person_filled_is_kept(self) -> None:
        body = TEMPLATE.replace("tablet | Image_Before | Image_After",
                                "a | <img src=\"https://example.test/b.png\"> | <img src=\"https://example.test/a.png\">")
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": "", "after/b.png": ""})
        text, _ = pr_media.apply(agent_dir, body, {"surfaces": ["ui"]})
        self.assertIn("https://example.test/a.png", text)
        self.assertNotIn("a | `before/a.png`", text)
        self.assertIn("b | — | `after/b.png`", text)

    def test_no_capture_replaces_the_placeholder_table_with_a_reason(self) -> None:
        text, info = pr_media.apply(self.dir, TEMPLATE, {"surfaces": ["data"]})
        self.assertIsNone(info)
        self.assertNotIn("Image_Before", text)
        self.assertIn("_No screenshots: not a UI change._", text)
        again, _ = pr_media.apply(self.dir, text, {"surfaces": ["ui"]})
        self.assertIn("_No screenshots: no device capture in this run._", again)
        self.assertEqual(again.count("_No screenshots:"), 1)

    def test_no_template_section_appends_a_table_only_when_there_are_captures(self) -> None:
        body = "Adds the summary.\n"
        self.assertEqual(pr_media.apply(self.dir, body, {"surfaces": ["ui"]})[0], body)
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": ""})
        text, _ = pr_media.apply(agent_dir, body, {"surfaces": ["ui"]})
        self.assertTrue(text.startswith("Adds the summary.\n\n### Screenshots\n\nScreen | Before | After\n"))

    def test_piped_tables_keep_their_style(self) -> None:
        body = "## Screenshots\n\n| Screen | Before | After |\n|---|---|---|\n| x | y | z |\n"
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": ""})
        text, _ = pr_media.apply(agent_dir, body, {"surfaces": ["ui"]})
        self.assertIn("| a | `before/a.png` | `after/a.png` |", text)
        self.assertNotIn("| x | y | z |", text)

    def test_reveal_is_off_when_disabled(self) -> None:
        with mock.patch("subprocess.run") as run:
            self.assertFalse(pr_media.reveal(str(self.dir)))
        run.assert_not_called()


class DeliverFillsTheTableTests(unittest.TestCase):
    def test_deliver_fills_the_table_and_reports_the_files(self) -> None:
        with AndroidProject() as root:
            finished_run(root, body=TEMPLATE)
            media(run_dir(root), {"before/summary.png": "", "after/summary.png": "", "after/extra.png": ""})
            result = deliver(root, no_pr=True)
            text = (run_dir(root) / "pr-description.md").read_text(encoding="utf-8")
        self.assertIn("summary | `before/summary.png` | `after/summary.png`", text)
        self.assertEqual(result["media"]["rows"], 2)
        self.assertTrue(any("extra (after only)" in warning for warning in result["warnings"]))
        self.assertNotIn("opened", result["media"])

    def test_a_feedback_round_keeping_the_body_never_opens_finder(self) -> None:
        with AndroidProject() as root:
            finished_run(root, body=TEMPLATE)
            media(run_dir(root), {"before/a.png": "", "after/a.png": ""})
            with mock.patch("android_workflow.delivery.remote_host", return_value=("github", "x")), \
                    mock.patch("android_workflow.delivery.open_pull_request",
                               return_value={"status": "updated", "url": "u", "body": "kept"}), \
                    mock.patch("android_workflow.pr_media.reveal") as reveal:
                result = deliver(root, keep_body=True)
        reveal.assert_not_called()
        self.assertNotIn("opened", result["media"])

    def test_a_new_pull_request_opens_the_media_folder_when_uploads_are_off(self) -> None:
        with AndroidProject() as root:
            finished_run(root, body=TEMPLATE)
            media(run_dir(root), {"before/a.png": "", "after/a.png": ""})
            with mock.patch("android_workflow.delivery.remote_host", return_value=("github", "x")), \
                    mock.patch("android_workflow.delivery.gh_can_attach", return_value=False), \
                    mock.patch("android_workflow.delivery.open_pull_request",
                               return_value={"status": "created", "url": "u"}), \
                    mock.patch("android_workflow.pr_media.reveal", return_value=True) as reveal:
                result = deliver(root)
                folder = str(run_dir(root) / "media")
        reveal.assert_called_once_with(folder)
        self.assertTrue(result["media"]["opened"])


    def test_uploaded_captures_never_open_finder_and_are_recorded(self) -> None:
        def fake_open(target, host, remote, branch, base, subject, body_file, **kwargs):
            self.assertTrue(kwargs["attach"])
            text = body_file.read_text(encoding="utf-8")
            self.assertIn("![a before](media/before/a.png)", text)
            body_file.write_text(text.replace("media/before/a.png", "https://github.com/user-attachments/assets/b1")
                                 .replace("media/after/a.png", "https://github.com/user-attachments/assets/a1"),
                                 encoding="utf-8")
            return {"status": "created", "url": "u", "attached": 2}

        with AndroidProject() as root:
            finished_run(root, body=TEMPLATE)
            media(run_dir(root), {"before/a.png": "", "after/a.png": ""})
            with mock.patch("android_workflow.delivery.remote_host", return_value=("github", "x")), \
                    mock.patch("android_workflow.delivery.shutil.which", return_value="/bin/gh"), \
                    mock.patch("android_workflow.delivery.gh_can_attach", return_value=True), \
                    mock.patch("android_workflow.delivery.open_pull_request", side_effect=fake_open), \
                    mock.patch("android_workflow.pr_media.reveal") as reveal:
                result = deliver(root)
                known = pr_media.ledger(run_dir(root))
        reveal.assert_not_called()
        self.assertEqual(result["media"]["uploaded"], 2)
        self.assertEqual(known["after/a.png"]["url"], "https://github.com/user-attachments/assets/a1")

    def test_upload_media_false_keeps_captures_off_github(self) -> None:
        with AndroidProject() as root:
            finished_run(root, body=TEMPLATE)
            media(run_dir(root), {"before/a.png": "", "after/a.png": ""})
            config = root / ".ai" / "android-workflow.json"
            data = __import__("json").loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
            data["upload_media"] = False
            config.parent.mkdir(exist_ok=True)
            config.write_text(__import__("json").dumps(data), encoding="utf-8")
            with mock.patch("android_workflow.delivery.remote_host", return_value=("github", "x")), \
                    mock.patch("android_workflow.delivery.shutil.which", return_value="/bin/gh"), \
                    mock.patch("android_workflow.delivery.gh_can_attach", return_value=True), \
                    mock.patch("android_workflow.delivery.open_pull_request",
                               return_value={"status": "created", "url": "u"}) as opened, \
                    mock.patch("android_workflow.pr_media.reveal", return_value=True):
                deliver(root)
                text = (run_dir(root) / "pr-description.md").read_text(encoding="utf-8")
        self.assertFalse(opened.call_args.kwargs["attach"])
        self.assertIn("a | `before/a.png` | `after/a.png`", text)


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="aw-upload-"))

    def test_cells_reference_the_files_for_gh_attach(self) -> None:
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": "", "after/flow.mp4": ""})
        text, info = pr_media.apply(agent_dir, TEMPLATE, {"surfaces": ["ui"]}, upload=True)
        self.assertIn("a | ![a before](media/before/a.png) | ![a after](media/after/a.png)", text)
        self.assertIn("flow | — | [flow after (video)](media/after/flow.mp4)", text)
        self.assertTrue(info["upload"])
        self.assertEqual(pr_media.pending(agent_dir, text), [
            str((agent_dir / "media/before/a.png").resolve()), str((agent_dir / "media/after/a.png").resolve()),
            str((agent_dir / "media/after/flow.mp4").resolve())])
        self.assertEqual(pr_media.apply(agent_dir, text, {"surfaces": ["ui"]}, upload=True)[0], text)

    def test_an_unchanged_capture_reuses_its_url_and_a_changed_one_goes_up_again(self) -> None:
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": ""})
        text, _ = pr_media.apply(agent_dir, TEMPLATE, {"surfaces": ["ui"]}, upload=True)
        live = text.replace("media/before/a.png", "https://github.com/user-attachments/assets/b1") \
            .replace("media/after/a.png", "https://github.com/user-attachments/assets/a1")
        self.assertEqual(set(pr_media.record(agent_dir, live)), {"before/a.png", "after/a.png"})
        again, _ = pr_media.apply(agent_dir, live, {"surfaces": ["ui"]}, upload=True)
        self.assertEqual(again, live)
        self.assertEqual(pr_media.pending(agent_dir, again), [])
        (agent_dir / "media/after/a.png").write_bytes(b"new capture")
        changed, _ = pr_media.apply(agent_dir, live, {"surfaces": ["ui"]}, upload=True)
        self.assertIn("![a before](https://github.com/user-attachments/assets/b1) | ![a after](media/after/a.png)",
                      changed)

    def test_a_row_a_person_filled_stays_with_uploads_on(self) -> None:
        body = TEMPLATE.replace("tablet | Image_Before | Image_After",
                                "a | ![x](https://example.test/b.png) | ![y](https://example.test/a.png)")
        agent_dir = media(self.dir, {"before/a.png": "", "after/a.png": ""})
        text, _ = pr_media.apply(agent_dir, body, {"surfaces": ["ui"]}, upload=True)
        self.assertIn("https://example.test/a.png", text)
        self.assertEqual(pr_media.pending(agent_dir, text), [])

    def test_to_manual_turns_references_into_file_names(self) -> None:
        body = "a | ![a before](media/before/a.png) | [flow after (video)](media/after/flow.mp4) | ![k](https://x.test/k.png)"
        self.assertEqual(pr_media.to_manual(body),
                         "a | `before/a.png` | `after/flow.mp4` (video) | ![k](https://x.test/k.png)")


class OpenWithAttachTests(unittest.TestCase):
    def fake_gh(self, root: Path, create_fails_with_attach: bool = False) -> tuple[Path, Path]:
        bin_dir = root / "fakebin"
        bin_dir.mkdir()
        calls = root / "gh-calls.txt"
        fail = ('case "$*" in *--attach*) echo "upload refused" >&2; exit 1;; esac\n'
                if create_fails_with_attach else "")
        (bin_dir / "gh").write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> {calls}\n"
            '[ "$2" = view ] && case "$*" in *--jq*) echo "body from GitHub"; exit 0;; *) exit 1;; esac\n'
            + fail +
            "echo https://github.com/o/r/pull/7\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        return bin_dir, calls

    def test_create_attaches_the_referenced_captures_and_mirrors_the_pr(self) -> None:
        from android_workflow.delivery import open_pull_request

        root = Path(tempfile.mkdtemp(prefix="aw-open-"))
        media(root, {"before/a.png": "", "after/a.png": "", "after/unused.png": ""})
        body = root / "pr-description.md"
        body.write_text("a | ![a before](media/before/a.png) | ![a after](media/after/a.png)\n", encoding="utf-8")
        bin_dir, calls = self.fake_gh(root)
        with mock.patch.dict("os.environ", {"PATH": f"{bin_dir}:/usr/bin:/bin"}), \
                mock.patch("android_workflow.watch.github_env", return_value={}):
            result = open_pull_request(root, "github", "git@github.com:o/r.git", "b", "main", "S", body, 30,
                                       attach=True)
        created = next(line for line in calls.read_text(encoding="utf-8").splitlines() if line.startswith("pr create"))
        self.assertEqual(result["attached"], 2)
        self.assertEqual(created.count("--attach"), 2)
        self.assertNotIn("unused.png", created)
        self.assertEqual(body.read_text(encoding="utf-8"), "body from GitHub\n")

    def test_a_refused_upload_opens_the_pr_with_file_names_to_drag(self) -> None:
        from android_workflow.delivery import open_pull_request

        root = Path(tempfile.mkdtemp(prefix="aw-open-"))
        media(root, {"before/a.png": "", "after/a.png": ""})
        body = root / "pr-description.md"
        body.write_text("a | ![a before](media/before/a.png) | ![a after](media/after/a.png)\n", encoding="utf-8")
        bin_dir, calls = self.fake_gh(root, create_fails_with_attach=True)
        with mock.patch.dict("os.environ", {"PATH": f"{bin_dir}:/usr/bin:/bin"}), \
                mock.patch("android_workflow.watch.github_env", return_value={}):
            result = open_pull_request(root, "github", "git@github.com:o/r.git", "b", "main", "S", body, 30,
                                       attach=True)
        creates = [line for line in calls.read_text(encoding="utf-8").splitlines() if line.startswith("pr create")]
        self.assertEqual(result["status"], "created")
        self.assertIn("upload refused", result["attach_warning"])
        self.assertNotIn("attached", result)
        self.assertNotIn("--attach", creates[-1])
        self.assertEqual(body.read_text(encoding="utf-8"), "a | `before/a.png` | `after/a.png`\n")


if __name__ == "__main__":
    unittest.main()
