"""Write the runs the 3D viewer plays back, and the stress test's waiting map for its heat-map layer.

Run from the repo root (after `pip install -e .`):

    python tools/build_viewer_replay.py

    viewer3d/terminal-replay.json   the integrated terminal (sim/terminal.py) on the demo morning: all
                                    five modules, three ships, 12 AGVs, the gate. The viewer plays this one.
    viewer3d/terminal-variants.json the recorded what-if runs the viewer's control room offers when no
                                    simulator is behind it: the same morning with one thing changed
                                    (sim.control.VARIANTS)
    viewer3d/demo-replay.json       the earlier six-container walkthrough (sim/run_demo.py); open the
                                    viewer with ?run=walkthrough to play it instead

A replay is a run's trace (Terminal.trace), reshaped so the viewer can show any moment of it without
knowing a rule of the simulation:

* vehicles: where each one starts and its legs (t0, t1, the cells it drives through, loaded or not).
  A leg may carry marks, [cell index, time] pairs for cells the vehicle is known to pass at a known
  time; the viewer keeps to them and moves it smoothly in between. Here they are the stopping points
  of PortSim's travel time, so two vehicles on one road stay as far apart as they are in the run;
* containers: phases in time order, each either at a place or being lifted from one place to another
  by a crane (`by`; a crane lifts one container at a time);
* log: the run's own event lines.

Places are ["ship", crane, n], ["ground", [x, y], k], ["vehicle", id], ["slot", slot id] and
["stack", block id]; n and k tell apart containers that share a ship bay or a ground buffer.
Times are seconds from the ship's arrival. Any module's run can be played the same way: write this
format from your own event log and the viewer needs no change.

The integrated run adds, all optional for the viewer:

* places ["ship", crane, n, vessel] and ["slot", slot id, row, tier]: the ship a container is on, and
  its exact position in the yard (row 1 to the block's container_rows, tier 1 on the ground);
* vessels: several, each with t_arrive (at the anchorage), t_cleared, t (berthed) and t_depart;
* managed_slots: the ground slots whose stacks the run keeps itself. The viewer draws only the run's
  containers there and leaves its example fill out;
* zones: the bays the stacking module uses and the cargo each takes;
* for an AGV, home (its charger), battery ([time, percent] points) and charging ([from, until] stays);
* a leg that ends before the vehicle's destination, because the road ahead was taken, carries dest;
* log lines carry the module they came from as a third item; summary and policies describe the run;
* disruptions: what was injected into the run, each with t0 and t1 (t1 null = to the end): {"kind": "agv",
  "id", "at"} a breakdown, {"kind": "road", "cells"} a closed road, {"kind": "crane", "id"} a crane out
  of service, {"kind": "gate", "lane", "cell"} a closed gate lane;
* settings (what sim.control was asked for) and problems (what the run's own checks found, usually nothing);
* series: what the terminal was doing over the run in equal steps of time (sim.metrics.timeline), with the
  fleet's mean and lowest charge at the end of each step;
* why: the reasons the modules gave for their decisions, [time, module, [what it is about], text];
* ships: each call with its draft, how many containers it brings and takes, and its times; trucks: every truck
  that came, with booked (its appointment; late: it lost it), picks and drops (container ids), arrived, in (let in) and gone, also
  one that was turned away and so is never drawn; an AGV's jobs are [from, until, what for, container] and a
  vehicle's waits [from, until, cell, held by]; a container's ship, truck and due.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout.timing import stopping_points, travel_time  # noqa: E402

OUT = ROOT / "viewer3d" / "demo-replay.json"
TERMINAL_OUT = ROOT / "viewer3d" / "terminal-replay.json"
VARIANTS_OUT = ROOT / "viewer3d" / "terminal-variants.json"
HEAT_FLEET, HEAT_SEED = 24, 7


def load(path: str, name: str):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def r1(t: float) -> float:
    return round(float(t), 1)


def marks(layout, stops, drive) -> list | None:
    """[cell index, time] at each stopping point a leg passes, if the leg takes PortSim's travel time."""
    path = [tuple(p) for p in drive["path"]]
    whole = travel_time(layout, path, loaded=drive["loaded"], stops=stops)
    if abs(whole - (drive["t1"] - drive["t0"])) > 0.05:
        return None
    return [[i, r1(drive["t0"] + travel_time(layout, path[:i + 1], loaded=drive["loaded"], stops=stops))]
            for i in range(1, len(path) - 1) if path[i] in stops]


