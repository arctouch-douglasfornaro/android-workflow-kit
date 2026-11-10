"""JVM renders: generated outside the repo, decided by code, off unless a module passed its probe."""

from __future__ import annotations

import os

os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_OFFICE_HOME", __import__("tempfile").mkdtemp(prefix="aw-office-"))
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"
os.environ.setdefault("ANDROID_WORKFLOW_STATE_HOME", __import__("tempfile").mkdtemp(prefix="aw-state-"))

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from android_workflow import render
from android_workflow.cli import load_state, read_json, source_fingerprint
from android_workflow.delivery import pull_request_disclosures
from android_workflow.paths import run_dir
from test_decide import NO_DEVICE, SCREEN, cli
from test_workflow import AndroidProject, fake_gradle_project

# Stands in for `./gradlew :app:testDebugUnitTest --tests … --init-script …`: reads the generated test the
# init script points at and writes one PNG per state, plus the JUnit XML a real run leaves in build/.
FAKE_RENDER = r'''
import re, sys, pathlib
args = sys.argv[1:]
script = pathlib.Path(args[args.index("--init-script") + 1]).read_text()
source = pathlib.Path(re.search(r'new File\("([^"]+)"\)', script).group(1))
test = next(source.rglob("*.kt")).read_text()
out = pathlib.Path(re.search(r'private val out = File\("([^"]+)"\)', test).group(1))
out.mkdir(parents=True, exist_ok=True)
for name in re.findall(r'capture\("([a-z0-9-]+)"', test):
    if "broken" not in name:
        (out / f"{name}.png").write_bytes(b"\x89PNG fake")
results = pathlib.Path("app/build/test-results/testDebugUnitTest")
results.mkdir(parents=True, exist_ok=True)
(results / "TEST-aw.render.ZzAwRenderTest.xml").write_text(
    '<testsuite><testcase name="state1"/><testcase name="state2"><failure message="boom"/></testcase></testsuite>')
pathlib.Path("render-calls.log").open("a").write(" ".join(args) + "\n")
'''


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = AndroidProject()
        self.root = self.project.__enter__()
        (self.root / SCREEN).write_text("package com.example\nclass ProfileScreen\n", encoding="utf-8")
        fake_gradle_project(self.root, {})
        (self.root / "fake_render.py").write_text(FAKE_RENDER, encoding="utf-8")
        with (self.root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("fake_render.py\nrender-calls.log\n")
        overrides = read_json(self.root / ".ai/android-workflow.json")
        overrides["module_commands"] = {"unit_tests": f"{sys.executable} fake_render.py {{module}}"}
        (self.root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")
        self.target = str(self.root)
        self.device = mock.patch("android_workflow.emulator.status", return_value=NO_DEVICE)
        self.device.start()

    def tearDown(self) -> None:
        self.device.stop()
        self.project.__exit__(None, None, None)

    @property
    def rundir(self) -> Path:
        return run_dir(self.root)

    def probed(self, seconds: float = 40, ok: bool = True) -> None:
        overrides = read_json(self.root / ".ai/android-workflow.json")
        overrides["render"] = {"modules": {":app": {"ok": ok, "seconds": seconds}}}
        (self.root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")

    def ready(self, proofs: str | None, acceptance: str = "Title reads Profile") -> None:
        cli("start", "--target", self.target, "--id", "R-1", "--title", "Change ProfileScreen title", "--type", "chore",
            "--level", "express")
        args = ["update-spec", "--target", self.target, "--acceptance", acceptance, "--surfaces", "ui"]
        cli(*args, *(["--proofs", proofs] if proofs else []))
        cli("next", "--target", self.target)
        (self.root / SCREEN).write_text("package com.example\nclass ProfileScreen { val a = 1 }\n", encoding="utf-8")
        (self.rundir / "implementation-notes.md").write_text("# Decisions\n\nDone.\n", encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Implementer", "--status", "completed", "--tokens", "1")
        gate = read_json(self.rundir / "gate-report.json")
        gate.update({"status": "passed", "fingerprint": source_fingerprint(self.root),
                     "steps": [{"command": "./gradlew test", "outcome": "passed"}]})
        (self.rundir / "gate-report.json").write_text(json.dumps(gate), encoding="utf-8")
        (self.rundir / "review.json").write_text(json.dumps({"schema_version": 1, "status": "approved", "blocking": [],
                                                         "concerns": [], "suggestions": []}), encoding="utf-8")
        cli("log", "--target", self.target, "--stage", "Reviewer", "--status", "completed", "--tokens", "1")

    def request(self, *names: str) -> None:
        (self.rundir / render.REQUEST_FILE).write_text(json.dumps({
            "module": ":app", "imports": ["com.example.ProfileScreen"],
            "states": [{"name": name, "ac": ["AC1"], "dark": name.endswith("dark"),
                        "content": "ProfileScreen(title = \"Profile\")"} for name in names]}), encoding="utf-8")

    # --- generation and validation -----------------------------------------------------------------

    def test_the_generated_test_draws_each_state_with_native_graphics(self) -> None:
        self.probed()
        overrides = read_json(self.root / ".ai/android-workflow.json")
        overrides["render"]["theme"] = {"wrap": "AppTheme(dark = $DARK) { $CONTENT }", "imports": ["com.example.AppTheme"]}
        (self.root / ".ai/android-workflow.json").write_text(json.dumps(overrides), encoding="utf-8")
        source = render.test_source(self.root, {"module": ":app", "imports": ["com.example.ProfileScreen"], "states": [
            {"name": "light", "content": "ProfileScreen()"},
            {"name": "dark-rtl", "dark": True, "qualifiers": "+ldrtl", "font_scale": 2, "width_dp": 360,
             "content": "ProfileScreen()"}]}, Path("/tmp/out"))
        self.assertIn("@GraphicsMode(GraphicsMode.Mode.NATIVE)", source)
        self.assertIn("@RunWith(AndroidJUnit4::class)", source)  # no base class configured: the generic rule
        self.assertIn('AppTheme(dark = false) { ProfileScreen() }', source)
        self.assertIn('@Config(qualifiers = "+night+ldrtl")', source)
        self.assertIn('capture("dark-rtl", 2.0f, 360)', source)
        script = render.init_script(":app", Path("/tmp/src"))
        self.assertIn('p.path == ":app"', script)
        self.assertIn("sourceSets.test.java.srcDir", script)
        self.assertIn("upToDateWhen { false }", script)  # never restored from the build cache without images

    def test_a_request_is_refused_outside_a_probed_module_or_past_the_limits(self) -> None:
        errors = render.validate_request(self.root, {"module": ":app", "states": [{"name": "a", "content": "X()"}]})
        self.assertTrue(any("render probe" in e for e in errors))
        self.probed()
        errors = render.validate_request(self.root, {"module": ":app", "states": [
            {"name": "Bad Name", "content": "X("}, *[{"name": f"s{i}", "content": "X()"} for i in range(7)]]})
        self.assertTrue(any("at most 6" in e for e in errors))
        self.assertTrue(any("lowercase slug" in e for e in errors))
        self.assertTrue(any("balanced" in e for e in errors))
        self.probed(seconds=900)  # slower than the budget: renders stay off in this module
        self.assertEqual(render.usable_modules(self.root), {})

    # --- the route ---------------------------------------------------------------------------------

    def test_the_route_is_the_device_unless_a_probe_and_the_proofs_say_otherwise(self) -> None:
        spec = {"route": ["T7"], "surfaces": ["ui"], "acceptance_criteria": ["a", "b"], "proofs": ["render", "unit"]}
        self.assertEqual(render.route(self.root, spec)["mode"], "device")  # no probe on record
        self.probed()
        self.assertEqual(render.route(self.root, spec)["mode"], "render")
        self.assertEqual(render.route(self.root, {**spec, "proofs": ["render", "device"]})["mode"], "both")
        self.assertEqual(render.route(self.root, {**spec, "surfaces": ["ui", "navigation"]})["mode"], "both")
        self.assertEqual(render.route(self.root, {**spec, "proofs": []})["mode"], "device")
        self.assertEqual(render.route(self.root, spec, {"render": {"prefer": "device"}})["mode"], "device")

    def test_proofs_must_match_the_ac(self) -> None:
        cli("start", "--target", self.target, "--id", "R-2", "--title", "t", "--type", "chore", "--level", "express")
        from android_workflow.cli import main

        with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            code = main(["update-spec", "--target", self.target, "--acceptance", "a|b", "--proofs", "render"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("proofs", read_json(self.rundir / "ticket-spec.json"))
        cli("update-spec", "--target", self.target, "--acceptance", "a|b", "--proofs", "render|unit")
        self.assertEqual(read_json(self.rundir / "ticket-spec.json")["proofs"], ["render", "unit"])

    # --- running -----------------------------------------------------------------------------------

    def test_a_render_leaves_the_repository_untouched_and_reports_each_state(self) -> None:
        self.probed()
        self.ready("render")
        self.request("title", "broken")
        before = subprocess.run(["git", "-C", self.target, "status", "--porcelain"], capture_output=True, text=True).stdout
        result = cli("render", "run", "--target", self.target)
        after = subprocess.run(["git", "-C", self.target, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "partial")
        report = read_json(self.rundir / render.REPORT_FILE)
        self.assertEqual([c["status"] for c in report["captures"]], ["rendered", "failed"])
        self.assertEqual(report["captures"][1]["error"], "boom")  # read from the JUnit XML before it is scrubbed
        self.assertFalse((self.root / "app/build/test-results/testDebugUnitTest/TEST-aw.render.ZzAwRenderTest.xml").exists())
        self.assertIn("--tests aw.render.ZzAwRenderTest --init-script", (self.root / "render-calls.log").read_text())
        self.assertFalse(any((self.root / "app/src").rglob("ZzAwRender*")))

    # --- the dev workflow --------------------------------------------------------------------------

    def test_static_ui_is_proved_by_renders_and_finishes_with_no_device(self) -> None:
        self.probed()
        self.ready("render")
        answer = cli("next", "--target", self.target)
        self.assertEqual((answer["next"], answer["agent"], answer["mode"]), ("spawn", "aw-media", "render"))
        self.assertEqual(answer["ac"], ["AC1"])
        self.assertEqual(cli("next", "--target", self.target)["next"], "wait")
        self.request("title", "title-dark")
        self.assertEqual(cli("render", "run", "--target", self.target)["status"], "passed")
        (self.rundir / render.REVIEW_FILE).write_text(json.dumps({"status": "pass", "acceptance": [
            {"id": "AC1", "text": "Title reads Profile", "status": "met", "captures": ["title", "title-dark"]}]}))
        recorded = cli("render", "verdict", "--target", self.target)
        self.assertEqual((recorded["verdict"], recorded["device_report"]), ("pass", "written"))
        self.assertIn(render.PROOF_LINE, (self.rundir / "device-report.md").read_text())
        self.assertTrue((self.rundir / "media/after/render-title.png").is_file())
        answer = cli("next", "--target", self.target)
        self.assertEqual(answer["command"], "CLI finish --target TARGET")
        cli("finish", "--target", self.target)
        self.assertEqual(load_state(self.root)["status"], "completed")
        lines = pull_request_disclosures({}, load_state(self.root), "Body")
        self.assertTrue(any("proved by JVM renders" in line for line in lines))

    def test_a_render_verdict_needs_rendered_captures(self) -> None:
        self.probed()
        self.ready("render")
        cli("next", "--target", self.target)
        self.request("title")
        cli("render", "run", "--target", self.target)
        (self.rundir / render.REVIEW_FILE).write_text(json.dumps({"status": "pass", "acceptance": [
            {"id": "AC1", "status": "met", "captures": ["other"]}]}))
        self.assertEqual(cli("render", "verdict", "--target", self.target)["status"], "invalid")

    def test_behavior_keeps_the_device_and_renders_run_alongside(self) -> None:
        self.probed()
        self.ready("device|render", acceptance="Tapping save closes the screen|Long titles wrap")
        with mock.patch("android_workflow.emulator.status", return_value={"status": "connected", "serial": "emu-1"}):
            answer = cli("next", "--target", self.target)
        self.assertEqual((answer["agent"], answer["mode"]), ("aw-device", "after"))
        self.assertEqual((answer["with_agent"]["agent"], answer["with_agent"]["mode"]), ("aw-media", "render"))
        self.assertEqual(answer["render_ac"], ["AC2"])

    def test_no_device_falls_back_to_renders_instead_of_nothing(self) -> None:
        self.probed()
        self.ready(None)
        answer = cli("next", "--target", self.target)
        self.assertEqual((answer["agent"], answer["mode"]), ("aw-media", "render"))
        self.assertIn("no device", answer["why"])

    def test_with_no_probe_nothing_changes(self) -> None:
        self.ready("render")
        answer = cli("next", "--target", self.target)
        self.assertEqual(answer["next"], "finish")
        self.assertIn("--skip-device", answer["command"])

    def test_a_linked_figma_frame_is_saved_before_the_renders(self) -> None:
        self.probed()
        cli("start", "--target", self.target, "--id", "R-3", "--title", "Change ProfileScreen title", "--type", "chore",
            "--level", "express", "--description", "Design: https://www.figma.com/design/AbC123/Profile?node-id=12-34")
        self.assertEqual(render.figma_links("https://www.figma.com/design/AbC123/Profile?node-id=12-34")[0]["node_id"], "12:34")
        step = render.design_step(self.root, [read_json(self.rundir / "ticket-spec.json")["ticket"]["description"]])
        self.assertEqual(step["next"], "fetch_design")
        self.assertEqual(step["writes"], ["RUN/design/AbC123-12-34.md"])
        cli("render", "design", "--target", self.target, "--skip", "no Figma tool")
        self.assertIsNone(render.design_step(self.root, ["https://www.figma.com/design/AbC123/Profile?node-id=12-34"]))


if __name__ == "__main__":
    unittest.main()
