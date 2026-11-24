"""Compare each module's policies on the same workloads, and the fleet size and the load: the experiment
Dr. Wang asked for ("varying fleet size or workload, using identical saved workloads and random seeds
for both policies"), with enough runs to say how sure each number is.

    python tools/experiments.py                    everything, 30 runs a setting; writes docs/experiments.csv and
                                                   docs/experiments-summary.json (about 25 minutes on two cores)
    python tools/experiments.py --reps 10          fewer runs (nothing is written unless everything is run with 30)
    python tools/experiments.py --only fleet       one comparison
    python tools/experiments.py --scale small      only the small workloads (two minutes)

Two sets of workloads:

  small   sim.scenario.generate(seed): three ships with about ten containers each, 14 trucks, 10 AGVs, on the
          reference numbers (PortSim's), where every time is fixed. What varies from run to run is the workload.
  shift   sim.scenario.shift(seed): a shift at real scale on the calibrated numbers (docs/data-sheet.md): four
          ships with 140 to 240 moves each, 80 trucks, 30 AGVs, the yard 65 percent full, crane times that
          vary, no-shows, breakdowns. What varies is the workload and every random time.

Every setting of a comparison runs the same seeds, so run k under one policy and run k under the other
have the same ships, trucks, containers and (in the shift) the same crane time for each container: the
difference between them comes from the policy. Each table gives the mean and the half-width of its 95
percent confidence interval (Student's t), and for a comparison of two settings the mean of the paired
differences with its interval; a difference whose interval does not include zero is marked with *.
The numbers are a model's: they rank the policies on this map, they are not a forecast for a real terminal.

  yard alone   the stacking owner's own test (Oct 29): 120 containers through a small yard, arriving and
               leaving all the time, at 50, 75 and 90 percent full; rehandles under each policy
  stacking, ship, gate, dispatch   each module's policies inside the whole terminal
  fleet        the number of AGVs
  trucks       (shift only) trucks an hour, to find where the truck exchange stops keeping up
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing
import os
import random
import sys
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402
from terminal_layout.timing import YARD_SERVICE_S  # noqa: E402

from sim import scenario as scenarios  # noqa: E402
from sim.kernel import Container  # noqa: E402
from sim.metrics import check, summary  # noqa: E402
from sim.ship_arrival import Tide  # noqa: E402
from sim.stacking import Yard, Zone  # noqa: E402
from sim.terminal import Terminal  # noqa: E402

OUT = ROOT / "docs" / "experiments.csv"
SUMMARY = ROOT / "docs" / "experiments-summary.json"
REPS = 30
TRIAL_ZONES = (Zone("trial", "general", "A1", (1, 2)),)       # 12 stacks, 5 high
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]     # Student's t, 97.5 percent, 1 to 30 degrees of freedom


def t975(df: int) -> float:
    return T975[df - 1] if df <= 30 else 2.021 if df <= 40 else 2.000 if df <= 60 else 1.980 if df <= 120 else 1.960


def interval(values) -> dict | None:
    """Mean and half-width of the 95 percent confidence interval of the mean."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    half = t975(len(values) - 1) * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {"mean": round(mean(values), 3), "half": round(half, 3), "n": len(values)}


