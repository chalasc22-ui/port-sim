"""Disruptions and what-if runs: a broken-down AGV, a closed road, a crane out of service, a closed gate
lane, and the settings the control room sends (sim/control.py)."""

import pytest

from sim import control, scenario
from sim.agv import AgvFleet, Job, Traffic, Vehicle
from sim.gate import Gate
from sim.kernel import Clock, EventLog, Truck
from sim.metrics import cell_conflicts, check, summary
from sim.terminal import Terminal


def world(layout):
    clock = Clock()
    return clock, Traffic(clock, layout, EventLog(clock), None)


def put(tr, name, cell):
    v = Vehicle(name, "agv", cell)
    tr.add(v, cell)
    return v


def said(t, text):
    return [e.t for e in t.log.entries if text in e.text]


# ---- the AGV module -------------------------------------------------------------------------------
def test_a_broken_down_vehicle_holds_its_cell_until_it_is_repaired_and_then_drives_on(dames):
    clock, tr = world(dames)
    v = put(tr, "V", (10, 33))
    done = {}
    tr.send(v, (25, 33), lambda v: done.setdefault("t", clock.now))
    clock.at(20.0, tr.break_down, v, 300.0)
    clock.run(until=200.0)
    stopped = v.cell
    assert not v.moving and stopped != (25, 33) and tr.held[stopped] == "V" and "t" not in done
    clock.run()
    assert v.cell == (25, 33) and done["t"] > 320.0 and v.broken_until is None
    assert [(b["vehicle"], b["t0"], b["t1"]) for b in tr.breakdowns] == [("V", 20.0, 320.0)]
    assert [leg["path"][-1] for leg in tr.legs] == [stopped, (25, 33)]          # one leg up to where it stopped, one after the repair
    assert cell_conflicts(tr, clock.now) == []


def test_the_vehicle_behind_a_breakdown_goes_round_when_that_beats_waiting_for_the_repair(dames):
    start, dest, spot = (102, 34), (96, 17), (96, 30)           # the way round (96, 30) is 14 moves longer
    for repair_s, goes_round in ((1800.0, True), (90.0, False)):
        clock, tr = world(dames)
        straight = tr.route(start, dest)
        assert spot in straight and len(tr._bfs(start, dest, {spot})) - len(straight) == 14
        a, b = put(tr, "A", spot), put(tr, "B", start)
        arrived = {}
        tr.send(a, (96, 5), lambda v: None)                     # A is on its way south when it breaks down
        tr.break_down(a, repair_s)
        tr.send(b, dest, lambda v: arrived.setdefault("t", clock.now))
        clock.run()
        assert b.cell == dest and cell_conflicts(tr, clock.now) == []
        assert b.moves == len(straight) - 1 + (14 if goes_round else 0)
        assert (b.waited_s == 0) == goes_round and (arrived["t"] < repair_s) == goes_round
    clock, tr = world(dames)                                    # a short way round is always taken, as round any standing vehicle
    a, b = put(tr, "A", (20, 33)), put(tr, "B", (15, 33))
    tr.break_down(a, 30.0)
    tr.send(b, (25, 33), lambda v: None)
    clock.run()
    assert b.cell == (25, 33) and b.moves == 12 and b.waited_s == 0


def test_a_closed_road_is_driven_round_and_a_vehicle_with_no_way_waits_and_is_reported(dames):
    clock, tr = world(dames)
    straight = tr.route((15, 33), (25, 33))
    tr.close_road([(20, 33)])
    round_it = tr.route((15, 33), (25, 33))
    assert (20, 33) in straight and (20, 33) not in round_it and len(round_it) > len(straight)
    assert tr.route((15, 33), (20, 33)) is None and tr.moves((15, 33), (20, 33)) is None
    tr.open_road([(20, 33)])
    assert tr.route((15, 33), (25, 33)) == straight and not tr.closed

    clock, tr = world(dames)                                    # the only lane to a bay is shut for ten minutes
    v = put(tr, "V", (15, 33))
    there = {}
    tr.close_road([(23, 24)], 600.0)
    tr.send(v, (22, 24), lambda v: there.setdefault("t", clock.now))       # no error: it waits for the road
    clock.run(until=300.0)
    assert v.cell == (15, 33) and [(w["vehicle"], w["cell"], w["blocked_by"]) for w in tr.still_waiting()] == [("V", (22, 24), "a closed road")]
    assert any("has no way to (22, 24)" in e.text for e in tr.log.entries)
    clock.run()
    assert v.cell == (22, 24) and there["t"] > 600.0 and tr.still_waiting() == [] and v.waited_s == pytest.approx(600.0)
    assert [(c["cells"], c["t0"], c["t1"]) for c in tr.closures] == [([(23, 24)], 0.0, 600.0)]

    clock, tr = world(dames)                                    # a road closed while a vehicle is on its way
    v = put(tr, "V", (10, 33))
    tr.send(v, (25, 33), lambda v: there.setdefault("on the way", clock.now))
    clock.at(15.0, tr.close_road, [(20, 33)], 900.0)
    clock.run()
    driven = [c for leg in tr.legs for c in leg["path"]]
    assert v.cell == (25, 33) and (20, 33) not in driven and there["on the way"] < 200.0 and v.waited_s == 0     # it replanned at its next stop
    with pytest.raises(ValueError, match="not a road cell"):
        tr.close_road([(0, 0)])


