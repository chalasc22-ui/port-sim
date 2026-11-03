"""One run of the whole terminal, every module joined through the layout: a proposal for integration.

Run from the repo root:

    python sim/run_demo.py

What happens: a ship berths at B1 and QC2 and QC3 discharge four imports. Each container waits in
its crane's transfer buffer (room for 2; the crane waits when it is full), an AGV carries it to the
slot the stacking module picks, and one import goes on by truck through the exchange zone. A truck
brings one export to the exchange, an AGV takes it to QC2, and QC2 loads it.

The point is the joins, not the numbers: every arrow between two modules is one method call in
sim/modules.py, and each owner swaps their stand-in for the real thing. The event loop below is the
"scheduler" nobody owns yet; it is deliberately small so the group can decide who grows it.
"""

from __future__ import annotations

import heapq
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402

from sim.modules import AgvFleet, Gate, ShipArrival, Stacking  # noqa: E402

QC_CYCLE_S = 90.0        # one quay crane move (placeholder for the ship arrival / quay owner)
RTG_CYCLE_S = 75.0       # one yard crane move (PortSim Jacksonville)
XC_CYCLE_S = 60.0        # the exchange crane, truck side <-> AGV side
HANDOFF_S = 15.0         # container onto or off an AGV


class Terminal:
    def __init__(self, layout):
        self.layout = layout
        self.ship, self.stacking, self.agvs, self.gate = ShipArrival(), Stacking(layout), AgvFleet(layout), Gate()
        self.events, self.seq, self.log = [], itertools.count(), []
        self.buffer = {c: [] for c in layout.quay_cranes}          # containers waiting under each quay crane
        self.cap = {c: q.buffer_capacity for c, q in layout.quay_cranes.items()}
        self.waiting_trucks = {}

    # ---- the event loop ------------------------------------------------------------------
    def at(self, t, what, *args):
        heapq.heappush(self.events, (t, next(self.seq), what, args))

    def say(self, t, text):
        self.log.append((t, text))

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
        queue = {}
        for c in self.ship.discharge_list(vessel):
            queue.setdefault(c.crane, []).append(c)
        for crane, items in queue.items():
            self.at(t, self.crane_move, crane, items)

    def crane_move(self, t, crane, items):
        if not items:
            return
        if len(self.buffer[crane]) >= self.cap[crane]:          # buffer full: the crane waits
            self.at(t + 10, self.crane_move, crane, items)
            return
        c = items.pop(0)
        done = t + QC_CYCLE_S
        self.say(done, f"{crane} sets {c.id} in its buffer ({len(self.buffer[crane]) + 1}/{self.cap[crane]})")
        self.buffer[crane].append(c)
        self.at(done, self.move_by_agv, c, self.layout.quay_cranes[crane].transfer_points[1], "yard")
        self.at(done, self.crane_move, crane, items)

    # ---- AGV legs -------------------------------------------------------------------------
    def move_by_agv(self, t, c, pickup, goal):
        agv = self.agvs.dispatch(pickup, t)
        empty = self.agvs.route(self.agvs.at[agv], pickup)
        start = max(t, self.agvs.free_at[agv])
        arrive = start + self.agvs.drive_time(empty, loaded=False) + HANDOFF_S
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
        if goal == "yard":
            self.say(done + RTG_CYCLE_S, f"yard crane stacks {c.id} in {c.slot}")
        elif goal == "exchange":
            self.at(done + XC_CYCLE_S, self.ready_for_truck, c)
        else:
            self.say(done + QC_CYCLE_S, f"{c.crane} loads {c.id} onto the ship")

    def picked_up(self, t, c, agv):
        for items in self.buffer.values():
            if c in items:
                items.remove(c)                                       # off the ground: room for the next one

    # ---- truck side -----------------------------------------------------------------------
    def truck_arrives(self, t, truck):
        inside = t + self.gate.admit(truck)
        spot = self.layout.blocks["XT"].transfer_points[0]
        path = self.layout.shortest_path(self.layout.gate_cells("in")[0].pos, spot)
        at_spot = inside + self.agvs.drive_time(path, loaded=truck.drops is not None)
        self.say(inside, f"{truck.id} clears the gate")
        self.say(at_spot, f"{truck.id} parks at truck spot {spot}")
        if truck.drops:
            c = truck.drops
            self.say(at_spot + XC_CYCLE_S, f"XC1 lifts {c.id} off {truck.id} to the AGV side")
            self.at(at_spot + XC_CYCLE_S, self.truck_leaves, truck, spot)
            self.at(at_spot + XC_CYCLE_S, self.move_by_agv, c, self.layout.blocks["XA"].transfer_points[0], "ship")
        if truck.picks:
            self.waiting_trucks[truck.picks] = (truck, spot)

    def ready_for_truck(self, t, c):
        if c.id not in self.waiting_trucks:                            # the truck is not here yet
            self.at(t + 30, self.ready_for_truck, c)
            return
        truck, spot = self.waiting_trucks.pop(c.id)
        self.say(t, f"XC1 loads {c.id} onto {truck.id}")
        self.at(t, self.truck_leaves, truck, spot)

    def truck_leaves(self, t, truck, spot):
        path = self.layout.shortest_path(spot, self.layout.gate_cells("out")[0].pos)
        out = t + self.agvs.drive_time(path, loaded=truck.picks is not None)
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
