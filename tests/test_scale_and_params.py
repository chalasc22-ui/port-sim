"""Step 2: the numbers in one place (sim/params.py), random variation, rolling driving, a pooled fleet,
and a shift at real scale (sim.scenario.shift)."""

import dataclasses
import math
from statistics import mean

import pytest
from terminal_layout.timing import GATE_SERVICE_S, HANDOFF_S, QUAY_SERVICE_S, YARD_SERVICE_S, segment_time

from sim import params as numbers
from sim import scenario as scenarios
from sim.agv import AgvFleet, Job, Traffic, Vehicle
from sim.kernel import Clock, EventLog
from sim.metrics import cell_conflicts, check, summary
from sim.params import CALIBRATED, REFERENCE
from sim.stacking import Yard
from sim.terminal import Terminal


# ---- the numbers ------------------------------------------------------------------------------------
def test_the_reference_numbers_are_portsims_and_nothing_varies():
    assert (REFERENCE.quay_move_s, REFERENCE.yard_move_s, REFERENCE.handoff_s, REFERENCE.gate_check_s) == (QUAY_SERVICE_S, YARD_SERVICE_S, HANDOFF_S, GATE_SERVICE_S)
    assert REFERENCE.seed is None and not REFERENCE.rolling and REFERENCE.agvs_per_crane == 2
    assert REFERENCE.seeded(5).move("quay", "A-01", 1) == QUAY_SERVICE_S          # no spread: a seed changes nothing
    assert CALIBRATED.move("quay", "A-01", 1) == CALIBRATED.quay_move_s           # a spread, but no seed
    assert scenarios.demo().params is REFERENCE


def test_a_time_is_drawn_from_the_stream_its_subject_names():
    p = CALIBRATED.seeded(3)
    a = p.move("yard", "A-01", 1)
    assert a == p.move("yard", "A-01", 1) != p.move("yard", "A-01", 2) != p.move("yard", "A-02", 1)
    assert a != CALIBRATED.seeded(4).move("yard", "A-01", 1)
    draws = [p.move("quay", f"C{i}", 1) for i in range(4000)]
    assert mean(draws) == pytest.approx(p.quay_move_s, rel=0.03)                  # the mean is the mean given
    spread = math.sqrt(mean((d - mean(draws)) ** 2 for d in draws)) / mean(draws)
    assert spread == pytest.approx(p.move_cv, rel=0.1)
    assert min(draws) > 0


def test_the_calibrated_numbers_follow_their_sources():
    assert CALIBRATED.quay_move_s == pytest.approx(3600 / 35, abs=0.5)            # 35 moves an hour
    assert (CALIBRATED.speed_empty_m_s, CALIBRATED.speed_loaded_m_s, CALIBRATED.accel_empty_m_s2, CALIBRATED.accel_loaded_m_s2) == (6.0, 6.0, 1.0, 0.5)
    assert CALIBRATED.keel_clearance_m == pytest.approx(0.76)                     # 2.5 ft
    assert numbers.TIDE_AMPLITUDE_M == pytest.approx((1.065 - 0.033) / 2) and numbers.TIDE_MEAN_DEPTH_M == pytest.approx(12.19 + 0.549, abs=0.01)
    assert numbers.TRANSIT_S == pytest.approx(14.2 * 1852 / (10 * 1852 / 3600), abs=1)        # 14.2 nautical miles at 10 knots
    assert numbers.YARD_MOVE_S == 100
    assert numbers.from_dict(dataclasses.asdict(CALIBRATED.seeded(9))) == CALIBRATED.seeded(9)


