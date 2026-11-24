"""The AGV module (the group's "traffic" module): how vehicles move, who takes which job, and charging.

Written from what the AGV owner described in the check-ins, for the owner to replace or adopt. Where a rule
comes from is noted beside it. Numbers are PortSim's (the course's reference simulator) unless marked.

Traffic: how any vehicle (AGV or truck) moves on the layout
    * A vehicle moves cell to cell along layout.neighbors(), the only movement rule (Sep 24).
    * A move is admitted one road segment at a time, PortSim's rule (section 3.2): before a vehicle leaves
      the cell it stands on it reserves every cell up to and including the next junction (or its
      destination), and keeps the cell it leaves until it arrives. A cell another vehicle stands on or has
      reserved is never given to it. So two vehicles are never in one cell, cannot swap head-on, and
      never enter an occupied service position.
    * If the stretch ahead is only partly free, the vehicle drives up to the vehicle in front and stops
      there (it queues in the lane behind a vehicle being served, as trucks do in an RTG lane). If the
      next cell is taken it waits.
    * Each stretch is driven from rest to rest with terminal_layout.timing.segment_time. A vehicle stops
      at junctions and at its destination; it does not stop at a transfer point, a charging station or a
      gate cell it only passes. So an unblocked trip takes terminal_layout.timing.travel_time with
      stops=traffic.stops (the junctions). (travel_time's own default also stops at every transfer
      point passed, which made a trip along a service lane a stop every 15 m.)
    * A vehicle drives past the transfer lanes of the quay cranes on the bypass lane and enters a lane
      only at its own crane. If a standing vehicle blocks the way and a short way round exists, it takes it.
    * Waits are counted by cell, and a wait longer than STALL_S is reported with what blocked it (Dr. Wang:
      "detection and reporting of ... prolonged lack of progress").
    * Disruptions, for what-if runs. break_down(vehicle, seconds): the vehicle stops where it stands (or
      where the move it is making ends) and holds its cell until it is repaired on the spot (the AGV
      owner, Oct 22: the others carry on and a person recovers it). A vehicle whose way it blocks turns
      off early if a way round costs less time than waiting for the repair. close_road(cells, seconds): nobody
      is routed over those cells; a vehicle on its way replans at its next stop, and one with no way
      left waits, reported, until the road opens.

AgvFleet: who takes a job, and energy
    * The scheduler submits a Job ("an AGV is needed at this cell"); the fleet picks the AGV by policy:
        nearest_feasible  the closest free AGV with enough charge (Oct 15 rule 1, Oct 22)
        longest_idle      the free AGV that has waited longest for work (Oct 22)
        balanced          drive time, less credit for idle time and state of charge (Oct 15 rule 2)
      Jobs are served oldest first, so the container that has waited longest goes first.
      Two more rules choose the job as well as the AGV, of every (waiting job, free AGV) pair the fleet could
      start (sim/dispatch_learned.py):
        nearest_job       the pair with the shortest drive to the pickup
        learned           the pair a Q-function learned in the simulator scores best (Oct 22: the owner's idea)
      Under every rule a job for a truck goes before a job for a ship: that order is the scheduler's. Under
      these two a job that has waited ten minutes (OVERDUE_S) is taken before any other, so that none is
      passed over for ever.
    * Energy is PortSim's (appendix C.1): 40 kWh, start at 75 percent, 0.003 kWh per metre empty, 1.35
      times that loaded, 0.6 kW standing, 100 kW charging up to 90 percent.
    * An AGV takes a job only if its charge covers the drive to the pickup, the loaded drive to the
      furthest place the container may have to go, and the drive home to its charger afterwards
      (Oct 22: "nearest feasible"; a loaded AGV must not run flat).
    * Every AGV has a home charging station, half the fleet in each depot: the two regions the owner
      asked for (Oct 22, Oct 29), which can be logical rather than fenced, as the owner said. An AGV with no job
      drives home and charges while it waits, so no idle AGV stands on a road, and the nearest-AGV rule
      keeps each one working near its own depot.

Two things change with the calibrated numbers (sim/params.py), and only with them:
    * Rolling. A vehicle does not stop at every junction, and it does not hold a whole stretch of road.
      It claims the road one cell at a time, at the last moment it could still brake before that cell,
      keeps its speed while it gets the cells, and brakes when it does not; a cell is free again when the
      vehicle has left it. So a vehicle holds its braking distance and no more, vehicles follow each
      other at that distance, and a junction crossed straight costs no stop. It stops to turn. (The
      reference reserves a stretch up to the next junction and drives it from rest to rest: with long
      stretches that lets one vehicle at a time onto 300 m of road, which no real fleet could afford.)
    * A pooled fleet, for more AGVs than charging stations. No AGV owns a station. An AGV with no job
      stands by on the quay apron, on the cells between the cranes' lanes, which other vehicles keep off;
      one whose charge is below Energy.charge_below takes a free station instead. A charged AGV gives
      its station up when another needs it.

Not built here (the owner's ideas, still the owner's to add): towing a dead AGV to a recovery bay, battery swap,
weather and degradation. A policy is one function in POLICIES, or in PAIR_POLICIES if it chooses the job too.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field

from terminal_layout.timing import SPEED_EMPTY_M_S, SPEED_LOADED_M_S, segment_time

from sim import dispatch_learned
from sim.dispatch_learned import PAIR_POLICIES
from sim.params import REFERENCE, Energy, Params

STALL_S = 600.0          # a wait this long is reported (an assumption: ten minutes)
REPAIR_S_PER_MOVE = 5.0  # a way round a broken-down vehicle is taken if its extra moves, at this many seconds each, beat the repair
DETOUR_MOVES = 12        # a way round a standing vehicle is taken if it adds at most this many moves (assumption)
OVERDUE_S = STALL_S      # a rule that may choose the job must take one that has waited this long before any other (assumption)


__all__ = ["AgvFleet", "Energy", "Job", "PAIR_POLICIES", "POLICIES", "Traffic", "Vehicle"]      # Energy lives in sim.params now


def _pass_time(d: float, v: float, a: float) -> float:
    """Seconds from rest until a vehicle that does not brake has driven d metres."""
    return math.sqrt(2.0 * d / a) if d <= v * v / (2.0 * a) else d / v + v / (2.0 * a)


def _brake_time(d: float, v: float, a: float) -> float:
    """Seconds from rest until a vehicle must begin to brake to stand still after d metres."""
    return math.sqrt(d / a) if d < v * v / a else d / v


class Vehicle:
    def __init__(self, vid: str, kind: str, cell, n: int = 0):
        self.id, self.kind, self.n = vid, kind, n
        self.cell = tuple(cell)          # the stopping cell it stands on (or left last)
        self.route: list = []            # cells still to drive
        self.dest = None
        self.on_arrive = None
        self.on_clear = None             # called once, when it has left the cell it stood on
        self.next = None                 # a new destination to take up at the next stopping point
        self.moving = False
        self.seg: list = []
        self.load: str | None = None     # id of the container on it
        self.leg = None
        self.wait_since: float | None = None
        self.wait_for = None
        self.wait_by = None              # who held the cell it waits for, and since when that one did
        self.wait_by_since = 0.0
        self.waited_s = 0.0
        self.moves = 0
        self.loaded_moves = 0
        self.battery: float | None = None
        self.energy_t = 0.0
        self.charging = False
        self.used_kwh = 0.0
        self.state = "idle"              # AGVs: parked, homing, idle, to_pickup, at_pickup, to_drop, at_drop
        self.job = None
        self.idle_since = 0.0
        self.home = None                 # its charging station
        self.jobs_done = 0
        self.broken_until: float | None = None       # broken down until this time (a disruption)
        self.run = None                  # rolling only: the stretch it is driving without a stop
        self.epoch = 0
        self.parked_since: float | None = None       # AGVs: since when it stands parked, and the totals so far
        self.parked_s = 0.0
        self.standby_s = 0.0             # of that, the time not on a charger

    @property
    def speed(self) -> float:
        return SPEED_LOADED_M_S if self.load else SPEED_EMPTY_M_S


class Traffic:
    def __init__(self, clock, layout, log=None, energy: Energy | None = None, params: Params | None = None):
        self.clock, self.layout, self.log, self.energy = clock, layout, log, energy
        self.p = params or REFERENCE
        self.cell_m = layout.cell_size_m
        roads = [c.pos for c in layout.cells() if layout.is_traversable(*c.pos)]
        self.adj = {p: layout.neighbors(*p) for p in roads}
        ways_in = dict.fromkeys(roads, 0)
        for p in roads:
            for q in self.adj[p]:
                ways_in[q] += 1
        self.stops = frozenset(p for p in roads if len(self.adj[p]) != 1 or ways_in[p] != 1)      # the junctions
        self.quay_lanes = frozenset(p for q in layout.quay_cranes.values() for p in q.transfer_points)
        self.vehicles: dict[str, Vehicle] = {}
        self.held: dict[tuple, str] = {}             # cell -> the vehicle standing on it or holding it for a move
        self.waiting: dict[str, float] = {}          # vehicle -> since when it waits for a cell
        self.legs: list[dict] = []                   # every stretch driven without a stop, for the replay
        self.segments: list[tuple] = []              # (vehicle, t0, t1, cells): every admitted move, for the audit
        self.rolled: list[bool] = []                 # for each of them: True if the vehicle did not stop at its end
        self.waits: list[tuple] = []                 # (vehicle, t0, t1, cell, held by): every wait for a cell, and who held it
        self.keep_off: frozenset = frozenset()       # stand-by cells: nobody is routed over them (a pooled fleet)
        self._from: dict[tuple, dict] = {}           # cell -> moves from it to every cell, worked out once
        self._look = False                           # rolling: a look at everyone waiting is due
        self.soc_log: list[tuple] = []               # (vehicle, t, kWh): the charge each time it is worked out, for the replay
        self.spawned: dict[str, tuple] = {}          # vehicle -> (time, cell) it came onto the map
        self.removed: dict[str, float] = {}
        self.wait_by_cell: dict[tuple, float] = defaultdict(float)
        self.stalls: list[dict] = []
        self.refused = 0
        self._moves: dict[tuple, int | None] = {}
        self.closed: set = set()                     # road cells shut for now: nobody is routed over them
        self._shut: dict[tuple, int] = {}            # cell -> how many closures cover it
        self.no_way: dict[str, float] = {}           # vehicle -> since when no open road leads to its destination
        self.breakdowns: list[dict] = []             # {"vehicle", "t0", "t1", "at"}, for the replay
        self._broken: set = set()                    # the vehicles broken down right now
        self.closures: list[dict] = []               # {"cells", "t0", "t1"}
        self.on_change = lambda: None                # called when a road opens or a vehicle is repaired: jobs may fit again

    # ---- the map ------------------------------------------------------------------------------
    def route(self, a, b, avoid=frozenset()) -> list | None:
        """Fewest moves from a to b, in layout.neighbors() order, keeping off other cranes' transfer lanes
        and off the cells in `avoid` where that is possible."""
        a, b = tuple(a), tuple(b)
        shut = self.closed - {a}                     # a vehicle standing on a closed cell may drive off it
        if b in shut:
            return None
        tries = (set(avoid) | self.quay_lanes, set(avoid), set())
        if self.keep_off:
            tries = (set(avoid) | self.quay_lanes | self.keep_off, *tries)
        for closed in tries:
            path = self._bfs(a, b, (closed - {a, b}) | shut)
            if path:
                return path
        return None

    def _bfs(self, a, b, closed) -> list | None:
        prev, queue = {a: None}, deque([a])
        while queue:
            u = queue.popleft()
            if u == b:
                out = []
                while u is not None:
                    out.append(u)
                    u = prev[u]
                return out[::-1]
            for v in self.adj[u]:
                if v not in prev and v not in closed:
                    prev[v] = u
                    queue.append(v)
        return None

    def moves(self, a, b) -> int | None:
        """Moves on the route from a to b (None: no road). One search from a serves every b."""
        a, b = tuple(a), tuple(b)
        key = (a, b)
        if key not in self._moves:
            n = self._quick(a, b)
            if n is None:                                # not reachable while keeping off the lanes: ask route()
                path = self.route(a, b)
                n = None if path is None else len(path) - 1
            self._moves[key] = n
        return self._moves[key]

    def _quick(self, a, b) -> int | None:
        """The length of route(a, b)'s first choice: the shortest way that keeps off other cranes' lanes (and
        the stand-by cells). None if there is no such way."""
        shut = self.closed - {a}
        if b in shut:
            return None
        kept = self.quay_lanes | self.keep_off
        if a not in self._from:
            closed = (kept - {a}) | shut
            dist, queue = {a: 0}, deque([a])
            while queue:
                u = queue.popleft()
                for w in self.adj[u]:
                    if w not in dist and w not in closed:
                        dist[w] = dist[u] + 1
                        queue.append(w)
            self._from[a] = dist
            if not hasattr(self, "_into"):
                into: dict[tuple, list] = defaultdict(list)
                for u, ws in self.adj.items():
                    for w in ws:
                        into[w].append(u)
                self._into = into
        dist = self._from[a]
        if b not in kept or b == a:
            return dist.get(b)
        ways = [dist[u] + 1 for u in self._into[b] if u in dist]       # b is kept off: step onto it from a cell beside it
        return min(ways) if ways else None

    # ---- vehicles come and go ----------------------------------------------------------------------
    def add(self, v: Vehicle, cell) -> None:
        cell = tuple(cell)
        if cell in self.held:
            raise RuntimeError(f"{v.id} cannot be put on {cell}: {self.held[cell]} is there")
        v.cell, v.energy_t = cell, self.clock.now
        self.held[cell] = v.id
        self.vehicles[v.id] = v
        self.spawned[v.id] = (self.clock.now, cell)

    def free(self, cell) -> bool:
        return tuple(cell) not in self.held

    def remove(self, v: Vehicle) -> None:
        self._close_leg(v)
        del self.held[v.cell]
        self.removed[v.id] = self.clock.now
        self._wake()

    def position(self, v: Vehicle):
        """The cell it stands on, or the one it will stop on next."""
        return v.seg[-1] if v.moving else v.cell

    # ---- driving -----------------------------------------------------------------------------
    def send(self, v: Vehicle, dest, on_arrive, on_clear=None) -> None:
        """Drive v to dest; on_arrive(v) is called when it stands there. A vehicle already moving takes the
        new destination up at its next stopping point."""
        dest = tuple(dest)
        if v.moving:
            v.next = (dest, on_arrive, on_clear)
            return
        path = self.route(v.cell, dest)
        if path is None and not self.closed:             # the map itself has no road there
            self._stall(v, dest, "unreachable")
            raise RuntimeError(f"{v.id}: no road from {v.cell} to {dest}")
        v.dest, v.on_arrive, v.route, v.next = dest, on_arrive, (None if path is None else path[1:]), None
        if on_clear:
            v.on_clear = on_clear
        self.clock.after(0, self._advance, v)

    def _advance(self, v: Vehicle, retry: bool = False) -> None:
        if v.moving or v.id not in self.vehicles or v.id in self.removed:
            return
        if v.next:
            dest, on_arrive, on_clear = v.next
            v.next = None
            self._close_leg(v)
            self.send(v, dest, on_arrive, on_clear)
            return
        if v.dest is None:
            return
        if v.route is None or (self.closed and any(c in self.closed for c in v.route)):       # a road on its way is shut: plan again
            path = self.route(v.cell, v.dest)
            if path is None:
                self._no_way(v)
                return
            v.route = path[1:]
        self.no_way.pop(v.id, None)
        if self._broken and v.route and v.broken_until is None:
            self._round_breakdown(v)
        if not v.route:                                            # it stands at its destination
            self._end_wait(v)
            self._close_leg(v)
            done, v.dest, v.on_arrive = v.on_arrive, None, None
            if done:
                done(v)
            return
        if v.broken_until is not None:                             # broken down: it stands here until it is repaired
            self._close_leg(v)
            return
        k = 0 if self.p.rolling else next((i for i, c in enumerate(v.route) if c in self.stops), len(v.route) - 1)
        seg = v.route[:k + 1]
        taken = next((i for i, c in enumerate(seg) if c in self.held), None)
        if taken == 0:
            self._blocked(v, seg[0], retry)
            return
        if taken:                                                  # drive up behind the vehicle in front
            seg = seg[:taken]
        if v.battery is not None and self.energy:
            self.settle(v)
            need = len(seg) * self.cell_m * self.energy.kwh_per_m * (self.energy.loaded_factor if v.load else 1.0)
            if v.battery < need:                                   # PortSim: the move is refused; the AGV is stranded
                self._stall(v, seg[-1], "battery flat")
                return
            v.battery -= need
            v.used_kwh += need
        self._end_wait(v)
        for c in seg:
            self.held[c] = v.id
        if self.p.rolling:
            self._roll(v, seg)
            return
        loaded = v.load is not None
        t0, dt = self.clock.now, round(segment_time(len(seg) * self.cell_m, self.p.speed(loaded), self.p.accel(loaded)), 3)
        v.moving, v.seg = True, seg
        if v.leg is None:
            v.leg = {"who": v.id, "t0": t0, "path": [v.cell], "loaded": v.load is not None, "what": v.load, "dest": v.dest}
        v.leg["path"] += seg
        v.leg["t1"] = t0 + dt
        self.segments.append((v.id, t0, t0 + dt, (v.cell, *seg)))
        self.rolled.append(False)
        self.clock.after(dt, self._arrive, v)

    def _arrive(self, v: Vehicle) -> None:
        origin, seg = v.cell, v.seg
        for c in (origin, *seg[:-1]):
            del self.held[c]
        v.cell, v.route, v.moving, v.seg = seg[-1], v.route[len(seg):], False, []
        v.moves += len(seg)
        if v.battery is not None:
            self.soc_log.append((v.id, self.clock.now, v.battery))
        if v.load:
            v.loaded_moves += len(seg)
        if v.on_clear:
            cleared, v.on_clear = v.on_clear, None
            cleared(v)
        self._advance(v)
        self._wake()

    # ---- rolling: no stop at a junction crossed straight (the calibrated numbers only) -----------------
    def _roll(self, v: Vehicle, seg: list) -> None:
        """v starts from rest with this stretch reserved."""
        now, loaded = self.clock.now, v.load is not None
        v.moving, v.seg = True, list(seg)
        v.run = {"t0": now, "v": self.p.speed(loaded), "a": self.p.accel(loaded), "d": len(seg) * self.cell_m, "parts": [(now, list(seg))]}
        if v.leg is None:
            v.leg = {"who": v.id, "t0": now, "path": [v.cell], "loaded": loaded, "what": v.load, "dest": v.dest}
        v.leg["path"] += seg
        self._decide_at(v)

    def _decide_at(self, v: Vehicle) -> None:
        """Look again at the last moment v can still stop at the end of what it has reserved."""
        r = v.run
        self.clock.at(max(self.clock.now, r["t0"] + _brake_time(r["d"], r["v"], r["a"])), self._decide, v, v.epoch)

    def _next_stretch(self, v: Vehicle) -> list | None:
        """The next cell, if v may roll onto it without stopping; None: it must stop where its claim ends."""
        rest = v.route[len(v.seg):]
        if not rest or v.next or v.broken_until is not None:
            return None
        if (self.closed and any(c in self.closed for c in rest)) or (self._broken and any(self.held.get(c) in self._broken for c in rest)):
            return None                                            # a closed road or a breakdown ahead: decide at a stop
        nxt = rest[:1]
        if nxt[0] in self.held:
            return None
        a, b = (v.seg[-2] if len(v.seg) > 1 else v.cell), v.seg[-1]
        if (b[0] - a[0], b[1] - a[1]) != (nxt[0][0] - b[0], nxt[0][1] - b[1]):
            return None                                            # it turns there
        return nxt

    def _decide(self, v: Vehicle, epoch: int) -> None:
        if epoch != v.epoch or not v.moving:
            return
        r, nxt = v.run, self._next_stretch(v)
        if nxt is not None and v.battery is not None and self.energy:
            self.settle(v)
            need = len(nxt) * self.cell_m * self.energy.kwh_per_m * (self.energy.loaded_factor if v.load else 1.0)
            if v.battery < need:
                nxt = None
            else:
                v.battery -= need
                v.used_kwh += need
        if nxt is None:                                            # brake: everything behind it is freed when it stands
            v.epoch += 1
            self.clock.at(max(self.clock.now, r["t0"] + segment_time(r["d"], r["v"], r["a"])), self._stopped, v, v.epoch)
            return
        for c in nxt:
            self.held[c] = v.id
        self.clock.at(max(self.clock.now, r["t0"] + _pass_time(r["d"], r["v"], r["a"])), self._passed, v, v.epoch)
        r["parts"].append((self.clock.now, nxt))
        r["d"] += len(nxt) * self.cell_m
        v.seg += nxt
        v.leg["path"] += nxt
        self._decide_at(v)

    def _behind(self, v: Vehicle, t0: float, cells: list, rolled: bool) -> None:
        """v has driven this stretch: free what lies behind it and note the move."""
        origin = v.cell
        for c in (origin, *cells[:-1]):
            del self.held[c]
        self.segments.append((v.id, t0, self.clock.now, (origin, *cells)))
        self.rolled.append(rolled)
        v.cell, v.route, v.seg = cells[-1], v.route[len(cells):], v.seg[len(cells):]
        v.moves += len(cells)
        if v.load:
            v.loaded_moves += len(cells)

    def _passed(self, v: Vehicle, epoch: int) -> None:
        """v crosses the end of the oldest stretch it holds, at speed."""
        if epoch != v.epoch or not v.moving:
            return
        freed = (v.cell, *v.run["parts"][0][1][:-1])
        self._behind(v, *v.run["parts"].pop(0), rolled=True)
        if v.on_clear:
            cleared, v.on_clear = v.on_clear, None
            cleared(v)
        self._wake_for(freed)

    def _stopped(self, v: Vehicle, epoch: int) -> None:
        """v stands at the end of what it had reserved."""
        if epoch != v.epoch:
            return
        parts, v.run = v.run["parts"], None
        freed = [v.cell]
        for i, (t0, cells) in enumerate(parts):
            self._behind(v, t0, cells, rolled=i < len(parts) - 1)
            freed += cells
        v.moving, v.seg = False, []
        v.leg["t1"] = self.clock.now
        if v.battery is not None:
            self.soc_log.append((v.id, self.clock.now, v.battery))
        if v.on_clear:
            cleared, v.on_clear = v.on_clear, None
            cleared(v)
        self._advance(v)
        self._wake_for(freed)                # also those waiting for the cell it stands on: it may be standing for good now

    def _wake_for(self, cells) -> None:
        """Cells have come free (or a vehicle has come to rest): let the vehicles waiting for them try again.
        Everyone else waiting is looked at once a minute, in case a way round has opened."""
        cells = set(cells)
        for vid in sorted(self.waiting, key=lambda i: (self.waiting[i], self.vehicles[i].n, i)):
            if vid in self.waiting and self.vehicles[vid].wait_for in cells:
                del self.waiting[vid]
                self._advance(self.vehicles[vid])
        if self.waiting and not self._look:
            self._look = True
            self.clock.after(60.0, self._look_again)

    def _look_again(self) -> None:
        self._look = False
        self._wake()
        if self.waiting:
            self._look = True
            self.clock.after(60.0, self._look_again)

    def _blocked(self, v: Vehicle, cell, retry: bool) -> None:
        self.refused += 1
        if v.wait_since is None:
            v.wait_since = self.clock.now
            self._close_leg(v)
            v.wait_by, v.wait_by_since = self.held[cell], self.clock.now
        elif self.held[cell] != v.wait_by or cell != v.wait_for:      # still waiting, now behind another vehicle
            self.waits.append((v.id, v.wait_by_since, self.clock.now, v.wait_for, v.wait_by))
            v.wait_by, v.wait_by_since = self.held[cell], self.clock.now
        v.wait_for = cell
        other = self.vehicles[self.held[cell]]
        standing = not other.moving and other.dest is None
        cycle = self._cycle(v)
        head = self._head(other)                                   # the vehicle at the front of the queue v would join
        broken = head.broken_until is not None
        if (standing or cycle or broken) and not retry and cell != v.dest:
            def stopped(x):                                        # behind a breakdown the whole queue is in the way
                return not x.moving and (x.dest is None or x.broken_until is not None or (broken and x.wait_since is not None))
            avoid = {c for c, vid in self.held.items() if vid != v.id and stopped(self.vehicles[vid])}
            if cycle:
                avoid.add(cell)
            ends, shut = {v.cell, v.dest}, self.closed - {v.cell}
            alt = self._bfs(v.cell, v.dest, ((avoid | self.quay_lanes) - ends) | shut) or self._bfs(v.cell, v.dest, (avoid - ends) | shut)
            extra = DETOUR_MOVES if not broken else max(DETOUR_MOVES, int((head.broken_until - self.clock.now) / REPAIR_S_PER_MOVE))
            if alt and alt[1:] != v.route and (cycle or len(alt) - 1 <= len(v.route) + extra) \
                    and not (set(alt[1:2]) & set(self.held)):
                if self.log:
                    longer = len(alt) - 1 - len(v.route)
                    why = "it and the vehicles ahead were waiting for each other in a ring" if cycle else \
                        f"{other.id} {'has broken down' if other.broken_until is not None else 'stands'} in its way at {cell}"
                    self.log.explain("agv", [v.id, other.id], f"{v.id} takes another way: {why}. The way round is "
                                                    + (f"{longer * self.cell_m:,.0f} m longer." if longer > 0 else "no longer."))
                v.route = alt[1:]
                self._advance(v, retry=True)
                return
        self.waiting[v.id] = v.wait_since

    def _cycle(self, v: Vehicle) -> bool:
        """True if v waits for a vehicle that (through others) waits for v: nobody in the ring can move."""
        seen, x = set(), v
        while x.wait_for in self.held and x.id not in seen:
            seen.add(x.id)
            x = self.vehicles[self.held[x.wait_for]]
            if x.moving or x.wait_since is None:
                return False
            if x is v:
                return True
        return False

    def _head(self, x: Vehicle) -> Vehicle:
        """The vehicle at the front of the queue x stands in: the one the others are waiting behind."""
        seen = set()
        while not x.moving and x.broken_until is None and x.wait_for in self.held and x.id not in seen:
            seen.add(x.id)
            x = self.vehicles[self.held[x.wait_for]]
        return x

    def _round_breakdown(self, v: Vehicle) -> None:
        """A broken-down vehicle stands on v's way. The fleet knows where it is, so v turns off early if a
        way round costs less time than the repair will still take when v gets there."""
        for i, cell in enumerate(v.route):
            other = self.vehicles.get(self.held.get(cell))
            if other is None or other is v or other.broken_until is None or other.moving or cell == v.dest:
                continue
            spare = (other.broken_until - self.clock.now) / REPAIR_S_PER_MOVE - i      # the wait it would face, in moves
            if spare > 0:
                avoid = {c for c, vid in self.held.items() if vid != v.id and vid in self._broken}
                ends, shut = {v.cell, v.dest}, self.closed - {v.cell}
                alt = self._bfs(v.cell, v.dest, ((avoid | self.quay_lanes) - ends) | shut) or self._bfs(v.cell, v.dest, (avoid - ends) | shut)
                if alt and len(alt) - 1 - len(v.route) <= spare:
                    if self.log:
                        self.log.explain("agv", [v.id, other.id], f"{v.id} turns off early: {other.id} has broken down on its way at {cell} and the repair "
                                         f"takes another {(other.broken_until - self.clock.now) / 60:.0f} min. The way round is "
                                         f"{(len(alt) - 1 - len(v.route)) * self.cell_m:,.0f} m longer.")
                    v.route = alt[1:]
            return

    def _no_way(self, v: Vehicle) -> None:
        """No open road leads to v's destination: it waits where it is until a road opens."""
        if v.wait_since is None:
            v.wait_since = self.clock.now
            self._close_leg(v)
            v.wait_by, v.wait_by_since = "a closed road", self.clock.now
        v.wait_for = v.dest
        if v.id not in self.no_way:
            self.no_way[v.id] = v.wait_since
            if self.log:
                self.log.add("agv", v.id, f"{v.id} has no way to {v.dest} while the road is closed, and waits at {v.cell}", stall=True)

    # ---- disruptions ----------------------------------------------------------------------------
    def break_down(self, v: Vehicle, seconds: float) -> None:
        """v stops where it stands (or where the move it is making ends) and holds that cell until it is repaired."""
        until = self.clock.now + seconds
        v.broken_until = until if v.broken_until is None else max(v.broken_until, until)
        self._broken.add(v.id)
        at = self.position(v)
        self.breakdowns.append({"vehicle": v.id, "t0": self.clock.now, "t1": v.broken_until, "at": at})
        if self.log:
            self.log.add("agv", v.id, f"{v.id} breaks down at {at}: the repair takes {seconds / 60:.0f} min", disruption=True)
        self.clock.at(v.broken_until, self._repaired, v)
        self._wake()

    def _repaired(self, v: Vehicle) -> None:
        if v.broken_until is None or self.clock.now < v.broken_until - 1e-6:
            return
        v.broken_until = None
        self._broken.discard(v.id)
        if self.log:
            self.log.add("agv", v.id, f"{v.id} is repaired", disruption=True)
        self._advance(v)
        self._wake()
        self.on_change()

    def close_road(self, cells, seconds: float | None = None) -> None:
        """Shut these road cells, for a time or until open_road(cells)."""
        cells = sorted({tuple(c) for c in cells})
        bad = [c for c in cells if c not in self.adj]
        if bad:
            raise ValueError(f"{bad[0]} is not a road cell")
        for c in cells:
            self._shut[c] = self._shut.get(c, 0) + 1
        self.closed = set(self._shut)
        self._moves.clear()
        self._from.clear()
        self.closures.append({"cells": cells, "t0": self.clock.now, "t1": None if seconds is None else self.clock.now + seconds})
        if self.log:
            self.log.add("agv", "road", f"The road is closed at {', '.join(map(str, cells))}"
                                         + (f" for {seconds / 60:.0f} min" if seconds is not None else ""), disruption=True)
        if seconds is not None:
            self.clock.after(seconds, self.open_road, cells)
        self._wake()

    def open_road(self, cells) -> None:
        cells = [tuple(c) for c in cells if tuple(c) in self._shut]
        if not cells:
            return
        for c in cells:
            self._shut[c] -= 1
            if not self._shut[c]:
                del self._shut[c]
        self.closed = set(self._shut)
        self._moves.clear()
        self._from.clear()
        if self.log:
            self.log.add("agv", "road", f"The road at {', '.join(map(str, sorted(cells)))} is open again", disruption=True)
        for vid in sorted(self.no_way, key=lambda i: (self.no_way[i], self.vehicles[i].n, i)):
            self._advance(self.vehicles[vid])
        self._wake()
        self.on_change()

    def _end_wait(self, v: Vehicle) -> None:
        if v.wait_since is None:
            return
        waited = self.clock.now - v.wait_since
        v.waited_s += waited
        self.wait_by_cell[v.wait_for] += waited
        self.waits.append((v.id, v.wait_by_since, self.clock.now, v.wait_for, v.wait_by))
        if waited >= STALL_S:
            self._stall(v, v.wait_for, f"waited {waited:.0f} s", since=v.wait_since)
        self.waiting.pop(v.id, None)
        v.wait_since, v.wait_for = None, None

    def _stall(self, v: Vehicle, cell, why: str, since: float | None = None) -> None:
        self.stalls.append({"vehicle": v.id, "at": v.cell, "cell": cell, "why": why,
                            "since": self.clock.now if since is None else since, "until": self.clock.now})
        if self.log:
            self.log.add("agv", v.id, f"{v.id} is held up at {v.cell}: {why}", stall=True)

    def _wake(self) -> None:
        for vid in sorted(self.waiting, key=lambda i: (self.waiting[i], self.vehicles[i].n, i)):
            if vid in self.waiting:
                del self.waiting[vid]
                self._advance(self.vehicles[vid])

    def _close_leg(self, v: Vehicle) -> None:
        if v.leg and len(v.leg["path"]) > 1:
            self.legs.append(v.leg)
        v.leg = None

    def still_waiting(self) -> list[dict]:
        """Vehicles that were still waiting for a cell when the run ended: a jam nobody resolved."""
        stuck = [{"vehicle": i, "at": self.vehicles[i].cell, "cell": self.vehicles[i].wait_for,
                  "blocked_by": self.held.get(self.vehicles[i].wait_for), "since": t} for i, t in sorted(self.waiting.items())]
        return stuck + [{"vehicle": i, "at": self.vehicles[i].cell, "cell": self.vehicles[i].dest, "blocked_by": "a closed road", "since": t}
                        for i, t in sorted(self.no_way.items())]

    # ---- energy ------------------------------------------------------------------------------
    def settle(self, v: Vehicle) -> None:
        """Bring v's charge up to now: charging at a station, or the standing draw anywhere else."""
        if v.battery is None or not self.energy:
            return
        e, hours = self.energy, (self.clock.now - v.energy_t) / 3600.0
        if v.charging:
            v.battery = max(v.battery, min(e.capacity_kwh * e.charge_to, v.battery + e.charge_kw * hours))
        else:
            used = min(v.battery, e.standing_kw * hours)
            v.battery -= used
            v.used_kwh += used
        v.energy_t = self.clock.now
        self.soc_log.append((v.id, self.clock.now, v.battery))


