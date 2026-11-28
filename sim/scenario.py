"""Workloads for the integrated simulation: what arrives, when, and with which policies it is handled.

    demo()                  one morning at Dames Point, written by hand so every module has something to show
    generate(seed, ...)     a small random workload from a seed, for experiments
    shift(seed, ...)        a shift at real scale on the calibrated numbers: four ships with hundreds of moves,
                            80 trucks, 30 AGVs, a yard two thirds full, no-shows and breakdowns

A Scenario is plain data. Terminal copies it before a run, so the same Scenario can be run again with
another policy and both runs see exactly the same ships, trucks and containers (Dr. Wang: "identical
saved workloads and random seeds for both policies"). save() and load() keep one as a JSON file.
"""

from __future__ import annotations

import dataclasses
import json
import random
from dataclasses import dataclass, field

from sim import params as numbers
from sim.kernel import Container, Truck, Vessel
from sim.params import REFERENCE, Params
from sim.ship_arrival import Tide
from sim.stacking import Zone

POLICIES = {
    "ship": ("fcfs", "earliest_eligible"),
    "stacking": ("shortest_stack", "departure_time"),
    "dispatch": ("nearest_feasible", "longest_idle", "balanced", "nearest_job", "learned"),      # the last two choose the job too: sim/dispatch_learned.py
    "gate": ("fifo", "appointment"),
}
DEFAULT_POLICIES = {"ship": "earliest_eligible", "stacking": "departure_time", "dispatch": "nearest_feasible", "gate": "appointment"}

# The part of the yard a run uses. The stacking owner tests with 50 to 200 containers, so a few bays, not
# all 11,790 positions. Each zone is the two bays at the exit end of a service lane, and the zones are on
# different lanes and under different yard cranes, so AGVs for one do not queue behind AGVs for another.
# west-* take containers of ships at berth B1, east-* of ships at B2; food and hazmat are far apart.
ZONES = (
    Zone("west-a", "general", "A1", (1, 2), serves="B1"),
    Zone("west-b", "general", "B2", (1, 2), serves="B1"),
    Zone("west-c", "general", "C3", (1, 2), serves="B1"),
    Zone("east-a", "general", "H1", (1, 3), serves="B2"),
    Zone("east-b", "general", "N1", (1, 2), serves="B2"),
    Zone("food", "food", "D3", (1, 1)),
    Zone("hazmat", "hazmat", "N4", (1, 1)),
)
MIN = 60.0

# Disruptions a scenario may carry in `events`. Each is a dict with "kind", "t" (seconds into the run) and
# "seconds" (how long it lasts; a road may stay closed to the end with None), plus what it hits:
EVENTS = {
    "agv_breakdown": "agv",            # {"kind": "agv_breakdown", "t": 1800, "seconds": 600, "agv": "AGV5"}
    "road_closed": "cells",            # {"kind": "road_closed", "t": 1200, "seconds": 900, "cells": [[57, 25]]}
    "crane_down": "crane",             # {"kind": "crane_down", "t": 900, "seconds": 900, "crane": "QC2"}
    "gate_lane_closed": "lane",        # {"kind": "gate_lane_closed", "t": 2400, "seconds": 1800, "lane": 1}   (lanes count from 1)
}