def test_every_row_of_the_data_sheet_names_a_real_number_and_a_listed_source():
    for row in numbers.ROWS:
        assert row["basis"] in ("source", "derived", "assumed", "PortSim"), row
        assert (row["source"] in numbers.SOURCES) if row["basis"] != "assumed" else row["source"] is None, row
        if "field" in row:
            obj = REFERENCE
            for part in row["field"].split("."):
                obj = getattr(obj, part)                                          # raises if the field is gone
        else:
            assert len(row["values"]) == 2
    fields = {r["field"] for r in numbers.ROWS if "field" in r}
    told = {f.name for f in dataclasses.fields(REFERENCE)} - {"name", "seed", "energy"} | {f"energy.{f.name}" for f in dataclasses.fields(REFERENCE.energy)}
    assert told - fields == {"energy.reserve_kwh", "yard_patience_s"}             # every number is in the sheet (these two are in a row of their own, or a margin)


# ---- driving ----------------------------------------------------------------------------------------
def drive(layout, params, a, b, load=None):
    clock = Clock()
    tr = Traffic(clock, layout, EventLog(clock), None, params)
    v = Vehicle("V1", "agv", a)
    v.load = load
    tr.add(v, a)
    done = []
    tr.send(v, b, lambda v: done.append(clock.now))
    clock.run()
    return done[0], tr


def test_a_rolling_vehicle_crosses_the_junctions_of_a_straight_road_without_stopping(dames):
    a, b = (2, 33), (50, 33)                                   # 48 cells along the bypass, every one a junction
    t_ref, tr = drive(dames, REFERENCE, a, b)
    assert t_ref == pytest.approx(48 * segment_time(15.0, 6.0, 1.2), abs=0.1)     # PortSim: rest to rest, 48 times
    t_roll, tr = drive(dames, CALIBRATED, a, b)
    assert t_roll == pytest.approx(segment_time(48 * 15.0, 6.0, 1.0), abs=0.1)    # one run from rest to rest
    assert sum(tr.rolled) == 47 and len(tr.segments) == 48                        # it stopped once, at the end
    assert cell_conflicts(tr, t_roll) == []
    t_loaded, _ = drive(dames, CALIBRATED, a, b, load="C1")
    assert t_loaded == pytest.approx(segment_time(48 * 15.0, 6.0, 0.5), abs=0.1)  # a loaded one gets going more slowly


def test_a_rolling_vehicle_stops_to_turn(dames):
    t, tr = drive(dames, CALIBRATED, (2, 33), (57, 30))        # east along the bypass, then south down the main road
    assert t == pytest.approx(segment_time(55 * 15.0, 6.0, 1.0) + segment_time(3 * 15.0, 6.0, 1.0), abs=0.1)
    assert [cells[-1] for (_, _, _, cells), rolled in zip(tr.segments, tr.rolled) if not rolled] == [(57, 33), (57, 30)]


def test_rolling_vehicles_follow_at_braking_distance_and_never_share_a_cell(dames):
    clock = Clock()
    tr = Traffic(clock, dames, EventLog(clock), None, CALIBRATED)
    arrived = {}
    for i, start in enumerate([(8, 33), (6, 33), (4, 33), (2, 33)]):               # four in a row, the first one in front
        v = Vehicle(f"V{i + 1}", "agv", start, n=i + 1)
        tr.add(v, start)
        tr.send(v, (50 - 2 * i, 33), lambda v: arrived.__setitem__(v.id, clock.now))
    clock.run()
    assert len(arrived) == 4 and cell_conflicts(tr, clock.now) == []
    alone, _ = drive(dames, CALIBRATED, (2, 33), (44, 33))
    assert arrived["V4"] < alone + 30                          # the last one is hardly held up: nobody waits for a whole stretch to clear
    assert tr.stalls == []


def test_the_distance_between_two_cells_is_the_length_of_the_route(dames):
    clock = Clock()
    tr = Traffic(clock, dames, EventLog(clock))
    lanes = sorted(tr.quay_lanes)
    stands = sorted(p for b in dames.blocks.values() for p in b.transfer_points)
    stations = [c.pos for c in dames.charging_stations()]
    cells = lanes[::5] + stands[::37] + stations[::4] + [(57, 32), (1, 33), (96, 5)]
    for a in cells:
        for b in cells:
            path = tr.route(a, b)
            assert tr.moves(a, b) == (None if path is None else len(path) - 1), (a, b)


