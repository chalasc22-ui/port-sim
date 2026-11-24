"""The gate module: which truck is let in, in what order, and what is checked on the way in and out.

Written from what the gate owner described in the check-ins, for the owner to replace or adopt.

* Every step is written to the terminal's event log, so a truck and its container can be followed (Oct 15).
* Two ways of calling trucks to a lane, the two the owner compares (Oct 15, Oct 22):
      fifo          in the order they arrive
      appointment   trucks with an appointment first, by appointment time, once their time has come;
                    a truck that is early waits for its time; a truck without an appointment goes
                    after them, but is not passed over for more than 20 minutes (Oct 29)
* At the lane (Oct 22, Oct 29): the plate is scanned and compared with the booking; the container is
  looked up in the terminal's records (Dr. Wang, Oct 15: the truck must be here for a container the
  terminal knows); a truck more than 15 minutes late for its appointment goes to the trouble desk and
  comes back without one; a damaged export is turned away for repair; the truck is weighed; the toll
  is paid, and a truck that cannot pay is turned away and must book again.
* A weight that does not match the papers sends the truck to the help desk to be weighed again (Oct 29).
* A truck that has passed the checks waits in the holding area (the owner's Oct 15 idea) until the terminal can
  serve it: a truck spot is free and, for a pick-up, its container has reached the exchange zone. So a
  truck that must wait does it off the road, and the gate lanes stay free for the next truck.
* On the way out the truck is weighed again and the container on it is checked against its booking
  (Oct 22, Oct 29; Dr. Wang: it must leave with the right container).

A disruption for what-if runs: close_lane(n, seconds) shuts one lane in. A truck being checked in it
is finished first; no other is called to it or driven in through it until it opens again.

A booked truck may not come at all (Truck.no_show: 14 percent of them in the shift at real scale). The gate
never sees it; what happens to the container fetched for it is the scheduler's rule (sim/terminal.py).
The time a check takes may vary from truck to truck (check_time; the calibrated numbers of sim/params.py).

Assumptions, the owner's to set: TROUBLE_S, TOLL_USD, WEIGHT_TOLERANCE, two lanes in and two out (the map's
four gate cells). The trouble desk, the holding area and the two scales have no cells on the map (the
layout was frozen on Nov 13 before their places were decided), so they take time here but no space. A truck
is on the map from the moment it drives in.
"""

from __future__ import annotations

from terminal_layout.timing import GATE_SERVICE_S

GRACE_S = 900.0             # up to 15 minutes late is on time (Oct 22)
SAFEGUARD_S = 1200.0        # a truck without an appointment is passed over for 20 minutes at most (Oct 29)
TROUBLE_S = 600.0           # time a problem takes at the trouble desk (assumption)
TOLL_USD = 25.0             # per entry (assumption)
WEIGHT_TOLERANCE = 0.05     # measured against declared (assumption)


