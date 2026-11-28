"""The 3D viewer's replay: the integration demo's trace, reshaped, and consistent with the layout."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "viewer3d" / "demo-replay.json"


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


def test_the_replay_file_is_what_the_generator_writes(dames):
    built = load("tools/build_viewer_replay.py", "build_viewer_replay").build(dames)
    same = json.dumps(built, separators=(",", ":")) + "\n" == FILE.read_text()      # compared first: pytest's diff of two long files takes minutes
    assert same, "viewer3d/demo-replay.json is out of date: run tools/build_viewer_replay.py"


def test_every_leg_is_legal_moves_and_starts_where_the_vehicle_is(dames, replay):
    assert [v["id"] for v in replay["vehicles"]] == ["AGV1", "AGV2", "AGV3", "TRK-B", "TRK-A"]
    for v in replay["vehicles"]:
        here, clock = tuple(v["at"]), v["t"]
        for leg in v["legs"]:
            path = [tuple(p) for p in leg["path"]]
            assert path[0] == here and leg["t0"] >= clock and leg["t1"] > leg["t0"], (v["id"], leg["t0"])
            assert all(b in dames.neighbors(*a) for a, b in zip(path, path[1:])), v["id"]
            here, clock = path[-1], leg["t1"]
        assert (v["gone"] is not None) == (v["type"] == "truck")


def test_marks_are_the_stopping_points_a_leg_passes_in_order(dames, replay):
    from terminal_layout.timing import stopping_points

    stops = stopping_points(dames)
    marked = 0
    for v in replay["vehicles"]:
        for leg in v["legs"]:
            path, marks = [tuple(p) for p in leg["path"]], leg.get("marks", [])
            assert [i for i, _ in marks] == [i for i in range(1, len(path) - 1) if path[i] in stops], v["id"]
            times = [leg["t0"]] + [t for _, t in marks] + [leg["t1"]]
            assert all(a < b for a, b in zip(times, times[1:])), (v["id"], leg["t0"])
            marked += len(marks)
    assert marked > 400


def test_each_container_is_always_somewhere_and_ends_where_the_run_says(replay):
    ends = {}
    for c in replay["containers"]:
        phases = c["phases"]
        assert "at" in phases[0] and "at" in phases[-1] and phases[-1]["t1"] is None
        for a, b in zip(phases, phases[1:]):
            assert a["t1"] == b["t0"] and ("at" in a) != ("at" in b), c["id"]      # rest, lift, rest, ...
            if "by" in b:
                assert b["from"] == a["at"] and b["t1"] >= b["t0"]
            else:
                assert b["at"] == a["to"]
        ends[c["id"]] = phases[-1]["at"]
    assert {k: v[:2] for k, v in ends.items()} == {
        "IMP-1": ["slot", "C2-01-01"], "IMP-2": ["slot", "C2-02-01"], "IMP-3": ["slot", "C2-04-01"],
        "IMP-5": ["slot", "C2-03-01"], "IMP-4": ["vehicle", "TRK-A"], "EXP-1": ["ship", "QC2"]}


def test_a_container_rides_only_while_its_vehicle_drives_loaded_with_it(replay):
    legs = {(v["id"], leg["what"]): leg for v in replay["vehicles"] for leg in v["legs"] if leg["what"]}
    for c in replay["containers"]:
        for ph in c["phases"]:
            if "at" in ph and ph["at"][0] == "vehicle" and ph["t0"] is not None and ph["t1"] is not None:
                leg = legs.get((ph["at"][1], c["id"]))
                if leg:      # on a parked truck there is no leg
                    assert leg["loaded"] and ph["t0"] <= leg["t0"] and leg["t1"] <= ph["t1"], (c["id"], ph)


def test_no_ground_buffer_holds_more_than_it_has_room_for(dames, replay):
    for c in replay["containers"]:
        for ph in c["phases"]:
            for place in (ph.get("at"), ph.get("to")):
                if place and place[0] == "ground":
                    assert place[2] < dames.cell(*place[1]).attrs["buffer_capacity"], (c["id"], place)


def test_the_lifts_are_the_ones_the_run_logged(replay):
    log = {text: t for t, text in replay["log"]}
    by_id = {c["id"]: [ph for ph in c["phases"] if "by" in ph] for c in replay["containers"]}
    assert by_id["IMP-1"][0]["by"] == "QC2" and by_id["IMP-1"][0]["t1"] == log["QC2 sets IMP-1 in its buffer (1/2)"]
    assert by_id["IMP-1"][-1]["by"] == "RTG2" and by_id["IMP-1"][-1]["t1"] == log["RTG2 stacks IMP-1 in C2-01-01"]
    assert by_id["IMP-4"][-1]["by"] == "XC1" and by_id["IMP-4"][-1]["t1"] == log["XC1 loads IMP-4 onto TRK-A"]
    assert by_id["EXP-1"][0]["by"] == "XC1" and by_id["EXP-1"][-1]["t1"] == log["QC2 loads EXP-1 onto the ship"]
    assert replay["t_end"] == max(t for t, _ in replay["log"])
    # into the buffer, onto the AGV, off the AGV into the stack: a crane each time
    assert [(ph["by"], ph["from"][0], ph["to"][0]) for ph in by_id["IMP-2"]] == [
        ("QC2", "ship", "ground"), ("QC2", "ground", "vehicle"), ("RTG2", "vehicle", "slot")]
    # straight onto the AGV that was waiting, then straight onto the truck: never on the ground
    assert [(ph["by"], ph["from"][0], ph["to"]) for ph in by_id["IMP-4"]] == [
        ("QC3", "ship", ["vehicle", "AGV3"]), ("XC1", "vehicle", ["vehicle", "TRK-A"])]


def test_no_crane_lifts_two_containers_at_once(replay):
    spans = {}
    for c in replay["containers"]:
        for ph in c["phases"]:
            if "by" in ph:
                assert ph["by"], (c["id"], ph)
                spans.setdefault(ph["by"], []).append((ph["t0"], ph["t1"]))
    for crane, s in spans.items():
        s.sort()
        assert all(a[1] <= b[0] for a, b in zip(s, s[1:])), crane


def test_the_waiting_map_is_the_stress_test_and_mostly_on_the_quay(dames, data):
    w = data["waiting"]
    quay = {c.pos for c in dames.quay_cells()}
    assert w["fleet"] == 24 and all(dames.is_traversable(x, y) and s > 0 for x, y, s in w["cells"])
    on_quay = sum(s for x, y, s in w["cells"] if (x, y) in quay) / sum(s for _, _, s in w["cells"])
    assert 0.5 < on_quay < 0.85


def test_a_truck_that_comes_after_its_container_waits_one_more_crane_move(dames):
    demo = load("sim/run_demo.py", "run_demo")
    import importlib
    modules = importlib.import_module("sim.modules")

    class Late(modules.Gate):
        def truck_arrivals(self):
            return [modules.Truck("TRK-A", 1500.0, picks="IMP-4")]

    terminal = demo.Terminal(dames)
    terminal.gate = Late()
    log = terminal.run()
    when = {text: t for t, text in log}
    assert "XC1 sets IMP-4 in the exchange stack" in when
    assert when["XC1 loads IMP-4 onto TRK-A"] == pytest.approx(when["TRK-A parks at truck spot (89, 4)"] + demo.XC_CYCLE_S)
    stack = [r for r in terminal.trace if r["kind"] == "lift" and r["what"] == "IMP-4" and r["by"] == "XC1"]
    assert [tuple(r["dst"]) for r in stack] == [("stack", "XT"), ("vehicle", "TRK-A")]