def test_a_broken_down_agv_takes_no_job_and_a_job_no_road_leads_to_waits_for_the_road(dames):
    clock = Clock()
    log = EventLog(clock)
    tr = Traffic(clock, dames, log, None)
    fleet = AgvFleet(clock, dames, tr, log, n=2)
    took = {}
    fleet.break_down("AGV1", 900.0)
    fleet.submit(Job("J1", "discharge", (14, 34), lambda agv, job: took.setdefault("J1", (agv, clock.now))))
    clock.run(until=600.0)
    assert took["J1"][0] == "AGV2"                               # AGV1 is nearer, but it is broken down
    with pytest.raises(ValueError, match="no AGV called"):
        fleet.break_down("AGV9", 60.0)

    clock = Clock()
    log = EventLog(clock)
    tr = Traffic(clock, dames, log, None)
    fleet = AgvFleet(clock, dames, tr, log, n=1)
    tr.close_road([(14, 34)], 1200.0)                            # QC1's lane cannot be reached for twenty minutes
    fleet.submit(Job("J2", "discharge", (14, 34), lambda agv, job: took.setdefault("J2", (agv, clock.now))))
    clock.run(until=1100.0)
    assert "J2" not in took and [j.id for j in fleet.pending] == ["J2"]
    clock.run()
    assert took["J2"][0] == "AGV1" and took["J2"][1] > 1200.0 and not fleet.pending


# ---- the gate ---------------------------------------------------------------------------------------
def test_a_closed_gate_lane_takes_no_truck_until_it_opens(dames):
    clock = Clock()
    log = EventLog(clock)
    gate = Gate(clock, log, policy="fifo", lanes=2)
    box = {f"C{i}": type("Box", (), {"truck": f"T{i}", "vessel": None, "weight_t": 20.0, "id": f"C{i}"})() for i in range(1, 5)}
    entered = []
    gate.find = box.get
    gate.spot_for = lambda truck: (90, 4)
    gate.on_enter = lambda truck, lane, spot: (entered.append((truck.id, lane, clock.now)), clock.after(5.0, gate.lane_clear, lane))
    gate.close_lane(1, 600.0)
    for i in range(1, 5):
        gate.expect(Truck(f"T{i}", 10.0 * i, plate="P", picks=f"C{i}"))
    clock.run(until=590.0)
    assert [lane for _, lane, _ in entered] == [1, 1, 1, 1] and gate.shut == {0: 600.0}      # lane index 1 is gate lane 2
    assert gate.closures == [{"lane": 1, "t0": 0.0, "t1": 600.0}]
    gate.expect(Truck("T5", 700.0, plate="P", picks="C1"))
    box["C1"].truck = "T5"
    clock.run()
    assert entered[-1][:2] == ("T5", 0) and not gate.shut
    with pytest.raises(ValueError, match="lanes 1 to 2"):
        gate.close_lane(3, 60.0)


# ---- the whole terminal -----------------------------------------------------------------------------
EVENTS = {
    "agv": {"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"},
    "road": {"kind": "road_closed", "t": 1200, "seconds": 900, "cells": [[57, 28]]},
    "crane": {"kind": "crane_down", "t": 700, "seconds": 900, "crane": "QC2"},
    "yard crane": {"kind": "crane_down", "t": 300, "seconds": 1200, "crane": "RTG1"},
    "gate": {"kind": "gate_lane_closed", "t": 1200, "seconds": 1800, "lane": 1},
}


