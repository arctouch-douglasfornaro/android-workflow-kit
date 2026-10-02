"""The office's movement model is JavaScript; when Node is installed these tests run it directly."""

from __future__ import annotations

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