# ---- a pooled fleet ---------------------------------------------------------------------------------
def pooled(layout, n, soc_start=None):
    clock = Clock()
    tr = Traffic(clock, layout, EventLog(clock), CALIBRATED.energy, CALIBRATED)
    return clock, tr, AgvFleet(clock, layout, tr, EventLog(clock), n=n, soc_start=soc_start)


def test_a_fleet_larger_than_the_stations_shares_them_and_stands_by_on_the_apron(dames):
    clock, tr, fleet = pooled(dames, 30)
    assert fleet.pool and len(fleet.standby) >= 60
    lanes, apron = tr.quay_lanes, {c.pos for c in dames.quay_cells()}
    last_lane = max(x for x, _ in lanes)
    for cell in fleet.standby:
        assert cell in apron and cell not in lanes and cell[0] < last_lane                    # on the apron, before a crane
        assert any(cell in tr.adj[u] for u in tr.adj if u not in apron and u not in lanes)      # reached straight from the road beside it
    at = [v.cell for v in fleet.agvs.values()]
    assert len(set(at)) == 30 and sum(c in fleet.station_id for c in at) == 12 and sum(c in fleet.standby for c in at) == 18
    assert all(v.charging == (v.cell in fleet.station_id) for v in fleet.agvs.values())
    assert tr.keep_off == frozenset(fleet.standby)
    route = tr.route((2, 33), (50, 34))                        # along the quay to a lane: not over anybody's stand-by cell
    assert not set(route[1:-1]) & tr.keep_off
    with pytest.raises(ValueError, match="stand-by cells"):
        pooled(dames, 200)


def test_a_pooled_agv_with_no_job_goes_to_the_nearest_free_stand_by_cell(dames):
    clock, tr, fleet = pooled(dames, 16)
    stand = sorted(dames.blocks["A1"].transfer_points)[3]
    fleet.submit(Job("J1", "discharge", stand, lambda agv, job: fleet.release(agv)))
    clock.run()
    v = fleet.agvs[fleet.jobs[0].agv]
    assert fleet.jobs[0].times["done"] and v.state == "parked" and v.cell == v.home and v.home in fleet.standby
    assert fleet.claimed[v.home] == v.id and len(set(fleet.claimed)) == 16 and tr.stalls == []
    assert v.home[0] <= 12                                     # it came up the west road and parked before the first crane


def test_a_pooled_agv_low_on_charge_gets_a_station_when_one_gives_it_up(dames):
    clock, tr, fleet = pooled(dames, 14, soc_start=(0.20, 0.20))            # all low: twelve on the stations, two stand by
    waiting = [v for v in fleet.agvs.values() if not v.charging]
    assert len(waiting) == 2 and all(fleet.soc(v) < CALIBRATED.energy.charge_below for v in waiting)
    clock.at(4 * 3600.0, lambda: None)                                      # the clock stops at its last event: have one four hours in
    clock.run()
    assert all(fleet.soc(v) > 0.6 for v in waiting)                         # the first to fill up gave their stations up
    assert {stay[0] for stay in fleet.charges} >= {v.id for v in waiting}
    assert all(v.state == "parked" for v in fleet.agvs.values()) and len(set(fleet.claimed)) == 14


# ---- the scheduler on the calibrated numbers --------------------------------------------------------
@pytest.fixture(scope="module")
def calibrated_morning(dames):
    return Terminal(dames, scenarios.demo().using(params=CALIBRATED.seeded(1))).run()


def test_the_demo_morning_runs_on_the_calibrated_numbers_and_every_check_holds(calibrated_morning, dames):
    t = calibrated_morning
    s = summary(t)
    assert check(t) == [] and s["ships_served"] == 3 and s["stuck_at_end"] == [] and s["agv_jobs_open"] == 0
    again = summary(Terminal(dames, scenarios.demo().using(params=CALIBRATED.seeded(1))).run())
    assert again == s                                           # the same seed, the same morning
    other = summary(Terminal(dames, scenarios.demo().using(params=CALIBRATED.seeded(2))).run())
    assert other != s and other["ships_served"] == 3


