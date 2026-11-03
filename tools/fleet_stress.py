"""Stress-test the roads: many AGVs at once, one vehicle per cell, and where they wait.

Run from the repo root:

    python tools/fleet_stress.py                         # 6, 12 and 24 AGVs on Dames Point, one hour each
    python tools/fleet_stress.py --fleet 12 --png docs/fleet-waiting.png
    python tools/fleet_stress.py --layout layouts/variants/dames-point-two-way.json

This is a test of the map, not an AGV controller. The rules are deliberately simple so a jam
points at the roads, not at a clever or a poor dispatcher:

* Jobs alternate import (quay crane -> yard bay) and export (yard bay -> quay crane), with the
  crane and the bay drawn at random (seeded). A vehicle goes to the pickup, waits for the
  handover, drives to the drop-off, waits again, and takes the next job.
* Time runs in ticks of one move (15 m at SPEED_M_S). A cell holds one vehicle. Each tick, in a
  rotating order, a vehicle moves to the next cell of its shortest path if that cell is free.
* A vehicle waiting at a transfer point stands in the lane, as on the real map, and blocks it.
* A vehicle blocked for REPLAN ticks plans again around the cells that are occupied right now.
* Gridlock: no vehicle can move for GRIDLOCK ticks while some want to. It is reported, then the
  run ends, because the simple rules cannot recover from it.

Output: jobs per hour, average wait per job, gridlocks, and the cells where vehicles waited most.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEED_M_S = 4.0          # PortSim's loaded speed; empty vehicles are faster there (6 m/s)
QUAY_HANDOVER_S = 60     # PortSim Jacksonville quay and yard service times
YARD_HANDOVER_S = 75
HOUR_S = 3600
REPLAN = 8               # ticks blocked before planning around occupied cells
GRIDLOCK = 60            # ticks with nobody able to move


def run(layout, fleet: int, seconds: int = HOUR_S, seed: int = 7, quay_rule: str = "lane"):
    """quay_rule "lane": a job names one transfer lane and the vehicle drives to it by the
    shortest path. "crane": a job names a crane; the vehicle takes the first of its free lanes
    and comes up from the bypass lane, so it never queues behind a vehicle being served."""
    rng = random.Random(seed)
    tick_s = layout.cell_size_m / SPEED_M_S
    quay_lanes = [p for c in layout.quay_cranes.values() for p in c.transfer_points]
    lanes_of = {p: c.transfer_points for c in layout.quay_cranes.values() for p in c.transfer_points}
    bays = [p for b in layout.blocks.values() for p in b.transfer_points]
    starts = rng.sample([c.pos for c in layout.cells("lane")], fleet)
    path_cache = {}

    def path(a, b, avoid=frozenset()):
        if not avoid:
            if (a, b) not in path_cache:
                path_cache[(a, b)] = layout.shortest_path(a, b)
            return path_cache[(a, b)]
        return layout.with_closures(avoid - {a, b}).shortest_path(a, b)

    vehicles = []
    for i, s in enumerate(starts):
        vehicles.append({"id": i, "at": s, "jobs": 0, "phase": "to_pick", "wait": 0, "blocked": 0,
                         "dwell": 0, "plan": None, "job": None, "waited": 0})

    def quay_target(v, lane):
        """With the crane rule: a free lane of the same crane, reached from the bypass cell below it."""
        if quay_rule != "crane":
            return lane, path(v["at"], lane)
        free = [p for p in lanes_of[lane] if p not in occupied] or [lane]
        best, best_path = None, None
        for p in free:
            below = (p[0], p[1] - 1)
            leg = path(v["at"], below) if v["at"] != below else [below]
            if leg is not None and (best_path is None or len(leg) < len(best_path)):
                best, best_path = p, leg + [p]
        return best, best_path

    def new_job(v):
        if v["jobs"] % 2 == 0:
            v["job"] = (rng.choice(quay_lanes), rng.choice(bays), QUAY_HANDOVER_S, YARD_HANDOVER_S)
        else:
            v["job"] = (rng.choice(bays), rng.choice(quay_lanes), YARD_HANDOVER_S, QUAY_HANDOVER_S)
        v["phase"] = "to_pick"
        if v["job"][0] in lanes_of:
            lane, v["plan"] = quay_target(v, v["job"][0])
            v["job"] = (lane,) + v["job"][1:]
        else:
            v["plan"] = path(v["at"], v["job"][0])

    occupied = {v["at"]: v["id"] for v in vehicles}
    for v in vehicles:
        new_job(v)
    waits = Counter()
    gridlock, stuck, done, job_waits = None, 0, 0, []
    ticks = int(seconds / tick_s)
    for t in range(ticks):
        moved, wanting = 0, 0
        order = vehicles[t % fleet:] + vehicles[:t % fleet]
        for v in order:
            if v["dwell"] > 0:
                v["dwell"] -= 1
                continue
            target = v["job"][0] if v["phase"] == "to_pick" else v["job"][1]
            if v["at"] == target:
                if v["phase"] == "to_pick":
                    v["phase"] = "to_drop"
                    v["dwell"] = round(v["job"][2] / tick_s)
                    if v["job"][1] in lanes_of:
                        lane, v["plan"] = quay_target(v, v["job"][1])
                        v["job"] = v["job"][:1] + (lane,) + v["job"][2:]
                    else:
                        v["plan"] = path(v["at"], v["job"][1])
                else:
                    v["dwell"] = round(v["job"][3] / tick_s)
                    v["jobs"] += 1
                    done += 1
                    job_waits.append(v["waited"] * tick_s)
                    v["waited"] = 0
                    new_job(v)
                continue
            wanting += 1
            plan = v["plan"]
            if plan is None:
                v["plan"] = path(v["at"], target, frozenset(occupied))
                waits[v["at"]] += 1
                v["waited"] += 1
                continue
            idx = plan.index(v["at"]) if v["at"] in plan else None
            if idx is None:
                v["plan"] = path(v["at"], target)
                continue
            nxt = plan[idx + 1]
            if nxt in occupied:
                v["blocked"] += 1
                v["waited"] += 1
                waits[v["at"]] += 1
                if v["blocked"] >= REPLAN:
                    v["plan"] = path(v["at"], target, frozenset(occupied)) or plan
                    v["blocked"] = 0
                continue
            del occupied[v["at"]]
            v["at"] = nxt
            occupied[nxt] = v["id"]
            v["blocked"] = 0
            moved += 1
        if wanting and not moved and not any(v["dwell"] for v in vehicles):
            stuck += 1
            if stuck >= GRIDLOCK:
                gridlock = round(t * tick_s)
                break
        else:
            stuck = 0
    hours = (t + 1) * tick_s / 3600
    return {"fleet": fleet, "jobs_per_hour": done / hours, "mean_wait_s": (sum(job_waits) / len(job_waits)) if job_waits else 0.0,
            "gridlock_at_s": gridlock, "waits": waits, "tick_s": tick_s}


def heatmap(layout, waits, tick_s, png):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(18, 7.5))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    layout.render_2d(ax=ax)
    pts = [(x + 0.5, y + 0.5, n * tick_s) for (x, y), n in waits.items()]
    if pts:
        top = max(p[2] for p in pts)
        sc = ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=[30 + 400 * p[2] / top for p in pts],
                        c=[p[2] for p in pts], cmap="inferno_r", alpha=0.75, zorder=9, edgecolors="black", linewidths=0.3)
        fig.colorbar(sc, ax=ax, fraction=0.02, pad=0.01, label="seconds vehicles spent waiting in this cell")
    ax.set_title(ax.get_title() + "  |  where AGVs waited for a free cell")
    fig.savefig(png, dpi=110, bbox_inches="tight")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default=str(ROOT / "layouts" / "dames-point.json"))
    ap.add_argument("--fleet", type=int, nargs="*", default=[6, 12, 24])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--png", default=None, help="waiting heat map for the last fleet size")
    ap.add_argument("--quay-rule", choices=["lane", "crane"], default="lane",
                    help="lane: go to one named transfer lane; crane: take a free lane of the crane, from the bypass")
    ap.add_argument("--hours", type=float, default=1.0)
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    layout = load_layout(args.layout)
    print(f"{layout.name}: {args.hours:g} simulated hour(s) per fleet size, {SPEED_M_S} m/s, seed {args.seed}, quay rule {args.quay_rule}")
    print(f"{'AGVs':>5} {'jobs/hour':>10} {'avg wait per job (s)':>21} {'gridlock':>10}  busiest waiting cells")
    for n in args.fleet:
        r = run(layout, n, seconds=int(args.hours * HOUR_S), seed=args.seed, quay_rule=args.quay_rule)
        top = ", ".join(f"{c}: {round(w * r['tick_s'])} s" for c, w in r["waits"].most_common(3))
        gl = f"{r['gridlock_at_s']} s" if r["gridlock_at_s"] is not None else "none"
        print(f"{n:5d} {r['jobs_per_hour']:10.1f} {r['mean_wait_s']:21.1f} {gl:>10}  {top}")
    if args.png:
        heatmap(layout, r["waits"], r["tick_s"], args.png)
        print(f"wrote {args.png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
