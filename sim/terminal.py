"""The scheduler: one terminal, the four modules joined on the layout.

On Oct 22 Dr. Wang said there must be a high-level scheduling system that sends the requests ("an AGV
is needed at this crane", "fetch this container for that truck") and connects every module, and that
nobody owned it. This file is that part, written so each module stays behind a few calls:

    ship arrival   ships.expect(v), ships.discharge_plan(v), ships.work_done(v); it calls on_cleared, on_berthed
    stacking       yard.assign(c, berth), yard.settle(id), yard.blockers(id), yard.rehandle_target(...)
    AGVs           fleet.submit(Job), fleet.carry(agv, id, cell, ...), fleet.release(agv); traffic moves trucks too
    gate           gate.expect(t), gate.exit(t, c, done); it calls spot_for, on_enter, on_turned_away

How a container changes hands (Christopher's rule of Nov 15, kept from sim/run_demo.py):
    * A crane does every lift, and one lift at a time.
    * A quay crane lowers a container straight onto an AGV waiting in one of its lanes. With no AGV
      there it sets the container in its buffer (the layout's buffer_capacity), and an AGV is loaded
      from the buffer when it comes. A full buffer stops the crane (Dr. Wang, Oct 15).
    * An AGV that brings a container waits for the crane and leaves once the container is clear of its
      deck (HANDOFF_S into the lift).
Trucks never drive into the yard (the group, Oct 29): a truck waits at a truck spot of the exchange
zone, AGVs bring or take the container on the other side, and the exchange crane lifts across. A
container whose truck or AGV is not there yet waits in the exchange stack. A truck that comes to collect
is called in from the gate's holding area once its container has reached the exchange zone, because the
four truck spots are one lane and a truck waiting there would hold up the ones behind it.

What the scheduler itself decides, all assumptions for the group to change: QUAY_POOL AGVs are called
to each crane of a ship's berth, from the moment the ship is cleared to come in, so they wait in the
lanes when it berths (Dr. Wang, Sep 24: "the vehicle will arrive first, beneath the crane"); a ship's exports are loaded at each crane after that crane
has finished discharging, the one with the fewest containers on top of it first; a ship waits for an
export that is already inside the terminal, and one whose truck never brought it in misses the ship;
an import is fetched PRESTAGE_S before its truck's appointment; a job for a truck goes before a job for
a ship.

Disruptions (scenario.events, for what-if runs) are passed on to the module they hit: an AGV breakdown
and a closed road to the AGV module, a closed lane to the gate. A crane out of service is handled here:
it finishes the lift it is making and starts no other until it is back.

The numbers come from scenario.params (sim/params.py): how long each crane move takes and how much it
varies, how many AGVs a crane calls, how long before a ship berths they are called. With the calibrated
set a yard crane also takes time to travel to the bay it is needed at, and with scenario.balance the
scheduler names the yard crane with the least work waiting, and the stacking module chooses the bay
among that crane's (at real scale one rule alone would send every container to the same bay).
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from dataclasses import dataclass, field

from terminal_layout.timing import YARD_SERVICE_S

from sim.agv import AgvFleet, Job, Traffic, Vehicle
from sim.gate import Gate
from sim.kernel import Clock, EventLog
from sim.ship_arrival import ShipArrival
from sim.stacking import Yard, YardFull

QUAY_POOL = 2               # AGVs called to a working quay crane at a time (the reference; scenario.params.agvs_per_crane)
PRESTAGE_S = 1800.0         # an import is fetched this long before its truck's appointment
TRUCK_FIRST = 1             # priority of a job for a truck over a job for a ship (0 = no priority)
LANE_ORDER = (1, 2, 0, 3)   # the crane's own column first
XC_SERVICE_S = YARD_SERVICE_S       # one move of the exchange crane, until the gate owner sets it


@dataclass
class Quay:
    id: str
    lanes: tuple
    cap: int
    vessel: object = None
    alongside: bool = False                           # the ship has berthed: the crane may work
    ship: deque = field(default_factory=deque)        # containers still on board for this crane
    buffer: deque = field(default_factory=deque)      # containers on the ground beside its lanes
    here: dict = field(default_factory=dict)          # lane -> (agv, job, container or None) standing ready
    claims: dict = field(default_factory=dict)        # lane -> job id: an AGV is coming to it or stands in it
    open: int = 0                                     # AGVs called for containers not yet handed over
    call_from: float = 0.0                            # AGVs are called for this ship from this time on
    loading: int = 0                                  # exports on their way from the yard to this crane
    loading_started: bool = False
    waiting_since: float | None = None
    waited_s: float = 0.0


class Terminal:
    def __init__(self, layout, scenario):
        sc = self.scenario = copy.deepcopy(scenario)
        self.layout = layout
        self.clock = Clock()
        self.log = EventLog(self.clock)
        p = self.p = sc.params
        self.traffic = Traffic(self.clock, layout, self.log, p.energy if sc.energy else None, p)
        self.fleet = AgvFleet(self.clock, layout, self.traffic, self.log, n=sc.agvs, policy=sc.policies["dispatch"], soc_start=sc.soc_start)
        self.yard = Yard(layout, sc.zones, policy=sc.policies["stacking"])
        self.yard.now = lambda: self.clock.now
        self.ships = ShipArrival(self.clock, self.log, layout, policy=sc.policies["ship"], channel_slots=sc.channel_slots,
                                 transit_s=sc.transit_s, tide=sc.tide, clearance_m=p.keel_clearance_m)
        varies = p.seed is not None and p.gate_cv > 0
        self.gate = Gate(self.clock, self.log, policy=sc.policies["gate"], lanes=len(layout.gate_cells("in")), check_s=p.gate_check_s,
                         check_time=(lambda truck, n: p.move("gate", truck, n)) if varies else None)
        self.pool = p.agvs_per_crane                 # AGVs called to a working quay crane at a time
        self._moved: dict[tuple, int] = {}           # (kind of move, container) -> how many so far: names the random stream
        self.crane_at: dict[str, tuple] = {}         # yard crane -> the bay it stands at
        self.crane_travel_s: dict[str, float] = defaultdict(float)
        self.containers: dict[str, object] = {}
        self.trace: list[dict] = []
        self.jobs = 0
        self.problems: list[str] = []
        self.busy: dict[str, bool] = {}
        self.crane_busy_s: dict[str, float] = defaultdict(float)
        self.lifts: dict[str, int] = defaultdict(int)
        self.quay = {q.id: Quay(q.id, q.transfer_points, q.buffer_capacity) for q in layout.quay_cranes.values()}
        self.quay_of = {lane: q for q in self.quay.values() for lane in q.lanes}
        self.yard_queue: dict[str, list] = {crane: [] for crane in layout.yard_cranes}
        self.xc = layout.blocks["XT"].crane
        by_arrival = lambda block: sorted(layout.blocks[block].transfer_points, reverse=True)     # the lane runs west
        self.truck_spots, self.agv_spots = by_arrival("XT"), by_arrival("XA")
        self.truck_at: dict[tuple, str] = {}
        self.agv_at: dict[tuple, str] = {}
        self.xslots: dict[str, str | None] = dict.fromkeys([*layout.blocks["XT"].slots, *layout.blocks["XA"].slots])
        self.x: dict[str, dict] = {}                 # containers passing through the exchange zone
        self.wanted: set[str] = set()                # imports a truck is coming for, not in the yard yet
        self.fetching: set[str] = set()              # imports an AGV has been called for
        self.gates_in = [c.pos for c in layout.gate_cells("in")]
        self.gates_out = [c.pos for c in layout.gate_cells("out")]
        self.missed: list[str] = []
        self.to_load: dict[str, list] = {}           # ship -> its exports not yet called for
        self.called: set[str] = set()                # containers an AGV has been called for, still in the yard
        self.down: dict[str, float] = {}             # crane -> out of service until this time (a disruption)
        self.outages: list[dict] = []                # {"crane", "t0", "t1"}, for the replay
        for crane in [*self.quay, *self.yard_queue]:
            self.busy[crane] = False

        self.ships.on_cleared, self.ships.on_berthed = self._cleared, self._berthed
        self.gate.find, self.gate.spot_for = self.containers.get, self._truck_spot
        self.gate.why_not = self._why_no_spot
        self.gate.on_enter, self.gate.on_cleared = self._truck_enters, self._truck_cleared
        if p.no_show_s is not None:
            self.gate.on_turned_away = lambda truck, why: truck.picks and self._send_back(truck.picks)
        self.sent_back: list[str] = []               # imports taken back to the yard: the truck did not come or was turned away
        self.x_queue: list[str] = []                 # imports wanted at the exchange while it holds as many as it may (params.exchange_ahead)
        self.quay_waits: list[tuple] = []            # (crane, from, until): a quay crane stood waiting with a full buffer and no AGV
        self._trucks = {k.id: k for k in sc.trucks}  # every truck of the workload, also one that will not come
        self._here: set[str] = set()                 # of those, the ones whose truck is at the gate already

        for v in self.fleet.agvs.values():
            self.trace.append({"kind": "vehicle", "id": v.id, "type": "agv", "at": v.cell, "t": 0.0})
        berth_of = {v.id: v.berth for v in sc.vessels}
        for c in sc.inventory:
            self.containers[c.id] = c
        self.yard.preload(sc.inventory, {c.id: berth_of.get(c.vessel) for c in sc.inventory}, spread=sc.spread_stock)
        for c in sc.inventory:
            pos = self.yard.where[c.id]
            c.move(0.0, ("yard", *pos))
            self.trace.append({"kind": "start", "what": c.id, "at": ("slot", *pos)})
        for v in sc.vessels:
            for c in v.discharge:
                self.containers[c.id] = c
                c.vessel = v.id
                c.move(0.0, ("ship", v.id))
            self.ships.expect(v)
        for t in sc.trucks:
            if t.drops:
                self.containers[t.drops.id] = t.drops
                t.drops.truck = t.id
            if not t.no_show:
                self.gate.expect(t)
            if t.picks and sc.policies["gate"] == "appointment" and t.appointment_s is not None:
                self.clock.at(max(0.0, t.appointment_s - PRESTAGE_S), self._want_import, t.picks)
                if p.no_show_s is not None:
                    self.clock.at(t.appointment_s + p.no_show_s, self._no_show, t)
        for e in sc.events:
            self._check_event(e)
            self.clock.at(e["t"], self._disrupt, e)

    # ---- running -----------------------------------------------------------------------------
    def run(self, until: float | None = None):
        self.clock.run(until if until is not None else self.scenario.horizon_s)
        self.end_s = max([e.t for e in self.log.entries] + [0.0])
        for leg in self.traffic.legs:
            self.trace.append({"kind": "drive", **leg})
        for v in self.scenario.vessels:
            if "berthed" in v.times:
                self.trace.append({"kind": "vessel", "id": v.id, "berth": v.berth, "t_arrive": v.times["arrived"],
                                   "t_cleared": v.times["cleared"], "t": v.times["berthed"], "t_depart": v.times.get("cast_off")})
        return self

    def say(self, subject: str, text: str, **data) -> None:
        self.log.add("terminal", subject, text, **data)

    # ---- disruptions -----------------------------------------------------------------------------
    def _check_event(self, e: dict) -> None:
        """Refuse an event that names something the terminal does not have, before the run starts."""
        kind = e.get("kind")
        if kind not in ("agv_breakdown", "road_closed", "crane_down", "gate_lane_closed"):
            raise ValueError(f"unknown event {kind!r}")
        if not isinstance(e.get("t"), (int, float)) or e["t"] < 0:
            raise ValueError(f"{kind}: its time must be a number of seconds from the start, not {e.get('t')!r}")
        seconds = e.get("seconds")
        if not (kind == "road_closed" and seconds is None) and not (isinstance(seconds, (int, float)) and seconds > 0):
            raise ValueError(f"{kind}: it must last a number of seconds, not {seconds!r}")
        if kind == "agv_breakdown" and e.get("agv") not in self.fleet.agvs:
            raise ValueError(f"agv_breakdown: there is no AGV called {e.get('agv')!r} (the fleet is AGV1 to AGV{len(self.fleet.agvs)})")
        if kind == "crane_down" and e.get("crane") not in self.busy:
            raise ValueError(f"crane_down: there is no crane called {e.get('crane')!r}")
        if kind == "gate_lane_closed" and e.get("lane") not in range(1, len(self.gate.lane) + 1):
            raise ValueError(f"gate_lane_closed: the gate has lanes 1 to {len(self.gate.lane)}, not {e.get('lane')!r}")
        if kind == "road_closed":
            cells = [tuple(c) for c in e.get("cells") or []]
            if not cells or any(c not in self.traffic.adj for c in cells):
                raise ValueError(f"road_closed: {next((c for c in cells if c not in self.traffic.adj), 'nothing')} is not a road cell")

    def _disrupt(self, e: dict) -> None:
        kind = e["kind"]
        if kind == "agv_breakdown":
            self.fleet.break_down(e["agv"], e["seconds"])
        elif kind == "road_closed":
            self.traffic.close_road(e["cells"], e.get("seconds"))
        elif kind == "crane_down":
            self.crane_down(e["crane"], e["seconds"])
        else:
            self.gate.close_lane(e["lane"], e["seconds"])

    def crane_down(self, crane: str, seconds: float) -> None:
        """A crane is out of service for so long: it finishes the lift it is making and starts no other."""
        until = max(self.down.get(crane, 0.0), self.clock.now + seconds)
        self.down[crane] = until
        self.outages.append({"crane": crane, "t0": self.clock.now, "t1": until})
        self.log.add("terminal", crane, f"{crane} is out of service for {seconds / 60:.0f} min", disruption=True)
        self.log.explain("terminal", [crane], f"{crane} is out of service until {_clock(until)}: it finishes the lift it is making and starts no other.")
        self.clock.at(until, self._crane_back, crane)

    def _crane_back(self, crane: str) -> None:
        if crane in self.down and self.clock.now >= self.down[crane] - 1e-6:
            del self.down[crane]
            self.log.add("terminal", crane, f"{crane} is back in service", disruption=True)
            if not self.busy[crane]:
                self._poke(crane)

    def _job(self, kind: str, pickup, on_pickup, drops, container=None, purpose: str = "") -> Job:
        """drops: every cell the container may have to be carried to, so the fleet can judge the charge needed.
        purpose: what the AGV is wanted for, in words (the fleet notes it with its reason for the AGV it picks)."""
        self.jobs += 1
        return Job(f"J{self.jobs:03d}", kind, tuple(pickup), on_pickup, drops=tuple(drops), container=container,
                   priority=TRUCK_FIRST if kind in ("deliver", "store") else 0, purpose=purpose)

    def _stands(self, cargo, berth) -> list:
        """Where the stacking module may send containers of these kinds from this berth: its bays' transfer points."""
        zones = [z for z in self.yard.zones if z.cargo in cargo]
        near = [z for z in zones if z.serves in (None, berth)] or zones
        return sorted({self.yard.stand(b) for b, z in self.yard.zone_of.items() if z in near})

    # ---- cranes: one lift at a time ----------------------------------------------------------------
    def _lift(self, crane: str, kind: str, c, src, dst, then=None, at_handoff=None) -> None:
        """One lift. kind: "quay", "yard" or "exchange" (a full move), or "handoff" (off the ground onto a vehicle)."""
        if self.busy[crane]:
            raise RuntimeError(f"{crane} is asked for a second lift at {self.clock.now:.0f} s")
        n = self._moved[(kind, c.id)] = self._moved.get((kind, c.id), 0) + 1
        seconds = self.p.draw(self.p.handoff_s, self.p.move_cv, kind, c.id, n) if kind == "handoff" else self.p.move(kind, c.id, n)
        self.busy[crane] = True
        t0 = self.clock.now
        self.crane_busy_s[crane] += seconds
        self.lifts[crane] += 1
        c.move(t0, ("crane", crane))
        self.trace.append({"kind": "lift", "by": crane, "what": c.id, "t0": t0, "t1": t0 + seconds, "src": tuple(src), "dst": tuple(dst)})
        if at_handoff:
            self.clock.after(min(self.p.handoff_s, seconds), at_handoff)
        self.clock.after(seconds, self._lifted, crane, then)

    def _lifted(self, crane: str, then) -> None:
        self.busy[crane] = False
        if then:
            then()
        if not self.busy[crane]:
            self._poke(crane)

    def _poke(self, crane: str) -> None:
        if crane in self.quay:
            self._poke_quay(self.quay[crane])
        elif crane == self.xc:
            self._poke_xc()
        else:
            self._poke_yard(crane)

    # ---- ship side -----------------------------------------------------------------------------
    def _cleared(self, vessel) -> None:
        """The ship is on its way in: its cranes get their work lists and AGVs are called to their lanes."""
        plan = self.ships.discharge_plan(vessel)
        cranes = list(plan)
        vessel.times["on_board"] = len(vessel.discharge)
        self.to_load[vessel.id] = list(vessel.load)
        lead = self.p.call_lead_s
        wait = 0.0 if lead is None else max(0.0, self.scenario.transit_s - lead)
        for crane, items in plan.items():
            q = self.quay[crane]
            q.vessel, q.alongside, q.loading_started, q.waiting_since = vessel, False, False, None
            q.call_from = self.clock.now + wait
            q.ship.extend(items)
        for crane in cranes:
            if wait:
                self.clock.after(wait, lambda q=self.quay[crane]: q.vessel is vessel and self._quay_next(q))
            else:
                self._quay_next(self.quay[crane])

    def _berthed(self, vessel) -> None:
        for q in self.quay.values():
            if q.vessel is vessel:
                q.alongside = True
                self._quay_next(q)

    def _quay_next(self, q: Quay) -> None:
        """After anything changes at a quay crane: call AGVs, start loading when discharge is over, work."""
        while q.vessel and q.open < self.pool and len(q.ship) + len(q.buffer) > q.open and self.clock.now >= q.call_from - 1e-6:
            lane = next((q.lanes[i] for i in LANE_ORDER if i < len(q.lanes) and q.lanes[i] not in q.claims), None)
            if lane is None:
                break
            job = self._job("discharge", lane, self._agv_at_quay, self._stands({c.cargo for c in [*q.buffer, *q.ship]}, q.vessel.berth),
                            purpose=f"to {q.id}, to take a container of {q.vessel.id} to the yard")
            q.claims[lane] = job.id
            q.open += 1
            self.fleet.submit(job)
        if q.vessel and q.alongside and not q.ship and not q.buffer and not q.open and not self.busy[q.id]:
            q.loading_started = True
            self._feed_load(q)
        self._poke_quay(q)
        if q.vessel and q.alongside:
            self._vessel_progress(q.vessel)

    def _agv_at_quay(self, agv: str, job) -> None:
        q = self.quay_of[job.pickup]
        q.here[job.pickup] = (agv, job, None)
        self._poke_quay(q)

    def _poke_quay(self, q: Quay) -> None:
        if self.busy[q.id] or q.id in self.down or not q.vessel or not q.alongside:
            return
        ready = sorted(q.here.items(), key=lambda kv: (kv[1][1].times.get("at_pickup", 0.0), kv[0]))
        export = next(((lane, agv, job, c) for lane, (agv, job, c) in ready if c is not None), None)
        if export:                                              # an export has come: load it
            lane, agv, job, c = export
            del q.here[lane]
            self._resume(q)
            self.say(q.id, f"{q.id} loads {c.id} from {agv} onto {q.vessel.id}", container=c.id)
            self._lift(q.id, "quay", c, ("vehicle", agv), ("ship", q.id),
                       then=lambda: self._on_board(q, c),
                       at_handoff=lambda: (self.fleet.unloaded(agv), self.fleet.release(agv, on_clear=lambda v: self._lane_free(q, lane))))
            return
        empty = next(((lane, agv, job) for lane, (agv, job, c) in ready if c is None), None)
        if empty and (q.buffer or q.ship):
            lane, agv, job = empty
            del q.here[lane]
            self._resume(q)
            if q.buffer:                                         # the oldest container in the buffer goes first
                c = q.buffer.popleft()
                self.say(q.id, f"{q.id} lifts {c.id} from its buffer onto {agv}", container=c.id)
                self._lift(q.id, "handoff", c, ("ground", q.lanes[1]), ("vehicle", agv), then=lambda: self._loaded_at_quay(q, lane, agv, job, c))
            else:
                c = q.ship.popleft()
                self.say(q.id, f"{q.id} sets {c.id} straight onto {agv}", container=c.id)
                self._lift(q.id, "quay", c, ("ship", q.id), ("vehicle", agv), then=lambda: (self._off_ship(q), self._loaded_at_quay(q, lane, agv, job, c)))
            return
        if q.ship and len(q.buffer) < q.cap:                     # no AGV here: into the buffer
            c = q.ship.popleft()
            self._resume(q)
            self.say(q.id, f"{q.id} sets {c.id} in its buffer ({len(q.buffer) + 1}/{q.cap})", container=c.id)
            self.log.explain("terminal", [q.id, c.id], f"{q.id} sets {c.id} on the ground: no AGV stands in its lanes, and its buffer has room "
                             f"({len(q.buffer)} of {q.cap} taken), so the crane does not wait.")
            self._lift(q.id, "quay", c, ("ship", q.id), ("ground", q.lanes[1]),
                       then=lambda: (self._off_ship(q), q.buffer.append(c), c.move(self.clock.now, ("buffer", q.id)), self._quay_next(q)))
        elif q.ship and q.waiting_since is None:
            q.waiting_since = self.clock.now
            self.say(q.id, f"{q.id} waits: its buffer is full ({len(q.buffer)}/{q.cap}) and no AGV is in its lanes")
            coming = [j for j in self.fleet.jobs if j.kind == "discharge" and j.pickup in q.lanes and "at_pickup" not in j.times]
            on_way = sorted(j.agv for j in coming if j.agv)
            self.log.explain("terminal", [q.id], f"{q.id} stops: its buffer is full ({len(q.buffer)} of {q.cap}) and no AGV stands in its lanes. "
                             + (f"{' and '.join(on_way)} {'is' if len(on_way) == 1 else 'are'} on the way." if on_way
                                else f"{len(coming)} call{'s' if len(coming) != 1 else ''} for an AGV {'are' if len(coming) != 1 else 'is'} waiting: every AGV is busy."))

    def _resume(self, q: Quay) -> None:
        if q.waiting_since is not None:
            waited = self.clock.now - q.waiting_since
            q.waited_s += waited
            self.quay_waits.append((q.id, q.waiting_since, self.clock.now))
            q.waiting_since = None
            self.say(q.id, f"{q.id} goes on after waiting {waited:.0f} s")

    def _off_ship(self, q: Quay) -> None:
        q.vessel.times["on_board"] -= 1

    def _loaded_at_quay(self, q: Quay, lane, agv: str, job, c) -> None:
        c.move(self.clock.now, ("vehicle", agv))
        q.open -= 1
        self._to_yard(agv, c, q.vessel.berth, on_clear=lambda v: self._lane_free(q, lane))
        self._quay_next(q)

    def _lane_free(self, q: Quay, lane) -> None:
        q.claims.pop(lane, None)
        self._quay_next(q)

    def _to_yard(self, agv: str, c, berth, on_clear=None) -> None:
        """An AGV has the container on: ask the stacking module where it goes and drive there."""
        try:
            bay = self.yard.assign(c, berth, crane=self._least_busy(c, berth) if self.scenario.balance else None)
        except YardFull as full:
            self.problems.append(str(full))
            self.say(c.id, f"{c.id} has nowhere to go: {full}")
            return
        self.say(agv, f"{agv} takes {c.id} to block {bay[0]}, bay {bay[1]}", quiet=True, container=c.id)
        self.log.explain("yard", [c.id, agv], f"{c.id} goes to block {bay[0]}, bay {bay[1]}: {self._why_bay(c, berth, bay)}.")
        self.fleet.carry(agv, c.id, self.yard.stand(bay), lambda a: self._ask_yard("stack", a, c, None), on_clear)

    def _why_bay(self, c, berth, bay) -> str:
        """Why the stacking module (and, when the scheduler balances, the scheduler) sent a container to this bay."""
        zone, crane = self.yard.zone_of[bay], self.yard.crane(bay)
        kind = {"general": "general cargo", "food": "food", "hazmat": "hazardous cargo"}[c.cargo]
        where = f"zone {zone.name} takes {kind}" + (f" from berth {berth}" if zone.serves and zone.serves == berth else "")
        if self.scenario.balance:
            cranes = self.yard.cranes_for(c, berth)
            load = lambda k: len(self.yard_queue[k]) + int(self.busy[k]) + self.yard.coming(k) - (1 if k == crane else 0)
            others = ", ".join(f"{k} {load(k)}" for k in cranes if k != crane)
            return (f"{where}. No yard crane with room had less work waiting or on its way than {crane} ({load(crane)}" + (f"; {others}" if others else "")
                    + f"), and under it, {self.yard.bay_reason[c.id]}")
        return f"{where}, and {self.yard.bay_reason[c.id]}"

    def _least_busy(self, c, berth) -> str | None:
        """The yard crane, of those with room for this container, that has the least work waiting or on its way."""
        cranes = self.yard.cranes_for(c, berth)
        load = lambda k: len(self.yard_queue[k]) + int(self.busy[k]) + self.yard.coming(k)
        return min(cranes, key=lambda k: (load(k), cranes.index(k))) if cranes else None

    def _feed_load(self, q: Quay) -> None:
        """Call AGVs for exports of this crane's ship: of those standing in the yard, the ones with the
        fewest containers on top first. One that is not in the yard yet is tried again later."""
        left = self.to_load[q.vessel.id]
        going = set(left) | self.called                       # these will be lifted off anyway: wait for them, do not rehandle them
        ready = sorted((cid for cid in left if self.containers[cid].place[0] == "yard" and not going & set(self.yard.blockers(cid))),
                       key=lambda cid: (len(self.yard.blockers(cid)), left.index(cid)))
        for cid in ready[:max(0, self.pool - q.loading)]:
            left.remove(cid)
            self.called.add(cid)
            c = self.containers[cid]
            q.loading += 1
            stand = self.yard.stand(self.yard.bay_of[c.id])
            on_top = len(self.yard.blockers(cid))
            self.log.explain("terminal", [cid, q.id], f"{cid} is called for {q.vessel.id} at {q.id}: of the exports still to load, "
                             + ("nothing stands on it" if not on_top else f"it has the fewest containers on top of it ({on_top})") + ".")
            self.fleet.submit(self._job("load", stand, lambda agv, job, c=c: self._ask_yard("retrieve", agv, c, lambda: self._export_to_quay(q, agv, job, c)),
                                        q.lanes, container=c.id, purpose=f"to fetch {cid} from the yard for {q.vessel.id} at {q.id}"))

    def _export_to_quay(self, q: Quay, agv: str, job, c) -> None:
        lane = next((q.lanes[i] for i in LANE_ORDER if i < len(q.lanes) and q.lanes[i] not in q.claims), None)
        if lane is None:                                         # every lane is still in use: hold at the bay a moment
            self.clock.after(5.0, self._export_to_quay, q, agv, job, c)
            return
        q.claims[lane] = job.id
        self.fleet.carry(agv, c.id, lane, lambda a: (q.here.__setitem__(lane, (a, job, c)), self._poke_quay(q)))

    def _on_board(self, q: Quay, c) -> None:
        c.move(self.clock.now, ("gone", "ship", q.vessel.id))
        q.loading -= 1
        self._feed_load(q)
        self._quay_next(q)

    def _vessel_progress(self, vessel) -> None:
        if vessel.times.get("work_done") is not None or vessel.times["on_board"] > 0:
            return
        mine = [q for q in self.quay.values() if q.vessel is vessel]
        if not all(q.loading_started and not q.loading and not self.busy[q.id] for q in mine):
            return
        late = [self.containers[cid] for cid in self.to_load[vessel.id]]
        if any(c.place[0] != "outside" for c in late):           # in the yard or on its way through the terminal: wait for it
            return
        for c in late:                                           # the cut-off: its truck never brought it in
            self.missed.append(c.id)
            self.say(c.id, f"{c.id} is not at the terminal and misses {vessel.id}", container=c.id)
        self.to_load[vessel.id] = []
        for q in mine:
            q.vessel, q.alongside = None, False
        self.ships.work_done(vessel)

    # ---- yard cranes ---------------------------------------------------------------------------
    def _ask_yard(self, kind: str, agv: str, c, then) -> None:
        """An AGV stands at a bay: 'stack' the container it brought, or 'retrieve' one onto it."""
        crane = self.yard.crane(self.yard.bay_of[c.id])
        self.yard_queue[crane].append((kind, agv, c, then, self.clock.now))
        self._poke_yard(crane)

    def _poke_yard(self, crane: str) -> None:
        queue = self.yard_queue[crane]
        if self.busy[crane] or crane in self.down or not queue:
            return
        if self.p.gantry_m_s:                                                # a crane that takes time to travel: the nearest job first,
            late = [r for r in queue if self.clock.now - r[4] >= self.p.yard_patience_s]         # unless one has waited long
            item = min(late, key=lambda r: r[4]) if late else \
                min(queue, key=lambda r: (self._gantry_s(crane, self.yard.bay_of[r[2].id]), r[4]))
        else:
            late = []
            item = next((r for r in queue if r[0] == "retrieve"), queue[0])  # PortSim: retrieve before storing
        if len(queue) > 1:                                                   # a choice among several: say why this one
            others = [r for r in queue if r is not item]
            away = lambda r: self._gantry_s(crane, self.yard.bay_of[r[2].id])
            waited = lambda r: f"{(self.clock.now - r[4]) / 60:.0f} min" if self.clock.now - r[4] >= 90 else f"{self.clock.now - r[4]:.0f} s"
            if late:
                why = (f"it has waited {waited(item)}, and an AGV that has waited {self.p.yard_patience_s / 60:.0f} minutes goes before nearer ones. "
                       f"{len(others)} more wait{'s' if len(others) == 1 else ''} at this crane")
            elif self.p.gantry_m_s:
                nxt = min(others, key=lambda r: (away(r), r[4]))
                here = "the crane already stands at its bay" if not away(item) else \
                    f"its bay is the nearest of the {len(queue)} the crane is wanted at, {away(item):.0f} s of travel"
                why = f"{here} (the next, {nxt[1]}, {away(nxt):.0f} s away), and none has waited {self.p.yard_patience_s / 60:.0f} minutes"
            elif item is queue[0]:
                why = f"the first of the {len(queue)} in line" + (", and a container to fetch goes before one to store" if item[0] == "retrieve" else ": nothing is to be fetched")
            else:
                why = f"a container to fetch goes before one to store, and {queue[0][1]} came first only to store one"
            self.log.explain("yard", [crane, item[1], item[2].id], f"{crane} serves {item[1]} next, {'to store' if item[0] == 'stack' else 'to fetch'} {item[2].id}: {why}.")
        queue.remove(item)
        kind, agv, c, then, _ = item
        bay = self.yard.bay_of[c.id]
        travel = self._gantry_s(crane, bay)
        self.crane_at[crane] = bay
        if travel:                                                           # the crane drives to the bay first
            self.busy[crane] = True
            self.crane_travel_s[crane] += travel
            self.trace.append({"kind": "gantry", "by": crane, "t0": self.clock.now, "t1": self.clock.now + travel, "to": self.yard.stand(bay)})
            self.clock.after(travel, self._at_bay, crane, item)
            return
        self._serve_yard(crane, item)

    def _gantry_s(self, crane: str, bay: tuple) -> float:
        """Seconds for a yard crane to travel from the bay it stands at to this one (0 with the reference numbers)."""
        at = self.crane_at.get(crane)
        if not self.p.gantry_m_s or at is None or at == bay:
            return 0.0
        (ax, ay), (bx, by) = self.yard.stand(at), self.yard.stand(bay)
        metres = (abs(ax - bx) + abs(ay - by)) * self.layout.cell_size_m
        return round(metres / self.p.gantry_m_s + (self.p.block_change_s if at[0] != bay[0] else 0.0), 1)

    def _at_bay(self, crane: str, item) -> None:
        self.busy[crane] = False
        if crane in self.down:                                               # put out of service on the way: take it up again later
            self.yard_queue[crane].insert(0, item)
            return
        self._serve_yard(crane, item)
        if not self.busy[crane]:
            self._poke_yard(crane)

    def _serve_yard(self, crane: str, item) -> None:
        kind, agv, c, then, _ = item
        if kind == "stack":
            pos = self.yard.settle(c.id)
            self.say(crane, f"{crane} stacks {c.id} in {pos[0]}, tier {pos[2]}", container=c.id)
            self.log.explain("yard", [c.id, crane], f"{crane} sets {c.id} in {pos[0]}, tier {pos[2]}, {self.yard.reason[c.id]}.")
            self._lift(crane, "yard", c, ("vehicle", agv), ("slot", *pos),
                       then=lambda: self._stacked(c, pos),
                       at_handoff=lambda: (self.fleet.unloaded(agv), self.fleet.release(agv)))
        else:
            self._dig(crane, agv, c, then)

    def _dig(self, crane: str, agv: str, c, then) -> None:
        """Get a container out: rehandle whatever stands on it, one move each, then lift it onto the AGV."""
        if crane in self.down:                                   # out of service between two lifts: take it up again later
            self.yard_queue[crane].insert(0, ("retrieve", agv, c, then, self.clock.now))
            return
        above = self.yard.blockers(c.id)
        if above:
            b = self.containers[above[0]]
            to = self.yard.rehandle_target(b.id, c.id)
            frm = self.yard.lift_off(b.id)
            self.say(crane, f"{crane} rehandles {b.id}: it is on top of {c.id}", container=b.id, rehandle=True)
            wanted = f"truck {c.truck}" if c.flow == "import" and c.truck else c.vessel or "its truck"
            self.log.explain("yard", [b.id, c.id, crane], f"{crane} moves {b.id} to {to[0]}, tier {to[2]}: it stands on {c.id}, which is wanted for {wanted}. "
                             f"{b.id} is set {self.yard.reason[b.id]}.")
            self._lift(crane, "yard", b, ("slot", *frm), ("slot", *to),
                       then=lambda: (self.yard.relocate(b.id, to), b.move(self.clock.now, ("yard", *to)), self._dig(crane, agv, c, then), self._in_yard(b)))
            return
        frm = self.yard.lift_off(c.id)
        self.say(crane, f"{crane} lifts {c.id} out of {frm[0]} onto {agv}", container=c.id)
        self._lift(crane, "yard", c, ("slot", *frm), ("vehicle", agv),
                   then=lambda: (self.yard.gone(c.id), c.move(self.clock.now, ("vehicle", agv)), then(), self._out_of_yard(c)))

    def _stacked(self, c, pos) -> None:
        self.yard.set_down(c.id, pos)
        c.move(self.clock.now, ("yard", *pos))
        self._in_yard(c)

    def _out_of_yard(self, c) -> None:
        """A container has been lifted out: an export that stood under it may be called for now."""
        self.called.discard(c.id)
        for q in self.quay.values():
            if q.vessel and q.loading_started and self.to_load.get(q.vessel.id):
                self._quay_next(q)

    def _in_yard(self, c) -> None:
        """A container stands in the yard (again): anything that was waiting for it can go ahead."""
        if c.id in self.wanted:
            self.wanted.discard(c.id)
            self._want_import(c.id)
        for q in self.quay.values():                             # an export a ship is waiting for
            if q.vessel and q.loading_started and c.id in self.to_load.get(q.vessel.id, ()):
                self._quay_next(q)

    # ---- truck side: the exchange zone -------------------------------------------------------------
    def _first_reachable(self, spots, taken) -> tuple | None:
        """The furthest spot along the lane that can be reached without passing a vehicle."""
        best = None
        for spot in spots:
            if spot in taken:
                break
            best = spot
        return best

    def _truck_spot(self, truck):
        """The gate asks whether a truck can be served now: a truck spot it can reach, and for a pick-up,
        its container in the exchange stack or on an AGV standing at the exchange."""
        if truck.picks:
            s = self.x.get(truck.picks)
            if not s or s["dir"] != "out" or (s["slot"] is None and s["agv"] is None):
                return None
        spot = self._first_reachable(self.truck_spots, self.truck_at)
        if spot is not None:
            self.truck_at[spot] = truck.id
        return spot

    def _why_no_spot(self, truck) -> str:
        """Why a checked truck cannot drive in yet (the gate asks, for its reason)."""
        if truck.picks:
            s, c = self.x.get(truck.picks), self.containers[truck.picks]
            if not s or s["dir"] != "out" or (s["slot"] is None and s["agv"] is None):
                at = {"yard": "still in the yard", "vehicle": "on its way on an AGV", "crane": "on a crane's hook", "ship": "still on its ship",
                      "buffer": "still at the quay", "exchange": "on its way back to the yard"}.get(c.place[0], "not at the exchange")
                return f"its container {c.id} is {at}, and a truck is called in when its container has reached the exchange"
        return "the truck spots it could reach are taken"

    def _truck_cleared(self, truck) -> None:
        if truck.picks:
            self._want_import(truck.picks, here=True)

    def _truck_enters(self, truck, lane: int, spot) -> None:
        cell = self.gates_in[lane]
        v = Vehicle(truck.id, "truck", cell)
        if truck.drops:
            v.load = truck.drops.id
            truck.drops.move(self.clock.now, ("vehicle", truck.id))
        self.trace.append({"kind": "vehicle", "id": truck.id, "type": "truck", "at": cell, "t": self.clock.now})
        self.traffic.add(v, cell)
        self.traffic.send(v, spot, lambda v: self._truck_parked(truck, spot), on_clear=lambda v: self.gate.lane_clear(lane))

    def _truck_parked(self, truck, spot) -> None:
        truck.state, truck.times["parked"] = "parked", self.clock.now
        self.say(truck.id, f"{truck.id} parks at truck spot {spot}", quiet=True)
        if truck.picks:
            s = self.x.setdefault(truck.picks, {"dir": "out", "since": self.clock.now, "agv": None, "slot": None, "truck": None})
            s["truck"], s["spot"] = truck, spot
        else:
            c = truck.drops
            self.x[c.id] = {"dir": "in", "since": self.clock.now, "truck": truck, "spot": spot, "agv": None, "slot": None}
            self._call_agv_for_export(c)
        self._poke_xc()

    def _want_import(self, cid: str, here: bool = False) -> None:
        """A truck is coming (or has come: here) for this import: have an AGV bring it to the exchange zone."""
        c = self.containers[cid]
        if cid in self.fetching or c.place[0] == "gone":
            return
        if c.place[0] != "yard":
            self.wanted.add(cid)                      # not stacked yet: fetched as soon as it is
            return
        limit = self.p.exchange_ahead
        if limit is not None and sum(1 for s in self.x.values() if s["dir"] == "out") >= limit:
            if cid in self.x_queue:                   # the exchange holds as many as it may: wait, trucks that are here first
                self.x_queue.remove(cid)
            self.x_queue.insert(sum(1 for q in self.x_queue if q in self._here) if here else len(self.x_queue), cid)
            if here:
                self._here.add(cid)
            return
        if cid in self.x_queue:
            self.x_queue.remove(cid)
        self.fetching.add(cid)
        self.called.add(cid)
        self.x.setdefault(cid, {"dir": "out", "since": self.clock.now, "agv": None, "slot": None, "truck": None})
        stand = self.yard.stand(self.yard.bay_of[cid])
        truck = self._trucks.get(c.truck)
        self.log.explain("terminal", [cid, c.truck], f"{cid} is fetched from the yard for truck {c.truck}: "
                         + ("the truck has been checked in at the gate" if here or truck is None or truck.appointment_s is None or truck.state != "expected"
                            else f"it is booked for {_clock(truck.appointment_s)}, and an import is brought to the exchange {PRESTAGE_S / 60:.0f} minutes ahead") + ".")
        self.fleet.submit(self._job("deliver", stand, lambda agv, job: self._ask_yard("retrieve", agv, c, lambda: self._import_to_exchange(agv, c)),
                                    self.agv_spots, container=cid, purpose=f"to fetch {cid} from the yard for truck {c.truck}"))

    def _no_show(self, truck) -> None:
        """The appointment is long past: if the truck has not reached the gate, its container goes back to the yard."""
        if truck.state == "expected":
            self._send_back(truck.picks)

    def _send_back(self, cid: str) -> None:
        """An import waiting at the exchange for a truck that will not come is taken back to the yard, so the
        few places of the exchange stack are not blocked. If the truck comes after all it is fetched again."""
        s, c = self.x.get(cid), self.containers[cid]
        if cid in self.wanted or cid in self.x_queue:        # not fetched yet: forget the wish
            self.wanted.discard(cid)
            if cid in self.x_queue:
                self.x_queue.remove(cid)
            return
        if not s or s["dir"] != "out" or s["truck"] is not None:
            return
        if s["slot"] is None:                                # still on its way to the exchange stack: sent back once it stands there
            s["unwanted"] = True
            return
        s["dir"], s["since"] = "in", self.clock.now
        self.fetching.discard(cid)
        self.sent_back.append(cid)
        self.say(cid, f"{cid} goes back to the yard: its truck has not come", container=cid)
        truck = self._trucks.get(c.truck)
        self.log.explain("terminal", [cid, c.truck], f"{cid} goes back to the yard: truck {c.truck} "
                         + ("was turned away at the gate" if truck is not None and truck.state == "turned_away"
                            else f"is {self.p.no_show_s / 60:.0f} minutes overdue") + ", and the exchange stack has eight places.")
        self._call_agv_for_export(c, back=True)
        if self.p.exchange_ahead is not None:
            self._x_next()

    def _x_next(self) -> None:
        """A place at the exchange has come free: fetch the next import that was waiting for one."""
        limit = self.p.exchange_ahead
        while self.x_queue and sum(1 for s in self.x.values() if s["dir"] == "out") < limit:
            cid = self.x_queue[0]
            self._want_import(cid, here=cid in self._here)
            if self.x_queue and self.x_queue[0] == cid:        # it could not be fetched after all
                self.x_queue.pop(0)
            self._here.discard(cid)

    def _agv_spot(self, who: str) -> tuple:
        """The AGV spot at the exchange an AGV is sent to: the furthest one it can reach without passing
        another. If even the first is taken it is sent to the first and queues on the road behind it."""
        spot = self._first_reachable(self.agv_spots, self.agv_at)
        if spot is None:
            return self.agv_spots[0]
        self.agv_at[spot] = who
        return spot

    def _agv_spot_free(self, spot, who: str) -> None:
        if self.agv_at.get(spot) == who:
            del self.agv_at[spot]

    def _import_to_exchange(self, agv: str, c) -> None:
        spot = self._agv_spot(agv)
        self.fleet.carry(agv, c.id, spot, lambda a: self._at_exchange(a, c, spot))

    def _call_agv_for_export(self, c, back: bool = False) -> None:
        def at_spot(agv, job):
            self._at_exchange(agv, c, job.pickup)
        vessel = next((v for v in self.scenario.vessels if v.id == c.vessel), None)
        job = self._job("store", (0, 0), at_spot, self._stands({c.cargo}, vessel.berth if vessel else None), container=c.id,
                        purpose=f"to the exchange, to take {c.id} " + ("back to the yard" if back else f"from truck {c.truck} to the yard"))
        job.pickup = self._agv_spot(job.id)
        self.fleet.submit(job)

    def _at_exchange(self, agv: str, c, spot) -> None:
        s = self.x[c.id]
        s["agv"], s["agv_spot"] = agv, spot
        self.agv_at[spot] = agv
        self.gate.recheck()
        self._poke_xc()

    def _poke_xc(self) -> None:
        if self.busy[self.xc] or self.xc in self.down:
            return
        free_slot = next((slot for slot, held in self.xslots.items() if held is None), None)
        for cid, s in sorted(self.x.items(), key=lambda kv: (kv[1]["since"], kv[0])):
            c, truck, agv = self.containers[cid], s["truck"], s["agv"]
            leave = lambda agv=agv, spot=s.get("agv_spot"): (self.fleet.unloaded(agv), self.fleet.release(agv, on_clear=lambda v: self._agv_spot_free(spot, agv)))
            if s["dir"] == "out" and agv and truck:
                s["agv"] = None
                self.say(self.xc, f"{self.xc} loads {cid} from {agv} onto {truck.id}", container=cid)
                self._lift(self.xc, "exchange", c, ("vehicle", agv), ("vehicle", truck.id), then=lambda: self._truck_served(truck, s, c), at_handoff=leave)
            elif s["dir"] == "out" and agv and free_slot:
                s["agv"] = None
                self.xslots[free_slot] = cid
                self.say(self.xc, f"{self.xc} sets {cid} in the exchange stack: its truck is not here yet", container=cid)
                self._lift(self.xc, "exchange", c, ("vehicle", agv), ("slot", free_slot, 1, 1),
                           then=lambda: (s.__setitem__("slot", free_slot), c.move(self.clock.now, ("exchange", free_slot)), self.gate.recheck(),
                                         s.get("unwanted") and s["truck"] is None and self._send_back(cid)), at_handoff=leave)
            elif s["dir"] == "out" and s["slot"] and truck:
                slot, s["slot"] = s["slot"], None
                self.say(self.xc, f"{self.xc} loads {cid} from the exchange stack onto {truck.id}", container=cid)
                self._lift(self.xc, "exchange", c, ("slot", slot, 1, 1), ("vehicle", truck.id),
                           then=lambda: (self.xslots.__setitem__(slot, None), self._truck_served(truck, s, c)))
            elif s["dir"] == "in" and truck and agv:
                s["truck"] = None
                self.say(self.xc, f"{self.xc} lifts {cid} off {truck.id} straight onto {agv}", container=cid)
                self._lift(self.xc, "exchange", c, ("vehicle", truck.id), ("vehicle", agv),
                           then=lambda: (self._truck_served(truck, s, None), self._export_on_agv(agv, c, s)))
            elif s["dir"] == "in" and truck and free_slot:
                s["truck"] = None
                self.xslots[free_slot] = cid
                self.say(self.xc, f"{self.xc} lifts {cid} off {truck.id} into the exchange stack", container=cid)
                self._lift(self.xc, "exchange", c, ("vehicle", truck.id), ("slot", free_slot, 1, 1),
                           then=lambda: (s.__setitem__("slot", free_slot), c.move(self.clock.now, ("exchange", free_slot)), self._truck_served(truck, s, None)))
            elif s["dir"] == "in" and s["slot"] and agv:
                slot, s["slot"] = s["slot"], None
                self.say(self.xc, f"{self.xc} lifts {cid} from the exchange stack onto {agv}", container=cid)
                self._lift(self.xc, "handoff", c, ("slot", slot, 1, 1), ("vehicle", agv),
                           then=lambda: (self.xslots.__setitem__(slot, None), self._export_on_agv(agv, c, s)))
            else:
                continue
            return

    def _export_on_agv(self, agv: str, c, s) -> None:
        c.move(self.clock.now, ("vehicle", agv))
        del self.x[c.id]
        vessel = next((v for v in self.scenario.vessels if v.id == c.vessel), None)
        self._to_yard(agv, c, vessel.berth if vessel else None, on_clear=lambda v: self._agv_spot_free(s["agv_spot"], agv))

    def _truck_served(self, truck, s, c) -> None:
        """The truck has what it came for (c), or has given up what it brought (c is None): it drives out."""
        v = self.traffic.vehicles[truck.id]
        truck.state, truck.times["served"] = "served", self.clock.now
        if c is not None:
            c.move(self.clock.now, ("vehicle", truck.id))
            v.load = c.id
            del self.x[c.id]
            if self.p.exchange_ahead is not None:
                self._x_next()
        else:
            v.load = None
        spot = s["spot"]
        def way_out(g):                                           # a free gate first, then the nearer one
            moves = self.traffic.moves(spot, g)
            return (not self.traffic.free(g), moves is None, moves or 0, g)
        out = min(self.gates_out, key=way_out)

        def cleared(_):
            self.truck_at.pop(spot, None)
            self.gate.recheck()
        self.traffic.send(v, out, lambda v: self.gate.exit(truck, self.containers.get(v.load), self._truck_gone), on_clear=cleared)

    def _truck_gone(self, truck) -> None:
        v = self.traffic.vehicles[truck.id]
        if v.load:
            self.containers[v.load].move(self.clock.now, ("gone", "truck", truck.id))
        self.traffic.remove(v)
        truck.state, truck.times["gone"] = "gone", self.clock.now
        self.trace.append({"kind": "gone", "who": truck.id, "t": self.clock.now})
        self.say(truck.id, f"{truck.id} leaves through the gate" + (f" with {v.load}" if v.load else ""), container=v.load)


def _clock(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}"
