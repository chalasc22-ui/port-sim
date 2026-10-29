"""The 3D viewer's demo routes: generated from the layout, and every step a legal move."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTES = ROOT / "viewer3d" / "demo-routes.json"


@pytest.fixture(scope="module")
def routes():
    return json.loads(ROUTES.read_text())["routes"]


def test_every_step_of_every_route_is_a_legal_move(dames, routes):
    assert len(routes) == 6
    for r in routes:
        path = [tuple(p) for p in r["path"]]
        steps = list(zip(path, path[1:])) + ([(path[-1], path[0])] if r["loop"] else [])
        bad = [(a, b) for a, b in steps if b not in dames.neighbors(*a)]
        assert not bad, (r["id"], bad[:3])


def test_stops_sit_where_the_route_says(dames, routes):
    for r in routes:
        for stop in r["stops"]:
            cell = dames.cell(*r["path"][stop["at"]])
            assert cell.type in ("transfer_point", "charging_station", "gate"), (r["id"], stop)


def test_routes_file_is_what_the_generator_writes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bvr", ROOT / "tools" / "build_viewer_routes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = ROUTES.read_text()
    assert module.main() == 0
    assert ROUTES.read_text() == before, "viewer3d/demo-routes.json is out of date: run tools/build_viewer_routes.py"


def test_the_viewer_reads_only_the_compiled_file_and_the_routes():
    page = (ROOT / "viewer3d" / "index.html").read_text()
    assert "../layouts/dames-point.compiled.json" in page and "demo-routes.json" in page
    assert "dames-point.json\"" not in page  # never the authored file


def test_the_agv_stub_completes_both_jobs_using_only_the_api(dames):
    """The integration smoke test from the timeline: a job driven with neighbors() alone."""
    spec = importlib.util.spec_from_file_location("agv_stub", ROOT / "examples" / "agv_stub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for legs in module.jobs(dames).values():
        moves, seconds, _ = module.drive(dames, legs)
        assert moves > 0 and seconds > 0