@dataclass
class Job:
    """A request from the scheduler: an AGV is needed at `pickup`. on_pickup(agv, job) is called when it
    stands there. drops: the cells the container may have to be taken to, for the battery check."""
    id: str
    kind: str
    pickup: tuple
    on_pickup: object
    drops: tuple = ()
    released: float = 0.0
    priority: int = 0
    container: str | None = None
    agv: str | None = None
    times: dict = field(default_factory=dict)
    purpose: str = ""                # what the AGV is sent for, in words, for the reason noted when one is chosen


def nearest_feasible(fleet, job, free):
    return min(free, key=lambda v: (fleet.traffic.moves(fleet.traffic.position(v), job.pickup), v.n))


def longest_idle(fleet, job, free):
    return min(free, key=lambda v: (v.idle_since, v.n))


def balanced(fleet, job, free):
    """Oct 15, rule 2: weigh the drive against how long the AGV has had no job and how full its battery is.
    The weights are assumptions: a minute of idleness is worth 30 s of driving, a full battery 4 minutes."""
    def score(v):
        drive_s = fleet.traffic.moves(fleet.traffic.position(v), job.pickup) * 5.0
        idle_s = min(fleet.clock.now - v.idle_since, 900.0)
        return drive_s - 0.5 * idle_s - 240.0 * fleet.soc(v), v.n
    return min(free, key=score)


