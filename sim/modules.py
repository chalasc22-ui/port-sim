"""Stand-in versions of the four other modules, with the calls the simulation makes to each.

Each class is the smallest thing that works, so sim/run_demo.py can run a whole terminal today.
Every owner replaces one class with their real module, keeping the method names and arguments
(or agreeing new ones with the group). The layout is the one real module here.

Since Nov 20 there is a fuller draft of each module (sim/ship_arrival.py, sim/agv.py, sim/stacking.py,
sim/gate.py), joined by sim/terminal.py and run with `python sim/run.py`: start from those. These
stand-ins stay for the short walkthrough in sim/run_demo.py.

    ShipArrival  ->  ship arrival owner     next_vessels(), discharge_list(vessel)
    Stacking     ->  container stacking     place(container), retrieve(container)
    AgvFleet     ->  AGV owner              dispatch(task, now), waiting_at(cells, now), route(a, b), drive_time(path, loaded)
    Gate         ->  gate owner             truck_arrivals(), admit(truck)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from terminal_layout.timing import GATE_SERVICE_S, stopping_points, travel_time


@dataclass
class Container:
    id: str
    kind: str               # "import" (ship -> yard -> truck) or "export" (truck -> yard -> ship)
    crane: str              # the quay crane that loads or discharges it
    truck: str | None = None
    slot: str | None = None
    log: list = field(default_factory=list)


@dataclass
class Vessel:
    id: str
    berth: str
    arrives_s: float


class ShipArrival:
    """REPLACE: the ship arrival module (queue at anchorage, channel, berth assignment)."""

    def next_vessels(self) -> list[Vessel]:
        return [Vessel("MV-DEMO", "B1", 0.0)]

    def discharge_list(self, vessel: Vessel) -> list[Container]:
        """Containers to take off the ship, in crane order. QC2 has three, one more than its buffer
        holds, so it has to wait for an AGV; one of QC3's goes out by truck."""
        return [
            Container("IMP-1", "import", "QC2"), Container("IMP-2", "import", "QC2"),
            Container("IMP-3", "import", "QC2"),
            Container("IMP-4", "import", "QC3", truck="TRK-A"), Container("IMP-5", "import", "QC3"),
        ]


class Stacking:
    """REPLACE: the container stacking module (which slot, which tier, rehandles)."""

    def __init__(self, layout):
        self.layout = layout
        self.height = {}

    def place(self, container: Container) -> str:
        """The ground slot an arriving container goes to. Stand-in: the lowest stack in block C2, one row
        of the block at a time, so each container goes to the next bay along the lane."""
        slots = self.layout.slots_in_block("C2")
        slot = min(slots, key=lambda s: (self.height.get(s, 0), self.layout.slot(s).attrs["row"], s))
        self.height[slot] = self.height.get(slot, 0) + 1
        container.slot = slot
        return slot

    def retrieve(self, container: Container) -> str:
        self.height[container.slot] -= 1
        return container.slot


class AgvFleet:
    """REPLACE: the AGV module (dispatch, routing, traffic, charging)."""

    def __init__(self, layout, n: int = 3):
        """Stand-in start: the AGVs stand on lanes out in the yard, except the last one, which already
        waits in QC3's lane. The demo's ship works QC2 and QC3, so the run shows both handovers: QC3
        lowers its first container straight onto that AGV, and QC2 has to use its buffer."""
        self.layout = layout
        self.stops = stopping_points(layout)
        lanes = [c.pos for c in layout.cells("lane")]
        self.free_at = {f"AGV{i + 1}": 0.0 for i in range(n)}
        self.at = {f"AGV{i + 1}": lanes[(i * 97) % len(lanes)] for i in range(n)}
        if "QC3" in layout.quay_cranes:
            self.at[f"AGV{n}"] = layout.quay_cranes["QC3"].transfer_points[1]

    def route(self, a, b):
        return self.layout.shortest_path(a, b)

    def drive_time(self, path, loaded: bool) -> float:
        return travel_time(self.layout, path, loaded=loaded, stops=self.stops)

    def dispatch(self, pickup, now: float) -> str:
        """Which vehicle takes a task. Stand-in: the one that can reach the pickup first."""
        def ready(v):
            return max(self.free_at[v], now) + self.drive_time(self.route(self.at[v], pickup), False)
        return min(self.free_at, key=ready)

    def waiting_at(self, cells, now: float) -> str | None:
        """An AGV with no task that is standing on one of these cells, or None. The crane there can
        serve it directly. Stand-in: the one that has stood there longest."""
        cells = {tuple(c) for c in cells}
        here = [v for v in self.free_at if self.free_at[v] <= now and self.at[v] in cells]
        return min(here, key=lambda v: (self.free_at[v], v)) if here else None


@dataclass
class Truck:
    id: str
    arrives_s: float
    drops: Container | None = None     # an export it brings
    picks: str | None = None           # the id of an import it collects


class Gate:
    """REPLACE: the gate module (appointments, ID check, weighing, help desk)."""

    CHECK_S = GATE_SERVICE_S          # PortSim's gate time, 30 s

    def truck_arrivals(self) -> list[Truck]:
        return [
            Truck("TRK-A", 300.0, picks="IMP-4"),
            Truck("TRK-B", 120.0, drops=Container("EXP-1", "export", "QC2", truck="TRK-B")),
        ]

    def admit(self, truck: Truck) -> float:
        """Seconds at the gate before the truck may drive in."""
        return self.CHECK_S
