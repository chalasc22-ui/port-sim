"""Write viewer3d/demo-routes.json: a few example vehicle journeys for the 3D viewer to animate.

Run from the repo root (after `pip install -e .`):

    python tools/build_viewer_routes.py

Every route is joined from `Layout.shortest_path` legs, so the viewer draws paths the layout
module computed and holds no movement rule of its own (spec section 7). These are examples
for the demo, not traffic: no dispatch, no conflicts between vehicles, constant speed.
Each stop says what happens there and whether the vehicle carries a container afterwards.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (id, kind, label, [(place, what happens there, loaded after the stop)]); the first place is the start
JOURNEYS = [
    ("AGV1", "agv", "QC1 discharge to block A1",
     [("crane:QC1", "Takes a container from QC1", True), ("bay:A1:6", "RTG1 lifts it into A1 bay 6", False)]),
    ("AGV2", "agv", "QC3 discharge to block C3",
     [("crane:QC3", "Takes a container from QC3", True), ("bay:C3:9", "RTG3 lifts it into C3 bay 9", False)]),
    ("AGV3", "agv", "Block E2 export to QC4",
     [("bay:E2:12", "RTG2 sets an export container on it", True), ("crane:QC4", "QC4 loads it onto the ship", False)]),
    ("AGV4", "agv", "QC6 discharge to block N2",
     [("crane:QC6", "Takes a container from QC6", True), ("bay:N2:6", "RTG6 lifts it into N2 bay 6", False)]),
    ("AGV5", "agv", "QC5 run, then a charge",
     [("crane:QC5", "Takes a container from QC5", True), ("bay:H3:4", "RTG5 lifts it into H3 bay 4", False),
      ("charger:CH9", "Charges at CH9", False)]),
    ("TRK1", "truck", "Gate in, pick up at G3, gate out",
     [("gate:G_IN1", "Checks in at G_IN1", False), ("bay:G3:10", "RTG4 loads it", True),
      ("gate:G_OUT1", "Checks out at G_OUT1", False)]),
]


def place(layout, name):
    kind, *rest = name.split(":")
    if kind == "crane":
        return layout.quay_cranes[rest[0]].transfer_points[1]           # the crane's own column
    if kind == "bay":
        block, bay = rest[0], int(rest[1])
        return next(p.pos for p in layout.transfer_points(block_id=block) if p.attrs["serves"]["bay"] == bay)
    if kind == "charger":
        return next(c.pos for c in layout.charging_stations() if c.attrs["id"] == rest[0])
    if kind == "gate":
        return next(g.pos for g in layout.gate_cells() if g.attrs["id"] == rest[0])
    raise ValueError(name)


def journey(layout, stops):
    """Concatenate shortest paths stop to stop; a vehicle (not a truck) loops back to its first stop."""
    points = [place(layout, name) for name, _, _ in stops]
    path, marks = [points[0]], [0]
    closes = not stops[0][0].startswith("gate:")
    legs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if closes else [])
    for a, b in legs:
        leg = layout.shortest_path(a, b)
        if leg is None:
            raise SystemExit(f"no route from {a} to {b}")
        path += leg[1:]
        marks.append(len(path) - 1)
    if closes:
        marks = marks[:-1]                                              # the loop's end is its start
        path = path[:-1]
    return [list(p) for p in path], marks


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    routes = []
    for vid, kind, label, stops in JOURNEYS:
        path, marks = journey(layout, stops)
        routes.append({
            "id": vid, "kind": kind, "label": label, "loop": kind != "truck", "path": path,
            "stops": [{"at": at, "what": what, "loaded": loaded} for at, (_, what, loaded) in zip(marks, stops)],
        })
    out = ROOT / "viewer3d" / "demo-routes.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"layout": "dames-point", "routes": routes}, separators=(",", ":")) + "\n")
    print(f"wrote {out.relative_to(ROOT)}: " + ", ".join(f"{r['id']} {len(r['path'])} cells" for r in routes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
