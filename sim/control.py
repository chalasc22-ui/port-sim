"""What-if runs: one plain dict of settings in, a finished run out.

The control room of the 3D model (tools/control_room.py) and the recorded what-if runs
(tools/build_viewer_replay.py) both go through here, so a run started by a click and a run started in
code are the same thing, and either can be repeated from its settings alone.

    from sim import control
    terminal = control.run({"agvs": 8, "policies": {"gate": "fifo"}}, layout)

Settings change the demo morning (sim.scenario.demo). All of them are optional; {} is the morning as planned.

    "policies"             {"ship": ..., "stacking": ..., "dispatch": ..., "gate": ...}, any of the four
    "agvs"                 fleet size, 2 to 12 (an idle AGV parks at a charger and there are 12)
    "tide_high_water_min"  when the tide peaks, in minutes from the start
    "vessels"              {"BRAVO": {"arrives_min": 25, "draft_m": 10.5, "radio_out_min": [20, 40]}}
    "trucks"               {"T06": {"arrives_min": 80}, "T08": {"no_show": true}}
    "walk_ins"             {"at_min": 35, "trucks": 6}: a rush of trucks without appointments, each for a
                           container in the yard that nobody has booked yet
    "events"               disruptions, as in sim.scenario.EVENTS:
                           {"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"}
                           {"kind": "road_closed", "t": 1200, "seconds": 900, "cells": [[57, 28]]}
                           {"kind": "crane_down", "t": 700, "seconds": 900, "crane": "QC2"}
                           {"kind": "gate_lane_closed", "t": 1200, "seconds": 1800, "lane": 1}
    "numbers"              "reference" (PortSim's, every time fixed) or "calibrated" (published numbers, docs/data-sheet.md:
                           the morning's tide then has NOAA's range; its ten-minute passage stays)
    "seed"                 with calibrated numbers: crane and gate times vary around their means, from this seed

Another workload instead of the morning:

    "workload"             "shift": a shift at real scale (sim.scenario.shift), always on the calibrated numbers. It takes
                           "seed" (which shift), "agvs" (12 to 40), "trucks_per_hour" (4 to 20), "policies" and "events".

A setting that makes no sense (an AGV that does not exist, a negative time) raises ValueError with a
sentence a person can read, before anything runs.
"""

from __future__ import annotations

import copy

from sim import params as numbers
from sim import scenario as scenarios
from sim.kernel import Truck
from sim.ship_arrival import Tide
from sim.terminal import Terminal

MIN = 60.0
AGVS = (2, 12)
LAST_MIN = 240.0            # nothing may be set later than four hours into the morning
POLICY_NAMES = {            # how the 3D model names each policy
    "fcfs": "First come, first served", "earliest_eligible": "Earliest eligible first",
    "shortest_stack": "Shortest stack", "departure_time": "By departure time",
    "nearest_feasible": "Nearest with charge", "longest_idle": "Idle the longest", "balanced": "Distance, idle, charge",
    "nearest_job": "Nearest job first", "learned": "Learned (Q-learning)",
    "fifo": "First in, first out", "appointment": "Appointments first",
}
KEYS = {"policies", "agvs", "tide_high_water_min", "vessels", "trucks", "walk_ins", "events", "numbers", "seed", "workload", "trucks_per_hour"}
SHIFT_KEYS = {"workload", "seed", "agvs", "trucks_per_hour", "policies", "events"}
SHIFT_AGVS, SHIFT_TRUCKS, SHIFT_LAST_MIN = (12, 40), (4, 20), 900.0

