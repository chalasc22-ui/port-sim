"""One run of the whole terminal, every module joined through the layout: a proposal for integration.
This is the short walkthrough on stand-ins (six containers). The integrated terminal, with a working
draft of every module, is sim/terminal.py: `python sim/run.py`, described in docs/integration.md.

Run from the repo root:

    python sim/run_demo.py

What happens: a ship berths at B1 and QC2 and QC3 discharge five imports. AGV3 is already waiting in
QC3's lane, so QC3 lowers its first container straight onto it. No AGV has reached QC2, so QC2 sets
its containers in its transfer buffer (room for 2); it has three imports, so the buffer fills and the
crane waits until an AGV has come and the crane has lifted one onto it. AGVs carry the containers to
the slots the stacking module picks, where the yard crane lifts each one straight off its AGV. One
import goes on by truck through the exchange zone. A truck brings one export to the exchange, an AGV
takes it to QC2, and QC2 loads it.

How a container changes hands:

* A crane does every lift, and one lift at a time. An AGV has a flat deck: it cannot pick a container
  up or set one down by itself.
* An AGV standing in the crane's lane is served directly: the crane lifts straight between it and the
  ship, the stack or the truck. An AGV that brings a container waits for the crane, and leaves once
  its container is clear of the deck (HANDOFF_S into the lift).
* With no AGV in its lane, a crane that has a container to hand over sets it in the buffer beside the
  lane and an AGV is called. When that AGV arrives, the crane lifts the container onto it (HANDOFF_S).
* A full buffer takes no more: the crane waits. With a buffer of 0 the crane waits for an AGV instead.

The point is the joins, not the numbers: every arrow between two modules is one method call in
sim/modules.py, and each owner swaps their stand-in for the real thing. The event loop below is the
"scheduler" nobody owns yet; it is deliberately small so the group can decide who grows it. Vehicles
do not block each other here (that is the AGV module's job); times are PortSim's
(terminal_layout.timing).

Terminal.trace records every drive and every lift with its times, so the run can be replayed:
tools/build_viewer_replay.py turns it into the file the 3D viewer plays.
"""

from __future__ import annotations

import heapq
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402
from terminal_layout.timing import HANDOFF_S, QUAY_SERVICE_S, YARD_SERVICE_S  # noqa: E402

from sim.modules import AgvFleet, Gate, ShipArrival, Stacking  # noqa: E402

QC_CYCLE_S = QUAY_SERVICE_S    # one quay crane move: 60 s
RTG_CYCLE_S = YARD_SERVICE_S   # one yard crane move: 75 s
XC_CYCLE_S = YARD_SERVICE_S    # the exchange crane, truck side <-> AGV side: a yard crane move until the gate owner sets one


