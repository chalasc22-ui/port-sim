"""What every module of the integrated simulation shares: the clock, the event log and the records.

* Clock: one event queue. Events at the same time run in the order they were scheduled, so a run with the
  same inputs always gives the same result.
* EventLog: the gate owner's idea (Oct 15): every part of the port writes what it does to one log, so a
  truck or a container can be followed from the gate to the ship and nothing is lost. Beside what was
  done it keeps why: explain() notes the reason for a decision (which AGV, which stack, which truck
  first) with the numbers it was made on, about the things it concerns. The 3D model shows these when
  a vehicle, a crane, a container or a ship is clicked. Reasons change nothing in a run.
* Container, Vessel, Truck: the records the modules hand to each other.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field


class Clock:
    def __init__(self):
        self.now = 0.0
        self.fired = 0
        self._heap: list = []
        self._seq = itertools.count()

    def at(self, t: float, fn, *args) -> None:
        if t < self.now - 1e-6:
            raise ValueError(f"cannot schedule at {t:.3f} s: it is already {self.now:.3f} s")
        heapq.heappush(self._heap, (round(max(t, self.now), 3), next(self._seq), fn, args))

    def after(self, seconds: float, fn, *args) -> None:
        self.at(self.now + max(0.0, seconds), fn, *args)

    def run(self, until: float | None = None, max_events: int = 2_000_000) -> float:
        """Run events in time order until none are left (or until the given time)."""
        while self._heap and (until is None or self._heap[0][0] <= until):
            self.now, _, fn, args = heapq.heappop(self._heap)
            fn(*args)
            self.fired += 1
            if self.fired > max_events:
                raise RuntimeError(f"{max_events} events and still running at {self.now:.0f} s: an endless loop?")
        return self.now

    @property
    def pending(self) -> int:
        return len(self._heap)


@dataclass(frozen=True)
class Entry:
    t: float
    module: str       # "ship", "agv", "yard", "gate", "terminal"
    subject: str      # the vessel, truck, vehicle, crane or container the line is about
    text: str
    data: dict


@dataclass(frozen=True)
class Reason:
    t: float
    module: str
    about: tuple      # the vehicles, cranes, containers, trucks or ships it concerns
    text: str         # a sentence: what was decided and on what grounds


class EventLog:
    """One log for the whole terminal. `lines()` is the readable run; `about(x)` follows one thing;
    `why(x)` is every decision that concerned it, with its reason."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.entries: list[Entry] = []
        self.reasons: list[Reason] = []

    def add(self, module: str, subject: str, text: str, **data) -> None:
        self.entries.append(Entry(self.clock.now, module, subject, text, data))

    def explain(self, module: str, about, text: str) -> None:
        """Note why something was decided. about: the names of the things it concerns."""
        self.reasons.append(Reason(self.clock.now, module, tuple(a for a in about if a), text))

    def why(self, name: str) -> list[Reason]:
        return [r for r in self.reasons if name in r.about]

    def lines(self, module: str | None = None) -> list[tuple[float, str]]:
        return [(e.t, e.text) for e in self.entries if module is None or e.module == module]

    def about(self, name: str) -> list[Entry]:
        return [e for e in self.entries if e.subject == name or name in e.data.values()]


@dataclass
class Container:
    id: str
    flow: str                       # "import": ship -> yard -> truck.  "export": truck -> yard -> ship.
    cargo: str = "general"          # "general", "food" or "hazmat": the yard keeps food and hazmat apart (Oct 22)
    weight_t: float = 20.0
    vessel: str | None = None       # the ship it comes off, or the one it is booked on
    truck: str | None = None        # the truck that collects it, or the one that brings it
    due_s: float | None = None      # when it is expected to leave the yard; None = not known
    crane: str | None = None        # the quay crane that handles it
    place: tuple = ("outside",)     # where it is now: exactly one place at a time
    history: list = field(default_factory=list)      # (time, place): the chain of custody

    def move(self, t: float, place: tuple) -> None:
        self.place = tuple(place)
        self.history.append((round(t, 3), self.place))


@dataclass
class Vessel:
    id: str
    arrives_s: float
    draft_m: float = 10.0
    berth: str | None = None                    # the berth it is planned for; None = any free one
    discharge: list = field(default_factory=list)     # Containers to take off
    load: list = field(default_factory=list)          # ids of export containers booked on it
    comms_out: tuple | None = None              # (from, to) seconds with no radio contact, a fault to inject
    state: str = "expected"                     # expected, anchorage, inbound, berthed, outbound, gone
    times: dict = field(default_factory=dict)   # arrived, cleared, berthed, work_done, cast_off, gone


@dataclass
class Truck:
    id: str
    arrives_s: float
    plate: str = ""
    appointment_s: float | None = None          # None = no appointment
    picks: str | None = None                    # id of the import it collects
    drops: Container | None = None              # the export it brings
    fault: str | None = None                    # plate_mismatch, no_payment, weight_mismatch, damaged
    declared_t: float = 15.0                    # weight on its papers for what it carries in (tractor and trailer: tare)
    no_show: bool = False                       # booked, but it never comes
    state: str = "expected"
    times: dict = field(default_factory=dict)   # arrived, admitted, parked, served, exit, gone
    notes: list = field(default_factory=list)
