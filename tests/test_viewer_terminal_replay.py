"""The replay of the integrated terminal that the 3D viewer plays (viewer3d/terminal-replay.json):
the demo morning's trace, reshaped, and consistent with the layout and with the run itself."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "viewer3d" / "terminal-replay.json"


def load(path, name):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data():
    return json.loads(FILE.read_text())


@pytest.fixture(scope="module")
def replay(data):
    return data["replay"]


def resting(replay, kind):
    """(place, from, until, container) for every stay of a container at a place of this kind."""
    end = replay["t_end"] + 1.0
    return [(tuple(map(str, ph["at"][1:])), -1.0 if ph["t0"] is None else ph["t0"], end if ph["t1"] is None else ph["t1"], c["id"])
            for c in replay["containers"] for ph in c["phases"] if "at" in ph and ph["at"][0] == kind]


def test_the_replay_file_is_what_the_generator_writes(dames, data):
    built = load("tools/build_viewer_replay.py", "build_viewer_replay").build_terminal(dames, heat=data["waiting"])
    same = json.dumps(built, separators=(",", ":")) + "\n" == FILE.read_text()      # compared first: pytest's diff of two long files takes minutes
    assert same, "viewer3d/terminal-replay.json is out of date: run tools/build_viewer_replay.py"


def test_it_is_the_demo_morning_with_every_module_in_it(replay):
    assert replay["source"] == "sim/terminal.py" and set(replay["policies"]) == {"ship", "stacking", "dispatch", "gate"}
    assert [(v["id"], v["berth"]) for v in replay["vessels"]] == [("ALPHA", "B1"), ("CHARLIE", "B2"), ("BRAVO", "B1")]
    kinds = [v["type"] for v in replay["vehicles"]]
    assert kinds.count("agv") == 12 and kinds.count("truck") == replay["summary"]["trucks_served"] == 12
    modules = {line[2] for line in replay["log"]}
    assert modules == {"ship", "agv", "gate", "terminal"} or modules == {"ship", "gate", "terminal"}     # the AGV module's lines are routine ones
    assert any("is turned away" in line[1] for line in replay["log"]) and any("waits: not enough water" in line[1] for line in replay["log"])
    assert replay["t_end"] == pytest.approx(replay["summary"]["end_min"] * 60, abs=6)


def test_every_leg_is_legal_moves_starts_where_the_vehicle_is_and_stops_where_it_says(dames, replay):
    short = 0
    gates_in, gates_out = {c.pos for c in dames.gate_cells("in")}, {c.pos for c in dames.gate_cells("out")}
    for v in replay["vehicles"]:
        here, clock = tuple(v["at"]), v["t"]
        for leg in v["legs"]:
            path = [tuple(p) for p in leg["path"]]
            assert path[0] == here and leg["t0"] >= clock - 0.05 and leg["t1"] > leg["t0"], (v["id"], leg["t0"])
            assert all(b in dames.neighbors(*a) for a, b in zip(path, path[1:])), v["id"]
            marks = leg.get("marks", [])
            cells, times = [0] + [i for i, _ in marks] + [len(path) - 1], [leg["t0"]] + [t for _, t in marks] + [leg["t1"]]
            assert all(a < b for a, b in zip(cells, cells[1:])) and all(a < b for a, b in zip(times, times[1:])), (v["id"], leg["t0"])
            if "dest" in leg:                                   # it stopped behind another vehicle, short of where it was going
                assert tuple(leg["dest"]) != path[-1]
                short += 1
            here, clock = path[-1], leg["t1"]
        assert (v["gone"] is not None) == (v["type"] == "truck")
        if v["type"] == "truck":
            assert tuple(v["at"]) in gates_in and here in gates_out and v["gone"] >= clock
    assert short > 20


def test_two_vehicles_are_never_shown_in_one_cell(replay):
    """At every moment a vehicle is known to stand still (a leg's start, its rests and its end), no other
    vehicle stands on that cell."""
    stands = {}                                              # cell -> [(from, until, vehicle)]
    for v in replay["vehicles"]:
        cell, since = tuple(v["at"]), v["t"]
        for leg in v["legs"]:
            stands.setdefault(cell, []).append((since, leg["t0"], v["id"]))
            cell, since = tuple(leg["path"][-1]), leg["t1"]
        stands.setdefault(cell, []).append((since, v["gone"] if v["gone"] is not None else replay["t_end"] + 1, v["id"]))
    for cell, spans in stands.items():
        spans.sort()
        for (_, a1, a), (b0, _, b) in zip(spans, spans[1:]):
            assert a == b or b0 >= a1 - 0.05, (cell, a, b, b0)


def test_each_container_is_always_somewhere_and_ends_where_the_run_says(replay):
    ends = {}
    for c in replay["containers"]:
        phases = c["phases"]
        assert "at" in phases[0] and "at" in phases[-1] and phases[-1]["t1"] is None, c["id"]
        for a, b in zip(phases, phases[1:]):
            assert a["t1"] == b["t0"] and ("at" in a) != ("at" in b), c["id"]      # rest, lift, rest, ...
            if "by" in b:
                assert b["by"] and b["from"] == a["at"] and b["t1"] > b["t0"], c["id"]
            else:
                assert b["at"] == a["to"], c["id"]
        ends[c["id"]] = phases[-1]["at"]
    assert ends["A-03"] == ["vehicle", "T13"] and ends["EXP-C3"][0] == "ship" and ends["EXP-C3"][3] == "CHARLIE"
    assert ends["STAY-02"][0] == "slot" and "EXP-D1" not in ends          # its truck was turned away: it never came in
    s = replay["summary"]
    assert sum(1 for e in ends.values() if e[0] == "ship") == s["loaded_on_ships"]
    assert sum(1 for e in ends.values() if e[0] == "vehicle") == s["left_by_truck"]
    assert sum(1 for c in replay["containers"] if c["phases"][0]["at"][0] == "ship") == s["discharged"]


def test_no_crane_lifts_two_containers_at_once(replay):
    spans = {}
    for c in replay["containers"]:
        for ph in c["phases"]:
            if "by" in ph:
                spans.setdefault(ph["by"], []).append((ph["t0"], ph["t1"]))
    assert set(spans) >= {"QC1", "QC2", "QC3", "QC4", "QC5", "QC6", "XC1", "RTG1"}
    for crane, s in spans.items():
        s.sort()
        assert all(a[1] <= b[0] for a, b in zip(s, s[1:])), crane


def test_a_yard_position_holds_one_container_and_none_floats(dames, replay):
    stays = resting(replay, "slot")
    assert all(len(place) == 3 for place, *_ in stays)                    # slot, row, tier: an exact position
    by_place = {}
    for place, t0, t1, cid in stays:
        slot, row, tier = place[0], int(place[1]), int(place[2])
        block = dames.blocks[dames.slot(slot).attrs["block"]]
        assert 1 <= row <= block.container_rows and 1 <= tier <= block.max_tier, (cid, place)
        by_place.setdefault((slot, row, tier), []).append((t0, t1, cid))
    for place, spans in by_place.items():
        spans.sort()
        assert all(a[1] <= b[0] + 0.05 for a, b in zip(spans, spans[1:])), place
    for (slot, row, tier), spans in by_place.items():                     # whatever stands at tier 2 or higher stands on something
        for t0, t1, cid in spans:
            if tier > 1:
                under = sorted(by_place.get((slot, row, tier - 1), []))
                covered = max(t0, 0.0)
                for a, b, _ in under:
                    if a <= covered + 0.05:
                        covered = max(covered, b)
                assert covered >= min(t1, replay["t_end"]) - 0.05, (cid, slot, row, tier)


def test_the_run_keeps_its_own_stacks_and_separates_food_from_hazardous(dames, replay):
    managed, cargo = set(replay["managed_slots"]), {c["id"]: c["cargo"] for c in replay["containers"]}
    zone_of = {slot: z for z in replay["zones"] for slot in z["slots"]}
    assert {z["cargo"] for z in replay["zones"]} == {"general", "food", "hazmat"}
    for place, _, _, cid in resting(replay, "slot"):
        assert place[0] in managed, (cid, place)
        if place[0] in zone_of:
            assert zone_of[place[0]]["cargo"] == cargo[cid], (cid, place)
    assert sum(1 for c in cargo.values() if c == "food") == 2 and sum(1 for c in cargo.values() if c == "hazmat") == 1


def test_no_ground_buffer_holds_more_than_it_has_room_for(dames, replay):
    used = set()
    for c in replay["containers"]:
        for ph in c["phases"]:
            for place in (ph.get("at"), ph.get("to")):
                if place and place[0] == "ground":
                    assert place[2] < dames.cell(*place[1]).attrs["buffer_capacity"], (c["id"], place)
                    used.add(tuple(place[1]))
    assert len(used) >= 3


def test_a_ship_is_at_one_berth_at_a_time_and_its_containers_name_it(replay):
    by_berth = {}
    for v in replay["vessels"]:
        assert v["t_arrive"] <= v["t_cleared"] < v["t"] < v["t_depart"]
        by_berth.setdefault(v["berth"], []).append((v["t_cleared"], v["t_depart"]))
    for spans in by_berth.values():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    here = {v["id"]: v for v in replay["vessels"]}
    for place, t0, t1, cid in resting(replay, "ship"):
        crane, n, vessel = place
        assert vessel in here and int(n) < 14, (cid, place)
    spots = [(place, cid) for place, *_, cid in resting(replay, "ship")]
    assert len({place for place, _ in spots}) == len(spots)              # no two containers share a spot on a ship's deck


def test_an_agv_charges_only_at_home_and_its_battery_stays_in_range(dames, replay):
    stations = {c.attrs["id"]: c.pos for c in dames.charging_stations()}
    charged = 0
    for v in replay["vehicles"]:
        if v["type"] != "agv":
            assert "battery" not in v
            continue
        points = v["battery"]
        assert points[0] == [0.0, 75.0] and all(0 < pct <= 90.0 for _, pct in points), v["id"]
        assert all(a[0] < b[0] for a, b in zip(points, points[1:])), v["id"]
        for t0, t1 in v["charging"]:
            assert t1 > t0 and all(leg["t1"] <= t0 + 0.05 or leg["t0"] >= t1 - 0.05 for leg in v["legs"]), (v["id"], t0)
            cell = tuple(v["at"])
            for leg in v["legs"]:
                if leg["t1"] <= t0 + 0.05:
                    cell = tuple(leg["path"][-1])
            assert cell == stations[v["home"]], (v["id"], t0)
            charged += t0 > 0
    assert charged >= 12                                                 # every AGV comes home to charge at least once in the morning


def test_the_viewer_reads_what_the_replay_adds():
    page = (ROOT / "viewer3d" / "index.html").read_text()
    for name in ("terminal-replay.json", "managed_slots", "SIM.zones", "t_depart", "sv.battery", "sv.charging", "l.dest",
                 "terminal-variants.json", "SIM.disruptions", "data.control", "function loadRun", '"/run"'):
        assert name in page, name


# ---- the recorded what-if runs --------------------------------------------------------------------------
VARIANTS = ROOT / "viewer3d" / "terminal-variants.json"


@pytest.fixture(scope="module")
def variants():
    return json.loads(VARIANTS.read_text())["variants"]


def test_the_what_if_file_is_what_the_generator_writes(dames):
    built = load("tools/build_viewer_replay.py", "build_viewer_replay").build_variants(dames)
    same = json.dumps(built, separators=(",", ":")) + "\n" == VARIANTS.read_text()
    assert same, "viewer3d/terminal-variants.json is out of date: run tools/build_viewer_replay.py"


def test_every_what_if_is_a_whole_run_that_passed_its_checks_and_differs_from_the_plan(dames, replay, variants):
    from sim import control

    assert [v["id"] for v in variants] == [v["id"] for v in control.VARIANTS] and variants[0]["replay"] is None
    assert {v["module"] for v in variants} >= {"Ship arrival", "AGVs", "Stacking", "Gate", "Layout"}
    plan = replay["summary"]
    for v in variants[1:]:
        r = v["replay"]
        assert r["settings"] == v["settings"] and r["problems"] == [] and v["title"] and v["about"], v["id"]
        assert set(r["summary"]) == set(plan) and r["summary"] != plan, v["id"]
        for vehicle in r["vehicles"]:                                  # every leg is legal moves from where the vehicle stands
            here = tuple(vehicle["at"])
            for leg in vehicle["legs"]:
                path = [tuple(c) for c in leg["path"]]
                assert path[0] == here and all(b in dames.neighbors(*a) for a, b in zip(path, path[1:])), (v["id"], vehicle["id"])
                here = path[-1]
        for c in r["containers"]:                                      # rest, lift, rest, ...
            assert all(a["t1"] == b["t0"] and ("at" in a) != ("at" in b) for a, b in zip(c["phases"], c["phases"][1:])), (v["id"], c["id"])


def test_a_what_if_records_what_was_done_to_the_run(variants):
    by_id = {v["id"]: v["replay"] for v in variants}
    assert replay_keys(by_id["agv-breakdown"]) == [("agv", 1900.0, 2500.0)] and by_id["agv-breakdown"]["disruptions"][0]["id"] == "AGV5"
    assert replay_keys(by_id["road-closed"]) == [("road", 1200.0, 2100.0)] and by_id["road-closed"]["disruptions"][0]["cells"] == [[57, 28]]
    assert replay_keys(by_id["crane-down"]) == [("crane", 700.0, 1600.0)] and by_id["ship-fcfs"]["disruptions"] == []
    assert by_id["agv-fleet-6"]["agvs"] == 6 and sum(1 for v in by_id["agv-fleet-6"]["vehicles"] if v["type"] == "agv") == 6
    assert by_id["gate-rush"]["summary"]["trucks_served"] == 18 and by_id["stack-shortest"]["policies"]["stacking"] == "shortest_stack"
    for leg in (leg for v in by_id["road-closed"]["vehicles"] for leg in v["legs"]):       # nobody drives onto the closed cell while it is shut
        if [57, 28] in leg["path"][1:]:
            assert leg["t1"] <= 1260.0 or leg["t0"] >= 2100.0
    stopped = by_id["agv-breakdown"]["disruptions"][0]["at"]                             # the broken AGV stands still until it is repaired
    agv5 = next(v for v in by_id["agv-breakdown"]["vehicles"] if v["id"] == "AGV5")
    assert not [leg for leg in agv5["legs"] if 1960.0 < leg["t0"] < 2500.0] and any(leg["path"][-1] == stopped for leg in agv5["legs"])


def replay_keys(replay):
    return [(d["kind"], d["t0"], d["t1"]) for d in replay["disruptions"]]
