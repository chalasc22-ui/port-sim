"""The ship arrival module (sim/ship_arrival.py): the anchorage, the tide, the channel and the berths."""

import pytest

from sim.kernel import Clock, Container, EventLog, Vessel
from sim.ship_arrival import ShipArrival, Tide

RISING = Tide(mean_depth_m=12.5, amplitude_m=0.9, high_water_s=9900.0)     # deep enough for an 11.9 m ship from about 2000 s


def port(layout, work_min=30, **settings):
    """A ShipArrival on its own: with no terminal behind it, a ship's cargo work takes a fixed time."""
    clock = Clock()
    ships = ShipArrival(clock, EventLog(clock), layout, **settings)
    ships.on_berthed = lambda v: clock.after(work_min * 60, ships.work_done, v)
    return clock, ships


def test_fcfs_holds_ships_behind_one_waiting_for_the_tide_and_earliest_eligible_lets_them_pass(dames):
    cleared = {}
    for policy in ("fcfs", "earliest_eligible"):
        clock, ships = port(dames, policy=policy, tide=RISING)
        deep, light = Vessel("DEEP", 0.0, draft_m=11.9), Vessel("LIGHT", 300.0, draft_m=10.0)
        ships.expect(deep)
        ships.expect(light)
        clock.run()
        cleared[policy] = (deep.times["cleared"], light.times["cleared"])
        assert deep.state == light.state == "gone" and deep.berth != light.berth
        assert deep.times["reasons"][0] == (0.0, "not enough water")
    assert cleared["fcfs"][0] == cleared["earliest_eligible"][0] == 2040.0       # the tide decides for the deep ship
    assert cleared["fcfs"][1] == 2040.0 and cleared["earliest_eligible"][1] == 300.0


def test_water_must_be_deep_enough_for_the_whole_passage(dames):
    clock, ships = port(dames, tide=Tide(mean_depth_m=12.5, amplitude_m=0.9, high_water_s=0.0), transit_s=600.0)
    falling = Vessel("V", 0.0, draft_m=12.0)                   # needs 13.0 m; the tide falls from 13.4 m
    assert ships.depth_ok(falling, 0.0) and ships.depth_ok(falling, 6000.0)
    assert not ships.depth_ok(falling, 6500.0)                 # deep enough now, but not ten minutes later
    assert ships.tide.depth(6500.0) >= 13.0 > ships.tide.depth(7100.0)


def test_the_channel_takes_as_many_ships_as_it_has_slots(dames):
    for slots, second in ((2, 0.0), (1, 600.0)):
        clock, ships = port(dames, channel_slots=slots)
        a, b = Vessel("A", 0.0), Vessel("B", 0.0)
        ships.expect(a)
        ships.expect(b)
        clock.run()
        assert a.times["cleared"] == 0.0 and b.times["cleared"] == second


def test_with_both_berths_taken_a_third_ship_waits_until_one_leaves(dames):
    clock, ships = port(dames)
    a, b, c = Vessel("A", 0.0), Vessel("B", 0.0), Vessel("C", 0.0)
    for v in (a, b, c):
        ships.expect(v)
    clock.run()
    assert {a.berth, b.berth} == {"B1", "B2"}
    assert c.times["cleared"] == a.times["cast_off"] == 2400.0 and c.berth == a.berth      # 10 min in, 30 min of work
    assert ("no free berth" in [why for _, why in c.times["reasons"]])


def test_a_ship_that_asked_to_leave_first_goes_before_one_that_asked_to_enter_later(dames):
    """Oct 22: boat A asks to leave at 9:50, boat B asks to enter at 9:55; A goes first."""
    clock, ships = port(dames, channel_slots=1)
    a, x, b = Vessel("A", 0.0), Vessel("X", 2300.0), Vessel("B", 2500.0)
    for v in (a, x, b):
        ships.expect(v)
    clock.run()
    assert a.times["work_done"] == 2400.0           # A asks to leave while X is in the channel
    assert x.times["berthed"] == 2900.0             # the channel is free again
    assert a.times["cast_off"] == 2900.0            # A asked before B, so A gets the channel
    assert b.times["cleared"] == 3500.0 and b.berth == "B1"      # B follows when A is clear, into A's berth


def test_a_ship_out_of_radio_contact_is_not_cleared_until_contact_is_back(dames):
    for outage, backup, expected in (((0.0, 5000.0), 600.0, 600.0), ((0.0, 200.0), 600.0, 200.0)):
        clock, ships = port(dames, backup_comms_s=backup)
        v = Vessel("V", 0.0, comms_out=outage)
        ships.expect(v)
        clock.run()
        assert v.times["cleared"] == expected and v.times["reasons"][0][1] == "no radio contact"


def test_a_ship_planned_for_a_berth_waits_for_that_berth(dames):
    clock, ships = port(dames)
    a, b = Vessel("A", 0.0, berth="B2"), Vessel("B", 60.0, berth="B2")
    ships.expect(a)
    ships.expect(b)
    clock.run()
    assert a.berth == b.berth == "B2" and b.times["cleared"] == a.times["cast_off"]


def test_the_cranes_of_the_berth_share_the_discharge_and_a_planned_crane_is_kept(dames):
    clock, ships = port(dames)
    boxes = [Container(f"C{i}", "import") for i in range(7)]
    boxes[0].crane = "QC6"
    v = Vessel("V", 0.0, berth="B2", discharge=boxes)
    plan = ships.discharge_plan(v)
    assert {crane: [c.id for c in cs] for crane, cs in plan.items()} == {"QC4": ["C3", "C6"], "QC5": ["C1", "C4"], "QC6": ["C0", "C2", "C5"]}
    with pytest.raises(ValueError, match="unknown ship policy"):
        ShipArrival(Clock(), None, dames, policy="biggest_first")