@dataclass
class Scenario:
    name: str
    vessels: list = field(default_factory=list)
    trucks: list = field(default_factory=list)
    inventory: list = field(default_factory=list)        # containers in the yard at the start, in stacking order
    zones: tuple = ZONES
    agvs: int = 8
    policies: dict = field(default_factory=lambda: dict(DEFAULT_POLICIES))
    tide: Tide = field(default_factory=Tide)
    channel_slots: int = 2
    transit_s: float = 600.0
    energy: bool = True
    horizon_s: float | None = None
    events: list = field(default_factory=list)           # disruptions to inject, plain dicts: see EVENTS
    params: Params = REFERENCE                           # the numbers the modules run on: sim/params.py
    soc_start: tuple | None = None                       # (low, high): each AGV starts with a charge drawn between them
    balance: bool = False                                # the scheduler names the least busy yard crane for each container
    spread_stock: int | None = None                      # a seed: the starting stock is spread over the bays at random

    def using(self, **changes) -> "Scenario":
        """A copy with other policies or settings: sc.using(stacking="shortest_stack", agvs=6)."""
        policies = dict(self.policies)
        fields = {}
        for key, value in changes.items():
            if key in POLICIES:
                if value not in POLICIES[key]:
                    raise ValueError(f"unknown {key} policy {value!r}: choose from {POLICIES[key]}")
                policies[key] = value
            elif key in {f.name for f in dataclasses.fields(self)}:
                fields[key] = value
            else:
                raise ValueError(f"a scenario has no setting {key!r}")
        return dataclasses.replace(self, policies=policies, **fields)

    def containers(self) -> list:
        return [*self.inventory, *(c for v in self.vessels for c in v.discharge), *(t.drops for t in self.trucks if t.drops)]


def save(scenario: Scenario, path) -> None:
    with open(path, "w") as f:
        json.dump(dataclasses.asdict(scenario), f, indent=1)
        f.write("\n")


def load(path) -> Scenario:
    with open(path) as f:
        d = json.load(f)
    box = lambda c: None if c is None else Container(**{**c, "place": tuple(c["place"]), "history": []})
    vessels = [Vessel(**{**v, "discharge": [box(c) for c in v["discharge"]],
                         "comms_out": None if v["comms_out"] is None else tuple(v["comms_out"])}) for v in d["vessels"]]
    trucks = [Truck(**{**t, "drops": box(t["drops"])}) for t in d["trucks"]]
    zones = tuple(Zone(**{**z, "bays": tuple(z["bays"])}) for z in d["zones"])
    extra = {"params": numbers.from_dict(d["params"])} if "params" in d else {}
    if d.get("soc_start"):
        extra["soc_start"] = tuple(d["soc_start"])
    return Scenario(**{**d, "vessels": vessels, "trucks": trucks, "inventory": [box(c) for c in d["inventory"]],
                       "zones": zones, "tide": Tide(**d["tide"]), **extra})


