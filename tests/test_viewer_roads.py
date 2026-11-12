"""The 3D viewer's route tool repeats three rules of terminal_layout in JavaScript: neighbors,
shortest_path and travel_time. This runs that part of viewer3d/index.html in Node and holds it to the
Python module, on the one-way map, the two-way variant and with roads closed. Skipped without Node."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from terminal_layout import load_layout
from terminal_layout.timing import stopping_points, travel_time

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "viewer3d" / "index.html"
HARNESS = """
const fs = require("fs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf8")), W = L.width, H = L.height, M_PER_CELL = L.cell_size_m;
const typeAt = (x, y) => (x >= 0 && y >= 0 && x < W && y < H ? L.grid[y][x] : null);
const DRIVABLE = new Set(["lane", "quay", "transfer_point", "charging_station", "gate"]);
const drivableAt = (x, y) => DRIVABLE.has(typeAt(x, y));
%s
const out = JSON.parse(fs.readFileSync(0, "utf8")).map(({ a, b, twoWay, closed }) => {
  road.twoWay = twoWay; road.closed = new Set(closed.map(c => key(c[0], c[1])));
  const p = shortestPath(a, b);
  return p && [p, travelTime(p, false), travelTime(p, true), travelTime(p, false, false), travelTime(p, true, false)];
});
console.log(JSON.stringify(out));
"""


def road_rules() -> str:
    page = PAGE.read_text(encoding="utf-8")
    return page.split("// ROADS:START")[1].split("// ROADS:END")[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node to run the viewer's JavaScript")
def test_the_viewers_routes_and_times_match_the_python_module(dames, tmp_path):
    two_way = load_layout(ROOT / "layouts" / "variants" / "dames-point-two-way.json")
    rng = random.Random(4105)
    roads = [c.pos for c in dames.cells() if dames.is_traversable(*c.pos)]
    closed = rng.sample([c.pos for c in dames.cells() if c.type == "lane"], 6)
    cases = [(dames, False, []), (two_way, True, []), (dames.with_closures(closed), False, closed), (two_way.with_closures(closed), True, closed)]
    jobs, expected = [], []
    for layout, is_two_way, shut in cases:
        stops = stopping_points(layout)
        for _ in range(120):
            a, b = rng.choice(roads), rng.choice(roads)
            jobs.append({"a": a, "b": b, "twoWay": is_two_way, "closed": shut})
            path = layout.shortest_path(a, b)
            expected.append(path and [path, travel_time(layout, path, False, stops), travel_time(layout, path, True, stops),
                                      travel_time(layout, path, False, junctions=False), travel_time(layout, path, True, junctions=False)])
    script = tmp_path / "roads.js"
    script.write_text(HARNESS % road_rules(), encoding="utf-8")
    run = subprocess.run(["node", str(script), str(ROOT / "layouts" / "dames-point.compiled.json")],
                         input=json.dumps(jobs), capture_output=True, text=True, check=True)
    got = json.loads(run.stdout)
    assert len(got) == len(expected)
    assert sum(e is None for e in expected) > 0, "the sample should include a trip with no legal route"
    for job, e, g in zip(jobs, expected, got):
        assert (e is None) == (g is None), job
        if e is None:
            continue
        assert [tuple(c) for c in g[0]] == e[0], job
        assert g[1:] == pytest.approx(e[1:], abs=1e-6), job


def test_the_road_rules_are_marked_in_the_page():
    rules = road_rules()
    for name in ("function neighbors", "function shortestPath", "function stoppingPoints", "function travelTime", "twoWayExits"):
        assert name in rules
