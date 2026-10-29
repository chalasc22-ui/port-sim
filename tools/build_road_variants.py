"""Write road-network variants of Dames Point for experiments, like PortSim's road studies.

Run from the repo root:

    python tools/build_road_variants.py

PortSim's Setting B keeps the stations, fleet and workload and changes only the roads (two-way grid,
fewer cross-roads, one-way, closed roads), and finds that connectivity alone moves completion a lot.
These variants keep every block, crane, gate and charger of layouts/dames-point.json:

* dames-point-two-way.json: every road usable in both directions (gates keep their own direction).
* dames-point-one-aisle.json: the x = 36 aisle removed, so the south-east yard has one cross-road
  instead of two. (The x = 18 aisle cannot go: rows F and G drain only through it.)
* Closed roads need no file: Layout.with_closures() does that on any layout.

Each is written with its compiled form to layouts/variants/, and checked by load_layout first.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "layouts" / "variants"
REVERSE = {"N": "S", "S": "N", "E": "W", "W": "E"}
STEP = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}


def builder():
    spec = importlib.util.spec_from_file_location("bdp", ROOT / "tools" / "build_dames_point.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def two_way(data: dict, layout) -> dict:
    """Rewrite the lanes one cell each, adding the reverse of every road link between lane-like cells."""
    lane_like = {"lane", "quay", "transfer_point", "charging_station"}
    exits = {}
    kinds = {}
    for c in layout.cells():
        if c.type not in lane_like:
            continue
        exits.setdefault(c.pos, set()).update(c.exits)
        berth = c.attrs.get("berth") or (c.attrs.get("serves") or {}).get("berth")
        kinds[c.pos] = berth if (c.type == "quay" or (c.type == "transfer_point" and berth)) else None
    for p, dirs in list(exits.items()):
        for d in list(dirs):
            q = (p[0] + STEP[d][0], p[1] + STEP[d][1])
            if q in exits:                                   # both ends are lane-like: open the way back
                exits[q].add(REVERSE[d])
    lanes = []
    for (x, y), dirs in sorted(exits.items()):
        entry = {"rect": [[x, y], [x, y]], "exits": [d for d in "NESW" if d in dirs]}
        if kinds[(x, y)]:
            entry.update(kind="quay", berth=kinds[(x, y)])
        lanes.append(entry)
    out = dict(data, name="dames-point-two-way", lanes=lanes)
    out["notes"] = data["notes"] + " Variant: every road two-way (tools/build_road_variants.py)."
    return out


def trips(layout):
    """The 6,704 gate, crane and bay legs, as in tests/test_dames_point.py."""
    from collections import deque

    def bfs(src, step):
        dist = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for w in step(u):
                if w not in dist:
                    dist[w] = dist[u] + 1
                    q.append(w)
        return dist

    back = {}
    for c in layout.cells():
        if layout.is_traversable(*c.pos):
            for n in layout.neighbors(*c.pos):
                back.setdefault(n, []).append(c.pos)
    cranes = [pos for b in layout.berths.values() for _, pos in b]
    bays = [p for b in layout.blocks.values() for p in b.transfer_points]
    out = []
    for s in [g.pos for g in layout.gate_cells("in")] + cranes:
        d = bfs(s, lambda p: layout.neighbors(*p)); out += [d.get(b) for b in bays]
    for t in [g.pos for g in layout.gate_cells("out")] + cranes:
        d = bfs(t, lambda p: back.get(p, [])); out += [d.get(b) for b in bays]
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import LayoutError
    from terminal_layout.loader import layout_from_dict

    b = builder()
    base_data = b.build()
    base = layout_from_dict(base_data)
    b.AISLES = [b.AISLES[0]]
    one_aisle = b.build()
    one_aisle["name"] = "dames-point-one-aisle"
    one_aisle["notes"] += " Variant: the x = 36 aisle removed (tools/build_road_variants.py)."
    variants = {"dames-point-two-way": two_way(base_data, base), "dames-point-one-aisle": one_aisle}
    OUT.mkdir(exist_ok=True)
    print(f"{'layout':24s} {'median':>6s} {'mean':>6s} {'longest':>7s}  (moves over the 6,704 gate, crane and bay legs)")
    t = trips(base)
    print(f"{'dames-point (base)':24s} {statistics.median(t):6.0f} {statistics.fmean(t):6.1f} {max(t):7d}")
    for name, data in variants.items():
        try:
            layout = layout_from_dict(data)
        except LayoutError as exc:
            print(f"{name}: does not load\n{exc}")
            return 1
        (OUT / f"{name}.json").write_text(b.dumps(data), encoding="utf-8", newline="\n")
        layout.to_json(OUT / f"{name}.compiled.json")
        t = trips(layout)
        assert None not in t
        print(f"{name:24s} {statistics.median(t):6.0f} {statistics.fmean(t):6.1f} {max(t):7d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