# The recorded what-if runs the shared 3D page offers: one question per module, each one change from the plan.
VARIANTS = [
    {"id": "plan", "module": "The plan", "title": "The morning as planned",
     "about": "Three ships, 12 AGVs and 15 trucks, every module on its default policy.", "settings": {}},
    {"id": "ship-fcfs", "module": "Ship arrival", "title": "Ships strictly first come, first served",
     "about": "BRAVO is waiting for the tide. Under this rule CHARLIE may not pass it, so berth B2 stands empty while both wait.",
     "settings": {"policies": {"ship": "fcfs"}}},
    {"id": "ship-radio", "module": "Ship arrival", "title": "CHARLIE loses radio contact",
     "about": "No contact from 12:00. It reaches the anchorage at 15:00 with a berth free, but is not cleared until the backup link reaches it at 22:00.",
     "settings": {"vessels": {"CHARLIE": {"radio_out_min": [12, 45]}}}},
    {"id": "agv-fleet-8", "module": "AGVs", "title": "Only 8 AGVs",
     "about": "A third of the fleet is missing. Watch the quay cranes fill their buffers and stop.", "settings": {"agvs": 8}},
    {"id": "agv-fleet-6", "module": "AGVs", "title": "Only 6 AGVs",
     "about": "Half the fleet. The cranes stand waiting for AGVs, and the morning takes almost twice as long.", "settings": {"agvs": 6}},
    {"id": "agv-breakdown", "module": "AGVs", "title": "An AGV breaks down on the main road",
     "about": "AGV5 stops on the main road from the quay to the yard at 31:40, for ten minutes. The AGVs behind it queue or take a longer way round.",
     "settings": {"events": [{"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"}]}},
    {"id": "dispatch-learned", "module": "AGVs", "title": "AGV jobs given out by a learned rule",
     "about": "Which AGV takes which job is decided by a rule learned by Q-learning in the simulator (docs/learned-dispatch.md). It chooses the job as well as the AGV. "
              "It learned on shifts with 30 AGVs; this small morning ends 6 minutes later under it than as planned. Click an AGV to read what the rule expected and which pair it passed over.",
     "settings": {"policies": {"dispatch": "learned"}}},
    {"id": "road-closed", "module": "Layout", "title": "The main road is closed for 15 minutes",
     "about": "Cell (57, 28) is shut from 20:00. More than half of the trips from the quay to the yard cross it: AGVs with no other way wait, and are reported.",
     "settings": {"events": [{"kind": "road_closed", "t": 1200, "seconds": 900, "cells": [[57, 28]]}]}},
    {"id": "stack-shortest", "module": "Stacking", "title": "Stack on the shortest stack",
     "about": "Containers go to the lowest stack whatever their departure time. With the yard this empty each gets a stack of its own and nothing is rehandled: the cleverer rule pays off only in a full yard.",
     "settings": {"policies": {"stacking": "shortest_stack"}}},
    {"id": "gate-fifo", "module": "Gate", "title": "Gate first in, first out",
     "about": "No appointments: a truck's container is fetched only once the truck has been checked, so trucks wait longer.",
     "settings": {"policies": {"gate": "fifo"}}},
    {"id": "gate-rush", "module": "Gate", "title": "A rush of six trucks without appointments",
     "about": "Six walk-in trucks arrive at 35:00. Booked trucks keep their turn; a walk-in is passed over for 20 minutes at most.",
     "settings": {"walk_ins": {"at_min": 35, "trucks": 6}}},
    {"id": "crane-down", "module": "Scheduler", "title": "Quay crane QC2 out of service",
     "about": "QC2 stops for 15 minutes at 11:40 with four of ALPHA's containers still on board. ALPHA sails 18 minutes late.",
     "settings": {"events": [{"kind": "crane_down", "t": 700, "seconds": 900, "crane": "QC2"}]}},
    {"id": "numbers-calibrated", "module": "Published numbers", "title": "The morning on published numbers",
     "about": "The same morning on the numbers of docs/data-sheet.md: a quay crane makes 35 moves an hour, not 60; an AGV keeps its speed through a junction; "
              "the tide has NOAA's range of 1.0 m, not 1.8, so BRAVO does not wait for it and takes the second berth at once; CHARLIE then waits for a berth.",
     "settings": {"numbers": "calibrated"}},
    {"id": "numbers-varied", "module": "Published numbers", "title": "The same, with times that vary",
     "about": "Every crane move and gate check is drawn around its mean (seed 1). Another seed gives another morning, which is why the experiment runs every setting 30 times.",
     "settings": {"numbers": "calibrated", "seed": 1}},
    {"id": "shift", "module": "Real scale", "title": "A shift at real scale",
     "about": "Four ships with 716 moves, 80 trucks, 30 AGVs sharing the 12 charging stations, and 2,163 containers on the terminal, on the published numbers. "
              "It lasts 14 hours of terminal time, so it plays 600 times faster.",
     "settings": {"workload": "shift", "seed": 1}},
]


