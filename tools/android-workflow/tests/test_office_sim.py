"""The office's movement model is JavaScript; when Node is installed these tests run it directly."""

from __future__ import annotations

import os

# Commands start the office watcher for a live run; tests never leave background processes behind.
os.environ["ANDROID_WORKFLOW_NO_WATCH"] = "1"
# The developer's own emulator may be connected; tests never ask adb.
os.environ["ANDROID_WORKFLOW_NO_DEVICE_CHECK"] = "1"

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from android_workflow.office import SIM_JS

NODE = shutil.which("node")


def run_sim(script: str) -> object:
    with tempfile.TemporaryDirectory() as folder:
        module = Path(folder) / "sim.js"
        module.write_text(SIM_JS, encoding="utf-8")
        code = f"const {{ SIM }} = require({json.dumps(str(module))});\n{script}"
        result = subprocess.run([NODE, "-e", code], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, "Node is not installed; the office model is JavaScript")
class OfficePlanTests(unittest.TestCase):
    SPOTS = """const spots = [...Object.values(SIM.SEATS), ...SIM.POIS.coffee, ...SIM.POIS.copa, ...SIM.POIS.sofa,
      ...SIM.POIS.chat.flat(), ...SIM.POIS.window, ...SIM.POIS.cooler];"""

    def test_every_spot_is_walkable_and_has_its_own_cell(self) -> None:
        result = run_sim(self.SPOTS + """
          const cells = spots.map(s => SIM.cellOf(s.x, s.y).join(","));
          console.log(JSON.stringify({ blocked: spots.filter(s => !SIM.walkable(s.x, s.y)), unique: new Set(cells).size, total: cells.length }));""")
        self.assertEqual(result["blocked"], [])
        self.assertEqual(result["unique"], result["total"])

    def test_every_spot_is_reachable_from_every_desk_within_one_slot(self) -> None:
        result = run_sim(self.SPOTS + """
          let missing = 0, longest = 0;
          for (const seat of Object.values(SIM.SEATS)) for (const s of spots) {
            const p = SIM.path(seat, s);
            if (!p) missing++; else longest = Math.max(longest, SIM.pathLength(p));
          }
          console.log(JSON.stringify({ missing, longest }));""")
        self.assertEqual(result["missing"], 0)
        self.assertLess(result["longest"], 30)

    def test_paths_never_cross_solid_furniture(self) -> None:
        result = run_sim(self.SPOTS + """
          let crossings = 0;
          for (const seat of Object.values(SIM.SEATS)) for (const s of spots) {
            const p = SIM.path(seat, s);
            for (const q of p.slice(1, -1)) if (!SIM.walkable(q.x, q.y)) crossings++;
          }
          console.log(JSON.stringify({ crossings }));""")
        self.assertEqual(result["crossings"], 0)


ROUTINE = """
const NOW = 1800000000;
const WORK = { Orchestrator: [[NOW - 600, null]], Implementer: [[NOW - 300, NOW - 100]], Reviewer: [[NOW - 40, null]] };
const states = (seed, t, work) => Object.fromEntries(SIM.AGENTS.map(r => [r, SIM.stateAt(seed, r, t, work)]));
"""