@pytest.fixture(scope="module")
def plan(dames):
    return Terminal(dames, scenario.demo()).run()


@pytest.mark.parametrize("name", list(EVENTS))
def test_the_morning_still_finishes_and_every_check_holds_with_a_disruption(dames, plan, name):
    t = Terminal(dames, scenario.demo().using(events=[EVENTS[name]])).run()
    a, b = summary(plan), summary(t)
    assert check(t) == [] and b["disruptions"] == 1 and b["stuck_at_end"] == []
    assert (b["ships_served"], b["trucks_served"], b["unfinished"]) == (a["ships_served"], a["trucks_served"], a["unfinished"])
    assert b["end_min"] > a["end_min"]                           # every one of them costs time


def test_a_crane_out_of_service_starts_no_lift_until_it_is_back(dames):
    t = Terminal(dames, scenario.demo().using(events=[EVENTS["crane"]])).run()
    (out,) = t.outages
    assert (out["crane"], out["t0"], out["t1"]) == ("QC2", 700.0, 1600.0)
    starts = sorted(r["t0"] for r in t.trace if r["kind"] == "lift" and r["by"] == "QC2")
    assert not [s for s in starts if 700.0 < s < 1600.0] and 1600.0 in starts and max(s for s in starts if s <= 700.0) == 660.0
    assert said(t, "QC2 is out of service for 15 min") == [700.0] and said(t, "QC2 is back in service") == [1600.0]
    assert next(v for v in t.scenario.vessels if v.id == "ALPHA").times["cast_off"] > 3000.0


def test_closing_the_main_road_holds_agvs_up_and_reports_each_long_wait(dames, plan):
    t = Terminal(dames, scenario.demo().using(events=[EVENTS["road"]])).run()
    assert len(said(t, "while the road is closed")) >= 4 and said(t, "The road at (57, 28) is open again") == [2100.0]
    stalls = [s for s in t.traffic.stalls if s["why"].startswith("waited")]
    assert len(stalls) >= 4 and all(s["until"] - s["since"] >= 600.0 for s in stalls)
    assert summary(t)["agv_blocked_min"] > 2 * summary(plan)["agv_blocked_min"]
    for leg in t.traffic.legs:                                   # nobody drove onto the closed cell while it was shut
        if (57, 28) in leg["path"][1:]:
            assert leg["t1"] <= 1200.0 + 60.0 or leg["t0"] >= 2100.0, leg["who"]


def test_an_event_that_names_something_the_terminal_does_not_have_is_refused_before_the_run(dames):
    for event, message in (({"kind": "agv_breakdown", "t": 5, "seconds": 60, "agv": "AGV99"}, "no AGV called"),
                           ({"kind": "road_closed", "t": 5, "seconds": 60, "cells": [[0, 0]]}, "not a road cell"),
                           ({"kind": "crane_down", "t": 5, "seconds": -3, "crane": "QC1"}, "must last"),
                           ({"kind": "crane_down", "t": 5, "seconds": 60, "crane": "QC9"}, "no crane called"),
                           ({"kind": "gate_lane_closed", "t": 5, "seconds": 60, "lane": 0}, "lanes 1 to 2"),
                           ({"kind": "storm", "t": 5, "seconds": 60}, "unknown event")):
        with pytest.raises(ValueError, match=message):
            Terminal(dames, scenario.demo().using(events=[event]))


# ---- what-if settings ---------------------------------------------------------------------------------
def test_no_settings_is_the_morning_as_planned_and_a_what_if_run_repeats_exactly(dames, plan):
    same = control.run({}, dames)
    assert [(e.t, e.text) for e in same.log.entries] == [(e.t, e.text) for e in plan.log.entries]
    settings = {"agvs": 8, "policies": {"gate": "fifo"}, "events": [EVENTS["agv"]]}
    a, b = control.run(settings, dames), control.run(settings, dames)
    assert [(e.t, e.text) for e in a.log.entries] == [(e.t, e.text) for e in b.log.entries]
    assert settings == {"agvs": 8, "policies": {"gate": "fifo"}, "events": [EVENTS["agv"]]}       # the settings are not changed by a run