class Gate:
    def __init__(self, clock, log, policy: str = "appointment", lanes: int = 2, check_s: float = GATE_SERVICE_S, check_time=None):
        """check_time(truck id, n): seconds for that truck's nth check, if checks vary; otherwise each takes check_s."""
        if policy not in ("fifo", "appointment"):
            raise ValueError(f"unknown gate policy {policy!r}: choose fifo or appointment")
        self.clock, self.log, self.policy, self.check_s = clock, log, policy, check_s
        self._check_time, self._checks = check_time, {}
        self.queue: list = []                    # trucks at the gate, not yet called to a lane
        self.lane: list = [None] * lanes         # the truck in each lane: being checked, or driving in
        self.holding: list = []                  # checked and waiting to be called in, oldest first
        self.desk: list = []
        self.trucks: dict[str, object] = {}
        self.tolls_usd = 0.0
        self.shut: dict[int, float] = {}         # lane -> closed until this time (a disruption)
        self.closures: list[dict] = []           # {"lane", "t0", "t1"}, for the replay
        self.turned_away: list[tuple] = []       # (truck, why)
        self.exit_problems: list[tuple] = []
        self.find = lambda container_id: None    # the terminal's records; the scheduler sets these four
        self.spot_for = lambda truck: None       # a free truck spot, or None
        self.why_not = lambda truck: "the terminal is not ready for it"       # why spot_for said no, in words
        self.on_cleared = lambda truck: None
        self.on_enter = lambda truck, lane, spot: None
        self.on_turned_away = lambda truck, why: None

    # ---- what the scheduler calls ------------------------------------------------------------------
    def expect(self, truck) -> None:
        self.trucks[truck.id] = truck
        self.clock.at(truck.arrives_s, self._arrive, truck)

    def _check(self, truck) -> float:
        """How long this check of this truck takes."""
        if self._check_time is None:
            return self.check_s
        n = self._checks[truck.id] = self._checks.get(truck.id, 0) + 1
        return self._check_time(truck.id, n)

    def lane_clear(self, lane: int) -> None:
        """The truck that drove in has left the gate cell: the lane can take the next one."""
        self.lane[lane] = None
        self._enter()
        self._call()

    def recheck(self) -> None:
        """Something changed inside (a truck spot freed, a container reached the exchange): look at the holding area again."""
        self._enter()

    def close_lane(self, lane: int, seconds: float) -> None:
        """A disruption: gate lane `lane` (counted from 1) takes no truck for so long."""
        i = lane - 1
        if not 0 <= i < len(self.lane):
            raise ValueError(f"the gate has lanes 1 to {len(self.lane)}, not {lane}")
        until = max(self.shut.get(i, 0.0), self.clock.now + seconds)
        self.shut[i] = until
        self.closures.append({"lane": lane, "t0": self.clock.now, "t1": until})
        self.log.add("gate", f"lane {lane}", f"Gate lane {lane} is closed for {seconds / 60:.0f} min", disruption=True)
        self.clock.at(until, self._lane_open, i)

    def _lane_open(self, i: int) -> None:
        if i in self.shut and self.clock.now >= self.shut[i] - 1e-6:
            del self.shut[i]
            self.log.add("gate", f"lane {i + 1}", f"Gate lane {i + 1} is open again", disruption=True)
            self._enter()
            self._call()

    def exit(self, truck, carrying, done) -> None:
        """The check on the way out. carrying: the Container on the truck, or None. done(truck) when it may go."""
        tare = truck.declared_t
        expect_id = truck.picks
        measured = tare + (carrying.weight_t if carrying else 0.0)
        expected = tare + (self.find(expect_id).weight_t if expect_id and self.find(expect_id) else 0.0)
        wrong = (carrying.id if carrying else None) != expect_id
        off = abs(measured - expected) > WEIGHT_TOLERANCE * max(expected, 1.0)
        truck.times["at_exit"] = self.clock.now
        truck.notes.append(f"weighed out at {measured:.1f} t")
        if wrong or off:
            why = "the wrong container" if wrong else "an exit weight that does not match"
            self.exit_problems.append((truck.id, why))
            self.log.add("gate", truck.id, f"{truck.id} is stopped at the gate out: {why}", container=expect_id)
            self.clock.after(self._check(truck) + TROUBLE_S, done, truck)
            return
        self.log.add("gate", truck.id, f"{truck.id} is checked out: {measured:.1f} t"
                                       + (f", carrying {carrying.id}" if carrying else ", empty"), quiet=True)
        self.clock.after(self._check(truck), done, truck)

    # ---- the queue and the lanes -------------------------------------------------------------------
    def _arrive(self, truck) -> None:
        truck.state, truck.times["arrived"] = "queue", self.clock.now
        truck.times["queued"] = self.clock.now
        self.queue.append(truck)
        what = f"to collect {truck.picks}" if truck.picks else f"with {truck.drops.id}"
        appt = "no appointment" if truck.appointment_s is None else f"appointment at {_clock(truck.appointment_s)}"
        self.log.add("gate", truck.id, f"{truck.id} arrives at the gate {what} ({appt})", container=truck.picks or truck.drops.id)
        if self.policy == "appointment" and truck.appointment_s is not None and truck.appointment_s > self.clock.now:
            self.log.add("gate", truck.id, f"{truck.id} is early and waits for its time", quiet=True)
            self.log.explain("gate", [truck.id], f"{truck.id} is early: its appointment is at {_clock(truck.appointment_s)}, and a booked truck is called to a lane when its time has come.")
            self.clock.at(truck.appointment_s, self._call)
        self._call()

    def order(self) -> list:
        """The waiting trucks in the order they would be called now. Early trucks are not in it."""
        now = self.clock.now
        if self.policy == "fifo":
            return sorted(self.queue, key=lambda t: (t.times["queued"], t.id))
        ranked = []
        for t in self.queue:
            if t.appointment_s is None:
                waited_out = now - t.times["queued"] >= SAFEGUARD_S
                ranked.append(((0 if waited_out else 2, t.times["queued"], t.id), t))
            elif t.appointment_s <= now:
                ranked.append(((1, t.appointment_s, t.times["queued"], t.id), t))
        return [t for _, t in sorted(ranked, key=lambda r: r[0])]

    def _call(self) -> None:
        for lane, held in enumerate(self.lane):
            waiting = self.order()
            if held is None and waiting and lane not in self.shut:
                truck = waiting[0]
                self.log.explain("gate", [truck.id], f"{truck.id} is called to lane {lane + 1}: {self._why_called(truck, waiting)}.")
                self.queue.remove(truck)
                self.lane[lane] = truck.id
                truck.state, truck.times["called"] = "lane", self.clock.now
                self.clock.after(self._check(truck), self._checked, truck, lane)

    def _why_called(self, truck, waiting: list) -> str:
        """Why this truck is the next to a lane, by the rule in force."""
        n, now = len(waiting), self.clock.now
        if self.policy == "fifo":
            return "it is the only truck waiting" if n == 1 else f"it has waited the longest of the {n} trucks at the gate (first in, first out)"
        if truck.appointment_s is None:
            waited = now - truck.times["queued"]
            if waited >= SAFEGUARD_S:
                return f"it has no appointment and has waited {waited / 60:.0f} minutes, the longest a truck without one is passed over"
            return "it has no appointment, and no booked truck whose time has come is waiting"
        return f"its appointment, at {_clock(truck.appointment_s)}, is the earliest that has come" + (f" among the {n} trucks waiting" if n > 1 else "")

    def _checked(self, truck, lane: int) -> None:
        problem = self._problem(truck)
        self.lane[lane] = None
        if problem:
            why, outcome = problem
            if outcome == "away":
                self._turn_away(truck, why)
            else:
                truck.state = "desk"
                truck.times.setdefault("desk", []).append(self.clock.now)
                self.desk.append(truck)
                self.log.add("gate", truck.id, f"{truck.id} goes to the trouble desk: {why}")
                self.log.explain("gate", [truck.id], f"{truck.id} is sent to the trouble desk for {TROUBLE_S / 60:.0f} minutes: {why}. "
                                 + ("Its appointment is gone: it then waits with the trucks that have none." if outcome == "requeue" else "It is then weighed again."))
                self.clock.after(TROUBLE_S, self._desk_done, truck, why, outcome)
            self._call()
            return
        self.tolls_usd += TOLL_USD
        truck.state, truck.times["cleared"] = "holding", self.clock.now
        truck.notes.append(f"weighed in at {self._weigh_in(truck):.1f} t, toll paid")
        self.holding.append(truck)
        self.log.add("gate", truck.id, f"{truck.id} is checked in: plate, booking, weight and toll in order", quiet=True)
        self.log.explain("gate", [truck.id, truck.picks or truck.drops.id], f"{truck.id} passes the gate: its plate matches the booking, the terminal knows its container, "
                         f"it weighs {self._weigh_in(truck):.1f} t as declared, and the toll of ${TOLL_USD:.0f} is paid.")
        self.on_cleared(truck)
        self._enter()
        self._call()

    def _problem(self, truck) -> tuple | None:
        """The first thing wrong with a truck at the lane, and what happens: turned away, back to the queue, or on."""
        done = truck.times.setdefault("resolved", [])
        if truck.fault == "plate_mismatch":
            return "its plate does not match the booking", "away"
        c = self.find(truck.picks) if truck.picks else truck.drops
        booked = c is not None and (c.truck == truck.id if truck.picks else c.vessel is not None)
        if not booked:
            return "the terminal has no booking for its container", "away"
        late = self.policy == "appointment" and truck.appointment_s is not None \
            and truck.times["arrived"] > truck.appointment_s + GRACE_S
        if late and "late" not in done:
            return "more than 15 minutes late for its appointment", "requeue"
        if truck.fault == "damaged" and truck.drops:
            return "its container is damaged and goes for repair", "away"
        if truck.fault == "weight_mismatch" and "weight" not in done:
            return "its weight does not match its papers", "reweigh"
        if truck.fault == "no_payment":
            return "it cannot pay the toll and must book again", "away"
        return None

    def _weigh_in(self, truck) -> float:
        return truck.declared_t + (truck.drops.weight_t if truck.drops else 0.0)

    def _desk_done(self, truck, why: str, outcome: str) -> None:
        self.desk.remove(truck)
        if outcome == "away":
            self._turn_away(truck, why)
            return
        truck.times["resolved"].append("late" if outcome == "requeue" else "weight")
        if outcome == "requeue":
            truck.times["booked"] = truck.appointment_s    # what it had booked, for the record
            truck.appointment_s = None                 # its appointment is gone; it waits with the others
        truck.state = "queue"
        self.queue.append(truck)
        self.log.add("gate", truck.id, f"{truck.id} leaves the trouble desk and joins the queue again", quiet=True)
        self._call()

    def _turn_away(self, truck, why: str) -> None:
        truck.state, truck.times["gone"] = "turned_away", self.clock.now
        self.turned_away.append((truck.id, why))
        self.log.add("gate", truck.id, f"{truck.id} is turned away: {why}", container=truck.picks or (truck.drops and truck.drops.id))
        self.log.explain("gate", [truck.id, truck.picks or (truck.drops and truck.drops.id)], f"{truck.id} is turned away: {why}.")
        self.on_turned_away(truck, why)

    def _enter(self) -> None:
        """Call trucks in from the holding area, oldest clearance first, while the terminal can serve them
        and a lane is free to drive through."""
        for truck in list(self.holding):
            lane = next((i for i, held in enumerate(self.lane) if held is None and i not in self.shut), None)
            if lane is None:
                return
            spot = self.spot_for(truck)
            if spot is None:
                if "held" not in truck.times:
                    truck.times["held"] = self.clock.now
                    self.log.add("gate", truck.id, f"{truck.id} waits in the holding area until the terminal is ready for it", quiet=True)
                    self.log.explain("gate", [truck.id], f"{truck.id} waits in the holding area, off the road: {self.why_not(truck)}.")
                continue
            self.holding.remove(truck)
            self.lane[lane] = truck.id
            truck.state, truck.times["entered"] = "inside", self.clock.now
            self.log.add("gate", truck.id, f"{truck.id} drives in through gate lane {lane + 1}")
            self.log.explain("gate", [truck.id], f"{truck.id} is called in to truck spot {tuple(spot)}: "
                             + (f"its container {truck.picks} has reached the exchange" if truck.picks else f"a spot is free for the export it brings, {truck.drops.id}") + ".")
            self.on_enter(truck, lane, spot)


def _clock(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}"