def replay_from(terminal, layout) -> dict:
    """Reshape a finished Terminal's trace and log. Raises ValueError if the trace does not hang together."""
    trace = terminal.trace
    stops = stopping_points(layout)
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
            leg = {"t0": r1(d["t0"]), "t1": r1(d["t1"]), "loaded": d["loaded"], "what": d["what"], "path": [list(p) for p in path]}
            passed = marks(layout, stops, d)
            if passed:
                leg["marks"] = passed
            v["legs"].append(leg)
    for v in vehicles.values():
        v["gone"] = v["legs"][-1]["t1"] if v["type"] == "truck" and v["legs"] else None   # a truck drives out of the picture

    lifts: dict[str, list[dict]] = {}
    busy_until: dict[str, float] = {}
    for lift in sorted((r for r in trace if r["kind"] == "lift"), key=lambda r: r["t0"]):
        lifts.setdefault(lift["what"], []).append(lift)
        if lift["by"]:
            if lift["t0"] < busy_until.get(lift["by"], float("-inf")) - 0.05:
                raise ValueError(f"{lift['by']} starts a lift at {lift['t0']:.0f} s before its last one ends")
            busy_until[lift["by"]] = lift["t1"]
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
            k = next(k for k in range(99) if all(not (a < until - 1e-6 and since < b - 1e-6) for a, b in taken.get(k, [])))
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


def rest_marks(leg, moves) -> list:
    """[cell index, time] where a vehicle comes to rest inside a leg: the end of each admitted move but
    the last. moves are the vehicle's (t0, t1, cells) in time order. Raises if they do not make up the leg."""
    path, marks, i = [tuple(c) for c in leg["path"]], [], 0
    for t0, t1, cells, *rolled in moves:                    # rolled: it passed the end of the move without stopping
        if t0 < leg["t0"] - 1e-6 or t1 > leg["t1"] + 1e-6:
            continue
        if tuple(cells) != tuple(path[i:i + len(cells)]):
            raise ValueError(f"{leg['who']}: the moves from {leg['t0']:.0f} s do not make up the leg")
        i += len(cells) - 1
        if i < len(path) - 1 and not (rolled and rolled[0]):
            marks.append([i, r1(t1)])
    if i != len(path) - 1:
        raise ValueError(f"{leg['who']}: the leg from {leg['t0']:.0f} s is longer than its moves")
    return [m for k, m in enumerate(marks) if leg["t0"] + 0.05 < m[1] < leg["t1"] - 0.05 and (not k or m[1] > marks[k - 1][1] + 0.05)]


def battery_points(samples, stays, energy, end: float) -> list:
    """[time, percent] points for one AGV, thinned: the charge when it parks and leaves, when charging
    reaches its limit, and whenever it has changed by half a percent."""
    cap, keep = energy.capacity_kwh, {round(t, 3) for stay in stays for t in stay[1:] if t is not None}
    points = sorted(samples)
    for _, t0, t1 in stays:                                  # the moment charging stops at its limit
        kwh = next((k for t, k in points if abs(t - t0) < 1e-6), None)
        if kwh is not None and kwh < cap * energy.charge_to:
            full = t0 + (cap * energy.charge_to - kwh) / energy.charge_kw * 3600.0
            if full < (end if t1 is None else t1):
                points.append((full, cap * energy.charge_to))
                keep.add(round(full, 3))
    out = []
    for t, kwh in sorted(points):
        pct = round(100.0 * kwh / cap, 1)
        if not out or round(t, 3) in keep or abs(pct - out[-1][1]) >= 0.5:
            if out and r1(t) == out[-1][0]:
                out[-1][1] = pct
            else:
                out.append([r1(t), pct])
    return out


