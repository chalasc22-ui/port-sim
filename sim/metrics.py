"""What a finished run adds up to, and whether it hangs together.

summary(terminal)   the measures Dr. Wang listed: completed jobs, throughput, job completion time, waiting
                    time and unfinished work, plus each module's own measure (rehandles, truck turnaround,
                    time at anchor, charge used).
check(terminal)     the run checked against itself, from its records only: a list of problems, empty if
                    none. It does not prove the model right; it shows the bookkeeping never broke:
                    every container in exactly one place at a time and moved only by a crane, no two
                    vehicles in one cell, no crane doing two lifts at once, no buffer or stack over its
                    limit, food and hazmat apart.
"""

from __future__ import annotations

import math
from statistics import mean


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 1) if values else None


def summary(t) -> dict:
    sc, hours = t.scenario, max(t.end_s, 1.0) / 3600.0
    boxes = list(t.containers.values())
    gone = [c for c in boxes if c.place[0] == "gone"]
    discharged = [c for v in sc.vessels for c in v.discharge if c.place[0] != "ship"]
    calling = {v.id for v in sc.vessels}                       # an export for a ship of a later shift, or an import whose truck
    stayed_away = {k.id for k in sc.trucks if k.no_show}       # never came, was not this run's to finish
    should_leave = [c for c in boxes if (c.vessel in calling if c.flow == "export" else bool(c.truck) and c.truck not in stayed_away)]
    trucks = sc.trucks
    served = [k for k in trucks if k.state == "gone"]
    jobs = [j for j in t.fleet.jobs if "done" in j.times]
    agvs = list(t.fleet.agvs.values())
    for v in agvs:
        t.traffic.settle(v)
    ships = [v for v in sc.vessels if "arrived" in v.times]
    worked = [v for v in ships if "work_done" in v.times]            # quay moves and crane-hours alongside, of the ships finished
    quay_moves = sum(len(v.discharge) + len(v.load) - sum(1 for cid in v.load if cid in t.missed) for v in worked)
    crane_hours = sum((v.times["work_done"] - v.times["berthed"]) / 3600.0 * len(t.ships.cranes[v.berth]) for v in worked)
    drive_s = sum(leg["t1"] - leg["t0"] for leg in t.traffic.legs if leg["who"] in t.fleet.agvs)
    parked = [t.fleet.parked_time(v) for v in agvs]
    busy_h = len(agvs) * hours - sum(p for p, _ in parked) / 3600.0
    standing_kw = t.traffic.energy.standing_kw if t.traffic.energy else 0.0
    to_yard = [j for j in jobs if j.kind in ("discharge", "store") and "at_drop" in j.times]
    yard_work = sum(t.crane_busy_s[k] for k in t.yard_queue)
    yard_travel = sum(t.crane_travel_s[k] for k in t.yard_queue)
    return {
        "end_min": round(t.end_s / 60.0, 1),
        "containers": len(boxes),
        "discharged": len(discharged),
        "loaded_on_ships": sum(1 for c in gone if c.place[1] == "ship"),
        "left_by_truck": sum(1 for c in gone if c.place[1] == "truck"),
        "moves_per_hour": round((len(discharged) + len(gone)) / hours, 1),
        "unfinished": sorted(c.id for c in should_leave if c.place[0] != "gone"),
        "missed_ship": sorted(t.missed),
        "ships_served": sum(1 for v in ships if "cast_off" in v.times),
        "ship_wait_min": _mean((v.times["cleared"] - v.times["arrived"]) / 60.0 for v in ships if "cleared" in v.times),
        "ship_wait_max_min": max((round((v.times["cleared"] - v.times["arrived"]) / 60.0, 1) for v in ships if "cleared" in v.times), default=None),
        "ship_turnaround_min": _mean((v.times["cast_off"] - v.times["arrived"]) / 60.0 for v in ships if "cast_off" in v.times),
        "ships_waiting": sorted(v.id for v in ships if "cleared" not in v.times),
        "trucks": len(trucks),
        "trucks_served": len(served),
        "trucks_turned_away": len(t.gate.turned_away),
        "truck_turnaround_min": _mean((k.times["gone"] - k.times["arrived"]) / 60.0 for k in served),
        "truck_gate_wait_min": _mean((k.times["entered"] - k.times["arrived"]) / 60.0 for k in served),
        "truck_inside_min": _mean((k.times["gone"] - k.times["entered"]) / 60.0 for k in served),
        "trouble_desk_visits": sum(len(k.times.get("desk", [])) for k in trucks),
        "tolls_usd": t.gate.tolls_usd,
        "rehandles": t.yard.rehandles,
        "yard_lifts_out": t.yard.taken,
        "yard_occupancy": round(t.yard.occupancy(), 3),
        "agv_jobs": len(jobs),
        "agv_job_min": _mean((j.times["done"] - j.released) / 60.0 for j in jobs),
        "agv_wait_for_agv_min": _mean((j.times["at_pickup"] - j.released) / 60.0 for j in jobs if "at_pickup" in j.times),
        "agv_jobs_open": len(t.fleet.jobs) - len(jobs),
        "agv_km": round(sum(v.moves for v in agvs) * t.layout.cell_size_m / 1000.0, 1),
        "agv_loaded_share": round(sum(v.loaded_moves for v in agvs) / max(1, sum(v.moves for v in agvs)), 2),
        "agv_kwh": round(sum(v.used_kwh for v in agvs), 1),
        "agv_blocked_min": round(sum(v.waited_s for v in agvs) / 60.0, 1),
        "quay_crane_wait_min": round(sum(q.waited_s for q in t.quay.values()) / 60.0, 1),
        "moves_refused": t.traffic.refused,
        "stalls": len(t.traffic.stalls),
        "disruptions": len(t.traffic.breakdowns) + len(t.traffic.closures) + len(t.outages) + len(t.gate.closures),
        "stuck_at_end": [w["vehicle"] for w in t.traffic.still_waiting()],
        # measures for the validation (docs/validation.md)
        "quay_moves": quay_moves,
        "quay_moves_per_crane_hour": round(quay_moves / crane_hours, 1) if crane_hours else None,
        "agv_speed_m_s": round(sum(v.moves for v in agvs) * t.layout.cell_size_m / drive_s, 2) if drive_s else None,
        "agv_kwh_per_operating_hour": round((sum(v.used_kwh for v in agvs) - standing_kw * sum(s for _, s in parked) / 3600.0) / busy_h, 1) if busy_h > 0 else None,
        "agv_parked_share": round(sum(p for p, _ in parked) / (len(agvs) * max(t.end_s, 1.0)), 2) if agvs else None,
        "agv_wait_at_yard_share": round(sum(j.times["done"] - j.times["at_drop"] for j in to_yard) / sum(j.times["done"] - j.times["assigned"] for j in to_yard), 2) if to_yard else None,
        "yard_crane_travel_share": round(yard_travel / (yard_work + yard_travel), 2) if yard_work else None,
        "truck_no_shows": sum(1 for k in trucks if k.no_show),
        "imports_sent_back": len(t.sent_back),
        "charging_stays": sum(1 for stay in t.fleet.charges if stay[1] > 0),
    }


