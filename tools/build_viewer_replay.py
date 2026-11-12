"""Write viewer3d/demo-replay.json: one simulation run for the 3D viewer to play back, and the
stress test's waiting map for its heat-map layer.

Run from the repo root (after `pip install -e .`):

    python tools/build_viewer_replay.py

The replay is the trace of sim/run_demo.py (Terminal.trace), reshaped so the viewer can show any
moment of it without knowing a rule of the simulation:

* vehicles: where each one starts and its legs (t0, t1, the cells it drives through, loaded or not);
* containers: phases in time order, each either at a place or being lifted from one place to another
  (by a crane, or by nobody for a handoff between a vehicle and the ground);
* log: the run's own event lines.

Places are ["ship", crane, n], ["ground", [x, y], k], ["vehicle", id], ["slot", slot id] and
["stack", block id]; n and k tell apart containers that share a ship bay or a ground buffer.
Times are seconds from the ship's arrival. Any module's run can be played the same way: write this
format from your own event log and the viewer needs no change.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "viewer3d" / "demo-replay.json"
HEAT_FLEET, HEAT_SEED = 24, 7


def load(path: str, name: str):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def r1(t: float) -> float:
    return round(float(t), 1)


def replay_from(terminal, layout) -> dict:
    """Reshape a finished Terminal's trace and log. Raises ValueError if the trace does not hang together."""
    trace = terminal.trace
    vehicles = {v["id"]: {"id": v["id"], "type": v["type"], "at": list(v["at"]), "t": r1(v["t"]), "legs": []}
                for v in trace if v["kind"] == "vehicle"}
    here = {v["id"]: tuple(v["at"]) for v in trace if v["kind"] == "vehicle"}
    for d in sorted((d for d in trace if d["kind"] == "drive"), key=lambda d: d["t0"]):
        v, path = vehicles[d["who"]], [tuple(p) for p in d["path"]]
        if path[0] != here[v["id"]]:
            raise ValueError(f"{v['id']} leaves {path[0]} at {d['t0']:.0f} s but is at {here[v['id']]}")
        if v["legs"] and d["t0"] < v["legs"][-1]["t1"] - 0.05:
            raise ValueError(f"{v['id']} starts a leg at {d['t0']:.0f} s before the last one ends")
        bad = [(a, b) for a, b in zip(path, path[1:]) if b not in layout.neighbors(*a)]
        if bad:
            raise ValueError(f"{v['id']}: illegal move {bad[0]}")
        here[v["id"]] = path[-1]
        if len(path) > 1:                                    # a "drive" to the cell it is already on is no leg
            v["legs"].append({"t0": r1(d["t0"]), "t1": r1(d["t1"]), "loaded": d["loaded"], "what": d["what"], "path": [list(p) for p in path]})
    for v in vehicles.values():
        v["gone"] = v["legs"][-1]["t1"] if v["type"] == "truck" and v["legs"] else None   # a truck drives out of the picture

    lifts: dict[str, list[dict]] = {}
    for lift in sorted((r for r in trace if r["kind"] == "lift"), key=lambda r: r["t0"]):
        lifts.setdefault(lift["what"], []).append(lift)
    ship_count: dict[str, int] = {}                  # crane -> ship positions handed out so far
    ground: dict[tuple, dict[int, tuple]] = {}       # cell -> {k: (from, until)}

    def place(p, since, until):
        kind = p[0]
        if kind == "ship":
            n = ship_count.get(p[1], 0)
            ship_count[p[1]] = n + 1
            return ["ship", p[1], n]
        if kind == "ground":
            cell, taken = tuple(p[1]), ground.setdefault(tuple(p[1]), {})
            k = next(k for k in range(99) if all(not (a < until and since < b) for a, b in taken.get(k, [])))
            taken.setdefault(k, []).append((since, until))
            return ["ground", list(cell), k]
        return [kind, p[1]]

    containers, end = [], max(t for t, _ in terminal.log)
    first_seen = {v["id"]: v["t"] for v in vehicles.values()}
    for cid, moves in lifts.items():
        for a, b in zip(moves, moves[1:]):
            if b["t0"] < a["t1"] - 0.05 or tuple(b["src"]) != tuple(a["dst"]):
                raise ValueError(f"{cid}: lift at {b['t0']:.0f} s does not follow the one before it")
        src0 = moves[0]["src"]
        start = first_seen[src0[1]] if src0[0] == "vehicle" else None
        phases = [{"t0": start, "t1": r1(moves[0]["t0"]), "at": place(src0, float("-inf"), moves[0]["t1"])}]
        for i, m in enumerate(moves):
            until = moves[i + 1]["t0"] if i + 1 < len(moves) else float("inf")
            leaves = moves[i + 1]["t1"] if i + 1 < len(moves) else float("inf")
            dst = place(m["dst"], m["t0"], leaves)           # its spot is taken from the lift toward it to the lift away
            phases.append({"t0": r1(m["t0"]), "t1": r1(m["t1"]), "by": m["by"], "from": phases[-1]["at"], "to": dst})
            phases.append({"t0": r1(m["t1"]), "t1": None if until == float("inf") else r1(until), "at": dst})
        containers.append({"id": cid, "kind": "import" if src0[0] == "ship" else "export", "phases": phases})
    for cell, taken in ground.items():
        cap = layout.cell(*cell).attrs.get("buffer_capacity", 0)
        if max(taken) >= max(cap, 1):
            print(f"warning: {max(taken) + 1} containers on the ground at {cell}, more than its buffer of {cap}", file=sys.stderr)
    return {
        "name": "Integration demo", "source": "sim/run_demo.py", "t_end": r1(end),
        "vessels": [{"id": v["id"], "berth": v["berth"], "t": r1(v["t"])} for v in trace if v["kind"] == "vessel"],
        "vehicles": list(vehicles.values()), "containers": containers,
        "log": [[r1(t), text] for t, text in sorted(terminal.log, key=lambda e: e[0])],
    }


def waiting(layout) -> dict:
    """Seconds vehicles spent waiting for a free cell, by cell: the stress test with 24 AGVs for an hour."""
    fleet = load("tools/fleet_stress.py", "fleet_stress")
    r = fleet.run(layout, HEAT_FLEET, seed=HEAT_SEED)
    cells = sorted([x, y, round(n * r["tick_s"])] for (x, y), n in r["waits"].items())
    return {"fleet": HEAT_FLEET, "seed": HEAT_SEED, "hours": 1, "jobs_per_hour": round(r["jobs_per_hour"]), "cells": cells}


def build(layout) -> dict:
    demo = load("sim/run_demo.py", "run_demo")
    terminal = demo.Terminal(layout)
    terminal.run()
    return {"layout": layout.name, "replay": replay_from(terminal, layout), "waiting": waiting(layout)}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    data = build(layout)
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    rp = data["replay"]
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB): {len(rp['vehicles'])} vehicles, "
          f"{len(rp['containers'])} containers, {rp['t_end'] / 60:.1f} min; waiting map of {len(data['waiting']['cells'])} cells")
    return 0


if __name__ == "__main__":
    sys.exit(main())