def demo() -> Scenario:
    """One morning: three ships for two berths on a rising tide, fifteen trucks, thirty containers in the yard.

    What it is built to show, module by module:
      ship arrival  BRAVO draws 11.9 m and must wait for the tide; CHARLIE, behind it, may pass it or not
                    (the two policies); the third ship waits for a berth.
      gate          trucks early, on time and late; one without an appointment; one turned away for its
                    plate, one for the toll, one for a damaged box; one sent to be weighed again.
      stacking      containers that leave soon are stacked among ones that stay; food and hazmat go to
                    their own zones.
      AGVs          twelve AGVs, six to a depot, share up to six cranes and the truck jobs, and charge
                    between jobs.
    """
    def box(cid, flow, cargo="general", vessel=None, truck=None, due=None, weight=20.0):
        return Container(cid, flow, cargo=cargo, weight_t=weight, vessel=vessel, truck=truck, due_s=due)

    # In the yard when the run starts, in the order they were stacked. due: when each is expected to leave.
    pickups = {f"IMP-{i:02d}": (f"T{i:02d}", m * MIN) for i, m in enumerate((15, 25, 35, 45, 55, 70, 80, 95, 110), start=1)}
    exports = {"EXP-A1": ("ALPHA", 55), "EXP-A2": ("ALPHA", 55), "EXP-A3": ("ALPHA", 55), "EXP-A4": ("ALPHA", 55),
               "EXP-B1": ("BRAVO", 120), "EXP-B2": ("BRAVO", 120), "EXP-C1": ("CHARLIE", 90), "EXP-C2": ("CHARLIE", 90)}
    order = ["IMP-09", "STAY-01", "EXP-B1", "IMP-01", "STAY-02", "EXP-A1", "IMP-06", "STAY-03", "EXP-C1", "IMP-02",
             "STAY-04", "EXP-A2", "IMP-08", "STAY-05", "EXP-B2", "IMP-03", "STAY-06", "EXP-A3", "IMP-07", "STAY-07",
             "EXP-C2", "IMP-04", "STAY-08", "EXP-A4", "IMP-05", "STAY-09", "STAY-10", "STAY-11", "STAY-12"]
    inventory = []
    for cid in order:
        if cid in pickups:
            inventory.append(box(cid, "import", truck=pickups[cid][0], due=pickups[cid][1]))
        elif cid in exports:
            inventory.append(box(cid, "export", vessel=exports[cid][0], due=exports[cid][1] * MIN))
        else:
            inventory.append(box(cid, "import"))

    def cargo_of(i):
        return "food" if i in (4, 9) else "hazmat" if i == 6 else "general"
    alpha = [box(f"A-{i:02d}", "import", cargo=cargo_of(i)) for i in range(1, 13)]
    for i, c in enumerate(alpha):                             # QC2 has six, two more than its two AGVs and its buffer take
        c.crane = "QC2" if i < 6 else "QC1" if i < 9 else "QC3"
    alpha[2].truck, alpha[2].due_s = "T13", 80 * MIN          # A-03 and A-08 are collected the same morning
    alpha[7].truck, alpha[7].due_s = "T14", 95 * MIN
    bravo = [box(f"B-{i:02d}", "import") for i in range(1, 9)]
    charlie = [box(f"C-{i:02d}", "import") for i in range(1, 7)]
    vessels = [
        Vessel("ALPHA", 0.0, draft_m=10.5, berth="B1", discharge=alpha, load=["EXP-A1", "EXP-A2", "EXP-A3", "EXP-A4"]),
        Vessel("BRAVO", 10 * MIN, draft_m=11.9, discharge=bravo, load=["EXP-B1", "EXP-B2", "EXP-B3", "EXP-D1"]),
        Vessel("CHARLIE", 15 * MIN, draft_m=10.0, discharge=charlie, load=["EXP-C1", "EXP-C2", "EXP-C3"]),
    ]

    def pick(n, appt_min, arrive_min, cid, fault=None):
        appt = None if appt_min is None else appt_min * MIN
        return Truck(f"T{n:02d}", arrive_min * MIN, plate=f"FL-{4100 + n}", appointment_s=appt, picks=cid, fault=fault)

    def drop(n, appt_min, arrive_min, c, fault=None):
        return Truck(f"T{n:02d}", arrive_min * MIN, plate=f"FL-{4100 + n}", appointment_s=appt_min * MIN, drops=c, fault=fault,
                     declared_t=15.0)
    trucks = [
        pick(1, 15, 12, "IMP-01"),                      # a little early: waits for its time
        pick(2, 25, 27, "IMP-02"),
        pick(3, 35, 57, "IMP-03"),                      # 22 minutes late: trouble desk, then without an appointment
        pick(4, 45, 44, "IMP-04"),
        pick(5, None, 40, "IMP-05"),                    # no appointment
        pick(6, 70, 72, "IMP-06"),
        pick(7, 80, 79, "IMP-07", fault="plate_mismatch"),
        pick(8, 95, 96, "IMP-08"),
        pick(9, 110, 108, "IMP-09"),
        drop(10, 30, 31, box("EXP-B3", "export", vessel="BRAVO", due=120 * MIN, weight=24.0)),
        drop(11, 20, 21, box("EXP-C3", "export", vessel="CHARLIE", due=90 * MIN, weight=18.0), fault="weight_mismatch"),
        drop(12, 50, 52, box("EXP-D1", "export", vessel="BRAVO", due=120 * MIN), fault="damaged"),
        pick(13, 80, 82, "A-03"),
        pick(14, 95, 93, "A-08"),
        pick(15, 60, 61, "STAY-01", fault="no_payment"),
    ]
    inventory[1].truck = "T15"                           # STAY-01 was booked out, but the truck cannot pay
    return Scenario("A morning at Dames Point", vessels=vessels, trucks=trucks, inventory=inventory, agvs=12,
                    tide=Tide(mean_depth_m=12.5, amplitude_m=0.9, high_water_s=165 * MIN))


