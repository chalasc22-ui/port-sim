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


def test_the_demo_finishes_every_container(dames):
    demo = load("sim/run_demo.py", "run_demo")
    text = " | ".join(line for _, line in demo.Terminal(dames).run())
    for c in ("IMP-1", "IMP-2", "IMP-4"):
        assert f"stacks {c}" in text
    assert "XC1 loads IMP-3 onto TRK-A" in text and "QC2 loads EXP-1 onto the ship" in text
    assert "TRK-A leaves" in text and "TRK-B leaves" in text
    assert "(2/2)" in text  # the quay buffer fills, so the crane waits for an AGV


def test_a_fleet_of_twelve_never_gridlocks(dames):
    fleet = load("tools/fleet_stress.py", "fleet_stress")
    r = fleet.run(dames, 12, seconds=1800, seed=1)
    assert r["gridlock_at_s"] is None and r["jobs_per_hour"] > 30