@unittest.skipUnless(NODE, "Node is not installed; the office model is JavaScript")
class OfficeRoutineTests(unittest.TestCase):
    def test_same_inputs_always_give_the_same_scene(self) -> None:
        result = run_sim(ROUTINE + """
          const a = JSON.stringify(states("APP-1", NOW + 77.7, WORK)), b = JSON.stringify(states("APP-1", NOW + 77.7, WORK));
          const other = JSON.stringify(states("APP-2", NOW + 77.7, WORK));
          console.log(JSON.stringify({ same: a === b, seedMatters: a !== other }));""")
        self.assertTrue(result["same"])
        self.assertTrue(result["seedMatters"])

    def test_agents_never_teleport(self) -> None:
        result = run_sim(ROUTINE + """
          let worst = 0, prev = null;
          for (let t = NOW - 400; t < NOW + 400; t += 0.1) {
            const s = states("APP-1", t, WORK);
            if (prev) for (const r of SIM.AGENTS) worst = Math.max(worst, Math.abs(s[r].x - prev[r].x) + Math.abs(s[r].y - prev[r].y));
            prev = s;
          }
          console.log(JSON.stringify({ worst }));""")
        self.assertLessEqual(result["worst"], 0.1 * 2.2 + 1e-6)

    def test_working_agents_go_to_their_desk_and_stay(self) -> None:
        result = run_sim(ROUTINE + """
          let away = 0, seatedLate = 0;
          for (let t = NOW - 300; t < NOW + 300; t += 0.5) {
            const s = states("APP-1", t, WORK);
            for (const r of SIM.AGENTS) {
              if (s[r].working && s[r].mode !== "desk" && s[r].mode !== "walk") away++;
            }
          }
          const r = SIM.stateAt("APP-1", "Reviewer", NOW + 60, WORK);
          console.log(JSON.stringify({ away, reviewer: [r.mode, r.working, +r.x.toFixed(2), +r.y.toFixed(2)], seat: [SIM.SEATS.Reviewer.x, SIM.SEATS.Reviewer.y] }));""")
        self.assertEqual(result["away"], 0)
        self.assertEqual(result["reviewer"][:2], ["desk", True])
        self.assertEqual(result["reviewer"][2:], result["seat"])

    def test_one_agent_per_spot_and_chats_come_in_pairs(self) -> None:
        result = run_sim(ROUTINE + """
          let clashes = 0, loneChat = 0;
          for (let t = NOW; t < NOW + 24 * 300; t += 6) {
            const s = states("OFFICE", t, {});
            const placed = SIM.AGENTS.filter(r => s[r].mode !== "walk");
            const spots = placed.map(r => s[r].x.toFixed(2) + "," + s[r].y.toFixed(2));
            if (new Set(spots).size !== spots.length) clashes++;
            // A partner may still be walking over; it must never be doing something else.
            for (const r of SIM.AGENTS) if (s[r].mode === "chat" && !["chat", "walk"].includes(s[s[r].partner].mode)) loneChat++;
          }
          console.log(JSON.stringify({ clashes, loneChat }));""")
        self.assertEqual(result["clashes"], 0)
        self.assertEqual(result["loneChat"], 0)

    def test_idle_agents_do_every_kind_of_thing(self) -> None:
        result = run_sim(ROUTINE + """
          const modes = {};
          for (let k = 0; k < 400; k++) for (const r of SIM.AGENTS) { const m = SIM.stateAt("X", r, NOW + k * SIM.SLOT + 20, {}).mode; modes[m] = (modes[m] || 0) + 1; }
          console.log(JSON.stringify(Object.keys(modes).sort()));""")
        self.assertEqual(result, sorted(["chat", "coffee", "copa", "cooler", "desk", "game", "sofa", "window"]))

    def test_cached_routes_match_fresh_ones_and_stay_untouched(self) -> None:
        result = run_sim(OfficePlanTests.SPOTS + """
          const a = SIM.path(SIM.SEATS.Planner, SIM.POIS.coffee[0]);
          a.push({ x: 99, y: 99 });  // a caller changing its copy must not change the cache
          const b = SIM.path(SIM.SEATS.Planner, SIM.POIS.coffee[0]);
          const shifted = SIM.path({ x: SIM.SEATS.Planner.x + 0.1, y: SIM.SEATS.Planner.y }, SIM.POIS.coffee[0]);
          console.log(JSON.stringify({ sameLength: a.length - 1 === b.length, last: b[b.length - 1], start: shifted[0] }));""")
        self.assertTrue(result["sameLength"])
        self.assertEqual(result["last"], {"x": 13.6, "y": 1.15})
        self.assertAlmostEqual(result["start"]["x"], 5.5, places=6)