# ---- a shift at real scale -------------------------------------------------------------------------
# Seven blocks for general cargo. Each is on a lane of its own, at the far end of it, so that AGVs waiting
# for the yard crane queue in the lane and not on the main road (a block beside the main road has room
# for one or two before the queue reaches the road), and each yard crane but RTG5 works one block, so it does not
# lose its time changing blocks. RTG1 to RTG3 take berth B1's containers, RTG4 to RTG6 berth B2's.
SHIFT_ZONES = (
    Zone("west-a", "general", "A1", (1, 16), serves="B1"),
    Zone("west-b", "general", "B2", (1, 17), serves="B1"),
    Zone("west-c", "general", "C3", (1, 9), serves="B1"),
    Zone("east-f", "general", "F2", (1, 17), serves="B2"),
    Zone("east-c", "general", "H3", (1, 12), serves="B2"),
    Zone("east-d", "general", "H4", (1, 12), serves="B2"),
    Zone("east-n", "general", "N1", (1, 12), serves="B2"),
    Zone("food", "food", "D3", (1, 6)),
    Zone("hazmat", "hazmat", "N4", (1, 12)),
)
SHIFT_ROOM = {"general": 2110, "food": 156, "hazmat": 312}             # containers the zones above take (a test checks it against the layout)
TRUCK_HOURS = (0.8, 1.2, 1.3, 1.1, 0.8, 1.1, 1.0, 0.7)                  # how the day's trucks spread over a shift's hours (assumption)
SLOT_S = 1800.0                                                        # an appointment is for a half-hour slot


def _poisson(r: random.Random, mean: float) -> int:
    n, limit, product = 0, pow(2.718281828459045, -mean), r.random()
    while product > limit:
        n, product = n + 1, product * r.random()
    return n