POLICIES = {"nearest_feasible": nearest_feasible, "longest_idle": longest_idle, "balanced": balanced}


class AgvFleet:
    def __init__(self, clock, layout, traffic: Traffic, log, n: int = 8, policy: str = "nearest_feasible",
                 pool: bool | None = None, soc_start: tuple | None = None):
        """pool: the fleet shares the charging stations and stands by on the quay apron (see the top of this
        file); None means "if there are more AGVs than stations". soc_start: (low, high), each AGV starts
        with a charge drawn between them instead of Energy.start."""
        if policy not in POLICIES and policy not in PAIR_POLICIES:
            raise ValueError(f"unknown dispatch policy {policy!r}: choose from {sorted([*POLICIES, *PAIR_POLICIES])}")
        self.clock, self.layout, self.traffic, self.log, self.policy = clock, layout, traffic, log, policy
        self.pair_rule = PAIR_POLICIES.get(policy)             # a rule that chooses the job as well as the AGV, or None
        learned = dispatch_learned.load_weights() if policy == "learned" else None     # raises with what to run if there are none yet
        self.weights = tuple(learned["weights"][f] for f in dispatch_learned.FEATURES) if learned else None
        self.expects = learned.get("expects") if learned else None    # what the weights were trained to keep low, for the reason noted
        self.energy = traffic.energy
        west = sorted((c for c in layout.charging_stations()), key=lambda c: (int(c.attrs["id"][2:])))
        half = len(west) // 2
        order = [s for pair in zip(west[:half], west[half:]) for s in pair] + west[2 * half:]
        self.pool = n > len(order) if pool is None else bool(pool)
        self.stations = [c.pos for c in order]
        self.station_id = {c.pos: c.attrs["id"] for c in order}
        self.standby: list = []                                # a pooled fleet: where an AGV without a job waits
        self.claimed: dict[tuple, str] = {}                    # a pooled fleet: station or stand-by cell -> the AGV on it or coming
        if self.pool:
            lanes = traffic.quay_lanes
            apron = {c.pos for c in layout.quay_cells() if c.pos in traffic.adj and c.pos not in lanes}
            firsts = sorted(min(q.transfer_points) for q in layout.quay_cranes.values())
            def upstream(cell):                                # how far before the next crane's lanes, then which crane
                ahead = [f for f in firsts if f[0] > cell[0]]
                return (ahead[0][0] - cell[0], cell[0]) if ahead else None
            direct = {w for u, ws in traffic.adj.items() if u not in apron and u not in lanes for w in ws}
            # a stand-by cell lies before a crane's lanes and can be driven onto straight from the road beside the
            # apron, so nobody has to cross another stand-by cell to reach it
            self.standby = sorted((c for c in apron if c in direct and upstream(c)), key=upstream)
            traffic.keep_off = frozenset(self.standby)
            traffic._moves.clear()
            traffic._from.clear()
            if n > len(order) + len(self.standby):
                raise ValueError(f"{n} AGVs but the layout has {len(order)} charging stations and {len(self.standby)} stand-by cells")
        elif n > len(order):
            raise ValueError(f"{n} AGVs but the layout has {len(order)} charging stations: an idle AGV parks at a station")
        self.agvs: dict[str, Vehicle] = {}
        self.pending: list[Job] = []
        self.jobs: list[Job] = []
        self.charges: list[list] = []                          # [agv, from, until]: each stay on a charger
        self._tick = False
        self._share_tick = False
        self._near_station: dict[tuple, int | None] = {}
        traffic.on_change = self.dispatch
        soc = [self.energy.start if self.energy else 1.0] * n
        if self.energy and soc_start:
            low, high = soc_start
            soc = [round(random.Random(f"{traffic.p.seed}|soc|{i}").uniform(low, high), 3) for i in range(n)]
        places = (self.stations + self.standby)[:n]
        if self.pool:                                          # the emptiest batteries start on the stations
            places = [cell for _, cell in sorted(zip(sorted(range(n), key=lambda i: (soc[i], i)), places))]
        for i in range(n):                                     # they start parked, alternating between the two depots
            v = Vehicle(f"AGV{i + 1}", "agv", places[i], n=i + 1)
            if self.energy:
                v.battery = self.energy.capacity_kwh * soc[i]
            v.state, v.home, v.charging = "parked", places[i], bool(self.energy) and places[i] in self.station_id
            if v.charging:
                self.charges.append([v.id, 0.0, None])
            if self.energy:
                traffic.soc_log.append((v.id, 0.0, v.battery))
            traffic.add(v, places[i])
            v.parked_since = 0.0
            if self.pool:
                self.claimed[places[i]] = v.id
            self.agvs[v.id] = v
        if self.pool and self.energy:
            clock.after(0, self._share_stations)               # one that starts low off a station gets the first that is free

    # ---- what the scheduler calls ------------------------------------------------------------------
    def submit(self, job: Job) -> None:
        job.released = self.clock.now
        self.pending.append(job)
        self.jobs.append(job)
        self.dispatch()

    def carry(self, agv: str, container: str, drop, on_drop, on_clear=None) -> None:
        """The container is on the AGV: drive it to drop. on_clear is called when it has left the pickup."""
        v = self.agvs[agv]
        v.load, v.state = container, "to_drop"
        v.job.times["loaded"] = self.clock.now

        def there(v):
            v.state = "at_drop"
            v.job.times["at_drop"] = self.clock.now
            on_drop(v.id)
        self.traffic.send(v, drop, there, on_clear)

    def unloaded(self, agv: str) -> None:
        """The container is off the deck (the AGV may still stand under the crane)."""
        self.agvs[agv].load = None

    def release(self, agv: str, on_clear=None) -> None:
        """The AGV's job is over: it takes the next one, or goes to charge."""
        v = self.agvs[agv]
        if v.job:
            v.job.times["done"] = self.clock.now
            v.jobs_done += 1
        v.job, v.load, v.state, v.idle_since = None, None, "idle", self.clock.now
        if on_clear:
            v.on_clear = on_clear
        self.dispatch()
        if v.state == "idle":
            self._go_home(v)

    def break_down(self, agv: str, seconds: float) -> None:
        """A disruption: this AGV is out of action for so long, wherever it is. A broken AGV takes no job."""
        if agv not in self.agvs:
            raise ValueError(f"there is no AGV called {agv!r}")
        self.traffic.break_down(self.agvs[agv], seconds)

    def soc(self, v: Vehicle) -> float:
        if v.battery is None:
            return 1.0
        self.traffic.settle(v)
        return v.battery / self.energy.capacity_kwh

    # ---- dispatch ----------------------------------------------------------------------------
    def dispatch(self) -> None:
        if self.pair_rule is not None:
            self._dispatch_pairs()
        else:
            for job in sorted(self.pending, key=lambda j: (-j.priority, j.released, j.id)):
                free = [v for v in self.agvs.values() if v.state in ("parked", "homing", "idle") and v.broken_until is None and self._fits(v, job)]
                if free:
                    self._assign(POLICIES[self.policy](self, job, free), job, free)
        if self.pending and not self._tick and self.energy and any(
                v.charging and self.soc(v) < self.energy.charge_to - 1e-9 for v in self.agvs.values()):
            self._tick = True                             # look again soon: an AGV on charge may have enough by then
            self.clock.after(30.0, self._again)

    def _again(self) -> None:
        self._tick = False
        if self.pending:
            self.dispatch()

    def pairs(self) -> list:
        """Every (job, AGV, moves to the pickup) the fleet could start now, of the jobs with the highest priority
        that an AGV can take: what a rule in PAIR_POLICIES chooses from."""
        free = [v for v in self.agvs.values() if v.state in ("parked", "homing", "idle") and v.broken_until is None]
        if not free:
            return []
        found = [(job, v, self.traffic.moves(self.traffic.position(v), job.pickup)) for job in self.pending for v in free if self._fits(v, job)]
        top = max((job.priority for job, _, _ in found), default=0)
        found = [p for p in found if p[0].priority == top]
        overdue = [p[0] for p in found if self.clock.now - p[0].released >= OVERDUE_S]
        if overdue:                                            # no job is passed over for ever: the one that has waited longest goes now
            first = min(overdue, key=lambda j: (j.released, j.id))
            found = [p for p in found if p[0] is first]
        return found

    def _dispatch_pairs(self) -> None:
        while self.pending:
            pairs = self.pairs()
            if not pairs:
                break
            chosen = self.pair_rule(self, pairs)
            job, v, _ = chosen
            # a rule put in from outside (a learner at work) notes no reason
            self._assign(v, job, [a for j, a, _ in pairs if j is job], why=dispatch_learned.why(self, chosen, pairs) if self.policy in PAIR_POLICIES else "")

    def _fits(self, v: Vehicle, job: Job) -> bool:
        moves = self.traffic.moves
        to_pickup = moves(self.traffic.position(v), job.pickup)
        if to_pickup is None:
            return False
        if v.battery is None:
            return True
        e = self.energy
        back = (lambda d: moves(d, v.home)) if not self.pool else self._to_station
        reach = [(moves(job.pickup, d), back(d)) for d in job.drops]
        reach = [(a, b) for a, b in reach if a is not None]            # a place no open road leads to is left out
        if job.drops and not reach:
            return False
        loaded = max((a for a, _ in reach), default=0)
        home = max((b for _, b in reach if b is not None), default=back(job.pickup) or 0)
        metres = (to_pickup + loaded * e.loaded_factor + home) * self.traffic.cell_m
        return self.soc(v) * e.capacity_kwh >= metres * e.kwh_per_m + e.reserve_kwh

    def parked_time(self, v: Vehicle) -> tuple:
        """(seconds parked, seconds of that standing by off a charger), up to now."""
        more = 0.0 if v.parked_since is None else self.clock.now - v.parked_since
        return v.parked_s + more, v.standby_s + (more if v.home not in self.station_id else 0.0)

    def _unpark(self, v: Vehicle) -> None:
        if v.parked_since is not None:
            v.parked_s, v.standby_s = self.parked_time(v)
            v.parked_since = None

    def _why(self, v: Vehicle, job: Job, free: list) -> str:
        """Why this AGV of the free ones, with the numbers the rule went by. It reads, and changes nothing."""
        moves = lambda a: self.traffic.moves(self.traffic.position(a), job.pickup)
        far = lambda a: f"{moves(a) * self.traffic.cell_m:,.0f} m"          # the drive to the pickup, along the roads
        idle = lambda a: self.clock.now - a.idle_since
        span = lambda s: f"{s / 60:.0f} min" if s >= 90 else f"{s:.0f} s"
        charge = lambda a: "" if a.battery is None else f", {100 * a.battery / self.energy.capacity_kwh:.0f}% charged"
        among = f"of the {len(free)} free AGVs with enough charge" if self.energy else f"of the {len(free)} free AGVs"
        if len(free) == 1:
            return f"the only free AGV{' with enough charge' if self.energy else ''}, a drive of {far(v)}"
        if self.policy == "longest_idle":
            nxt = min((a for a in free if a is not v), key=lambda a: (a.idle_since, a.n))
            return f"without a job the longest {among}, {span(idle(v))} (the next, {nxt.id}, {span(idle(nxt))}); it has a drive of {far(v)}"
        nxt = min((a for a in free if a is not v), key=lambda a: (moves(a), a.n))
        if self.policy == "nearest_feasible":
            return f"the nearest {among}, a drive of {far(v)} (the next, {nxt.id}, {far(nxt)})"
        return (f"the best balance of distance, idle time and charge {among}: a drive of {far(v)}, {span(idle(v))} without a job{charge(v)} "
                f"(the nearest other, {nxt.id}: {far(nxt)}, {span(idle(nxt))}{charge(nxt)})")

    def _assign(self, v: Vehicle, job: Job, free: list | None = None, why: str | None = None) -> None:
        self.pending.remove(job)
        self.traffic.settle(v)
        self._unpark(v)
        if free and why != "":
            self.log.explain("agv", [v.id, job.container], f"{v.id} is sent {job.purpose or 'to ' + str(job.pickup)}: {why or self._why(v, job, free)}.")
        was = v.state
        for stay in self.charges:
            if stay[0] == v.id and stay[2] is None:
                stay[2] = self.clock.now
        v.charging, v.state, v.job = False, "to_pickup", job
        if self.pool and self.claimed.get(v.home) == v.id:
            del self.claimed[v.home]                       # its station or stand-by cell is free for another
            if self.energy and v.home in self.station_id:
                self.clock.after(0, self._share_stations)
        job.agv = v.id
        job.times["assigned"] = self.clock.now
        start = self.traffic.position(v)
        self.log.add("agv", v.id, f"{v.id} takes {job.id} ({job.kind}) from {'its charger' if was == 'parked' and not self.pool else start}: "
                                  f"{self.traffic.moves(start, job.pickup)} moves to the pickup", quiet=True, job=job.id)

        def there(v):
            v.state = "at_pickup"
            job.times["at_pickup"] = self.clock.now
            job.on_pickup(v.id, job)
        self.traffic.send(v, job.pickup, there)

    def _go_home(self, v: Vehicle) -> None:
        if self.pool:
            spot = self._rest_spot(v)
            if spot is None:                               # nowhere free: it stays where it is and is still called for jobs
                return
            self._unpark(v)
            if spot in self.station_id and self.energy:
                self.log.explain("agv", [v.id], f"{v.id} has no job and goes to charge at {self.station_id[spot]}: "
                                 f"{100 * v.battery / self.energy.capacity_kwh:.0f}% left, under the {100 * self.energy.charge_below:.0f}% at which a pooled AGV charges.")
            if self.claimed.get(v.home) == v.id:
                del self.claimed[v.home]
            v.home = spot
            self.claimed[spot] = v.id
        v.state = "homing"
        self.traffic.send(v, v.home, self._parked)

    def _parked(self, v: Vehicle) -> None:
        self.traffic.settle(v)
        at_station = v.home in self.station_id
        v.state, v.charging, v.parked_since = "parked", bool(self.energy) and at_station, self.clock.now
        if v.charging:
            self.charges.append([v.id, self.clock.now, None])
        where = self.station_id[v.home] if at_station else f"stand-by {v.home}"
        self.log.add("agv", v.id, f"{v.id} parks at {where} with {self.soc(v):.0%} charge", quiet=True)
        self.dispatch()
        if self.pool and self.energy and not at_station and v.state == "parked" and self._low(v):
            self._share_stations()

    # ---- a pooled fleet: shared stations and stand-by cells ---------------------------------------
    def _to_station(self, cell) -> int | None:
        """Moves from a cell to the nearest charging station."""
        cell = tuple(cell)
        if cell not in self._near_station or self.traffic.closed:
            ways = [m for m in (self.traffic.moves(cell, s) for s in self.stations) if m is not None]
            if self.traffic.closed:
                return min(ways) if ways else None
            self._near_station[cell] = min(ways) if ways else None
        return self._near_station[cell]

    def _share_again(self) -> None:
        self._share_tick = False
        self._share_stations()

    def _nearest(self, v: Vehicle, cells) -> tuple | None:
        here = self.traffic.position(v)
        ways = [(m, c) for m, c in ((self.traffic.moves(here, c), c) for c in cells if c not in self.claimed) if m is not None]
        return min(ways)[1] if ways else None

    def _low(self, v: Vehicle) -> bool:
        return bool(self.energy) and self.soc(v) < self.energy.charge_below

    def _rest_spot(self, v: Vehicle) -> tuple | None:
        """Where an AGV with no job goes: a free station if its charge is low, otherwise the nearest free stand-by cell."""
        if self._low(v):
            spot = self._nearest(v, self.stations)
            if spot is not None:
                return spot
        return self._nearest(v, self.standby) or self._nearest(v, self.stations)

    def _share_stations(self) -> None:
        """An AGV standing by with a low charge takes a free station; if none is free, a charged AGV gives its station up."""
        waiting = sorted((v for v in self.agvs.values() if v.state == "parked" and not v.charging and v.broken_until is None and self._low(v)),
                         key=lambda v: (self.soc(v), v.n))
        if not waiting:
            return
        if all(s in self.claimed for s in self.stations):
            full = next((v for v in self.agvs.values() if v.state == "parked" and v.charging
                         and self.soc(v) >= self.energy.charge_to - 0.01 and v.broken_until is None), None)
            if full is None:                              # every station is charging someone: look again in a while
                if not self._share_tick:
                    self._share_tick = True
                    self.clock.after(300.0, self._share_again)
                return
            for stay in self.charges:
                if stay[0] == full.id and stay[2] is None:
                    stay[2] = self.clock.now
            full.charging = False
            self.log.explain("agv", [full.id, waiting[0].id], f"{full.id} is charged and gives its station up: {waiting[0].id} stands by with "
                             f"{100 * waiting[0].battery / self.energy.capacity_kwh:.0f}% and every station is taken.")
            self._go_home(full)                           # to a stand-by cell: its charge is not low
        for v in waiting:
            spot = self._nearest(v, self.stations)
            if spot is None:                              # more are waiting than stations came free: look again in a while
                if not self._share_tick:
                    self._share_tick = True
                    self.clock.after(300.0, self._share_again)
                return
            self._unpark(v)
            del self.claimed[v.home]
            v.home = spot
            self.claimed[spot] = v.id
            v.state = "homing"
            self.traffic.send(v, spot, self._parked)