def _number(value, what: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{what} must be a number from {low:g} to {high:g}, not {value!r}")
    return float(value)


def scenario_for(settings: dict | None = None) -> scenarios.Scenario:
    """The demo morning with these settings applied. Raises ValueError for a setting that makes no sense."""
    s = settings or {}
    if not isinstance(s, dict):
        raise ValueError("the settings must be an object")
    for key in s:
        if key not in KEYS:
            raise ValueError(f"unknown setting {key!r}: choose from {sorted(KEYS)}")
    policies = s.get("policies") or {}
    if not isinstance(policies, dict) or any(part not in scenarios.POLICIES for part in policies):
        raise ValueError(f"policies are set for {sorted(scenarios.POLICIES)}, not {sorted(policies) if isinstance(policies, dict) else policies!r}")
    seed = s.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 999_999):
        raise ValueError(f"the seed must be a whole number from 0 to 999999, not {seed!r}")
    workload = s.get("workload", "morning")
    if workload not in ("morning", "shift"):
        raise ValueError(f"the workload is 'morning' or 'shift', not {workload!r}")
    if workload == "shift":
        return _shift(s, policies, 1 if seed is None else seed)
    if "trucks_per_hour" in s:
        raise ValueError("trucks_per_hour is a setting of the shift; the morning's trucks are set one by one")
    sc = scenarios.demo().using(**policies)
    if "agvs" in s:
        sc = sc.using(agvs=_whole(s["agvs"], "the fleet", "AGVs", AGVS))
    which = s.get("numbers", "reference")
    if which not in numbers.SETS:
        raise ValueError(f"the numbers are {' or '.join(map(repr, numbers.SETS))}, not {which!r}")
    if which == "reference" and seed is not None:
        raise ValueError("the reference numbers do not vary: a seed needs \"numbers\": \"calibrated\"")
    if which == "calibrated":                    # the published numbers, and the tide with NOAA's range at the same hour
        sc = sc.using(params=numbers.CALIBRATED.seeded(seed),
                      tide=Tide(numbers.TIDE_MEAN_DEPTH_M, numbers.TIDE_AMPLITUDE_M, numbers.TIDE_PERIOD_S, sc.tide.high_water_s))
    if "tide_high_water_min" in s:
        t = sc.tide
        sc = sc.using(tide=Tide(t.mean_depth_m, t.amplitude_m, t.period_s, _number(s["tide_high_water_min"], "high water", -720, 720) * MIN))

    ships = {v.id: v for v in sc.vessels}
    for vid, change in (s.get("vessels") or {}).items():
        if vid not in ships:
            raise ValueError(f"there is no ship called {vid!r}: the ships are {', '.join(ships)}")
        v = ships[vid]
        for key, value in change.items():
            if key == "arrives_min":
                v.arrives_s = _number(value, f"{vid}'s arrival", 0, LAST_MIN) * MIN
            elif key == "draft_m":
                v.draft_m = _number(value, f"{vid}'s draft", 6, 13.4)
            elif key == "radio_out_min":
                if value is None:
                    v.comms_out = None
                    continue
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValueError(f"{vid}: radio_out_min is [from, to] in minutes, not {value!r}")
                a, b = (_number(x, f"{vid}'s radio outage", 0, LAST_MIN) for x in value)
                if b <= a:
                    raise ValueError(f"{vid}: the radio outage must end after it starts")
                v.comms_out = (a * MIN, b * MIN)
            else:
                raise ValueError(f"{vid}: a ship has arrives_min, draft_m and radio_out_min, not {key!r}")
    sc.vessels.sort(key=lambda v: v.arrives_s)

    trucks = {t.id: t for t in sc.trucks}
    for tid, change in (s.get("trucks") or {}).items():
        if tid not in trucks:
            raise ValueError(f"there is no truck called {tid!r}")
        for key, value in change.items():
            if key == "arrives_min":
                trucks[tid].arrives_s = _number(value, f"{tid}'s arrival", 0, LAST_MIN) * MIN
            elif key == "no_show":
                if value:
                    sc.trucks.remove(trucks[tid])
            else:
                raise ValueError(f"{tid}: a truck has arrives_min and no_show, not {key!r}")

    rush = s.get("walk_ins")
    if rush:
        free = [c for c in sc.inventory if c.truck is None and c.vessel is None]
        n = rush.get("trucks")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(free):
            raise ValueError(f"a rush is 1 to {len(free)} trucks (one for each container nobody has booked), not {n!r}")
        at = _number(rush.get("at_min"), "the rush", 0, LAST_MIN) * MIN
        for k, c in enumerate(free[:n], start=1):
            c.truck = f"W{k:02d}"
            sc.trucks.append(Truck(c.truck, at + 20.0 * (k - 1), plate=f"FL-{7000 + k}", appointment_s=None, picks=c.id))

    events = s.get("events") or []
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise ValueError("events must be a list of objects")
    for e in events:
        if isinstance(e.get("t"), (int, float)) and e["t"] > LAST_MIN * MIN:
            raise ValueError(f"{e.get('kind')}: nothing may be set later than {LAST_MIN:g} minutes into the morning")
    sc = sc.using(events=copy.deepcopy(sorted(events, key=lambda e: (e.get("t", 0), e.get("kind", "")))))
    return sc


