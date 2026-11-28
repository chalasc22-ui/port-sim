"""The four modules running as one terminal (sim/terminal.py), on the demo morning and on generated workloads."""

import pytest

from sim import scenario
from sim.metrics import check, summary
from sim.run import run
from sim.terminal import QUAY_POOL


@pytest.fixture(scope="module")
def morning(dames):
    return run(scenario.demo(), dames)


def said(t, text):
    return [e.t for e in t.log.entries if text in e.text]


def journey(t, cid):
    return [p[0] if p[0] not in ("crane", "vehicle") else f"{p[0]}:{p[1]}" for _, p in t.containers[cid].history]


def test_the_demo_morning_runs_to_the_end_and_every_check_holds(morning):
    assert check(morning) == []
    s = summary(morning)
    assert (s["ships_served"], s["trucks_served"], s["trucks_turned_away"]) == (3, 12, 3)
    assert (s["discharged"], s["loaded_on_ships"], s["left_by_truck"]) == (26, 10, 10)
    assert s["unfinished"] == ["EXP-D1", "IMP-07", "STAY-01"]         # their trucks were turned away at the gate
    assert s["agv_jobs_open"] == 0 and s["stalls"] == 0 and s["stuck_at_end"] == []
    assert s["tolls_usd"] == 12 * 25.0 and s["trouble_desk_visits"] == 2
    assert all(v.state == "parked" and v.load is None for v in morning.fleet.agvs.values())


def test_a_crane_lowers_straight_onto_a_waiting_agv_and_uses_its_buffer_only_when_none_is_there(morning):
    """Christopher's rule of Nov 15, and Dr. Wang's of Oct 15: a full buffer stops the crane."""
    qc2 = [(r["src"][0], r["dst"][0]) for r in morning.trace if r["kind"] == "lift" and r["by"] == "QC2"][:5]
    assert qc2[:QUAY_POOL] == [("ship", "vehicle")] * QUAY_POOL       # the AGVs called while the ship was in the channel
    assert qc2[QUAY_POOL:4] == [("ship", "ground")] * 2               # no AGV left in its lanes: into the buffer
    assert qc2[4] == ("ground", "vehicle")                            # the next AGV is loaded from the buffer
    full, on = said(morning, "QC2 waits: its buffer is full (2/2)"), said(morning, "QC2 goes on after waiting")
    assert full and on and full[0] < on[0]
    berthed = said(morning, "ALPHA berths at B1")[0]
    assert said(morning, "QC2 sets A-01 straight onto")[0] == berthed  # the AGV was already there


def test_a_container_can_be_followed_from_the_ship_to_the_truck_and_from_the_truck_to_the_ship(morning):
    imp = journey(morning, "A-03")
    assert imp[0] == "ship" and imp[-1] == "gone" and morning.containers["A-03"].place == ("gone", "truck", "T13")
    assert [p.split(":")[0] for p in imp] == ["ship", "crane", "buffer", "crane", "vehicle", "crane", "yard", "crane", "vehicle",
                                              "crane", "exchange", "crane", "vehicle", "gone"]
    assert imp[1] == "crane:QC2" and imp[9] == "crane:XC1" and imp[12] == "vehicle:T13"
    exp = journey(morning, "EXP-C3")
    assert exp[0] == "vehicle:T11" and exp[-1] == "gone" and morning.containers["EXP-C3"].place == ("gone", "ship", "CHARLIE")
    assert "yard" in exp and exp[-2].startswith("crane:QC")
    lines = [e.text for e in morning.log.about("A-03")]
    assert any("T13 arrives at the gate" in x for x in lines) and any("stacks A-03" in x for x in lines)
    assert {e.module for e in morning.log.about("A-03")} >= {"gate", "terminal"}


def test_each_module_does_in_the_demo_what_its_owner_described(morning):
    ships = {v.id: v for v in morning.scenario.vessels}
    assert ships["BRAVO"].times["reasons"][0][1] == "not enough water"            # ship arrival: the tide
    assert "no free berth" in [why for _, why in ships["BRAVO"].times["reasons"]]
    assert ships["CHARLIE"].times["cleared"] == ships["CHARLIE"].times["arrived"]  # earliest eligible: it passes BRAVO
    away = dict(morning.gate.turned_away)                                          # gate: the three refusals
    assert "plate" in away["T07"] and "damaged" in away["T12"] and "toll" in away["T15"]
    assert said(morning, "T03 goes to the trouble desk: more than 15 minutes late")
    assert said(morning, "T11 goes to the trouble desk: its weight does not match")
    assert said(morning, "EXP-D1 is not at the terminal and misses BRAVO")
    yard = morning.yard                                                            # stacking: cargo kept apart
    assert yard.mixed() == [] and {yard.zone_of[yard.bay_of[c]].cargo for c in ("A-04", "A-09")} == {"food"}
    assert yard.zone_of[yard.bay_of["A-06"]].cargo == "hazmat"
    assert all(v.used_kwh > 0 and v.jobs_done >= 1 for v in morning.fleet.agvs.values())      # AGVs: all worked and charged
    assert said(morning, "parks at CH")