def timeline(t, step_s: float | None = None) -> dict:
    """What the terminal was doing over the run, in equal steps of time: the curves of the 3D model's
    dashboard. Everything is worked out from the run's own records after it has finished.

    A total ("discharged", "loaded", "trucks_out", "rehandles") is how many had happened by the end of each
    step. A level (ships at anchor, trucks waiting, what the AGVs are doing, containers in the yard) is the
    average over the step, so the six AGV levels of a step add up to the fleet. A crane's value is the
    share of the step it was lifting or travelling."""
    end = max(t.end_s, 1.0)
    step = step_s or next((s for s in (30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0) if end / s <= 200), 3600.0)
    n = max(1, math.ceil(end / step - 1e-9))
    last = end - (n - 1) * step                                # the last step may be a short one

    def total(times) -> list:
        counts = [0] * n
        for x in times:
            counts[min(n - 1, int(x // step))] += 1
        for i in range(1, n):
            counts[i] += counts[i - 1]
        return counts

    def level(spans) -> list:
        seconds = [0.0] * n
        for a, b in spans:
            a, b = max(0.0, a), end if b is None else min(b, end)
            if b <= a:
                continue
            i, j = int(a // step), min(n - 1, int((b - 1e-9) // step))
            if i == j:
                seconds[i] += b - a
                continue
            seconds[i] += (i + 1) * step - a
            for k in range(i + 1, j):
                seconds[k] += step
            seconds[j] += b - j * step
        return [round(x / (step if i < n - 1 else last), 2) for i, x in enumerate(seconds)]

    sc, fleet, traffic = t.scenario, t.fleet, t.traffic
    lifts = [r for r in t.trace if r["kind"] == "lift"]
    trucks = [k for k in sc.trucks if "arrived" in k.times]
    ships = [v for v in sc.vessels if "arrived" in v.times]
    held = {}                                                  # what kind of place each container stood at, and when
    for c in t.containers.values():
        for (t0, place), (t1, _) in zip(c.history, [*c.history[1:], (end, None)]):
            held.setdefault(place[0], []).append((t0, t1))

    agvs = set(fleet.agvs)
    jobs = [j for j in fleet.jobs if j.agv]
    waits = [(a, b) for vid, a, b, _, _ in traffic.waits if vid in agvs]
    waits += [(traffic.vehicles[vid].wait_by_since, end) for vid in [*traffic.waiting, *traffic.no_way] if vid in agvs]
    waits += [(b["t0"], min(b["t1"], end)) for b in traffic.breakdowns if b["vehicle"] in agvs]
    state = {
        "loaded": level((leg["t0"], leg["t1"]) for leg in traffic.legs if leg["who"] in agvs and leg["loaded"]),
        "empty": level((leg["t0"], leg["t1"]) for leg in traffic.legs if leg["who"] in agvs and not leg["loaded"]),
        "crane": level([(j.times["at_pickup"], j.times.get("loaded", j.times.get("done"))) for j in jobs if "at_pickup" in j.times]
                       + [(j.times["at_drop"], j.times.get("done")) for j in jobs if "at_drop" in j.times]),
        "held": level(waits),
        "charger": level((a, b) for _, a, b in fleet.charges),
    }
    state["idle"] = [round(max(0.0, len(agvs) - sum(state[k][i] for k in state)), 2) for i in range(n)]

    busy: dict[str, list] = {crane: [] for crane in t.busy}
    for r in t.trace:
        if r["kind"] in ("lift", "gantry"):
            busy[r["by"]].append((r["t0"], r["t1"]))
    return {
        "step_s": step, "n": n, "agvs": len(agvs), "yard_capacity": t.yard.capacity(),
        "discharged": total(r["t1"] for r in lifts if r["src"][0] == "ship"),
        "loaded": total(r["t1"] for r in lifts if r["dst"][0] == "ship"),
        "trucks_out": total(k.times["gone"] for k in trucks if k.state == "gone"),
        "rehandles": total(r["t1"] for r in lifts if r["src"][0] == "slot" and r["dst"][0] == "slot"),
        "ships_anchor": level((v.times["arrived"], v.times.get("cleared")) for v in ships),
        "ships_berth": level((v.times["berthed"], v.times.get("cast_off")) for v in ships if "berthed" in v.times),
        "trucks_gate": level((k.times["arrived"], k.times.get("entered", k.times.get("gone"))) for k in trucks),
        "trucks_inside": level((k.times["entered"], k.times.get("gone")) for k in trucks if "entered" in k.times),
        "jobs_waiting": level((j.released, j.times.get("assigned")) for j in fleet.jobs),
        "quay_waiting": level([(a, b) for _, a, b in t.quay_waits] + [(q.waiting_since, end) for q in t.quay.values() if q.waiting_since is not None]),
        "buffered": level(held.get("buffer", [])),
        "exchange": level(held.get("exchange", [])),
        "yard": level(held.get("yard", [])),
        "agv": state,
        "cranes": {crane: level(spans) for crane, spans in sorted(busy.items())},
    }


def cell_conflicts(traffic, end_s: float) -> list[str]:
    """Rebuild who held which cell when from the admitted moves, and report any cell two vehicles shared."""
    held: dict[tuple, list] = {}
    last = dict(traffic.spawned)
    for vid, t0, t1, cells in traffic.segments:
        since, origin = last[vid]
        if origin != cells[0]:
            return [f"{vid} left {cells[0]} at {t0:.0f} s but stood on {origin}"]
        held.setdefault(cells[0], []).append((since, t1, vid))          # it keeps the cell it leaves until it arrives
        for c in cells[1:-1]:
            held.setdefault(c, []).append((t0, t1, vid))
        last[vid] = (t0, cells[-1])                                     # the cell ahead is its own from the start of the move
    for vid, (since, cell) in last.items():
        held.setdefault(cell, []).append((since, traffic.removed.get(vid, end_s + 1.0), vid))
    out = []
    for cell, spans in held.items():
        spans.sort()
        for (a0, a1, va), (b0, _, vb) in zip(spans, spans[1:]):
            if va != vb and b0 < a1 - 1e-6:
                out.append(f"{va} and {vb} share cell {cell} at {b0:.0f} s")
    return out


def check(t) -> list[str]:
    problems = list(t.problems)
    problems += cell_conflicts(t.traffic, t.end_s)

    by_crane: dict[str, list] = {}
    for r in t.trace:
        if r["kind"] == "lift":
            by_crane.setdefault(r["by"], []).append((r["t0"], r["t1"], r["what"]))
    for crane, lifts in by_crane.items():
        lifts.sort()
        for (_, a1, a), (b0, _, b) in zip(lifts, lifts[1:]):
            if b0 < a1 - 1e-6:
                problems.append(f"{crane} lifts {a} and {b} at once at {b0:.0f} s")

    resting: dict[tuple, list] = {}                      # place -> (from, to, container), for places that hold one or a few
    for c in t.containers.values():
        h = c.history
        for (t0, a), (t1, b) in zip(h, h[1:]):
            if t1 < t0 - 1e-6:
                problems.append(f"{c.id}: its record goes back in time at {t1:.0f} s")
            moved_by_crane = a[0] == "crane" or b[0] == "crane"
            arrives_or_leaves = b[0] == "gone" or (a[0] == "outside")
            if a != b and not moved_by_crane and not arrives_or_leaves:
                problems.append(f"{c.id} went from {a} to {b} at {t1:.0f} s without a crane")
        for (t0, a), (t1, _) in zip(h, [*h[1:], (t.end_s + 1.0, None)]):
            if a[0] in ("vehicle", "buffer", "yard", "exchange"):
                resting.setdefault(a, []).append((t0, t1, c.id))
        if c.place[0] == "crane":
            problems.append(f"{c.id} ends the run hanging from {c.place[1]}")
    for place, spans in resting.items():
        limit = t.quay[place[1]].cap if place[0] == "buffer" else 1
        spans.sort()
        for i, (t0, _, cid) in enumerate(spans):
            together = [x for x in spans[:i] if x[1] > t0 + 1e-6]
            if len(together) + 1 > limit:
                problems.append(f"{place} holds {len(together) + 1} containers at {t0:.0f} s ({cid} and {together[0][2]}); its limit is {limit}")

    for stack, held in t.yard.stacks.items():
        bay = next(b for b, st in t.yard.bays.items() if stack in st)
        if len(held) > t.yard.max_tier[bay]:
            problems.append(f"stack {stack} is {len(held)} high")
        for tier, cid in enumerate(held, start=1):
            if t.containers[cid].place != ("yard", *stack, tier):
                problems.append(f"{cid}: the yard has it at {(*stack, tier)}, its record says {t.containers[cid].place}")
    problems += [f"{cid} stands in a zone for other cargo, bay {bay}" for bay, cid in t.yard.mixed()]
    in_yard = sum(1 for c in t.containers.values() if c.place[0] == "yard")
    if in_yard != t.yard.count():
        problems.append(f"{in_yard} containers say they are in the yard, the yard counts {t.yard.count()}")
    problems += [f"{w['vehicle']} still waits for cell {w['cell']} (" + ("behind a closed road" if w["blocked_by"] == "a closed road"
                 else f"held by {w['blocked_by']}") + ") when the run ends" for w in t.traffic.still_waiting()]
    return problems