def test_each_setting_changes_the_morning_it_describes(dames):
    sc = control.scenario_for({"policies": {"ship": "fcfs", "dispatch": "balanced"}, "agvs": 7, "tide_high_water_min": 200,
                               "vessels": {"BRAVO": {"arrives_min": 30, "draft_m": 10.0, "radio_out_min": [40, 55]}},
                               "trucks": {"T02": {"arrives_min": 50}, "T06": {"no_show": True}},
                               "walk_ins": {"at_min": 35, "trucks": 3}, "events": [EVENTS["gate"], EVENTS["crane"]]})
    bravo = next(v for v in sc.vessels if v.id == "BRAVO")
    assert sc.policies == {"ship": "fcfs", "stacking": "departure_time", "dispatch": "balanced", "gate": "appointment"} and sc.agvs == 7
    assert sc.tide.high_water_s == 12000.0 and (bravo.arrives_s, bravo.draft_m, bravo.comms_out) == (1800.0, 10.0, (2400.0, 3300.0))
    assert [v.id for v in sc.vessels] == ["ALPHA", "CHARLIE", "BRAVO"]                    # in arrival order again
    trucks = {t.id: t for t in sc.trucks}
    assert "T06" not in trucks and trucks["T02"].arrives_s == 3000.0
    assert [(trucks[w].arrives_s, trucks[w].appointment_s, trucks[w].picks) for w in ("W01", "W02", "W03")] == [
        (2100.0, None, "STAY-02"), (2120.0, None, "STAY-03"), (2140.0, None, "STAY-04")]
    assert [c.truck for c in sc.inventory if c.id in ("STAY-02", "STAY-05")] == ["W01", None]
    assert [e["kind"] for e in sc.events] == ["crane_down", "gate_lane_closed"]           # in time order
    assert control.scenario_for({}).agvs == 12 and control.scenario_for(None).events == []


def test_a_rush_of_walk_in_trucks_is_served_after_the_booked_ones(dames, plan):
    t = control.run({"walk_ins": {"at_min": 35, "trucks": 6}}, dames)
    s = summary(t)
    assert check(t) == [] and s["trucks_served"] == summary(plan)["trucks_served"] + 6
    assert s["truck_turnaround_min"] > 2 * summary(plan)["truck_turnaround_min"]
    walk_ins = [k for k in t.scenario.trucks if k.id.startswith("W")]
    assert len(walk_ins) == 6 and all(k.state == "gone" for k in walk_ins)


def test_a_setting_that_makes_no_sense_is_refused_with_a_sentence(dames):
    for settings, message in (({"agvs": 13}, "from 2 to 12"), ({"agvs": "8"}, "whole number"), ({"policies": {"ship": "x"}}, "unknown ship policy"),
                              ({"policies": {"crane": "x"}}, "policies are set for"), ({"vessels": {"ZULU": {}}}, "no ship called"),
                              ({"vessels": {"BRAVO": {"radio_out_min": [30, 10]}}}, "must end after"), ({"vessels": {"BRAVO": {"length": 300}}}, "a ship has"),
                              ({"trucks": {"T99": {}}}, "no truck called"), ({"walk_ins": {"at_min": 5, "trucks": 40}}, "a rush is 1 to 11"),
                              ({"tide_high_water_min": "noon"}, "must be a number"), ({"speed": 2}, "unknown setting"), ([1, 2], "must be an object"),
                              ({"events": [{"kind": "agv_breakdown", "t": 99999, "seconds": 5, "agv": "AGV1"}]}, "later than 240")):
        with pytest.raises(ValueError, match=message):
            control.run(settings, dames)


def test_the_control_room_is_told_what_it_may_change(dames):
    o = control.options(dames)
    assert {p: [x["id"] for x in names] for p, names in o["policies"].items()} == {p: list(n) for p, n in scenario.POLICIES.items()}
    assert o["defaults"]["agvs"] == 12 and o["agvs"] == [2, 12] and o["walk_ins"] == 11 and o["gate_lanes"] == 2
    assert [v["id"] for v in o["vessels"]] == ["ALPHA", "BRAVO", "CHARLIE"] and "QC2" in o["cranes"] and "XC1" in o["cranes"]
    assert len({v["id"] for v in control.VARIANTS}) == len(control.VARIANTS) and control.VARIANTS[0]["settings"] == {}
    for v in control.VARIANTS:
        control.scenario_for(v["settings"])                      # every recorded what-if is a valid set of settings