def _whole(n, what: str, unit: str, limits: tuple) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or not limits[0] <= n <= limits[1]:
        raise ValueError(f"{what} must be a whole number of {unit} from {limits[0]} to {limits[1]}, not {n!r}")
    return n


def _shift(s: dict, policies: dict, seed: int) -> scenarios.Scenario:
    """A shift at real scale with these settings."""
    extra = sorted(set(s) - SHIFT_KEYS)
    if extra:
        raise ValueError(f"a shift takes {', '.join(sorted(SHIFT_KEYS - {'workload'}))}, not {extra[0]!r}")
    made = {}
    if "agvs" in s:
        made["agvs"] = _whole(s["agvs"], "the fleet", "AGVs", SHIFT_AGVS)
    if "trucks_per_hour" in s:
        made["trucks_per_hour"] = _number(s["trucks_per_hour"], "trucks an hour", *SHIFT_TRUCKS)
    sc = scenarios.shift(seed, **made).using(**policies)
    events = s.get("events") or []
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise ValueError("events must be a list of objects")
    for e in events:
        if isinstance(e.get("t"), (int, float)) and e["t"] > SHIFT_LAST_MIN * MIN:
            raise ValueError(f"{e.get('kind')}: nothing may be set later than {SHIFT_LAST_MIN:g} minutes into the shift")
    return sc.using(events=sorted([*sc.events, *copy.deepcopy(events)], key=lambda e: (e.get("t", 0), e.get("kind", ""))))


def run(settings: dict | None, layout) -> Terminal:
    """Run the morning with these settings to the end. Terminal checks the events against the terminal."""
    return Terminal(layout, scenario_for(settings)).run()


def options(layout) -> dict:
    """What the control room may change, for the 3D model to build its controls from."""
    sc = scenarios.demo()
    free = [c for c in sc.inventory if c.truck is None and c.vessel is None]
    return {
        "policies": {part: [{"id": name, "name": POLICY_NAMES[name]} for name in names] for part, names in scenarios.POLICIES.items()},
        "defaults": {"policies": dict(sc.policies), "agvs": sc.agvs, "tide_high_water_min": sc.tide.high_water_s / MIN},
        "agvs": list(AGVS),
        "vessels": [{"id": v.id, "arrives_min": v.arrives_s / MIN, "draft_m": v.draft_m} for v in sc.vessels],
        "trucks": [{"id": t.id, "arrives_min": t.arrives_s / MIN} for t in sc.trucks],
        "walk_ins": len(free),
        "cranes": sorted([*layout.quay_cranes, *layout.yard_cranes]),
        "gate_lanes": len(layout.gate_cells("in")),
        "last_min": LAST_MIN,
        "numbers": sorted(numbers.SETS, reverse=True),
        "shift": {"agvs": list(SHIFT_AGVS), "trucks_per_hour": list(SHIFT_TRUCKS)},
    }
