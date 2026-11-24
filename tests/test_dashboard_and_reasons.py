"""Step 3: the run in equal steps of time for the dashboard (sim.metrics.timeline), the reasons the modules
give for their decisions (EventLog.explain), and what the replay carries of both for the 3D model."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from sim import control
from sim import scenario as scenarios
from sim.metrics import check, summary, timeline
from sim.terminal import Terminal

ROOT = Path(__file__).resolve().parent.parent
MODULES = {"ship", "agv", "yard", "gate", "terminal"}


@pytest.fixture(scope="module")
def morning(dames):
    return Terminal(dames, scenarios.demo()).run()


@pytest.fixture(scope="module")
def short_shift(dames):
    return Terminal(dames, scenarios.shift(2, hours=3.0, calls=2, agvs=20, trucks_per_hour=10)).run()


@pytest.fixture(scope="module")
def replay():
    return json.loads((ROOT / "viewer3d" / "terminal-replay.json").read_text())["replay"]


@pytest.fixture(scope="module")
def recorded():
    return {v["id"]: v["replay"] for v in json.loads((ROOT / "viewer3d" / "terminal-variants.json").read_text())["variants"] if v["replay"]}


def widths(tl, end):
    """Seconds in each step: the last one may be short."""
    return [min((i + 1) * tl["step_s"], end) - i * tl["step_s"] for i in range(tl["n"])]


def over(tl, values, end):
    """A level summed over the run: thing-seconds."""
    return sum(v * w for v, w in zip(values, widths(tl, end)))


# ---- the run in equal steps of time -------------------------------------------------------------------
def test_the_steps_cover_the_run_and_there_are_at_most_two_hundred(morning, short_shift):
    for t in (morning, short_shift):
        tl = timeline(t)
        assert tl["n"] <= 200 and (tl["n"] - 1) * tl["step_s"] < t.end_s <= tl["n"] * tl["step_s"]
        series = [tl[k] for k in ("discharged", "loaded", "trucks_out", "rehandles", "ships_anchor", "ships_berth", "trucks_gate", "trucks_inside",
                                  "jobs_waiting", "quay_waiting", "buffered", "exchange", "yard")] + list(tl["agv"].values()) + list(tl["cranes"].values())
        assert all(len(s) == tl["n"] for s in series)
    assert timeline(morning)["step_s"] == 60 and timeline(short_shift)["step_s"] == 300
    assert timeline(morning, step_s=600)["n"] == 12                           # a step can be asked for


def test_the_totals_end_at_the_numbers_of_the_summary_and_never_fall(morning, short_shift):
    for t in (morning, short_shift):
        tl, s = timeline(t), summary(t)
        assert tl["discharged"][-1] == s["discharged"] and tl["loaded"][-1] == s["loaded_on_ships"]
        assert tl["rehandles"][-1] == s["rehandles"] and tl["trucks_out"][-1] == s["trucks_served"]
        for key in ("discharged", "loaded", "trucks_out", "rehandles"):
            assert all(a <= b for a, b in zip(tl[key], tl[key][1:])), key
    assert timeline(morning)["discharged"][-1] + timeline(morning)["loaded"][-1] == 36


def test_what_the_agvs_are_doing_adds_up_to_the_fleet_in_every_step(morning, short_shift):
    for t in (morning, short_shift):
        tl = timeline(t)
        assert tl["agvs"] == len(t.fleet.agvs) and set(tl["agv"]) == {"loaded", "empty", "crane", "held", "charger", "idle"}
        for i in range(tl["n"]):
            assert sum(tl["agv"][k][i] for k in tl["agv"]) == pytest.approx(tl["agvs"], abs=0.03), i
    assert max(timeline(morning)["agv"]["idle"]) < 0.05           # with a charger each, an AGV with no job is at its charger
    assert max(timeline(short_shift)["agv"]["idle"]) > 5          # a pooled fleet stands by on the apron


def test_the_time_agvs_are_held_up_is_the_time_the_summary_reports(morning, short_shift):
    for t in (morning, short_shift):
        tl, agvs = timeline(t), set(t.fleet.agvs)
        waited = sum(t.traffic.vehicles[a].waited_s for a in agvs)
        broken = sum(min(b["t1"], t.end_s) - b["t0"] for b in t.traffic.breakdowns if b["vehicle"] in agvs)
        assert over(tl, tl["agv"]["held"], t.end_s) == pytest.approx(waited + broken, rel=0.01, abs=20)
        assert waited / 60 == pytest.approx(summary(t)["agv_blocked_min"], abs=0.06)
        parked = (over(tl, tl["agv"]["idle"], t.end_s) + over(tl, tl["agv"]["charger"], t.end_s)) / (len(agvs) * t.end_s)
        assert parked == pytest.approx(summary(t)["agv_parked_share"], abs=0.02)


def test_the_queues_are_the_waits_of_the_ships_trucks_jobs_and_quay_cranes(morning, short_shift):
    for t in (morning, short_shift):
        tl, sc, end = timeline(t), t.scenario, t.end_s
        ships = [v for v in sc.vessels if "arrived" in v.times]
        assert over(tl, tl["ships_anchor"], end) == pytest.approx(sum(v.times["cleared"] - v.times["arrived"] for v in ships), abs=2 * tl["n"])
        trucks = [k for k in sc.trucks if "arrived" in k.times]
        assert over(tl, tl["trucks_gate"], end) == pytest.approx(sum(k.times.get("entered", k.times.get("gone", end)) - k.times["arrived"] for k in trucks), abs=2 * tl["n"])
        assert over(tl, tl["trucks_inside"], end) == pytest.approx(sum(k.times.get("gone", end) - k.times["entered"] for k in trucks if "entered" in k.times), abs=2 * tl["n"])
        assert over(tl, tl["jobs_waiting"], end) == pytest.approx(sum(j.times.get("assigned", end) - j.released for j in t.fleet.jobs), abs=2 * tl["n"])
        assert over(tl, tl["quay_waiting"], end) / 60 == pytest.approx(summary(t)["quay_crane_wait_min"], abs=0.5)
    assert max(timeline(morning)["ships_anchor"]) == 1 and max(timeline(morning)["ships_berth"]) == 2       # two berths, and one ship waits


def test_a_crane_is_at_work_while_it_lifts_or_travels(morning, short_shift):
    for t in (morning, short_shift):
        tl, busy = timeline(t), {}
        for r in t.trace:
            if r["kind"] in ("lift", "gantry"):
                busy[r["by"]] = busy.get(r["by"], 0.0) + r["t1"] - r["t0"]
        assert set(tl["cranes"]) == set(t.busy) and len(tl["cranes"]) == 13
        for crane, shares in tl["cranes"].items():
            assert all(0 <= x <= 1 for x in shares), crane
            assert over(tl, shares, t.end_s) == pytest.approx(busy.get(crane, 0.0), abs=1.6 * tl["n"]), crane
    assert any(r["kind"] == "gantry" for r in short_shift.trace) and not any(r["kind"] == "gantry" for r in morning.trace)


def test_the_yard_never_holds_more_than_it_has_room_for(morning, short_shift):
    for t in (morning, short_shift):
        tl = timeline(t)
        assert 0 < max(tl["yard"]) <= tl["yard_capacity"] and max(tl["exchange"]) <= 8.01 and min(tl["buffered"]) >= 0
    assert timeline(morning)["yard"][0] == 29                                # the morning starts with 29 containers in the yard


# ---- the reasons ------------------------------------------------------------------------------------------
def test_noting_reasons_changes_nothing_in_the_run(morning):
    s = summary(morning)
    assert (s["end_min"], s["containers"], s["rehandles"], s["ship_wait_min"], s["truck_turnaround_min"], s["agv_blocked_min"]) == (115.1, 58, 4, 10.8, 14.7, 81.4)
    assert check(morning) == [] and len(morning.log.reasons) > 200
    assert not any(e.text == r.text for e in morning.log.entries for r in morning.log.reasons[:20])      # kept apart from the log


def test_every_reason_names_its_module_and_things_of_the_run(morning, short_shift):
    for t in (morning, short_shift):
        sc = t.scenario
        things = set(t.fleet.agvs) | set(t.containers) | set(t.busy) | {v.id for v in sc.vessels} | {k.id for k in sc.trucks}
        last = 0.0
        for r in t.log.reasons:
            assert r.module in MODULES and r.t >= last and r.about, r
            assert set(r.about) <= things, (r.about, r.text)
            assert r.text.endswith(".") and "  " not in r.text and "None" not in r.text and "::" not in r.text, r.text
            assert r.text.startswith(tuple(things)), r.text                  # a sentence about a thing of the run
            last = r.t
        assert {r.module for r in t.log.reasons} == MODULES
    assert morning.log.why("BRAVO") == [r for r in morning.log.reasons if "BRAVO" in r.about]


def test_the_ship_arrival_module_says_why_a_ship_waits(morning):
    tide, berth = [r for r in morning.log.why("BRAVO") if r.text.startswith("BRAVO waits")]
    bravo = next(v for v in morning.scenario.vessels if v.id == "BRAVO")
    assert tide.t == bravo.times["arrived"] == 600 and "waits for the tide" in tide.text and f"draws {bravo.draft_m} m" in tide.text
    m = re.search(r"needs ([\d.]+) m of water .* The channel has ([\d.]+) m now, and enough from (\d+):(\d\d)\.", tide.text)
    assert m and float(m[1]) > float(m[2]) > bravo.draft_m                   # not enough now, though it would float
    assert int(m[3]) * 3600 + int(m[4]) * 60 == berth.t                      # when there is water it asks again: now the berths are taken
    assert berth.text == "BRAVO waits for a berth: B1 is taken by ALPHA and B2 is taken by CHARLIE."
    cleared = [r for r in morning.log.reasons if " is cleared for " in r.text]
    assert [r.about[0] for r in cleared] == ["ALPHA", "CHARLIE", "BRAVO"] and all("the channel has" in r.text for r in cleared)


def test_first_come_first_served_says_who_is_in_front(dames):
    t = Terminal(dames, scenarios.demo().using(ship="fcfs")).run()
    held = [r for r in t.log.reasons if "may not enter yet" in r.text]
    assert held and held[0].about == ("CHARLIE", "BRAVO") and "BRAVO, which came first, is still waiting" in held[0].text


def test_the_agv_module_says_why_this_agv_with_the_numbers_it_went_by(morning, dames):
    sent = [r for r in morning.log.reasons if r.module == "agv" and " is sent " in r.text]
    assert len(sent) == sum(1 for j in morning.fleet.jobs if j.agv)             # one for every job that got an AGV
    first = sent[0]
    assert first.text == ("AGV1 is sent to QC1, to take a container of ALPHA to the yard: the nearest of the 12 free AGVs with enough charge, "
                          "a drive of 300 m (the next, AGV3, 315 m).")
    for r in sent:                                                              # the nearest is never farther than the next
        m = re.search(r"a drive of ([\d,]+) m \(the next, AGV\d+, ([\d,]+) m\)", r.text)
        assert "the only free AGV" in r.text or (m and int(m[1].replace(",", "")) <= int(m[2].replace(",", ""))), r.text
    idle = Terminal(dames, scenarios.demo().using(dispatch="longest_idle")).run()
    assert any("without a job the longest of the" in r.text for r in idle.log.reasons)
    balanced = Terminal(dames, scenarios.demo().using(dispatch="balanced")).run()
    assert any("the best balance of distance, idle time and charge" in r.text and "% charged" in r.text for r in balanced.log.reasons)


def test_an_agv_that_takes_another_way_says_what_was_in_its_way(dames):
    t = control.run({"events": [{"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"}]}, dames)
    round_it = [r for r in t.log.reasons if "turns off early" in r.text or "takes another way" in r.text]
    assert round_it and all(r.module == "agv" and len(r.about) == 2 for r in round_it)
    assert any("AGV5 has broken down" in r.text and re.search(r"The way round is [\d,]+ m longer\.", r.text) for r in round_it)


def test_the_stacking_module_says_why_this_bay_and_why_this_stack(morning, dames):
    bay = {r.about[0]: r.text for r in morning.log.reasons if " goes to block " in r.text}
    stack = {r.about[0]: r.text for r in morning.log.reasons if re.search(r"^\S+ sets ", r.text)}
    discharged = [c for c in morning.containers.values() if c.flow == "import" and c.id.startswith(("A-", "B-", "C-"))]
    assert len(discharged) == 26 and all(c.id in bay and c.id in stack for c in discharged)     # every container taken off a ship
    assert "zone food takes food, and it is the only bay with room" in bay["A-09"]
    assert "zone hazmat takes hazardous cargo" in bay["A-06"]
    assert "zone west-a takes general cargo from berth B1, and of the 6 bays with room, it alone has a stack for it" in bay["A-07"]
    assert stack["A-08"].endswith("on IMP-08: it leaves at 1:35, and nothing under it leaves sooner (IMP-08 leaves at the same time).")
    assert stack["A-10"].endswith("tier 1, on the ground: no stack of the bay holds only containers that leave after it.")
    moved = [r for r in morning.log.reasons if " moves " in r.text and "it stands on" in r.text]
    assert len(moved) == summary(morning)["rehandles"] == 4 and all(len(r.about) == 3 and "which is wanted for" in r.text for r in moved)
    shortest = Terminal(dames, scenarios.demo().using(stacking="shortest_stack")).run()
    texts = [r.text for r in shortest.log.reasons if r.module == "yard"]
    assert any("its lowest stack is the lowest" in x for x in texts) and any("on the lowest stack of the bay that has room" in x or "the lowest there is" in x for x in texts)


def test_a_yard_crane_that_travels_says_which_agv_it_serves_next(short_shift):
    served = [r for r in short_shift.log.reasons if " serves " in r.text]
    assert len(served) > 20 and all(r.module == "yard" and len(r.about) == 3 for r in served)
    near = [r for r in served if "the nearest of the" in r.text or "already stands at its bay" in r.text]
    for r in near:
        m = re.search(r"([\d.]+) s of travel \(the next, AGV\d+, ([\d.]+) s away\)", r.text)
        assert "already stands at its bay" in r.text or float(m[1]) <= float(m[2]), r.text
    balance = [r for r in short_shift.log.reasons if "No yard crane with room had less work" in r.text]
    assert balance and all(" goes to block " in r.text for r in balance)


def test_the_gate_says_why_a_truck_is_called_checked_held_or_turned_away(morning):
    gate = [r for r in morning.log.reasons if r.module == "gate"]
    by = lambda truck: [r.text for r in gate if r.about[0] == truck]
    assert by("T01") == [
        "T01 is early: its appointment is at 0:15, and a booked truck is called to a lane when its time has come.",
        "T01 is called to lane 1: its appointment, at 0:15, is the earliest that has come.",
        "T01 passes the gate: its plate matches the booking, the terminal knows its container, it weighs 15.0 t as declared, and the toll of $25 is paid.",
        "T01 waits in the holding area, off the road: its container IMP-01 is on its way on an AGV, and a truck is called in when its container has reached the exchange.",
        "T01 is called in to truck spot (89, 4): its container IMP-01 has reached the exchange.",
    ]
    away = {r.about[0]: r.text for r in gate if "is turned away" in r.text}
    assert set(away) == {"T07", "T12", "T15"} and "its plate does not match the booking" in away["T07"] and "cannot pay the toll" in away["T15"]
    assert sum("is sent to the trouble desk" in r.text for r in gate) == 2


def test_the_scheduler_says_why_a_crane_uses_its_buffer_stops_or_calls_for_an_export(morning):
    mine = [r for r in morning.log.reasons if r.module == "terminal"]
    assert any(r.text == "QC2 stops: its buffer is full (2 of 2) and no AGV stands in its lanes. AGV10 is on the way." for r in mine)
    assert sum("on the ground: no AGV stands in its lanes" in r.text for r in mine) >= 10
    called = [r for r in mine if " is called for " in r.text]
    assert len(called) == 10 == summary(morning)["loaded_on_ships"] and all(len(r.about) == 2 for r in called)
    fetched = [r for r in mine if " is fetched from the yard for truck " in r.text]
    assert len(fetched) >= 10 and "30 minutes ahead" in fetched[0].text


def test_the_command_line_prints_the_reasons_about_one_thing(capsys):
    run = importlib.util.spec_from_file_location("run_cli", ROOT / "sim" / "run.py")
    module = importlib.util.module_from_spec(run)
    run.loader.exec_module(module)
    assert module.main(["--why", "BRAVO"]) == 0
    out = capsys.readouterr().out
    assert "0:10:00  [ship] BRAVO waits for the tide: it draws 11.9 m" in out and "BRAVO is cleared for B1" in out
    assert "ALPHA is cleared" not in out and "checks: all hold" in out and "T01 arrives at the gate" not in out      # the reasons, not the log
    assert module.main(["--why", "NOBODY", "--quiet"]) == 0
    assert "nothing was decided about NOBODY in this run" in capsys.readouterr().out


# ---- what the replay carries ------------------------------------------------------------------------------
def test_the_replay_carries_the_series_with_the_charge_of_the_fleet(replay, morning):
    s, tl = replay["series"], timeline(morning)
    assert {k: v for k, v in s.items() if not k.startswith("battery_")} == json.loads(json.dumps(tl))
    assert len(s["battery_mean"]) == len(s["battery_low"]) == s["n"]
    assert all(0 <= low <= mean <= 100 for low, mean in zip(s["battery_low"], s["battery_mean"]))
    assert s["battery_mean"][0] == pytest.approx(75, abs=2) and min(s["battery_low"]) >= 6.2     # they start at 75%, and none goes under its reserve of 2.5 kWh


def test_the_replay_carries_every_reason_in_time_order(replay, morning, recorded):
    assert replay["why"] == [[round(r.t, 1), r.module, list(r.about), r.text] for r in morning.log.reasons]
    for run in [replay, *recorded.values()]:
        ids = {v["id"] for v in run["vehicles"]} | {c["id"] for c in run["containers"]} | {s["id"] for s in run["ships"]} | {k["id"] for k in run["trucks"]}
        times = [w[0] for w in run["why"]]
        assert times == sorted(times) and all(w[1] in MODULES for w in run["why"])
        never_in = {k["drops"] for k in run["trucks"] if k["in"] is None and k["drops"]}       # what a truck that was turned away had brought
        unknown = {a for w in run["why"] for a in w[2]} - ids - never_in
        cranes = {a for a in unknown if re.fullmatch(r"(QC|RTG|XC)\d+", a)}
        assert all(re.fullmatch(r"T\d+", a) for a in unknown - cranes), sorted(unknown - cranes)[:5]     # besides cranes, only trucks that were booked and never came
        assert run["name"] != "A morning at Dames Point" or unknown == cranes                    # and every truck of the morning comes
    assert len(recorded["shift"]["why"]) > 2500


def test_the_replay_lists_the_ships_and_every_truck_that_came(replay, recorded):
    for run in [replay, *recorded.values()]:
        drawn = {v["id"] for v in run["vehicles"] if v["type"] == "truck"}
        for s in run["ships"]:
            times = [s[k] for k in ("arrived", "cleared", "berthed", "work_done", "cast_off", "gone") if s[k] is not None]
            assert times == sorted(times) and s["draft_m"] > 0
            off = sum(1 for c in run["containers"] if c["phases"][0].get("at", [None])[0] == "ship" and c["phases"][0]["at"][3] == s["id"])
            assert off == s["discharge"], s["id"]
        for k in run["trucks"]:
            assert k["arrived"] is not None and (k["in"] is None or k["arrived"] <= k["in"]) and (k["gone"] is None or k["gone"] >= (k["in"] or k["arrived"]))
            assert (k["id"] in drawn) == (k["in"] is not None), k["id"]            # a truck is drawn once it is let in
            assert not (k["turned_away"] and k["in"] is not None)
    assert [s["id"] for s in replay["ships"]] == ["ALPHA", "BRAVO", "CHARLIE"] and len(replay["trucks"]) == 15
    assert sum(k["turned_away"] for k in replay["trucks"]) == 3 == replay["summary"]["trucks_turned_away"]
    late = [k for k in recorded["shift"]["trucks"] if k["late"]]
    assert late and all(k["booked"] is not None for k in late)                 # one that came late keeps what it had booked, for the record


def test_the_replay_says_what_each_agv_was_sent_for_and_who_held_it_up(replay, recorded):
    for name, run in [("plan", replay), *recorded.items()]:
        ids = {v["id"] for v in run["vehicles"]}
        for v in run["vehicles"]:
            jobs = v.get("jobs", [])
            assert all(a[1] is None or a[1] <= b[0] + 0.05 for a, b in zip(jobs, jobs[1:])), (name, v["id"])      # one job at a time
            assert all(j[2] and (j[1] is None or j[1] >= j[0]) for j in jobs)
            for t0, t1, cell, by in v.get("waits", []):
                assert 0 <= t0 and (t1 is None or t0 + 3.0 <= t1 + 0.05 <= run["t_end"] + 0.2) and len(cell) == 2
                assert by in ids or by == "a closed road", (name, v["id"], by)
    assert sum(len(v.get("jobs", [])) for v in replay["vehicles"]) == replay["summary"]["agv_jobs"]
    assert any(w[3] == "a closed road" for v in recorded["road-closed"]["vehicles"] for w in v.get("waits", []))
    assert all(v["home"] is None for v in recorded["shift"]["vehicles"] if v["type"] == "agv")       # a pooled fleet shares the stations
    assert all(v["home"] for v in replay["vehicles"] if v["type"] == "agv")


def test_a_container_in_the_replay_says_what_it_came_on_and_leaves_on(replay):
    by_id = {c["id"]: c for c in replay["containers"]}
    assert by_id["IMP-05"]["truck"] == "T05" and by_id["IMP-05"]["due"] == 3300 and "ship" not in by_id["IMP-05"]
    assert by_id["EXP-A1"]["ship"] == "ALPHA" and by_id["A-07"]["ship"] == "ALPHA" and "truck" not in by_id["A-07"]


# ---- the page ---------------------------------------------------------------------------------------------
def test_the_viewer_reads_the_series_the_reasons_and_the_lists():
    page = (ROOT / "viewer3d" / "index.html").read_text()
    for name in ("SIM.series", "SIM.why", "SIM.ships", "SIM.trucks", "function dashRun", "function drawLines", "function drawColumns", "function drawMeters",
                 "function drawTable", "function heldChain", "function shipCard", "data-jump", "data-pin", 'id="dash-table"', "sv.jobs", "v.sim.waits"):
        assert name in page, name
    for token in ("--s1:", "--s6:", "--dim:", "--grid:", "--track:"):                # every colour of the charts in the light theme and both dark blocks
        assert page.count(token) == 3, token
    assert "pts.length === 2" in page                                             # a move of one cell is drawn (it had no speed to drive at)
    assert page.count("localStorage") == 2 and page.count("try {") >= 2             # the drawer's state is a convenience: it works without storage


def test_the_integration_notes_describe_the_dashboard_and_the_reasons():
    notes = (ROOT / "docs" / "integration.md").read_text()
    assert "## The dashboard and the reasons" in notes
    for word in ("timeline", "EventLog.explain", "series", "why", "Decisions"):
        assert word in notes, word
