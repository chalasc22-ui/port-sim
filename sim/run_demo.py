"""One run of the whole terminal, every module joined through the layout: a proposal for integration.

Run from the repo root:

    python sim/run_demo.py

What happens: a ship berths at B1 and QC2 and QC3 discharge five imports. Each container waits in
its crane's transfer buffer (room for 2), an AGV carries it to the slot the stacking module picks,
and one import goes on by truck through the exchange zone. QC2 has three imports, so its buffer
fills and the crane waits until an AGV takes one. A truck brings one export to the exchange, an AGV
takes it to QC2, and QC2 loads it.

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
        self.buffer = {c: [] for c in layout.quay_cranes}          # containers waiting under each quay crane
        self.cap = {c: q.buffer_capacity for c, q in layout.quay_cranes.items()}
        self.crane_waiting = {}                                      # crane -> (since, containers still to move)
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
        path, loaded, what) and lift (by, what, t0, t1, src, dst). A lift's `by` is a crane id, or None
        for a handoff between a vehicle and the ground. Places are ("ship", crane), ("ground", cell),
        ("vehicle", id), ("slot", slot id) and ("stack", block id)."""
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
        if not items:
            return
        lane = self.layout.quay_cranes[crane].transfer_points[1]
        if self.cap[crane] == 0:                                 # no buffer: the crane lifts straight onto an AGV
            self.move_by_agv(t, items.pop(0), lane, "yard", lift_s=QC_CYCLE_S, then=(crane, items))
            return
        if len(self.buffer[crane]) >= self.cap[crane]:          # buffer full: the crane waits for an AGV
            self.crane_waiting[crane] = (t, items)
            self.say(t, f"{crane} waits: its buffer is full ({len(self.buffer[crane])}/{self.cap[crane]})")
            return
        c = items.pop(0)
        done = t + QC_CYCLE_S
        self.say(done, f"{crane} sets {c.id} in its buffer ({len(self.buffer[crane]) + 1}/{self.cap[crane]})")
        self.buffer[crane].append(c)
        self.rec("lift", by=crane, what=c.id, t0=t, t1=done, src=("ship", crane), dst=("ground", lane))
        self.at(done, self.move_by_agv, c, lane, "yard")
        self.at(done, self.crane_move, crane, items)

    # ---- AGV legs -------------------------------------------------------------------------
    def move_by_agv(self, t, c, pickup, goal, lift_s=HANDOFF_S, then=None):
        """An AGV fetches c at pickup and takes it to goal. lift_s: the time to get c onto the AGV once
        it is there (a handoff from the ground, or a whole crane move when there is no buffer). then:
        (crane, its remaining containers) for a crane that was lifting straight onto this AGV."""
        agv = self.agvs.dispatch(pickup, t)
        empty = self.agvs.route(self.agvs.at[agv], pickup)
        start = max(t, self.agvs.free_at[agv])
        arrive = start + self.agvs.drive_time(empty, loaded=False) + lift_s
        self.rec("drive", who=agv, t0=start, t1=arrive - lift_s, path=empty, loaded=False, what=None)
        if then:                                                       # the crane was busy until now
            crane, rest = then
            self.say(arrive, f"{crane} sets {c.id} straight onto {agv} (no buffer)")
            self.rec("lift", by=crane, what=c.id, t0=arrive - lift_s, t1=arrive, src=("ship", crane), dst=("vehicle", agv))
            self.at(arrive, self.crane_move, crane, rest)
        else:
            self.rec("lift", by=None, what=c.id, t0=arrive - lift_s, t1=arrive, src=("ground", pickup), dst=("vehicle", agv))
        if goal == "yard" and c.truck:                                # an import that leaves by truck
            goal = "exchange"
        if goal == "yard":
            slot = self.stacking.place(c)
            drop = self.layout.transfer_point_for(slot).pos
            where = f"slot {slot}"
        elif goal == "exchange":
            drop = self.layout.blocks["XA"].transfer_points[0]
            where = "the exchange zone"
        else:                                                          # an export to its quay crane
            drop = self.layout.quay_cranes[c.crane].transfer_points[1]
            where = c.crane
        loaded = self.agvs.route(pickup, drop)
        done = arrive + self.agvs.drive_time(loaded, loaded=True) + HANDOFF_S
        self.agvs.at[agv], self.agvs.free_at[agv] = drop, done
        self.at(arrive, self.picked_up, c, agv)
        self.say(arrive, f"{agv} picks up {c.id}")
        self.say(done, f"{agv} delivers {c.id} to {where} ({len(empty) + len(loaded) - 2} moves)")
        self.rec("drive", who=agv, t0=arrive, t1=done - HANDOFF_S, path=loaded, loaded=True, what=c.id)
        self.rec("lift", by=None, what=c.id, t0=done - HANDOFF_S, t1=done, src=("vehicle", agv), dst=("ground", drop))
        if goal == "yard":
            rtg = self.layout.blocks[self.layout.slot(c.slot).attrs["block"]].crane
            self.say(done + RTG_CYCLE_S, f"yard crane stacks {c.id} in {c.slot}")
            self.rec("lift", by=rtg, what=c.id, t0=done, t1=done + RTG_CYCLE_S, src=("ground", drop), dst=("slot", c.slot))
        elif goal == "exchange":
            self.at(done + XC_CYCLE_S, self.ready_for_truck, c, drop)
        else:
            self.say(done + QC_CYCLE_S, f"{c.crane} loads {c.id} onto the ship")
            self.rec("lift", by=c.crane, what=c.id, t0=done, t1=done + QC_CYCLE_S, src=("ground", drop), dst=("ship", c.crane))

    def picked_up(self, t, c, agv):
        for crane, items in self.buffer.items():
            if c in items:
                items.remove(c)                                       # off the ground: room for the next one
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
        if truck.drops:
            c, agv_side = truck.drops, self.layout.blocks["XA"].transfer_points[0]
            self.say(t + XC_CYCLE_S, f"XC1 lifts {c.id} off {truck.id} to the AGV side")
            self.rec("lift", by="XC1", what=c.id, t0=t, t1=t + XC_CYCLE_S, src=("vehicle", truck.id), dst=("ground", agv_side))
            self.at(t + XC_CYCLE_S, self.truck_leaves, truck, spot)
            self.at(t + XC_CYCLE_S, self.move_by_agv, c, agv_side, "ship")
        if truck.picks in self.in_exchange:                            # its container is in the stack: one more crane move
            self.in_exchange.discard(truck.picks)
            self.say(t + XC_CYCLE_S, f"XC1 loads {truck.picks} onto {truck.id}")
            self.rec("lift", by="XC1", what=truck.picks, t0=t, t1=t + XC_CYCLE_S, src=("stack", "XT"), dst=("vehicle", truck.id))
            self.at(t + XC_CYCLE_S, self.truck_leaves, truck, spot)
        elif truck.picks:
            self.waiting_trucks[truck.picks] = (truck, spot)              # here first: XC1 can load it straight away

    def ready_for_truck(self, t, c, agv_side):
        """XC1 has taken c off the AGV side: onto its truck if that is parked, into the stack if not."""
        if c.id in self.waiting_trucks:
            truck, spot = self.waiting_trucks.pop(c.id)
            self.say(t, f"XC1 loads {c.id} onto {truck.id}")
            self.rec("lift", by="XC1", what=c.id, t0=t - XC_CYCLE_S, t1=t, src=("ground", agv_side), dst=("vehicle", truck.id))
            self.at(t, self.truck_leaves, truck, spot)
        else:
            self.in_exchange.add(c.id)
            self.say(t, f"XC1 sets {c.id} in the exchange stack")
            self.rec("lift", by="XC1", what=c.id, t0=t - XC_CYCLE_S, t1=t, src=("ground", agv_side), dst=("stack", "XT"))

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
