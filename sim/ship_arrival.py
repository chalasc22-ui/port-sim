"""The ship arrival module (the group's "demand and schedules" module): which ship comes to which berth
and when, and when it may leave.

Written from what the ship arrival owner described in the check-ins, for the owner to replace or adopt.

* A ship arrives at its arrival time and waits at the anchorage until it is cleared to enter (Oct 15).
* Before it is cleared (the owner's try_entry pseudocode, Oct 29): radio contact must be up, the water must be
  deep enough for its draft for the whole passage, a berth must be free, and the channel must have room.
* The channel takes two ships at a time (Oct 29; one on Oct 22). A ship leaving needs a channel slot too.
  Requests are served in the order they were made: a ship that asked to leave at 9:50 goes before one
  that asked to enter at 9:55 (the owner's Oct 22 example).
* Two policies for the anchorage (Oct 15):
      fcfs               ships enter strictly in arrival order; a ship that is not ready holds up those behind it
      earliest_eligible  a ship that is not ready is passed over until it is
* With both berths taken a third ship waits until one leaves (Oct 22).
* A ship may leave when its work is done: every container it came to discharge is off, and its exports
  that were in the yard at the cut-off are on (Oct 15).
* Radio failure (raised Oct 22, answered Oct 29): a ship out of contact is not cleared until contact is
  back, by itself or over the backup link after backup_comms_s.

Assumptions, the owner's to set: the tide (depth, range, time of high water), the clearance under the keel, the
passage time, the backup link time. Not built: a ship stuck in the channel, ship length against berth
length, pilots and tugs.

sim/params.py has published numbers for the first three, used by the shift at real scale: NOAA's tide
range at Dames Point on 40 ft of water at the berths, the pilots' 2.5 ft under the keel, and 85 minutes
from the sea buoy (docs/data-sheet.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tide:
    """Depth of the channel over time: a plain sine tide. Every number here is an assumption."""
    mean_depth_m: float = 12.5
    amplitude_m: float = 0.9
    period_s: float = 44_712.0           # 12 h 25 min
    high_water_s: float = 0.0

    def depth(self, t: float) -> float:
        return self.mean_depth_m + self.amplitude_m * math.cos(2 * math.pi * (t - self.high_water_s) / self.period_s)


class ShipArrival:
    def __init__(self, clock, log, layout, policy: str = "fcfs", channel_slots: int = 2, transit_s: float = 600.0,
                 tide: Tide | None = None, clearance_m: float = 1.0, backup_comms_s: float = 600.0):
        if policy not in ("fcfs", "earliest_eligible"):
            raise ValueError(f"unknown ship policy {policy!r}: choose fcfs or earliest_eligible")
        self.clock, self.log, self.layout, self.policy = clock, log, layout, policy
        self.channel_slots, self.transit_s = channel_slots, transit_s
        self.tide, self.clearance_m, self.backup_comms_s = tide or Tide(), clearance_m, backup_comms_s
        self.berth: dict[str, str | None] = dict.fromkeys(layout.berths)      # berth -> the ship at it or on its way
        self.cranes = {b: [crane for crane, _ in cranes] for b, cranes in layout.berths.items()}
        self.vessels: dict[str, object] = {}
        self.anchorage: list = []               # ships waiting to enter, in arrival order
        self.leaving: list = []                 # (time asked, ship) waiting for a channel slot to leave
        self.in_channel = 0
        self.on_cleared = lambda vessel: None   # the scheduler sets these three
        self.on_berthed = lambda vessel: None
        self.on_gone = lambda vessel: None
        self._wake_t: float | None = None
        self._behind: dict[str, str] = {}        # ship -> the ship it was last told it may not pass

    # ---- what the scheduler calls ------------------------------------------------------------------
    def expect(self, vessel) -> None:
        self.vessels[vessel.id] = vessel
        self.clock.at(vessel.arrives_s, self._arrive, vessel)

    def discharge_plan(self, vessel) -> dict[str, list]:
        """Which quay crane takes each container off: the berth's cranes in turn."""
        cranes = self.cranes[vessel.berth]
        plan = {crane: [] for crane in cranes}
        for i, c in enumerate(vessel.discharge):
            c.crane = c.crane if c.crane in plan else cranes[i % len(cranes)]
            plan[c.crane].append(c)
        return plan

    def work_done(self, vessel) -> None:
        """The scheduler reports the ship's cargo work finished: it asks to leave."""
        vessel.times["work_done"] = self.clock.now
        self.leaving.append((self.clock.now, vessel))
        self.log.add("ship", vessel.id, f"{vessel.id} has finished at {vessel.berth} and asks to leave")
        self._review()

    def depth_ok(self, vessel, t: float | None = None) -> bool:
        """Deep enough for this ship from t for the whole passage."""
        t = self.clock.now if t is None else t
        need = vessel.draft_m + self.clearance_m
        steps = max(1, int(self.transit_s // 60))
        return all(self.tide.depth(t + self.transit_s * i / steps) >= need for i in range(steps + 1))

    def in_contact(self, vessel, t: float | None = None) -> bool:
        t = self.clock.now if t is None else t
        if not vessel.comms_out:
            return True
        lost, back = vessel.comms_out
        return not lost <= t < min(back, lost + self.backup_comms_s)

    # ---- the anchorage, the channel and the berths -----------------------------------------------
    def _arrive(self, vessel) -> None:
        vessel.state, vessel.times["arrived"] = "anchorage", self.clock.now
        self.anchorage.append(vessel)
        self.log.add("ship", vessel.id, f"{vessel.id} arrives at the anchorage (draft {vessel.draft_m:.1f} m, "
                                        f"{len(vessel.discharge)} to discharge, {len(vessel.load)} to load)")
        self._review()

    def _free_berth(self, vessel) -> str | None:
        wanted = [vessel.berth] if vessel.berth else list(self.berth)
        return next((b for b in wanted if self.berth[b] is None), None)

    def _not_ready(self, vessel) -> str | None:
        if not self.in_contact(vessel):
            return "no radio contact"
        if not self.depth_ok(vessel):
            return "not enough water"
        if self._free_berth(vessel) is None:
            return "no free berth"
        return None

    def _review(self) -> None:
        """Grant what can be granted, in the order the requests were made."""
        granted = True
        while granted and self.in_channel < self.channel_slots:
            granted = False
            requests = sorted([(t, 0, v.id, v) for t, v in self.leaving] + [(v.times["arrived"], 1, v.id, v) for v in self.anchorage])
            head_waits = False
            for _, entering, _, v in requests:
                if not entering:                              # a ship leaving needs only a channel slot
                    self._cast_off(v)
                    granted = True
                    break
                if not head_waits:
                    why = self._not_ready(v)
                    if why is None:
                        self._clear(v)
                        granted = True
                        break
                    self._note(v, why)
                    head_waits, head = self.policy == "fcfs", v        # nobody passes the ship at the head of the queue
                elif self._behind.get(v.id) != head.id:
                    self._behind[v.id] = head.id
                    self.log.explain("ship", [v.id, head.id], f"{v.id} may not enter yet: ships enter in the order they arrived, and {head.id}, "
                                                             "which came first, is still waiting.")
        for v in self.anchorage:
            if self.in_channel >= self.channel_slots and self._not_ready(v) is None:
                self._note(v, "the channel is full")
        self._wake_for_tide_and_radio()

    def _note(self, vessel, why: str) -> None:
        if vessel.times.get("held_by") != why:
            vessel.times["held_by"] = why
            vessel.times.setdefault("reasons", []).append((self.clock.now, why))
            self.log.add("ship", vessel.id, f"{vessel.id} waits: {why}")
            self.log.explain("ship", [vessel.id], self._detail(vessel, why))

    def _detail(self, vessel, why: str) -> str:
        """The reason a ship waits, with the numbers behind it."""
        now = self.clock.now
        if why == "not enough water":
            need = vessel.draft_m + self.clearance_m
            ok = next((now + 60.0 * k for k in range(1, int(self.tide.period_s // 60) + 2) if self.depth_ok(vessel, now + 60.0 * k)), None)
            return (f"{vessel.id} waits for the tide: it draws {vessel.draft_m:.1f} m and needs {need:.1f} m of water for the whole "
                    f"{self.transit_s / 60:.0f} minutes of the passage. The channel has {self.tide.depth(now):.1f} m now"
                    + (f", and enough from {_clock(ok)}." if ok is not None else "."))
        if why == "no free berth":
            wanted = [vessel.berth] if vessel.berth else list(self.berth)
            return f"{vessel.id} waits for a berth: " + " and ".join(f"{b} is taken by {self.berth[b]}" for b in wanted) + "."
        if why == "no radio contact":
            lost, back = vessel.comms_out
            return (f"{vessel.id} is not cleared: no radio contact since {_clock(lost)}. Contact comes back by itself or over the backup link, "
                    f"at {_clock(min(back, lost + self.backup_comms_s))}.")
        if why == "the channel is full":
            return f"{vessel.id} is ready, but the channel takes {self.channel_slots} ships at a time and {self.in_channel} are in it."
        return f"{vessel.id} waits: {why}."

    def _clear(self, vessel) -> None:
        berth = self._free_berth(vessel)
        passed = [v.id for v in self.anchorage if v.times["arrived"] < vessel.times["arrived"]]
        self.log.explain("ship", [vessel.id], f"{vessel.id} is cleared for {berth}: the channel has {self.tide.depth(self.clock.now):.1f} m for the "
                         f"{vessel.draft_m + self.clearance_m:.1f} m it needs, {berth} is free and the channel has room."
                         + (f" It passes {' and '.join(passed)}, still waiting: a ship that is ready goes first." if passed else ""))
        self.anchorage.remove(vessel)
        self.berth[berth] = vessel.id
        self.in_channel += 1
        vessel.berth, vessel.state, vessel.times["cleared"] = berth, "inbound", self.clock.now
        vessel.times.pop("held_by", None)
        waited = self.clock.now - vessel.times["arrived"]
        self.log.add("ship", vessel.id, f"{vessel.id} is cleared for {berth} after {waited / 60:.0f} min at anchor and enters the channel")
        self.on_cleared(vessel)
        self.clock.after(self.transit_s, self._berthed, vessel)

    def _berthed(self, vessel) -> None:
        self.in_channel -= 1
        vessel.state, vessel.times["berthed"] = "berthed", self.clock.now
        self.log.add("ship", vessel.id, f"{vessel.id} berths at {vessel.berth}")
        self.on_berthed(vessel)
        self._review()

    def _cast_off(self, vessel) -> None:
        self.leaving = [(t, v) for t, v in self.leaving if v is not vessel]
        self.berth[vessel.berth] = None
        self.in_channel += 1
        vessel.state, vessel.times["cast_off"] = "outbound", self.clock.now
        vessel.times.pop("held_by", None)
        self.log.add("ship", vessel.id, f"{vessel.id} leaves {vessel.berth}")
        self.clock.after(self.transit_s, self._gone, vessel)

    def _gone(self, vessel) -> None:
        self.in_channel -= 1
        vessel.state, vessel.times["gone"] = "gone", self.clock.now
        self.log.add("ship", vessel.id, f"{vessel.id} is clear of the channel", quiet=True)
        self.on_gone(vessel)
        self._review()

    def _wake_for_tide_and_radio(self) -> None:
        """Look again when the tide or a radio comes back, since no other event will."""
        now, times = self.clock.now, []
        for v in self.anchorage:
            if not self.in_contact(v):
                times.append(min(v.comms_out[1], v.comms_out[0] + self.backup_comms_s))
            elif not self.depth_ok(v):
                t = next((now + 60.0 * k for k in range(1, int(self.tide.period_s // 60) + 2) if self.depth_ok(v, now + 60.0 * k)), None)
                if t is None:
                    self._note(v, "never enough water on this tide")
                else:
                    times.append(t)
        if times and (self._wake_t is None or self._wake_t <= now or min(times) < self._wake_t):
            self._wake_t = min(times)
            self.clock.at(self._wake_t, self._review)


def _clock(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}"