class Terminal:
    def __init__(self, layout):
        self.layout = layout
        self.ship, self.stacking, self.agvs, self.gate = ShipArrival(), Stacking(layout), AgvFleet(layout), Gate()
        self.events, self.seq, self.log = [], itertools.count(), []
        self.buffer = {c: [] for c in layout.quay_cranes}          # containers on the ground beside each quay crane's lane
        self.cap = {c: q.buffer_capacity for c, q in layout.quay_cranes.items()}
        self.crane_waiting = {}                                      # crane -> (since, containers still to move)
        self.calendar = {}                                           # crane -> [(from, to)], the lifts it is booked for
        self.xc = layout.blocks["XT"].crane                          # the exchange crane
        self.agv_side = layout.blocks["XA"].transfer_points          # where AGVs stand at the exchange
        self.truck_spot = dict.fromkeys(layout.blocks["XT"].transfer_points)   # spot -> truck id, or None
        self.waiting_trucks = {}                                     # container id -> (truck, spot) parked and waiting for it
        self.in_exchange = set()                                     # container ids in the exchange stack, truck not here yet
        self.trace = []                                              # what moved where and when, for a replay
        for agv, cell in self.agvs.at.items():
            self.rec("vehicle", id=agv, type="agv", at=cell, t=0.0)

    # ---- the event loop ------------------------------------------------------------------
    def at(self, t, what, *args):
        heapq.heappush(self.events, (t, next(self.seq), what, args))

    def say(self, t, text):
        self.log.append((t, text))

    def rec(self, kind, **fields):
        """One trace record. Kinds: vehicle (id, type, at, t), vessel (id, berth, t), drive (who, t0, t1,
        path, loaded, what) and lift (by, what, t0, t1, src, dst). A lift's `by` is the crane that does
        it. Places are ("ship", crane), ("ground", cell), ("vehicle", id), ("slot", slot id) and
        ("stack", block id)."""
        self.trace.append({"kind": kind, **fields})

    def run(self):
        for vessel in self.ship.next_vessels():
            self.at(vessel.arrives_s, self.berth, vessel)
        for truck in self.gate.truck_arrivals():
            self.at(truck.arrives_s, self.truck_arrives, truck)
        while self.events:
            t, _, what, args = heapq.heappop(self.events)
            what(t, *args)
        return self.log

    # ---- cranes ---------------------------------------------------------------------------
    def book(self, crane, t, seconds):
        """When this crane can start a lift of that length, at t or later. That time is then taken: a
        crane does one lift at a time, in the order the work is asked for."""
        taken = self.calendar.setdefault(crane, [])
        start = t
        for a, b in sorted(taken):
            if start + seconds <= a:
                break
            start = max(start, b)
        taken.append((start, start + seconds))
        return start

    def crane_at(self, cell):
        """The crane that serves a transfer point: its quay crane, or its block's yard or exchange crane."""
        serves = self.layout.cell(*cell).attrs["serves"]
        return serves.get("crane") or self.layout.blocks[serves["block"]].crane

    # ---- ship side ------------------------------------------------------------------------
    def berth(self, t, vessel):
        self.say(t, f"{vessel.id} berths at {vessel.berth}")
        self.rec("vessel", id=vessel.id, berth=vessel.berth, t=t)
        queue = {}
        for c in self.ship.discharge_list(vessel):
            queue.setdefault(c.crane, []).append(c)
        for crane, items in queue.items():
            self.at(t, self.crane_move, crane, items)

    def crane_move(self, t, crane, items):
        """The crane's next container off the ship: straight onto an AGV waiting in one of its lanes, or
        into its buffer when no AGV is there."""
        if not items:
            return
        qc = self.layout.quay_cranes[crane]
        lane = qc.transfer_points[1]
        agv = self.agvs.waiting_at(qc.transfer_points, t)
        if agv is None and self.cap[crane] == 0:                 # no AGV and no buffer: call one, lift when it is here
            self.fetch(t, items.pop(0), lane, "yard", lift_s=QC_CYCLE_S, then=(crane, items))
            return
        if agv is None and len(self.buffer[crane]) >= self.cap[crane]:      # no AGV and a full buffer: the crane waits
            self.crane_waiting[crane] = (t, items)
            self.say(t, f"{crane} waits: its buffer is full ({len(self.buffer[crane])}/{self.cap[crane]})")
            return
        c = items.pop(0)
        start = self.book(crane, t, QC_CYCLE_S)
        done = start + QC_CYCLE_S
        if agv:
            self.say(done, f"{crane} sets {c.id} straight onto {agv}")
            self.rec("lift", by=crane, what=c.id, t0=start, t1=done, src=("ship", crane), dst=("vehicle", agv))
            self.carry(done, c, agv, self.agvs.at[agv], "yard")
        else:
            self.say(done, f"{crane} sets {c.id} in its buffer ({len(self.buffer[crane]) + 1}/{self.cap[crane]})")
            self.buffer[crane].append(c)
            self.rec("lift", by=crane, what=c.id, t0=start, t1=done, src=("ship", crane), dst=("ground", lane))
            self.at(done, self.fetch, c, lane, "yard")
        self.at(done, self.crane_move, crane, items)

    # ---- AGV legs -------------------------------------------------------------------------
    def fetch(self, t, c, pickup, goal, lift_s=HANDOFF_S, then=None):
        """An AGV is called to pickup, where the crane lifts c onto it: from the buffer (HANDOFF_S), or
        straight from the ship in a whole crane move when the crane has no buffer (then: that crane and
        its remaining containers). The AGV then carries c to goal."""
        agv = self.agvs.dispatch(pickup, t)
        empty = self.agvs.route(self.agvs.at[agv], pickup)
        start = max(t, self.agvs.free_at[agv])
        arrive = start + self.agvs.drive_time(empty, loaded=False)
        self.rec("drive", who=agv, t0=start, t1=arrive, path=empty, loaded=False, what=None)
        crane = self.crane_at(pickup)
        lift = self.book(crane, arrive, lift_s)                      # the AGV waits if the crane is busy
        on = lift + lift_s
        if then:
            self.say(on, f"{crane} sets {c.id} straight onto {agv}")
            self.rec("lift", by=crane, what=c.id, t0=lift, t1=on, src=("ship", crane), dst=("vehicle", agv))
            self.at(on, self.crane_move, *then)
        else:
            self.say(on, f"{crane} lifts {c.id} from its buffer onto {agv}")
            self.rec("lift", by=crane, what=c.id, t0=lift, t1=on, src=("ground", pickup), dst=("vehicle", agv))
            self.at(on, self.off_the_ground, c)
        self.carry(on, c, agv, pickup, goal, len(empty) - 1)

    def carry(self, t, c, agv, pickup, goal, moves=0):
        """The AGV, loaded with c at time t, drives to where c goes, and the crane there lifts it
        straight off. moves: the cells the AGV drove empty to fetch c."""
        if goal == "yard" and c.truck:                                # an import that leaves by truck
            goal = "exchange"
        if goal == "yard":
            slot = self.stacking.place(c)
            drop, cycle = self.layout.transfer_point_for(slot).pos, RTG_CYCLE_S
            where = f"block {self.layout.slot(slot).attrs['block']}"
        elif goal == "exchange":
            drop, cycle, where = self.agv_side[0], XC_CYCLE_S, "the exchange zone"
        else:                                                          # an export to its quay crane
            drop, cycle, where = self.layout.quay_cranes[c.crane].transfer_points[1], QC_CYCLE_S, c.crane
        path = self.agvs.route(pickup, drop)
        there = t + self.agvs.drive_time(path, loaded=True)
        crane = self.crane_at(drop)
        lift = self.book(crane, there, cycle)                        # the AGV waits if the crane is busy
        self.agvs.at[agv], self.agvs.free_at[agv] = drop, lift + HANDOFF_S      # it leaves once its container is clear
        self.rec("drive", who=agv, t0=t, t1=there, path=path, loaded=True, what=c.id)
        self.say(there, f"{agv} brings {c.id} to {where} ({moves + len(path) - 1} moves)")
        if goal == "yard":
            self.say(lift + cycle, f"{crane} stacks {c.id} in {c.slot}")
            self.rec("lift", by=crane, what=c.id, t0=lift, t1=lift + cycle, src=("vehicle", agv), dst=("slot", c.slot))
        elif goal == "exchange":
            self.at(lift, self.off_the_agv_for_a_truck, c, agv)
        else:
            self.say(lift + cycle, f"{crane} loads {c.id} onto the ship")
            self.rec("lift", by=crane, what=c.id, t0=lift, t1=lift + cycle, src=("vehicle", agv), dst=("ship", crane))

    def off_the_ground(self, t, c):
        for crane, items in self.buffer.items():
            if c in items:
                items.remove(c)                                       # room in the buffer for the next one
                if crane in self.crane_waiting:                       # the crane was waiting for this
                    since, rest = self.crane_waiting.pop(crane)
                    self.say(t, f"{crane} goes on after waiting {t - since:.0f} s")
                    self.at(t, self.crane_move, crane, rest)

    # ---- truck side -----------------------------------------------------------------------
    def truck_arrives(self, t, truck):
        inside = t + self.gate.admit(truck)
        self.rec("vehicle", id=truck.id, type="truck", at=self.layout.gate_cells("in")[0].pos, t=t)
        self.say(inside, f"{truck.id} clears the gate")
        self.at(inside, self.truck_to_spot, truck)

    def truck_to_spot(self, t, truck):
        free = [p for p, who in self.truck_spot.items() if who is None]
        if not free:                                                   # every truck spot taken: wait by the gate
            self.at(t + 30, self.truck_to_spot, truck)
            return
        spot = free[0]
        self.truck_spot[spot] = truck.id
        path = self.layout.shortest_path(self.layout.gate_cells("in")[0].pos, spot)
        parked = t + self.agvs.drive_time(path, loaded=truck.drops is not None)
        self.rec("drive", who=truck.id, t0=t, t1=parked, path=path, loaded=truck.drops is not None,
                 what=truck.drops.id if truck.drops else None)
        self.at(parked, self.truck_parked, truck, spot)

    def truck_parked(self, t, truck, spot):
        self.say(t, f"{truck.id} parks at truck spot {spot}")
        if truck.drops:                                                # an export: the exchange crane lifts it off
            c = truck.drops
            start = self.book(self.xc, t, XC_CYCLE_S)
            done = start + XC_CYCLE_S
            agv = self.agvs.waiting_at(self.agv_side, t)
            if agv:
                self.say(done, f"{self.xc} lifts {c.id} off {truck.id} straight onto {agv}")
                self.rec("lift", by=self.xc, what=c.id, t0=start, t1=done, src=("vehicle", truck.id), dst=("vehicle", agv))
                self.carry(done, c, agv, self.agvs.at[agv], "ship")
            else:                                                      # no AGV there: onto the ground on the AGV side
                self.say(done, f"{self.xc} lifts {c.id} off {truck.id} to the AGV side")
                self.rec("lift", by=self.xc, what=c.id, t0=start, t1=done, src=("vehicle", truck.id), dst=("ground", self.agv_side[0]))
                self.at(done, self.fetch, c, self.agv_side[0], "ship")
            self.at(done, self.truck_leaves, truck, spot)
        if truck.picks in self.in_exchange:                            # its container is in the stack: one more crane move
            self.in_exchange.discard(truck.picks)
            self.load_from_stack(t, truck.picks, truck, spot)
        elif truck.picks:
            self.waiting_trucks[truck.picks] = (truck, spot)              # here first: loaded straight off the AGV

    def off_the_agv_for_a_truck(self, t, c, agv):
        """The exchange crane starts to lift c off its AGV: straight onto its truck if that is parked,
        into the exchange stack if not."""
        done = t + XC_CYCLE_S
        if c.id in self.waiting_trucks:
            truck, spot = self.waiting_trucks.pop(c.id)
            self.say(done, f"{self.xc} loads {c.id} onto {truck.id}")
            self.rec("lift", by=self.xc, what=c.id, t0=t, t1=done, src=("vehicle", agv), dst=("vehicle", truck.id))
            self.at(done, self.truck_leaves, truck, spot)
        else:
            self.say(done, f"{self.xc} sets {c.id} in the exchange stack")
            self.rec("lift", by=self.xc, what=c.id, t0=t, t1=done, src=("vehicle", agv), dst=("stack", "XT"))
            self.at(done, self.in_the_stack, c)

    def in_the_stack(self, t, c):
        if c.id in self.waiting_trucks:                               # its truck parked while the crane was stacking it
            self.load_from_stack(t, c.id, *self.waiting_trucks.pop(c.id))
        else:
            self.in_exchange.add(c.id)

    def load_from_stack(self, t, cid, truck, spot):
        start = self.book(self.xc, t, XC_CYCLE_S)
        done = start + XC_CYCLE_S
        self.say(done, f"{self.xc} loads {cid} onto {truck.id}")
        self.rec("lift", by=self.xc, what=cid, t0=start, t1=done, src=("stack", "XT"), dst=("vehicle", truck.id))
        self.at(done, self.truck_leaves, truck, spot)

    def truck_leaves(self, t, truck, spot):
        self.truck_spot[spot] = None
        path = self.layout.shortest_path(spot, self.layout.gate_cells("out")[0].pos)
        out = t + self.agvs.drive_time(path, loaded=truck.picks is not None)
        self.rec("drive", who=truck.id, t0=t, t1=out, path=path, loaded=truck.picks is not None, what=truck.picks)
        self.say(out, f"{truck.id} leaves through the gate")


def main() -> int:
    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    log = Terminal(layout).run()
    for t, text in sorted(log, key=lambda e: e[0]):
        print(f"{int(t // 60):3d}:{int(t % 60):02d}  {text}")
    print(f"\n{len(log)} events, last at {max(t for t, _ in log) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
