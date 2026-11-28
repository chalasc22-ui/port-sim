"""The gate module (sim/gate.py): the order trucks are called in, the checks, the trouble desk, the way out."""

import pytest

from sim.gate import GRACE_S, SAFEGUARD_S, TOLL_USD, TROUBLE_S, Gate
from sim.kernel import Clock, Container, EventLog, Truck

CHECK_S = 30.0


class Yard:
    """Stands in for the terminal behind the gate: it knows the bookings and takes every truck at once."""

    def __init__(self, policy="appointment", lanes=1):
        self.clock = Clock()
        self.gate = Gate(self.clock, EventLog(self.clock), policy=policy, lanes=lanes)
        self.books, self.entered, self.ready = {}, [], lambda truck: True
        self.gate.find = self.books.get
        self.gate.spot_for = lambda truck: (0, 0) if self.ready(truck) else None
        self.gate.on_enter = self.enter

    def enter(self, truck, lane, spot):
        self.entered.append((truck.id, self.clock.now))
        self.clock.after(5.0, self.gate.lane_clear, lane)              # it takes five seconds to drive off the gate cell

    def collects(self, n, arrive, appointment=None, fault=None):
        c = Container(f"IMP-{n}", "import", truck=f"T{n}")
        self.books[c.id] = c
        t = Truck(f"T{n}", arrive, appointment_s=appointment, picks=c.id, fault=fault)
        self.gate.expect(t)
        return t

    def brings(self, n, arrive, appointment=None, fault=None, booked=True):
        c = Container(f"EXP-{n}", "export", vessel="ALPHA" if booked else None, weight_t=20.0)
        t = Truck(f"T{n}", arrive, appointment_s=appointment, drops=c, fault=fault)
        self.gate.expect(t)
        return t

    def run(self):
        self.clock.run()
        return dict(self.entered)


def test_trucks_with_an_appointment_are_called_before_one_without(dames):
    y = Yard()
    y.collects(1, 0.0, appointment=0.0)
    y.collects(2, 1.0)                                # no appointment, here first
    y.collects(3, 2.0, appointment=0.0)
    assert list(y.run()) == ["T1", "T3", "T2"]


def test_a_truck_without_an_appointment_is_not_passed_over_for_more_than_twenty_minutes(dames):
    y = Yard()
    y.collects(0, 0.0, appointment=0.0)
    y.collects(99, 0.0)                               # no appointment
    for k in range(1, 60):                            # a booked truck every 30 s, more than one lane can take
        y.collects(k, 30.0 * k, appointment=30.0 * k)
    entered = y.run()
    assert SAFEGUARD_S < entered["T99"] <= SAFEGUARD_S + 2 * (CHECK_S + 5.0) + CHECK_S
    passed_over = [t for t, at in entered.items() if at > entered["T99"] and y.gate.trucks[t].arrives_s < SAFEGUARD_S]
    assert len(passed_over) >= 3                      # booked trucks that were already waiting went after it


def test_an_early_truck_waits_for_its_time_and_fifo_takes_trucks_as_they_come(dames):
    y = Yard()
    y.collects(1, 100.0, appointment=600.0)
    assert y.run() == {"T1": 600.0 + CHECK_S}
    y = Yard(policy="fifo")
    y.collects(1, 100.0, appointment=600.0)
    y.collects(2, 50.0)
    assert y.run() == {"T2": 50.0 + CHECK_S, "T1": 100.0 + CHECK_S}


def test_fifteen_minutes_late_is_on_time_and_later_goes_to_the_trouble_desk(dames):
    y = Yard()
    on_time, late = y.collects(1, GRACE_S, appointment=0.0), y.collects(2, 2000.0 + GRACE_S + 1.0, appointment=2000.0)
    entered = y.run()
    assert entered["T1"] == GRACE_S + CHECK_S and "desk" not in on_time.times
    assert entered["T2"] == late.arrives_s + CHECK_S + TROUBLE_S + CHECK_S        # checked, desk, checked again
    assert late.appointment_s is None and len(late.times["desk"]) == 1


def test_a_wrong_plate_an_unknown_container_or_no_toll_is_turned_away_and_a_good_truck_pays(dames):
    y = Yard(lanes=2)
    y.collects(1, 0.0, fault="plate_mismatch")
    y.collects(2, 0.0, fault="no_payment")
    y.brings(3, 0.0, booked=False)
    y.brings(4, 0.0, fault="damaged")
    y.collects(5, 0.0)
    unknown = Truck("T6", 0.0, picks="NOBODY-KNOWS")
    y.gate.expect(unknown)
    entered = y.run()
    assert list(entered) == ["T5"] and y.gate.tolls_usd == TOLL_USD
    why = dict(y.gate.turned_away)
    assert "plate" in why["T1"] and "toll" in why["T2"] and "no booking" in why["T3"] and "damaged" in why["T4"] and "no booking" in why["T6"]
    assert unknown.state == "turned_away"


def test_a_weight_that_does_not_match_the_papers_is_weighed_again_once(dames):
    y = Yard(policy="fifo")
    t = y.brings(1, 0.0, fault="weight_mismatch")
    assert y.run() == {"T1": CHECK_S + TROUBLE_S + CHECK_S}
    assert len(t.times["desk"]) == 1 and "weighed in at 35.0 t" in t.notes[0]


def test_a_checked_truck_waits_in_the_holding_area_and_leaves_the_lane_free(dames):
    y = Yard(policy="fifo")
    y.ready = lambda truck: truck.id != "T1"          # the terminal is not ready for T1 yet
    y.collects(1, 0.0)
    y.collects(2, 10.0)
    entered = y.run()
    assert list(entered) == ["T2"] and [t.id for t in y.gate.holding] == ["T1"]
    y.ready = lambda truck: True
    y.gate.recheck()
    y.clock.run()
    assert [t for t, _ in y.entered] == ["T2", "T1"] and y.gate.holding == []


def test_the_gate_out_weighs_again_and_stops_a_truck_with_the_wrong_container(dames):
    y = Yard(policy="fifo")
    t = y.collects(1, 0.0)
    y.run()
    left = []
    right, wrong = y.books["IMP-1"], Container("IMP-X", "import", weight_t=20.0)
    y.gate.exit(t, right, lambda truck: left.append(y.clock.now))
    start = y.clock.now
    y.clock.run()
    assert left == [start + CHECK_S] and y.gate.exit_problems == []
    y.gate.exit(t, wrong, lambda truck: left.append(y.clock.now))
    y.clock.run()
    assert y.gate.exit_problems == [("T1", "the wrong container")] and left[1] == left[0] + CHECK_S + TROUBLE_S
    y.gate.exit(t, None, lambda truck: None)          # it came for a container and leaves empty
    assert y.gate.exit_problems[-1] == ("T1", "the wrong container")
    with pytest.raises(ValueError, match="unknown gate policy"):
        Gate(Clock(), None, policy="vip")