def stacking_trial(layout, policy: str, seed: int, n: int = 120, fill: float = 0.75) -> dict:
    """The stacking module alone: n containers come and go through two bays kept about `fill` full.
    Each has a known time to leave. Returns the rehandles it took to get them all out."""
    rng = random.Random(f"stack:{seed}:{n}:{fill}")
    yard = Yard(layout, TRIAL_ZONES, policy=policy)
    room = sum(yard._room(b) for b in yard.bays)
    target = max(1, round(fill * room))
    gap = 60.0
    arrivals = [(i * gap, Container(f"C{i:03d}", "import")) for i in range(n)]
    for t, c in arrivals:
        c.due_s = t + rng.uniform(0.2, 1.8) * target * gap               # mean stay keeps about `target` in the yard
    events = sorted([(t, 1, c.id, c) for t, c in arrivals] + [(c.due_s, 0, c.id, c) for _, c in arrivals])
    waiting, late = [], []

    def take(c):
        for b in yard.blockers(c.id):
            to = yard.rehandle_target(b, c.id)
            yard.lift_off(b)
            yard.relocate(b, to)
        yard.lift_off(c.id)
        yard.gone(c.id)
        while waiting and yard.count() < target:
            x = waiting.pop(0)
            yard.assign(x)
            yard.set_down(x.id, yard.settle(x.id))
            if x in late:                                                # it was due out before it could come in
                late.remove(x)
                take(x)
    for _, arriving, _, c in events:
        if arriving:
            if yard.count() >= target:
                waiting.append(c)
                continue
            yard.assign(c)
            yard.set_down(c.id, yard.settle(c.id))
        elif c.id in yard.where:
            take(c)
        else:
            late.append(c)
    return {"rehandles": yard.rehandles, "lifts": yard.taken, "handling_min": round((yard.taken + yard.rehandles) * YARD_SERVICE_S / 60.0, 1),
            "left_in_yard": yard.count()}


# ---- the comparisons ---------------------------------------------------------------------------------
POLICY = {**scenarios.POLICIES, "dispatch": ("nearest_feasible", "longest_idle", "balanced")}     # the rules written by hand; the learned
#                                                               one is trained and tested by tools/learned_dispatch.py on these same seeds
SMALL = {
    "stacking": ([{"stacking": p} for p in POLICY["stacking"]], ("rehandles", "truck_turnaround_min", "end_min")),
    "ship": ([{"ship": p} for p in POLICY["ship"]], ("ship_wait_min", "ship_wait_max_min", "ship_turnaround_min")),
    "gate": ([{"gate": p} for p in POLICY["gate"]], ("truck_turnaround_min", "truck_gate_wait_min", "truck_inside_min")),
    "dispatch": ([{"dispatch": p} for p in POLICY["dispatch"]], ("agv_wait_for_agv_min", "end_min", "agv_km", "agv_kwh")),
    "fleet": ([{"agvs": n} for n in (6, 8, 10, 12)], ("end_min", "moves_per_hour", "ship_turnaround_min", "truck_turnaround_min", "quay_crane_wait_min")),
}
SHIFT = {
    "stacking": ([{"stacking": p} for p in POLICY["stacking"]], ("rehandles", "quay_moves_per_crane_hour", "ship_turnaround_min", "truck_turnaround_min")),
    "ship": ([{"ship": p} for p in POLICY["ship"]], ("ship_wait_min", "ship_wait_max_min", "ship_turnaround_min")),
    "gate": ([{"gate": p} for p in POLICY["gate"]], ("truck_turnaround_min", "truck_gate_wait_min", "truck_inside_min", "imports_sent_back")),
    "dispatch": ([{"dispatch": p} for p in POLICY["dispatch"]], ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "agv_km")),
    "fleet": ([{"agvs": n} for n in (18, 24, 30, 36)], ("quay_moves_per_crane_hour", "ship_turnaround_min", "truck_turnaround_min", "quay_crane_wait_min", "agv_blocked_min")),
    "trucks": ([{"trucks_per_hour": n} for n in (6, 10, 14, 18)], ("truck_turnaround_min", "truck_gate_wait_min", "truck_inside_min", "trucks_served", "ship_turnaround_min")),
}
SHIFT_DEFAULT = {"agvs": 30, "trucks_per_hour": 10}
# What the validation (docs/validation.md) reads off the shift as planned.
BASELINE = ("quay_moves", "quay_moves_per_crane_hour", "ship_turnaround_min", "ship_wait_min", "truck_turnaround_min", "truck_gate_wait_min",
            "truck_inside_min", "trucks", "trucks_served", "truck_no_shows", "imports_sent_back", "rehandles", "yard_lifts_out", "yard_occupancy",
            "agv_speed_m_s", "agv_kwh_per_operating_hour", "agv_parked_share", "agv_wait_at_yard_share", "yard_crane_travel_share", "agv_job_min",
            "charging_stays", "disruptions", "stalls", "end_min", "containers")

_layout = None