def test_crane_moves_vary_around_their_mean_and_a_yard_crane_takes_time_to_travel(calibrated_morning, dames):
    t = calibrated_morning
    quay = [r["t1"] - r["t0"] for r in t.trace if r["kind"] == "lift" and r["by"].startswith("QC") and r["src"][0] == "ship"]
    assert len(set(round(d, 1) for d in quay)) > 10 and 60 < mean(quay) < 150
    travel = [r for r in t.trace if r["kind"] == "gantry"]
    assert travel and all(r["t1"] > r["t0"] for r in travel) and sum(t.crane_travel_s.values()) > 0
    plan = Terminal(dames, scenarios.demo()).run()
    assert not [r for r in plan.trace if r["kind"] == "gantry"] and not plan.crane_travel_s


def test_an_import_whose_truck_is_turned_away_goes_back_to_the_yard(calibrated_morning, dames):
    """T07 (wrong plate) and T15 (cannot pay) are turned away. In the reference their containers stay in the
    exchange stack; with the calibrated numbers an AGV takes them back, so the eight places are not blocked."""
    t = calibrated_morning
    assert sorted(t.sent_back) == ["IMP-07", "STAY-01"]
    assert t.containers["IMP-07"].place[0] == "yard" and t.containers["STAY-01"].place[0] == "yard"
    plan = Terminal(dames, scenarios.demo()).run()
    assert plan.sent_back == [] and plan.containers["IMP-07"].place[0] == "exchange"


def test_the_same_container_gets_the_same_crane_time_under_another_policy(dames):
    def quay_times(policy):
        t = Terminal(dames, scenarios.demo().using(params=CALIBRATED.seeded(4), stacking=policy)).run()
        return {r["what"]: round(r["t1"] - r["t0"], 3) for r in t.trace if r["kind"] == "lift" and r["by"].startswith("QC") and r["src"][0] == "ship"}
    a, b = quay_times("shortest_stack"), quay_times("departure_time")
    assert a == b and len(a) == 26                              # common random numbers: the difference between two runs is the policy


def test_the_scheduler_names_the_least_busy_yard_crane_when_asked_to_balance(dames):
    sc = scenarios.demo().using(balance=True)
    t = Terminal(dames, sc).run()
    assert check(t) == [] and summary(t)["ships_served"] == 3
    c = next(c for c in t.containers.values() if c.id == "A-01")
    assert t.yard.cranes_for(c, "B1") == ["RTG1", "RTG2", "RTG3"]             # the west zones' cranes, in the order of their zones


# ---- a shift at real scale --------------------------------------------------------------------------
def test_the_shift_zones_hold_what_the_generator_counts_on(dames):
    yard = Yard(dames, scenarios.SHIFT_ZONES)
    room: dict[str, int] = {}
    for bay in yard.bays:
        room[yard.zone_of[bay].cargo] = room.get(yard.zone_of[bay].cargo, 0) + yard._room(bay)
    assert room == scenarios.SHIFT_ROOM
    lanes = [(yard.stand(bay)[1], yard.zone_of[bay].block) for bay in yard.bays if yard.zone_of[bay].cargo == "general"]
    assert len({lane for lane, _ in lanes}) == len({block for _, block in lanes})          # every block on a lane of its own