def replay_from_run(terminal, layout, settings: dict | None = None, problems: list | None = None) -> dict:
    """Reshape a finished run of the integrated terminal (sim/terminal.py). Raises ValueError if the
    trace does not hang together: a leg that does not start where its vehicle stands, an illegal move,
    a crane with two lifts at once, a lift that does not follow the one before, two containers in one spot."""
    trace, end, fleet, traffic = terminal.trace, terminal.end_s, terminal.fleet, terminal.traffic
    vehicles = {v["id"]: {"id": v["id"], "type": v["type"], "at": list(v["at"]), "t": r1(v["t"]), "legs": [], "gone": None}
                for v in trace if v["kind"] == "vehicle"}
    here = {v["id"]: tuple(v["at"]) for v in trace if v["kind"] == "vehicle"}
    moves: dict[str, list] = {}
    for (vid, t0, t1, cells), rolled in zip(traffic.segments, traffic.rolled):
        moves.setdefault(vid, []).append((t0, t1, cells, rolled))
    for d in sorted((d for d in trace if d["kind"] == "drive"), key=lambda d: (d["t0"], d["who"])):
        v, path = vehicles[d["who"]], [tuple(c) for c in d["path"]]
        if path[0] != here[v["id"]]:
            raise ValueError(f"{v['id']} leaves {path[0]} at {d['t0']:.0f} s but is at {here[v['id']]}")
        if v["legs"] and d["t0"] < v["legs"][-1]["t1"] - 0.05:
            raise ValueError(f"{v['id']} starts a leg at {d['t0']:.0f} s before the last one ends")
        bad = [(a, b) for a, b in zip(path, path[1:]) if b not in layout.neighbors(*a)]
        if bad:
            raise ValueError(f"{v['id']}: illegal move {bad[0]}")
        here[v["id"]] = path[-1]
        leg = {"t0": r1(d["t0"]), "t1": r1(d["t1"]), "loaded": d["loaded"], "what": d["what"], "path": [list(c) for c in path]}
        if d.get("dest") and tuple(d["dest"]) != path[-1]:
            leg["dest"] = list(d["dest"])                    # it stopped short of where it was going: the road ahead was taken
        passed = rest_marks(d, moves.get(d["who"], []))
        if passed:
            leg["marks"] = passed
        v["legs"].append(leg)
    for g in trace:
        if g["kind"] == "gone":
            vehicles[g["who"]]["gone"] = r1(g["t"])
    samples: dict[str, list] = {}
    for vid, t, kwh in traffic.soc_log:
        samples.setdefault(vid, []).append((t, kwh))
    for vid, agv in fleet.agvs.items():
        v, stays = vehicles[vid], [stay for stay in fleet.charges if stay[0] == vid]
        v["home"] = None if fleet.pool else fleet.station_id.get(agv.home)      # None: a pooled fleet, which shares the stations and stands by on the apron
        if traffic.energy:
            v["battery"] = battery_points(samples.get(vid, []), stays, traffic.energy, end)
            v["charging"] = [[r1(t0), r1(end if t1 is None else t1)] for _, t0, t1 in stays if (end if t1 is None else t1) > t0]

    lifts: dict[str, list[dict]] = {}
    busy_until: dict[str, float] = {}
    for lift in sorted((r for r in trace if r["kind"] == "lift"), key=lambda r: (r["t0"], r["by"])):
        lifts.setdefault(lift["what"], []).append(lift)
        if lift["t0"] < busy_until.get(lift["by"], float("-inf")) - 0.05:
            raise ValueError(f"{lift['by']} starts a lift at {lift['t0']:.0f} s before its last one ends")
        busy_until[lift["by"]] = lift["t1"]
    start = {r["what"]: tuple(r["at"]) for r in trace if r["kind"] == "start"}
    vessel_of = {cid: c.vessel for cid, c in terminal.containers.items()}
    ship_count: dict[tuple, int] = {}                # (vessel, crane) -> ship positions handed out so far
    ground: dict[tuple, dict[int, list]] = {}        # cell -> {k: [(from, until)]}
    spots: dict[tuple, list] = {}                    # (slot, row, tier) -> [(from, until, container)]

    def place(cid, p, since, until):
        kind = p[0]
        if kind == "ship":
            key = (vessel_of[cid], p[1])
            n = ship_count.get(key, 0)
            ship_count[key] = n + 1
            return ["ship", p[1], n, vessel_of[cid]]
        if kind == "ground":
            cell, taken = tuple(p[1]), ground.setdefault(tuple(p[1]), {})
            k = next(k for k in range(99) if all(not (a < until - 1e-6 and since < b - 1e-6) for a, b in taken.get(k, [])))
            taken.setdefault(k, []).append((since, until))
            return ["ground", list(cell), k]
        if kind == "slot":
            spots.setdefault(tuple(p[1:]), []).append((since, until, cid))
            return ["slot", p[1], p[2], p[3]]
        return [kind, p[1]]

    first_seen = {v["id"]: v["t"] for v in vehicles.values()}
    order = sorted(set(lifts) | set(start), key=lambda cid: (lifts[cid][0]["t0"] if cid in lifts else -1.0, cid))
    containers = []
    for cid in order:
        c, ms = terminal.containers[cid], lifts.get(cid, [])
        for a, b in zip(ms, ms[1:]):
            if b["t0"] < a["t1"] - 0.05 or tuple(b["src"]) != tuple(a["dst"]):
                raise ValueError(f"{cid}: lift at {b['t0']:.0f} s does not follow the one before it")
        src0 = start[cid] if cid in start else ms[0]["src"]
        if cid in start and ms and tuple(ms[0]["src"]) != start[cid]:
            raise ValueError(f"{cid}: its first lift is from {ms[0]['src']} but it started at {start[cid]}")
        since = first_seen[src0[1]] if src0[0] == "vehicle" else None
        leaves = ms[0]["t1"] if ms else float("inf")
        phases = [{"t0": since, "t1": r1(ms[0]["t0"]) if ms else None, "at": place(cid, src0, float("-inf"), leaves)}]
        for i, m in enumerate(ms):
            until = ms[i + 1]["t0"] if i + 1 < len(ms) else float("inf")
            leaves = ms[i + 1]["t1"] if i + 1 < len(ms) else float("inf")
            dst = place(cid, m["dst"], m["t0"], leaves)      # its spot is taken from the lift toward it to the lift away
            phases.append({"t0": r1(m["t0"]), "t1": r1(m["t1"]), "by": m["by"], "from": phases[-1]["at"], "to": dst})
            phases.append({"t0": r1(m["t1"]), "t1": None if until == float("inf") else r1(until), "at": dst})
        kind = "stock" if c.flow not in ("import", "export") else c.flow
        containers.append({"id": cid, "kind": kind, "cargo": c.cargo, "phases": phases})
        for key, value in (("ship", c.vessel), ("truck", c.truck), ("due", None if c.due_s is None else r1(c.due_s))):
            if value is not None:                            # what it came on or leaves on, and when it is expected to leave the yard
                containers[-1][key] = value
    for cell, taken in ground.items():
        cap = layout.cell(*cell).attrs.get("buffer_capacity", 0)
        if max(taken) >= max(cap, 1):
            raise ValueError(f"{max(taken) + 1} containers on the ground at {cell}, more than its buffer of {cap}")
    for spot, spans in spots.items():
        spans.sort()
        for (_, a1, a), (b0, _, b) in zip(spans, spans[1:]):
            if b0 < a1 - 0.05:
                raise ValueError(f"{a} and {b} are both at {spot} at {b0:.0f} s")

    yard, sc = terminal.yard, terminal.scenario
    zones = [{"name": z.name, "cargo": z.cargo, "block": z.block, "serves": z.serves,
              "slots": sorted({slot for bay, stacks in yard.bays.items() if yard.zone_of[bay] is z for slot, _ in stacks})}
             for z in yard.zones]
    managed = sorted({slot for z in zones for slot in z["slots"]} | set(terminal.xslots))
    from sim.metrics import summary, timeline
    numbers = summary(terminal)
    series = timeline(terminal)
    charge = [v["battery"] for v in vehicles.values() if v.get("battery")]
    if charge:                                               # the fleet's charge at the end of each step: the mean and the lowest
        def at(points, t):
            k = next((i for i, (t0, _) in enumerate(points) if t0 > t), len(points))
            if k == 0 or k == len(points):
                return points[min(k, len(points) - 1)][1]
            (t0, a), (t1, b) = points[k - 1], points[k]
            return a + (b - a) * (t - t0) / (t1 - t0)
        marks = [min(end, (i + 1) * series["step_s"]) for i in range(series["n"])]
        levels = [[at(points, t) for points in charge] for t in marks]
        series["battery_mean"] = [round(sum(row) / len(row), 1) for row in levels]
        series["battery_low"] = [round(min(row), 1) for row in levels]
    held_up: dict[str, list] = {}                            # vehicle -> [from, until, cell, held by] for every wait of 3 s or more
    for vid, t0, t1, cell, by in traffic.waits:
        if t1 - t0 >= 3.0 and vid in vehicles:
            held_up.setdefault(vid, []).append([r1(t0), r1(t1), list(cell), by])
    for vid in [*traffic.waiting, *traffic.no_way]:
        w = traffic.vehicles[vid]
        if vid in vehicles and w.wait_for is not None:
            held_up.setdefault(vid, []).append([r1(w.wait_by_since), None, list(w.wait_for), w.wait_by])
    for vid, spans in held_up.items():
        vehicles[vid]["waits"] = spans
    when = lambda times, key: None if times.get(key) is None else r1(times[key])
    booked = lambda k: k.times.get("booked", k.appointment_s)          # one that came late lost its appointment at the gate
    trucks = [{"id": k.id, "booked": None if booked(k) is None else r1(booked(k)), "late": "booked" in k.times, "picks": k.picks, "drops": k.drops.id if k.drops else None,
               "arrived": when(k.times, "arrived"), "in": when(k.times, "entered"), "gone": when(k.times, "gone"), "turned_away": k.state == "turned_away"}
              for k in sc.trucks if "arrived" in k.times]     # a truck is drawn from the moment it is let in: before that it waits before the gate
    for j in sorted((j for j in fleet.jobs if j.agv and "assigned" in j.times), key=lambda j: j.times["assigned"]):
        done = j.times.get("done")                           # what each AGV was sent for, in time order: [from, until, what for, container]
        vehicles[j.agv].setdefault("jobs", []).append([r1(j.times["assigned"]), None if done is None else r1(done), j.purpose or j.kind, j.container])
    keep = ("end_min", "containers", "discharged", "loaded_on_ships", "left_by_truck", "ships_served", "trucks", "trucks_served",
            "trucks_turned_away", "rehandles", "agv_jobs", "agv_km", "agv_kwh", "quay_crane_wait_min", "stalls",
            "ship_wait_min", "ship_turnaround_min", "truck_turnaround_min", "truck_gate_wait_min", "agv_blocked_min", "moves_per_hour")
    span = lambda d: {"t0": r1(d["t0"]), "t1": None if d["t1"] is None else r1(d["t1"])}
    disruptions = sorted(
        [{"kind": "agv", "id": b["vehicle"], "at": list(b["at"]), **span(b)} for b in traffic.breakdowns]
        + [{"kind": "road", "cells": [list(c) for c in cl["cells"]], **span(cl)} for cl in traffic.closures]
        + [{"kind": "crane", "id": o["crane"], **span(o)} for o in terminal.outages]
        + [{"kind": "gate", "lane": g["lane"], "cell": list(terminal.gates_in[g["lane"] - 1]), **span(g)} for g in terminal.gate.closures],
        key=lambda d: (d["t0"], d["kind"]))
    return {
        "name": sc.name, "source": "sim/terminal.py", "t_end": r1(end), "policies": dict(sc.policies), "agvs": len(fleet.agvs),
        "numbers": sc.params.name, "seed": sc.params.seed, "series": series,
        "why": [[r1(r.t), r.module, list(r.about), r.text] for r in terminal.log.reasons],
        "ships": [{"id": v.id, "berth": v.berth, "draft_m": v.draft_m, "discharge": len(v.discharge), "load": len(v.load),
                   **{k: when(v.times, k) for k in ("arrived", "cleared", "berthed", "work_done", "cast_off", "gone")}}
                  for v in sc.vessels],
        "trucks": trucks,
        "summary": {**{k: numbers[k] for k in keep}, "unfinished": len(numbers["unfinished"])},
        "settings": settings or {}, "problems": list(problems or []), "disruptions": disruptions,
        "vessels": [{"id": v["id"], "berth": v["berth"], "t_arrive": r1(v["t_arrive"]), "t_cleared": r1(v["t_cleared"]), "t": r1(v["t"]),
                     "t_depart": None if v["t_depart"] is None else r1(v["t_depart"])}
                    for v in sorted((v for v in trace if v["kind"] == "vessel"), key=lambda v: (v["t"], v["id"]))],
        "vehicles": list(vehicles.values()), "containers": containers, "zones": zones, "managed_slots": managed,
        "log": [[r1(e.t), e.text, e.module] for e in terminal.log.entries if not e.data.get("quiet")],
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


def build_terminal(layout, scenario=None, heat: dict | None = None) -> dict:
    """The integrated terminal on the demo morning (or on another workload). A run that fails its own
    checks is not written."""
    from sim import scenario as scenarios
    from sim.metrics import check
    from sim.terminal import Terminal

    terminal = Terminal(layout, scenario or scenarios.demo()).run()
    problems = check(terminal)
    if problems:
        raise ValueError(f"the run fails {len(problems)} checks, the first: {problems[0]}")
    return {"layout": layout.name, "replay": replay_from_run(terminal, layout), "waiting": heat or waiting(layout)}


def what_if(layout, settings: dict) -> dict:
    """One what-if run as a replay: the demo morning with these settings (sim.control). The run's own
    checks are reported in the replay, not raised: a road closed for good may leave a vehicle waiting,
    and that is the answer to the question."""
    from sim import control
    from sim.metrics import check

    terminal = control.run(settings, layout)
    return replay_from_run(terminal, layout, settings=settings, problems=check(terminal))


def build_variants(layout) -> dict:
    """The recorded what-if runs. The plan itself is terminal-replay.json, so it carries no replay here."""
    from sim import control

    out = []
    for v in control.VARIANTS:
        replay = what_if(layout, v["settings"]) if v["settings"] else None
        if replay and replay["problems"]:
            raise ValueError(f"what-if {v['id']!r} fails its checks: {replay['problems'][0]}")
        out.append({**v, "replay": replay})
    return {"layout": layout.name, "variants": out}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    data = build(layout)
    for out, d in ((OUT, data), (TERMINAL_OUT, build_terminal(layout, heat=data["waiting"]))):
        out.write_text(json.dumps(d, separators=(",", ":")) + "\n")
        rp = d["replay"]
        print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KB): {len(rp['vehicles'])} vehicles, "
              f"{len(rp['containers'])} containers, {rp['t_end'] / 60:.1f} min; waiting map of {len(d['waiting']['cells'])} cells")
    variants = build_variants(layout)
    VARIANTS_OUT.write_text(json.dumps(variants, separators=(",", ":")) + "\n")
    print(f"wrote {VARIANTS_OUT.relative_to(ROOT)} ({VARIANTS_OUT.stat().st_size / 1024:.0f} KB): {len(variants['variants'])} what-if runs, "
          + ", ".join(f"{v['id']} {v['replay']['t_end'] / 60:.0f} min" for v in variants["variants"] if v["replay"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