def layout():
    global _layout
    if _layout is None:
        _layout = load_layout(ROOT / "layouts" / "dames-point.json")
    return _layout


def label_of(setting: dict) -> str:
    return ", ".join(f"{k} = {v}" for k, v in setting.items())


def workload(scale: str, name: str, seed: int, setting: dict):
    """The scenario of one run: the seed's workload with one setting changed."""
    if scale == "shift":
        made = {**SHIFT_DEFAULT, **{k: v for k, v in setting.items() if k in SHIFT_DEFAULT}}
        return scenarios.shift(seed, **made).using(**{k: v for k, v in setting.items() if k not in SHIFT_DEFAULT})
    if name != "ship":
        return scenarios.generate(seed, agvs=10).using(**setting)
    sc = scenarios.generate(seed, vessels=4, agvs=10).using(tide=Tide(high_water_s=3 * 3600.0), **setting)
    sc.vessels[0].draft_m = 11.9             # the first ship in needs the tide (about 48 minutes); the others do not
    for v in sc.vessels[1:]:
        v.draft_m = min(v.draft_m, 11.0)
    return sc


def one_run(task: tuple) -> tuple:
    scale, name, seed, setting = task
    sc = workload(scale, name, seed, setting)
    t = Terminal(layout(), sc).run()
    s = summary(t)
    s["problems"] = len(check(t))
    s["finished"] = int(s["ships_served"] == len(sc.vessels) and not s["agv_jobs_open"] and not s["stuck_at_end"])
    return task, {k: v for k, v in s.items() if isinstance(v, (int, float)) or v is None}


def key_of(scale: str, name: str, seed: int, setting: dict) -> tuple:
    """Runs that are the same run are made once: in the shift the planned setting is part of every comparison."""
    if scale == "shift":
        full = {**SHIFT_DEFAULT, **scenarios.DEFAULT_POLICIES, **setting}
        return (scale, seed, tuple(sorted(full.items())))
    return (scale, name == "ship", seed, tuple(sorted({"agvs": 10, **scenarios.DEFAULT_POLICIES, **setting}.items())))