def test_a_shift_is_the_same_for_the_same_seed_and_its_parts_have_their_own_streams():
    a, b = scenarios.shift(5), scenarios.shift(5)
    assert dataclasses.asdict(a) == dataclasses.asdict(b)
    assert a.params == CALIBRATED.seeded(5) and a.transit_s == numbers.TRANSIT_S and a.balance and a.agvs == 30
    more = scenarios.shift(5, trucks_per_hour=16, agvs=36)
    ships = lambda sc: [(v.id, v.arrives_s, v.draft_m, v.berth, len(v.discharge), len(v.load)) for v in sc.vessels]
    assert ships(more) == ships(a) and len(more.trucks) == 128 and len(a.trucks) == 80
    cranes = lambda sc: [e for e in sc.events if e["kind"] == "crane_down"]
    assert cranes(more) == cranes(a)                            # more AGVs: other AGV breakdowns, the same crane outages
    assert ships(scenarios.shift(6)) != ships(a)
    assert all(140 <= len(v.discharge) + len(v.load) <= 240 and 9.0 <= v.draft_m <= numbers.MAX_DRAFT_M for v in a.vessels)
    booked = [t for t in a.trucks if t.appointment_s is not None]
    assert all(t.appointment_s % scenarios.SLOT_S == 0 for t in booked) and 0 < sum(t.no_show for t in booked) < 0.3 * len(booked)
    assert not any(t.no_show for t in a.trucks if t.appointment_s is None)
    stock = len(a.inventory)
    assert stock == pytest.approx(0.65 * sum(scenarios.SHIFT_ROOM.values()), abs=2)


@pytest.fixture(scope="module")
def short_shift(dames):
    sc = scenarios.shift(2, hours=3.0, calls=2, agvs=20, trucks_per_hour=10)
    return sc, Terminal(dames, sc).run()


def test_a_shift_at_real_scale_runs_to_the_end_and_every_check_holds(short_shift):
    sc, t = short_shift
    s = summary(t)
    assert check(t) == []
    assert s["ships_served"] == 2 and s["stuck_at_end"] == [] and s["agv_jobs_open"] == 0 and s["missed_ship"] == []
    assert s["quay_moves"] == sum(len(v.discharge) + len(v.load) for v in sc.vessels) > 250
    assert t.fleet.pool and len(t.fleet.agvs) == 20
    assert 8 < s["quay_moves_per_crane_hour"] < 35              # under the crane's own 35 an hour
    assert 3.5 < s["agv_speed_m_s"] < 6.0 and 8 < s["agv_kwh_per_operating_hour"] < 25
    assert 0.5 < s["yard_occupancy"] < 0.85


def test_in_a_shift_no_show_trucks_never_come_and_their_containers_do_not_block_the_exchange(short_shift):
    sc, t = short_shift
    gone = [k for k in t.scenario.trucks if k.no_show]
    assert gone and all(k.state == "expected" and "arrived" not in k.times for k in gone)
    assert all(k.state in ("gone", "turned_away") for k in t.scenario.trucks if not k.no_show)        # every truck that came was dealt with
    assert not [c.id for c in t.containers.values() if c.place[0] == "exchange"]                    # nothing is left in the exchange stack
    assert set(t.sent_back) <= {k.picks for k in t.scenario.trucks if k.picks and (k.no_show or k.state == "turned_away")}
    held = {}                                                   # the exchange never held more imports than it may
    for c in t.containers.values():
        for (t0, a), (t1, _) in zip(c.history, [*c.history[1:], (t.end_s, None)]):
            if a[0] == "exchange":
                held.setdefault(a[1], []).append((t0, t1))
    assert all(b0 >= a1 - 1e-6 for spans in held.values() for (_, a1), (b0, _) in zip(sorted(spans), sorted(spans)[1:]))


def test_a_shift_run_saved_and_loaded_gives_the_same_run(short_shift, dames, tmp_path):
    sc, t = short_shift
    scenarios.save(sc, tmp_path / "shift.json")
    again = Terminal(dames, scenarios.load(tmp_path / "shift.json")).run()
    assert summary(again) == summary(t)