TEAM = """const OFFICE = SIM.build([...SIM.BASE, "Tech Lead", "Implementer 2", "Implementer 3"]);
const spots = [...Object.values(OFFICE.SEATS), ...OFFICE.POIS.coffee, ...OFFICE.POIS.copa, ...OFFICE.POIS.sofa,
  ...OFFICE.POIS.chat.flat(), ...OFFICE.POIS.window, ...OFFICE.POIS.cooler];"""


@unittest.skipUnless(NODE, "Node is not installed; the office model is JavaScript")
class OfficeTeamTests(unittest.TestCase):
    def test_a_team_gets_its_desks_and_every_spot_stays_reachable(self) -> None:
        result = run_sim(TEAM + """
          const cells = spots.map(s => OFFICE.cellOf(s.x, s.y).join(","));
          let missing = 0, crossings = 0, longest = 0;
          for (const seat of Object.values(OFFICE.SEATS)) for (const s of spots) {
            const p = OFFICE.path(seat, s);
            if (!p) { missing++; continue; }
            longest = Math.max(longest, OFFICE.pathLength(p));
            for (const q of p.slice(1, -1)) if (!OFFICE.walkable(q.x, q.y)) crossings++;
          }
          console.log(JSON.stringify({ agents: OFFICE.AGENTS, blocked: spots.filter(s => !OFFICE.walkable(s.x, s.y)).length,
            unique: new Set(cells).size, total: cells.length, missing, crossings, longest,
            desks: OFFICE.FURNITURE.filter(f => f.kind === "desk").map(f => f.role) }));""")
        self.assertEqual(result["agents"], ["Orchestrator", "Setup", "Planner", "Tech Lead", "Implementer",
                                            "Implementer 2", "Implementer 3", "Reviewer", "Device", "Delivery"])
        self.assertIn("Implementer 3", result["desks"])
        self.assertEqual(result["blocked"], 0)
        self.assertEqual(result["unique"], result["total"])
        self.assertEqual((result["missing"], result["crossings"]), (0, 0))
        self.assertLess(result["longest"], 30)

    def test_without_a_team_its_desks_are_not_furniture(self) -> None:
        result = run_sim("""console.log(JSON.stringify(SIM.FURNITURE.filter(f => f.kind === "desk").map(f => f.role)));""")
        self.assertNotIn("Tech Lead", result)
        self.assertNotIn("Implementer 2", result)

    def test_team_members_follow_the_routine_and_never_teleport(self) -> None:
        result = run_sim(TEAM + """
          const NOW = 1800000000, work = { "Implementer 2": [[NOW - 50, NOW + 50]], "Tech Lead": [[NOW - 200, NOW - 20]] };
          let worst = 0, prev = null, atDesk = true;
          for (let t = NOW - 300; t < NOW + 300; t += 0.1) {
            const s = Object.fromEntries(OFFICE.AGENTS.map(r => [r, OFFICE.stateAt("APP-9", r, t, work)]));
            if (prev) for (const r of OFFICE.AGENTS) worst = Math.max(worst, Math.abs(s[r].x - prev[r].x) + Math.abs(s[r].y - prev[r].y));
            if (t > NOW + 20 && t < NOW + 50 && s["Implementer 2"].mode !== "desk") atDesk = false;
            prev = s;
          }
          console.log(JSON.stringify({ worst, atDesk }));""")
        self.assertLess(result["worst"], 0.5)
        self.assertTrue(result["atDesk"])


@unittest.skipUnless(NODE, "Node is not installed; the page script is JavaScript")
class OfficePageScriptTests(unittest.TestCase):
    def test_the_page_script_parses(self) -> None:
        import re

        from android_workflow.office import PAGE

        page = PAGE.replace("__SIM__", SIM_JS).replace("__DATA__", json.dumps({"ticket": None, "history": [], "desks": []}))
        script = re.search(r"<script>\n(.*)</script>", page, re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run([NODE, "--check", str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
