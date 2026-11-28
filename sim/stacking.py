"""The container stacking module (the group's "yard" module): where a container goes, and what it costs
to get one out again.

Written from what the stacking owner described in the check-ins, for the owner to replace or adopt.

* The goal is the fewest rehandles: moves of a container that is only in the way (Oct 15).
* Two policies, the two the owner compared on Oct 22 and Oct 29:
      shortest_stack   the lowest stack that has room
      departure_time   on a stack whose containers all leave later (so this one is on top when its turn
                       comes); failing that an empty stack; failing that the stack whose first leaver
                       leaves latest
  Weight (heaviest at the bottom) is left out for now, as Dr. Wang said on Oct 22.
* Hazardous cargo and food are kept apart (Dr. Wang's first priority, Oct 22): each zone of the yard
  takes one kind of cargo, and a food zone and a hazmat zone must be at least MIN_GAP_CELLS apart.
* Containers come and go at the same time (Dr. Wang, Oct 29); nothing here assumes the yard is filled
  first and emptied afterwards.

At real scale two more things are asked of this module (sim/terminal.py asks, see docs/integration.md):
assign() takes the yard crane the scheduler would like used, and chooses the bay among that crane's; and
preload() can spread a starting stock of thousands over the bays at random, the stack within each bay
still chosen by the policy.

A bay is the group of stacks one transfer point serves: the block's rows times its container rows,
each up to the block's max_tier high. An AGV is sent to a bay; the stack is chosen when the yard crane
starts the lift, from what is standing there at that moment. A position is (slot id, container row, tier).
Each bay keeps max_tier - 1 positions free, so the lowest container of a full stack can always be dug out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

MIN_GAP_CELLS = 4        # food and hazmat zones at least this far apart, in cells of 15 m (an assumption)
LATE = float("inf")      # a container with no known departure is treated as leaving last
CARGO = ("general", "food", "hazmat")


class YardFull(RuntimeError):
    pass


@dataclass(frozen=True)
class Zone:
    name: str
    cargo: str               # "general", "food" or "hazmat"
    block: str
    bays: tuple              # (first, last), inclusive
    serves: str | None = None        # the berth whose containers it takes first; None = any


class Yard:
    def __init__(self, layout, zones, policy: str = "departure_time"):
        if policy not in ("shortest_stack", "departure_time"):
            raise ValueError(f"unknown stacking policy {policy!r}: choose shortest_stack or departure_time")
        self.layout, self.policy, self.zones = layout, policy, tuple(zones)
        self.stacks: dict[tuple, list] = {}          # (slot, row) -> container ids, bottom first
        self.bays: dict[tuple, list] = {}            # (block, bay) -> its stacks
        self.zone_of: dict[tuple, Zone] = {}
        self.max_tier: dict[tuple, int] = {}
        self.reserved: dict[tuple, int] = {}         # bay -> containers on their way to it
        self.bay_of: dict[str, tuple] = {}           # container -> the bay it is in or on its way to
        self.where: dict[str, tuple] = {}            # container -> (slot, row, tier) once it stands
        self.due: dict[str, float] = {}
        self.cargo: dict[str, str] = {}
        self.reason: dict[str, str] = {}             # container -> why the policy chose its stack, the end of a sentence
        self.bay_reason: dict[str, str] = {}         # container -> why the policy chose its bay, as a clause
        self.now = None                              # set by the scheduler: the time, so a reason can say that a container is overdue
        self.rehandles = 0
        self.placed = 0
        self.taken = 0
        for z in self.zones:
            if z.cargo not in CARGO:
                raise ValueError(f"zone {z.name}: unknown cargo {z.cargo!r}")
            block = layout.blocks[z.block]
            for slot in block.slots:
                a = layout.slot(slot).attrs
                if not z.bays[0] <= a["bay"] <= z.bays[1]:
                    continue
                bay = (z.block, a["bay"])
                if self.zone_of.get(bay, z) is not z:
                    raise ValueError(f"bay {bay} is in two zones: {self.zone_of[bay].name} and {z.name}")
                self.zone_of[bay], self.max_tier[bay] = z, block.max_tier
                for row in range(1, block.container_rows + 1):
                    self.stacks[(slot, row)] = []
                    self.bays.setdefault(bay, []).append((slot, row))
        self._by_cargo = {k: [b for b in self.bays if self.zone_of[b].cargo == k] for k in CARGO}
        self._stock: dict[tuple, list] = {}
        self._check_segregation()

    def _check_segregation(self) -> None:
        cells = {k: [self.layout.slot(s).pos for b, st in self.bays.items() if self.zone_of[b].cargo == k for s, _ in st]
                 for k in ("food", "hazmat")}
        gap = min((max(abs(ax - bx), abs(ay - by)) for ax, ay in cells["food"] for bx, by in cells["hazmat"]), default=None)
        if gap is not None and gap < MIN_GAP_CELLS:
            raise ValueError(f"a food zone and a hazmat zone are {gap} cells apart; they must be at least {MIN_GAP_CELLS}")

    # ---- what the scheduler asks ---------------------------------------------------------------
    def _candidates(self, cargo: str, berth: str | None) -> list:
        """The bays with room for this kind of cargo: those of the zones that serve the berth, if any has room."""
        mine = [b for b in self._by_cargo[cargo] if self._room(b) > 0]
        near = [b for b in mine if self.zone_of[b].serves in (None, berth)] if berth else mine
        return near or mine

    def assign(self, c, berth: str | None = None, crane: str | None = None) -> tuple:
        """The bay a container goes to. Room for it is kept from now on. Raises YardFull.
        crane: the scheduler's wish, the yard crane to use if one of its bays has room (see cranes_for)."""
        if c.id in self.bay_of:
            raise RuntimeError(f"{c.id} already has a place in the yard")
        self.due[c.id] = LATE if c.due_s is None else c.due_s
        self.cargo[c.id] = c.cargo
        candidates = self._candidates(c.cargo, berth)
        if not candidates:
            raise YardFull(f"no room in the yard for {c.cargo} container {c.id}")
        if crane is not None:
            candidates = [b for b in candidates if self.crane(b) == crane] or candidates
        bay = min(candidates, key=lambda b: (self._bay_score(c.id, b), self._order(b)))
        self.bay_reason[c.id] = self._why_bay(c.id, bay, candidates)
        self.reserved[bay] = self.reserved.get(bay, 0) + 1
        self.bay_of[c.id] = bay
        return bay

    def cranes_for(self, c, berth: str | None = None) -> list:
        """The yard cranes that could take this container now, in the order of their zones."""
        out = []
        for b in self._candidates(c.cargo, berth):
            if self.crane(b) not in out:
                out.append(self.crane(b))
        return out

    def coming(self, crane: str) -> int:
        """Containers on their way to this crane's bays."""
        return sum(n for b, n in self.reserved.items() if n and self.crane(b) == crane)

    def stand(self, bay: tuple) -> tuple:
        """The cell where an AGV stands to be served at this bay."""
        return self.layout.transfer_point_for(self.bays[bay][0][0]).pos

    def crane(self, bay: tuple) -> str:
        return self.layout.blocks[bay[0]].crane

    def settle(self, cid: str) -> tuple:
        """The position the crane sets an arriving container on, chosen from the stacks as they stand now."""
        bay = self.bay_of[cid]
        stack = self._best_stack(cid, [s for s in self.bays[bay] if len(self.stacks[s]) < self.max_tier[bay]])
        self.reason[cid] = self._why(cid, stack)
        return (*stack, len(self.stacks[stack]) + 1)

    def set_down(self, cid: str, pos: tuple) -> None:
        stack = (pos[0], pos[1])
        if len(self.stacks[stack]) + 1 != pos[2]:
            raise RuntimeError(f"{cid} cannot be set at {pos}: the stack is {len(self.stacks[stack])} high")
        if cid not in self.where:                       # an arrival, not a rehandle
            bay = self.bay_of[cid]
            self.reserved[bay] -= 1
            self.placed += 1
        self.stacks[stack].append(cid)
        self.where[cid] = tuple(pos)

    def blockers(self, cid: str) -> list:
        """The containers standing on top of this one, top first: each needs a rehandle before it can go."""
        slot, row, tier = self.where[cid]
        return self.stacks[(slot, row)][tier:][::-1]

    def rehandle_target(self, cid: str, keep_clear: str) -> tuple:
        """Where a container that is in the way is put: another stack of the same bay, never the stack
        the wanted container (keep_clear) is in."""
        bay, off = self.bay_of[cid], self.where[keep_clear][:2]
        room = [s for s in self.bays[bay] if s != off and len(self.stacks[s]) < self.max_tier[bay]]
        if not room:
            raise YardFull(f"bay {bay} has no room to rehandle {cid}")
        stack = self._best_stack(cid, room)
        self.reason[cid] = self._why(cid, stack)
        return (*stack, len(self.stacks[stack]) + 1)

    def lift_off(self, cid: str) -> tuple:
        """The crane lifts a container off the top of its stack (to rehandle it or to send it away)."""
        slot, row, tier = self.where[cid]
        stack = self.stacks[(slot, row)]
        if stack[-1] != cid:
            raise RuntimeError(f"{cid} is not on top of its stack: {stack[tier:]} are on it")
        stack.pop()
        return (slot, row, tier)

    def relocate(self, cid: str, pos: tuple) -> None:
        """A rehandle: the container, lifted off, is set on another stack of its bay."""
        self.set_down(cid, pos)
        self.rehandles += 1

    def gone(self, cid: str) -> None:
        """The container has left the yard for good."""
        for d in (self.where, self.bay_of, self.due, self.cargo):
            d.pop(cid, None)
        self.taken += 1

    def preload(self, containers, berth_of=None, spread: int | None = None) -> None:
        """Containers that are in the yard when the run starts, stacked in the order given by the policy.
        spread: a seed. The bay of each is then drawn at random among those with room (a yard that was
        filled over weeks, not by today's rule, and thousands of containers placed quickly); the stack
        within the bay is still the policy's choice."""
        rng = None if spread is None else random.Random(f"stock|{spread}")
        for c in containers:
            berth = (berth_of or {}).get(c.id)
            if rng is None:
                self.assign(c, berth)
            else:
                self.due[c.id] = LATE if c.due_s is None else c.due_s
                self.cargo[c.id] = c.cargo
                bays = self._stock_bays(c.cargo, berth)
                while bays and self._room(bays[k := rng.randrange(len(bays))]) <= 0:
                    bays[k] = bays[-1]
                    bays.pop()
                if not bays:
                    raise YardFull(f"no room in the yard for {c.cargo} container {c.id}")
                self.reserved[bays[k]] = self.reserved.get(bays[k], 0) + 1
                self.bay_of[c.id] = bays[k]
            self.set_down(c.id, self.settle(c.id))

    def _stock_bays(self, cargo: str, berth: str | None) -> list:
        """The bays the starting stock of one kind is spread over (a list kept between calls, emptied as bays fill)."""
        key = (cargo, berth)
        if key not in self._stock:
            mine = list(self._by_cargo[cargo])
            near = [b for b in mine if self.zone_of[b].serves in (None, berth)] if berth else mine
            self._stock[key] = near or mine
        return self._stock[key]

    # ---- numbers -----------------------------------------------------------------------------
    def count(self) -> int:
        return len(self.where)

    def capacity(self) -> int:
        return sum(len(st) * self.max_tier[b] for b, st in self.bays.items())

    def occupancy(self) -> float:
        return self.count() / self.capacity()

    def heights(self) -> list[int]:
        return [len(s) for s in self.stacks.values()]

    def mixed(self) -> list:
        """Bays that hold a container of the wrong kind for their zone; always empty if the rules hold."""
        return [(b, cid) for cid, b in self.bay_of.items() if self.cargo[cid] != self.zone_of[b].cargo]

    # ---- the policies ------------------------------------------------------------------------
    def _room(self, bay) -> int:
        total = len(self.bays[bay]) * self.max_tier[bay] - (self.max_tier[bay] - 1)
        return total - sum(len(self.stacks[s]) for s in self.bays[bay]) - self.reserved.get(bay, 0)

    def _order(self, bay) -> tuple:
        return (self.zones.index(self.zone_of[bay]), bay)

    def _fit(self, cid: str, stack) -> tuple:
        """How good a stack is for a container: lower is better."""
        here = self.stacks[stack]
        if self.policy == "shortest_stack":
            return (len(here),)
        if not here:
            return (1, 0.0, 0)
        first_out = min(self.due[x] for x in here)
        if self.due[cid] <= first_out:                               # it goes on top and is out before any below it
            gap = 0.0 if first_out == LATE else first_out - self.due[cid]
            return (0, gap, len(here))
        return (2, -first_out, len(here))                           # it will be in the way: of the latest leaver, then

    def _why_bay(self, cid: str, bay, candidates) -> str:
        """Why the policy chose this bay of the ones with room, as a clause. It reads, and changes nothing."""
        n = len(candidates)
        if n == 1:
            return "it is the only bay with room"
        score = self._bay_score(cid, bay)
        if self.policy == "shortest_stack":
            return f"of the {n} bays with room, its lowest stack is the lowest, {score[0]} high"
        if score[0] == 0:
            others = sum(1 for b in candidates if b != bay and self._bay_score(cid, b)[0] == 0)
            return (f"of the {n} bays with room, " + ("it has the best stack for it" if others else "it alone has a stack for it")
                    + ": one it can go on top of and still leave before anything under it")
        if score[0] == 1:
            return f"of the {n} bays with room, none has a stack it could go on top of and leave first, and this one has an empty stack"
        return f"of the {n} bays with room, every stack holds a container that leaves before it, and this bay's leaves last"

    def _why(self, cid: str, stack) -> str:
        """Why the policy chose this stack, as the end of a sentence that begins "... is set"."""
        here = self.stacks[stack]
        if self.policy == "shortest_stack":
            return f"on the lowest stack of the bay that has room, {len(here)} high" if here else "on the ground: the bay has an empty stack, the lowest there is"
        when = lambda t: "at no known time" if t == LATE else f"at {_clock(t)}" if t < 86_400 else f"on day {int(t // 86_400) + 1} at {_clock(t % 86_400)}"
        late = self.now is not None and self.due[cid] < self.now()                    # it should have left already
        mine = f"was due out {when(self.due[cid])}" if late else f"leaves {when(self.due[cid])}"
        if not here:
            return "on the ground: no stack of the bay holds only containers that leave after it"
        first_out = min(self.due[x] for x in here)
        first = next(x for x in here if self.due[x] == first_out)
        if self.due[cid] <= first_out:
            if first_out == LATE:
                return f"on {here[-1]}: nothing in that stack has a time to leave" + (", nor has it" if self.due[cid] == LATE else f", and it {mine}")
            if self.due[cid] == first_out:
                return f"on {here[-1]}: it {mine}, and nothing under it leaves sooner ({first} leaves at the same time)"
            return f"on {here[-1]}: it {mine}, before anything under it (the first of them, {first}, {when(first_out)})"
        sooner = "which leaves sooner" if first == here[-1] else f"although {first} under it leaves sooner"
        return (f"on {here[-1]}, {sooner} ({when(first_out)}; this one {when(self.due[cid])}): every stack with room holds a container that "
                "leaves before it, and this stack's leaves last. It will be in the way then")

    def _best_stack(self, cid: str, stacks) -> tuple:
        return min(stacks, key=lambda s: (self._fit(cid, s), s))

    def _bay_score(self, cid: str, bay) -> tuple:
        open_stacks = [s for s in self.bays[bay] if len(self.stacks[s]) < self.max_tier[bay]]
        fits = sorted(self._fit(cid, s) for s in open_stacks)
        best = fits[min(self.reserved.get(bay, 0), len(fits) - 1)]    # the containers already on their way take the best stacks
        if self.policy == "shortest_stack":                          # count them stack by stack
            heights = sorted(len(self.stacks[s]) for s in self.bays[bay])
            for _ in range(self.reserved.get(bay, 0)):
                heights[0] += 1
                heights.sort()
            best = (heights[0],)
        return (*best, self.reserved.get(bay, 0))


def _clock(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}"