# ---- what-if settings: the numbers and the shift ------------------------------------------------------
def test_a_what_if_can_run_the_morning_on_the_calibrated_numbers(dames):
    from sim import control
    sc = control.scenario_for({"numbers": "calibrated"})
    assert sc.params == CALIBRATED and sc.tide.amplitude_m == numbers.TIDE_AMPLITUDE_M and sc.tide.high_water_s == scenarios.demo().tide.high_water_s
    assert sc.transit_s == scenarios.demo().transit_s                     # the morning keeps its ten-minute passage
    assert control.scenario_for({"numbers": "calibrated", "seed": 3}).params == CALIBRATED.seeded(3)
    assert control.scenario_for({"numbers": "reference"}).params is REFERENCE
    t = control.run({"numbers": "calibrated"}, dames)
    bravo = next(v for v in t.scenario.vessels if v.id == "BRAVO")
    assert check(t) == [] and bravo.times["cleared"] == bravo.times["arrived"]         # with NOAA's tide it need not wait
    for bad, why in (({"seed": 3}, "do not vary"), ({"numbers": "measured"}, "the numbers are"), ({"seed": 1.5, "numbers": "calibrated"}, "seed"),
                     ({"workload": "week"}, "workload"), ({"trucks_per_hour": 9}, "setting of the shift")):
        with pytest.raises(ValueError, match=why):
            control.scenario_for(bad)


def test_a_what_if_can_ask_for_a_shift_at_real_scale(dames):
    from sim import control
    sc = control.scenario_for({"workload": "shift"})
    assert dataclasses.asdict(sc) == dataclasses.asdict(scenarios.shift(1))
    sc = control.scenario_for({"workload": "shift", "seed": 4, "agvs": 24, "trucks_per_hour": 6, "policies": {"gate": "fifo"},
                               "events": [{"kind": "crane_down", "t": 9000, "seconds": 600, "crane": "QC3"}]})
    assert sc.agvs == 24 and len(sc.trucks) == 48 and sc.policies["gate"] == "fifo" and sc.params.seed == 4
    assert {"kind": "crane_down", "t": 9000, "seconds": 600, "crane": "QC3"} in sc.events and len(sc.events) > len(scenarios.shift(4, agvs=24, trucks_per_hour=6).events) - 1
    for bad, why in (({"workload": "shift", "walk_ins": {"at_min": 5, "trucks": 2}}, "a shift takes"), ({"workload": "shift", "agvs": 50}, "from 12 to 40"),
                     ({"workload": "shift", "trucks_per_hour": 40}, "from 4 to 20"), ({"workload": "shift", "numbers": "reference"}, "a shift takes")):
        with pytest.raises(ValueError, match=why):
            control.scenario_for(bad)
    options = control.options(dames)
    assert options["numbers"] == ["reference", "calibrated"] and options["shift"] == {"agvs": [12, 40], "trucks_per_hour": [4, 20]}
    assert len({v["id"] for v in control.VARIANTS}) == len(control.VARIANTS) == 15


def test_the_documents_written_from_the_code_are_up_to_date():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("data_sheet", root / "tools" / "data_sheet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run python tools/data_sheet.py"
    sheet = (root / "docs" / "data-sheet.md").read_text()
    assert all(f"**{sid}**" in sheet for sid in numbers.SOURCES) and "| One quay crane move | 60 | 103 | s | published |" in sheet


def test_the_experiment_reports_a_mean_with_its_confidence_interval():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("experiments", root / "tools" / "experiments.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    i = ex.interval([10.0, 12.0, 14.0, 16.0])
    assert i["mean"] == 13.0 and i["n"] == 4 and i["half"] == pytest.approx(3.182 * 2.582 / 2, abs=0.01)       # t for 3 degrees of freedom
    assert ex.interval([5.0]) == {"mean": 5.0, "half": 0.0, "n": 1} and ex.interval([None]) is None
    assert ex.t975(29) == 2.045 and ex.t975(1000) == 1.96
    same = ex.key_of("shift", "stacking", 3, {"stacking": "departure_time"}) == ex.key_of("shift", "fleet", 3, {"agvs": 30})
    assert same                                                 # the planned shift is one run, whichever comparison asks for it
    assert ex.key_of("shift", "fleet", 3, {"agvs": 24}) != ex.key_of("shift", "fleet", 3, {"agvs": 30})
    sc = ex.workload("shift", "trucks", 2, {"trucks_per_hour": 6})
    assert len(sc.trucks) == 48 and sc.agvs == 30