def cell(i: dict | None, star: bool = False) -> str:
    return f"{'-':>22s}" if i is None else f"{i['mean']:14.1f} ±{i['half']:5.1f}{'*' if star else ' '}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare policies, fleet sizes and loads on matched workloads.")
    p.add_argument("--reps", type=int, default=REPS, help="runs for each setting (30)")
    p.add_argument("--only", choices=["yard", *SHIFT])
    p.add_argument("--scale", choices=["small", "shift"], help="only the small workloads, or only the shift")
    p.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="runs at a time")
    a = p.parse_args(argv)
    seeds = range(1, a.reps + 1)
    records, lines, result = [], [], {"reps": a.reps, "experiments": []}

    if a.only in (None, "yard") and a.scale in (None, "small"):
        lines.append(f"yard alone: rehandles to get 120 containers out, mean of {a.reps * 3} workloads each (reference numbers)")
        lines.append(f"  {'policy':18s}{'50% full':>18s}{'75% full':>18s}{'90% full':>18s}{'handling min at 75%':>24s}")
        entry = {"name": "yard alone", "scale": "small", "settings": []}
        for policy in POLICY["stacking"]:
            cells, measures = [], {}
            for fill in (0.5, 0.75, 0.9):
                trials = [stacking_trial(layout(), policy, s, fill=fill) for s in range(1, a.reps * 3 + 1)]
                measures[f"rehandles at {fill:.0%}"] = interval([t["rehandles"] for t in trials])
                cells.append(measures[f"rehandles at {fill:.0%}"])
                if fill == 0.75:
                    measures["handling_min at 75%"] = interval([t["handling_min"] for t in trials])
                records += [{"experiment": "yard alone", "scale": "small", "setting": f"{policy}, {fill:.0%} full", "seed": s, "measure": "rehandles",
                             "value": t["rehandles"]} for s, t in enumerate(trials, start=1)]
            entry["settings"].append({"label": policy, "measures": measures})
            lines.append(f"  {policy:18s}" + "".join(f"{c['mean']:11.1f} ±{c['half']:5.1f}" for c in cells)
                         + f"{measures['handling_min at 75%']['mean']:17.1f} ±{measures['handling_min at 75%']['half']:5.1f}")
        result["experiments"].append(entry)
        lines.append("")

    plan = [(scale, name, settings, measures) for scale, table in (("small", SMALL), ("shift", SHIFT)) if a.scale in (None, scale)
            for name, (settings, measures) in table.items() if a.only in (None, name)]
    tasks = {}
    for scale, name, settings, _ in plan:
        for setting in settings:
            for seed in seeds:
                tasks.setdefault(key_of(scale, name, seed, setting), (scale, name, seed, setting))
    print(f"{len(tasks)} runs on {a.jobs} at a time ...", file=sys.stderr, flush=True)
    done = {}
    with multiprocessing.Pool(a.jobs) as pool:
        for n, (task, s) in enumerate(pool.imap_unordered(one_run, sorted(tasks.values(), key=lambda t: (t[0] != "shift", t[2])), chunksize=1), start=1):
            done[key_of(*task)] = s
            if n % 20 == 0:
                print(f"  {n} of {len(tasks)}", file=sys.stderr, flush=True)

    for scale, name, settings, measures in plan:
        rows = {label_of(st): [done[key_of(scale, name, seed, st)] for seed in seeds] for st in settings}
        unfinished = sum(1 - r["finished"] for rs in rows.values() for r in rs)
        broken = sum(r["problems"] for rs in rows.values() for r in rs)
        entry = {"name": name, "scale": scale, "measures": list(measures), "unfinished": unfinished, "problems": broken, "settings": []}
        what = "a shift at real scale, calibrated numbers" if scale == "shift" else "small workloads, reference numbers"
        lines.append(f"{name} ({what}): mean of {a.reps} runs ± half the 95% interval (runs not finished: {unfinished}, check problems: {broken})")
        lines.append(f"  {'setting':30s}" + "".join(f"{m:>22s}" for m in measures))
        first = rows[label_of(settings[0])]
        for st in settings:
            lab = label_of(st)
            ints = {m: interval([r[m] for r in rows[lab]]) for m in measures}
            entry["settings"].append({"label": lab, "measures": ints})
            lines.append(f"  {lab:30s}" + "".join(cell(ints[m]) for m in measures))
            for seed, r in zip(seeds, rows[lab]):
                records += [{"experiment": name, "scale": scale, "setting": lab, "seed": seed, "measure": m, "value": r[m]} for m in (*measures, "finished", "problems")]
        for st in settings[1:]:                        # each setting against the first, run by run
            lab = label_of(st)
            diffs = {m: interval([b[m] - x[m] for x, b in zip(first, rows[lab]) if x[m] is not None and b[m] is not None]) for m in measures}
            entry.setdefault("differences", []).append({"label": lab, "against": label_of(settings[0]), "measures": diffs})
            lines.append(f"  {'  minus ' + label_of(settings[0]):30s}" + "".join(cell(diffs[m], diffs[m] is not None and abs(diffs[m]["mean"]) > diffs[m]["half"]) for m in measures))
        result["experiments"].append(entry)
        lines.append("")

    if a.scale in (None, "shift") and a.only is None:
        base = [done[key_of("shift", "fleet", seed, {})] for seed in seeds]
        result["baseline"] = {"setting": {**SHIFT_DEFAULT, **scenarios.DEFAULT_POLICIES}, "measures": {m: interval([r[m] for r in base]) for m in BASELINE},
                              "finished": sum(r["finished"] for r in base), "problems": sum(r["problems"] for r in base)}
        lines.append(f"the shift as planned ({a.reps} runs): " + ", ".join(f"{m} {i['mean']:.1f} ±{i['half']:.1f}" for m, i in result["baseline"]["measures"].items() if i))
    print("\n".join(lines))
    if a.only is None and a.scale is None and a.reps == REPS:
        with open(OUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["experiment", "scale", "setting", "seed", "measure", "value"])
            w.writeheader()
            w.writerows(records)
        SUMMARY.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)} ({len(records)} rows) and {SUMMARY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
