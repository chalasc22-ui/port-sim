"""The integration demo and the fleet stress test run on the real map."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


def first(log, text):
    return min(t for t, line in log if text in line)


def test_the_demo_finishes_every_container(dames):
    demo = load("sim/run_demo.py", "run_demo")
    log = demo.Terminal(dames).run()
    text = " | ".join(line for _, line in log)
    for c in ("IMP-1", "IMP-2", "IMP-3", "IMP-5"):
        assert f"stacks {c}" in text
    assert "XC1 loads IMP-4 onto TRK-A" in text and "QC2 loads EXP-1 onto the ship" in text
    assert "TRK-A leaves" in text and "TRK-B leaves" in text
    assert first(log, "XC1 loads IMP-4") >= first(log, "TRK-A parks")   # only once the truck is there


def test_a_quay_crane_waits_while_its_buffer_is_full(dames):
    """QC2 has three imports and room for two on the ground: it stops until an AGV takes one."""
    demo = load("sim/run_demo.py", "run_demo")
    log = demo.Terminal(dames).run()
    text = " | ".join(line for _, line in log)
    assert "QC2 waits: its buffer is full (2/2)" in text and "(3/2)" not in text
    assert first(log, "QC2 waits") < first(log, "picks up IMP-1") == first(log, "QC2 goes on")
    assert first(log, "QC2 sets IMP-3") == first(log, "QC2 goes on") + demo.QC_CYCLE_S


def test_a_late_truck_is_loaded_only_once_it_has_parked(dames):
    demo = load("sim/run_demo.py", "run_demo")
    modules = importlib.import_module("sim.modules")

    class Late(modules.Gate):
        def truck_arrivals(self):
            return [modules.Truck("TRK-A", 1000.0, picks="IMP-4")]

    terminal = demo.Terminal(dames)
    terminal.gate = Late()
    log = terminal.run()
    assert first(log, "XC1 loads IMP-4") >= first(log, "TRK-A parks") > first(log, "TRK-A clears the gate")
    assert first(log, "TRK-A leaves") > first(log, "XC1 loads IMP-4")


def test_with_no_quay_buffer_the_crane_lifts_straight_onto_an_agv(dames):
    """buffer_capacity 0 is a coupled handover: nothing waits on the ground, the crane waits for an AGV."""
    import json

    from terminal_layout.loader import layout_from_dict

    data = json.loads((ROOT / "layouts" / "dames-point.json").read_text())
    for berth in data["berths"]:
        for crane in berth["cranes"]:
            crane["buffer_capacity"] = 0
    demo = load("sim/run_demo.py", "run_demo")
    log = demo.Terminal(layout_from_dict(data)).run()
    text = " | ".join(line for _, line in log)
    for c in ("IMP-1", "IMP-2", "IMP-3", "IMP-5"):
        assert f"sets {c} straight onto" in text and f"stacks {c}" in text
    assert "in its buffer" not in text and "waits: its buffer is full" not in text
    assert first(log, "QC2 sets IMP-2") >= first(log, "QC2 sets IMP-1") + demo.QC_CYCLE_S


def test_two_trucks_at_once_get_two_truck_spots(dames):
    demo = load("sim/run_demo.py", "run_demo")
    modules = importlib.import_module("sim.modules")   # the module the demo itself imported

    class TwoAtOnce(modules.Gate):
        def truck_arrivals(self):
            return [modules.Truck("T1", 0.0, picks="IMP-4"), modules.Truck("T2", 0.0, picks="none")]

    terminal = demo.Terminal(dames)
    terminal.gate = TwoAtOnce()
    spots = [line.split("spot ")[1] for _, line in terminal.run() if "parks at truck spot" in line]
    assert len(spots) == 2 and spots[0] != spots[1]


def test_a_fleet_of_twelve_never_gridlocks(dames):
    fleet = load("tools/fleet_stress.py", "fleet_stress")
    r = fleet.run(dames, 12, seconds=1800, seed=1)
    assert r["gridlock_at_s"] is None and r["jobs_per_hour"] > 30
    assert 0 < r["mean_wait_s"] <= r["max_wait_s"] and 0 < r["quay_share"] < 1
    for rule in ("crane", "bypass"):                      # the other lane rules run too
        assert fleet.run(dames, 12, seconds=600, seed=1, quay_rule=rule)["jobs_per_hour"] > 0


def test_fleet_jobs_use_the_quay_lanes_and_the_storage_bays_only(dames):
    """The truck spots (XT) are not an AGV destination, and the exchange stack (XA) is not storage."""
    fleet = load("tools/fleet_stress.py", "fleet_stress")
    quay_lanes, bays = fleet.job_places(dames)
    exchange = set(dames.blocks["XA"].transfer_points) | set(dames.blocks["XT"].transfer_points)
    assert len(quay_lanes) == 24 and len(bays) == 419 and not exchange & set(bays)


def test_charging_demand_is_every_agv_transfer_point(dames):
    siting = load("tools/charger_siting.py", "charger_siting")
    demand = siting.demand_points(dames)
    assert len(demand) == 447 == len(dames.cells("transfer_point")) - 4
    assert not set(dames.blocks["XT"].transfer_points) & set(demand)
    assert set(dames.blocks["XA"].transfer_points) <= set(demand)