def test_a_run_is_the_same_every_time_and_leaves_the_workload_untouched(dames):
    sc = scenario.generate(4)
    a, b = run(sc, dames), run(sc, dames)
    assert [(e.t, e.text) for e in a.log.entries] == [(e.t, e.text) for e in b.log.entries]
    assert all(c.place == ("outside",) and not c.history for c in sc.containers())
    assert all(v.state == "expected" and not v.times for v in sc.vessels)


def test_a_saved_workload_runs_the_same_as_the_one_it_was_saved_from(dames, tmp_path):
    sc = scenario.demo()
    scenario.save(sc, tmp_path / "morning.json")
    again = scenario.load(tmp_path / "morning.json")
    assert again.policies == sc.policies and again.zones == sc.zones and again.tide == sc.tide
    a, b = run(sc, dames), run(again, dames)
    assert [(e.t, e.text) for e in a.log.entries] == [(e.t, e.text) for e in b.log.entries]


def test_a_policy_changes_what_its_module_does_on_the_same_workload(dames, morning):
    fcfs = run(scenario.demo().using(ship="fcfs"), dames)
    charlie = next(v for v in fcfs.scenario.vessels if v.id == "CHARLIE")
    bravo = next(v for v in fcfs.scenario.vessels if v.id == "BRAVO")
    assert charlie.times["cleared"] >= bravo.times["cleared"] > 1800           # nobody passes the ship that waits for the tide
    fifo = run(scenario.demo().using(gate="fifo"), dames)
    assert not said(fifo, "more than 15 minutes late") and said(morning, "more than 15 minutes late")
    assert summary(fifo)["truck_turnaround_min"] > summary(morning)["truck_turnaround_min"]
    for t in (fcfs, fifo):
        assert check(t) == [] and summary(t)["ships_served"] == 3
    with pytest.raises(ValueError, match="unknown stacking policy"):
        scenario.demo().using(stacking="tallest")
    with pytest.raises(ValueError, match="no setting"):
        scenario.demo().using(cranes=9)


def test_more_agvs_finish_the_same_work_sooner(dames, morning):
    few = run(scenario.demo().using(agvs=6), dames)
    assert check(few) == []
    a, b = summary(few), summary(morning)
    assert (a["discharged"], a["loaded_on_ships"], a["left_by_truck"]) == (b["discharged"], b["loaded_on_ships"], b["left_by_truck"])
    assert a["end_min"] > 1.4 * b["end_min"] and a["quay_crane_wait_min"] > b["quay_crane_wait_min"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_generated_workloads_finish_without_a_jam_under_every_policy(dames, seed):
    settings = [dict(agvs=6), dict(agvs=12, ship="fcfs", dispatch="longest_idle"),
                dict(agvs=9, stacking="shortest_stack", gate="fifo", dispatch="balanced")][seed % 3]
    sc = scenario.generate(seed).using(**settings)
    t = run(sc, dames)
    s = summary(t)
    assert check(t) == []
    assert s["ships_served"] == len(sc.vessels) and s["trucks_served"] + s["trucks_turned_away"] == s["trucks"]
    assert s["agv_jobs_open"] == 0 and s["stalls"] == 0 and s["unfinished"] == []


def test_the_scenario_generator_draws_each_part_from_its_own_stream(dames):
    a, b = scenario.generate(7, trucks=4), scenario.generate(7, trucks=14)
    assert [(v.id, v.arrives_s, v.draft_m, len(v.discharge)) for v in a.vessels] == [(v.id, v.arrives_s, v.draft_m, len(v.discharge)) for v in b.vessels]
    assert [c.id for c in a.inventory] == [c.id for c in b.inventory] and len(a.trucks) <= 4 < len(b.trucks)
    assert scenario.generate(7).vessels[0].discharge[0].weight_t != scenario.generate(8).vessels[0].discharge[0].weight_t