def shift(seed: int, hours: float = 8.0, calls: int = 4, agvs: int = 30, trucks_per_hour: float = 10.0, fill: float = 0.65,
          faults: bool = True, params: Params | None = None) -> Scenario:
    """A shift at real scale, from a seed, on the calibrated numbers (sim/params.py, docs/data-sheet.md).

    ships    `calls` ships, alternately for berth B1 and B2, each with 90 to 150 containers to discharge
             and 50 to 90 to load, drawing 9.0 to 12.19 m (40 ft, the deepest the berths take). They arrive
             at the sea buoy, 85 minutes out; the first at the start, the later ones about every half shift.
    yard     seven blocks for general cargo, part of one for food, one for hazmat, `fill` full at the start with
             the ships' exports, the imports trucks will collect, and containers that stay.
    trucks   `trucks_per_hour` on average, more in the second and third hour; a little over half collect.
             Four in five have an appointment for a half-hour slot and come around it; 14 percent of
             those do not come at all (PierPass); a few have a problem at the gate.
    fleet    `agvs` AGVs sharing the 12 charging stations, starting between 35 and 90 percent charged.
    faults   AGV breakdowns and crane outages at random (the rates are assumptions).
    Every part draws from its own stream, so changing one does not change the others."""
    p = (params or numbers.CALIBRATED).seeded(seed)
    r = {part: random.Random(f"shift|{seed}|{part}") for part in ("ships", "stock", "trucks", "gate", "agv faults", "crane faults", "tide")}
    span = hours * 3600.0
    weight = lambda rr: round(rr.uniform(8, 28), 1)
    cargo = lambda rr: rr.choices(("general", "food", "hazmat"), (0.88, 0.08, 0.04))[0]

    ships, exports, names = [], [], ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL"]
    rs = r["ships"]
    for k in range(calls):
        berth, turn = ("B1", "B2")[k % 2], k // 2
        arrive = max(0.0, turn * span / max(1, (calls + 1) // 2) + (k % 2) * 20 * MIN + (rs.uniform(-20, 20) * MIN if k > 1 else 0.0))
        name = names[k]
        v = Vessel(name, round(arrive, 0), draft_m=round(rs.uniform(9.0, numbers.MAX_DRAFT_M), 2), berth=berth,
                   discharge=[Container(f"{name[0]}-{i:03d}", "import", cargo=cargo(rs), weight_t=weight(rs)) for i in range(1, rs.randint(90, 150) + 1)])
        for i in range(1, rs.randint(50, 90) + 1):
            c = Container(f"X{name[0]}-{i:03d}", "export", vessel=name, due_s=v.arrives_s + numbers.TRANSIT_S + 2 * 3600.0, weight_t=weight(rs))
            v.load.append(c.id)
            exports.append(c)
        ships.append(v)
    ships.sort(key=lambda v: v.arrives_s)

    rt, rg = r["trucks"], r["gate"]
    n_trucks = round(trucks_per_hour * hours)
    shares = [TRUCK_HOURS[int(h * len(TRUCK_HOURS) / max(1, int(hours)))] for h in range(max(1, int(hours)))]
    plan = []                                              # (planned time, collects?)
    for _ in range(n_trucks):
        hour = rt.choices(range(len(shares)), shares)[0]
        plan.append((min(span - 60.0, (hour + rt.random()) * span / len(shares)), rt.random() < 0.55))
    plan.sort()
    pickups = [f"IMP-{i:04d}" for i in range(1, sum(1 for _, picks in plan if picks) + 1)]

    rk = r["stock"]
    general = round(fill * SHIFT_ROOM["general"])
    stock = list(exports)
    stock += [Container(cid, "import", weight_t=weight(rk)) for cid in pickups]
    def stays(n, kind, tag):                               # a container that stays: two in five have a known day to leave
        return [Container(f"{tag}-{i:04d}", "import", cargo=kind, weight_t=weight(rk),
                          due_s=round(span + rk.uniform(4, 96) * 3600.0, 0) if rk.random() < 0.4 else None) for i in range(1, n + 1)]
    stock += stays(max(0, general - len(stock)), "general", "STAY")
    stock += stays(round(fill * SHIFT_ROOM["food"]), "food", "FOOD") + stays(round(fill * SHIFT_ROOM["hazmat"]), "hazmat", "HAZ")
    rk.shuffle(stock)
    by_id = {c.id: c for c in stock}

    trucks, next_ships = [], ("NEXT-1", "NEXT-2")          # ships of a later shift, for the exports brought in today
    for n, (when, picks) in enumerate(plan, start=1):
        tid = f"T{n:03d}"
        booked = rt.random() < 0.8
        appt = (when // SLOT_S) * SLOT_S if booked else None
        arrive = when if not booked else max(0.0, appt + min(35.0, max(-20.0, rt.gauss(8.0, 9.0))) * MIN)
        fault = rg.choices((None, "plate_mismatch", "no_payment", "weight_mismatch"), (0.945, 0.015, 0.01, 0.03))[0]
        t = Truck(tid, round(arrive, 0), plate=f"FL-{6000 + n}", appointment_s=appt, fault=fault,
                  no_show=booked and rt.random() < numbers.NO_SHOW_SHARE)
        if picks:
            c = by_id[pickups.pop()]
            c.truck, c.due_s, t.picks = tid, (appt if booked else when), c.id
        else:
            t.drops = Container(f"EXP-{n:03d}", "export", vessel=rt.choice(next_ships), due_s=round(span + rt.uniform(4, 30) * 3600.0, 0), weight_t=weight(rt))
            if fault is None and rg.random() < 0.01:
                t.fault = "damaged"
        trucks.append(t)

    events = []
    if faults:
        cranes = [f"QC{i}" for i in range(1, 7)] + [f"RTG{i}" for i in range(1, 7)] + ["XC1"]
        rf = r["agv faults"]
        for _ in range(_poisson(rf, agvs * hours / 150.0)):
            events.append({"kind": "agv_breakdown", "t": round(rf.uniform(0.05, 1.0) * span, 0), "agv": f"AGV{rf.randint(1, agvs)}",
                           "seconds": round(min(3600.0, rf.lognormvariate(6.69, 0.47)), 0)})        # mean 15 minutes
        rf = r["crane faults"]
        for _ in range(_poisson(rf, len(cranes) * hours / 300.0)):
            events.append({"kind": "crane_down", "t": round(rf.uniform(0.05, 1.0) * span, 0), "crane": rf.choice(cranes),
                           "seconds": round(min(5400.0, rf.lognormvariate(6.98, 0.47)), 0)})        # mean 20 minutes
        events.sort(key=lambda e: (e["t"], e["kind"]))
    tide = Tide(mean_depth_m=numbers.TIDE_MEAN_DEPTH_M, amplitude_m=numbers.TIDE_AMPLITUDE_M, period_s=numbers.TIDE_PERIOD_S,
                high_water_s=round(r["tide"].uniform(0, numbers.TIDE_PERIOD_S), 0))
    return Scenario(f"a shift at real scale, seed {seed}", vessels=ships, trucks=trucks, inventory=stock, zones=SHIFT_ZONES, agvs=agvs,
                    tide=tide, transit_s=numbers.TRANSIT_S, events=events, params=p, soc_start=(0.35, 0.90), balance=True, spread_stock=seed)


def generate(seed: int, vessels: int = 3, per_vessel: tuple = (8, 14), stock: int = 36, trucks: int = 14, agvs: int = 8,
             hours: float = 2.0, late_share: float = 0.15, walk_in_share: float = 0.2) -> Scenario:
    """A random workload. Each part draws from its own stream, so asking for more trucks does not change
    the ships. Faults (damage, plates, tolls) are left to demo(); here trucks are only early, late or unbooked."""
    rng = {part: random.Random(f"{seed}:{part}") for part in ("ships", "stock", "trucks")}
    span = hours * 3600.0
    ships, names = [], ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT"]
    for i in range(vessels):
        r = rng["ships"]
        arrive = 0.0 if i == 0 else round(r.uniform(0.05, 0.45) * span, 0)
        n = r.randint(*per_vessel)
        cargo = lambda: r.choices(("general", "food", "hazmat"), (0.86, 0.09, 0.05))[0]
        ships.append(Vessel(names[i], arrive, draft_m=round(r.uniform(9.5, 11.9), 1),
                            discharge=[Container(f"{names[i][0]}-{k:02d}", "import", cargo=cargo(), weight_t=round(r.uniform(8, 28), 1))
                                       for k in range(1, n + 1)]))
    ships.sort(key=lambda v: v.arrives_s)
    r = rng["stock"]
    inventory, bookable = [], []
    for k in range(1, stock + 1):
        kind = r.choices(("pickup", "export", "stay"), (0.35, 0.25, 0.40))[0]
        if kind == "export":
            v = r.choice(ships)
            c = Container(f"EXP-{k:02d}", "export", vessel=v.id, due_s=v.arrives_s + 0.4 * span, weight_t=round(r.uniform(8, 28), 1))
            v.load.append(c.id)
        elif kind == "pickup":
            c = Container(f"IMP-{k:02d}", "import", due_s=round(r.uniform(0.1, 0.95) * span, 0), weight_t=round(r.uniform(8, 28), 1))
            bookable.append(c)
        else:
            c = Container(f"STAY-{k:02d}", "import", weight_t=round(r.uniform(8, 28), 1))
        inventory.append(c)
    r = rng["trucks"]
    fleet = []
    for n, c in enumerate(sorted(bookable, key=lambda c: c.due_s)[:trucks], start=1):
        c.truck = f"T{n:02d}"
        walk_in = r.random() < walk_in_share
        late = not walk_in and r.random() < late_share
        arrive = c.due_s + (r.uniform(16, 30) if late else r.uniform(-6, 9)) * MIN
        fleet.append(Truck(c.truck, max(0.0, round(arrive, 0)), plate=f"FL-{5000 + n}",
                           appointment_s=None if walk_in else c.due_s, picks=c.id))
    return Scenario(f"generated workload, seed {seed}", vessels=ships, trucks=fleet, inventory=inventory, agvs=agvs,
                    tide=Tide(high_water_s=0.75 * span))
