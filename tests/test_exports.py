"""The road-graph export and the road variants."""

import importlib.util
import json
from collections import deque
from pathlib import Path

import pytest

from terminal_layout import load_layout

ROOT = Path(__file__).resolve().parent.parent


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_graph_distances_equal_grid_distances(dames):
    """Every segment follows legal moves, and shortest distances between stops are unchanged."""
    g = tool("export_graph").graph(dames)
    for s in g["segments"]:
        cells = [tuple(c) for c in s["cells"]]
        assert all(b in dames.neighbors(*a) for a, b in zip(cells, cells[1:]))
        assert s["length_m"] == (len(cells) - 1) * 15
    pos = {n["id"]: tuple(n["cell"]) for n in g["nodes"]}
    adj = {}
    for s in g["segments"]:
        adj.setdefault(s["from"], []).append((s["to"], s["length_m"]))
    start = next(n["id"] for n in g["nodes"] if n.get("equipment") == "G_IN1")
    import heapq
    dist, heap = {start: 0}, [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v, w in adj.get(u, []):
            if d + w < dist.get(v, 1e18):
                dist[v] = d + w
                heapq.heappush(heap, (d + w, v))
    for node in g["nodes"][::25]:
        moves = len(dames.shortest_path(pos[start], pos[node["id"]])) - 1
        assert dist[node["id"]] == moves * 15


def test_graph_file_is_up_to_date(dames):
    on_disk = json.loads((ROOT / "layouts" / "dames-point.graph.json").read_text())
    assert on_disk == json.loads(json.dumps(tool("export_graph").graph(dames)))


@pytest.mark.parametrize("name", ["dames-point-two-way", "dames-point-one-aisle"])
def test_road_variants_load_and_keep_every_station(dames, name):
    variant = load_layout(ROOT / "layouts" / "variants" / f"{name}.json")
    assert variant.warnings == () and variant.validate() == []
    for kind in ("transfer_point", "charging_station", "gate"):
        assert [c.pos for c in variant.cells(kind)] == [c.pos for c in dames.cells(kind)]


def test_segment_time_matches_portsim_worked_examples():
    """PortSim appendix B.4: 24 m edges, 4 m/s, 2 m/s^2 -> a four-edge trip takes 32 s."""
    from terminal_layout.timing import segment_time

    assert 4 * segment_time(24, 4.0, 2.0) == pytest.approx(32.0)
    assert segment_time(4, 4.0, 2.0) == pytest.approx(2 * (4 / 2) ** 0.5)  # too short to reach 4 m/s
    assert segment_time(0, 4.0) == 0.0


def test_travel_time_sums_the_graph_segments(dames):
    """Timing a path cell by cell gives the same answer as adding up the exported segments."""
    from terminal_layout.timing import stopping_points, travel_time

    g = tool("export_graph").graph(dames)
    seg = {(tuple(s["cells"][0]), tuple(s["cells"][-1])): s for s in g["segments"]}
    stops = stopping_points(dames)
    path = dames.shortest_path((80, 0), dames.blocks["XT"].transfer_points[0])
    marks = [i for i, c in enumerate(path) if i == 0 or c in stops or i == len(path) - 1]
    by_graph = sum(seg[(path[i], path[j])]["time_empty_s"] for i, j in zip(marks, marks[1:]))
    assert travel_time(dames, path, stops=stops) == pytest.approx(by_graph, abs=0.01)
    assert travel_time(dames, path, loaded=True, stops=stops) > travel_time(dames, path, stops=stops)


def test_driving_through_junctions_is_faster_than_stopping_at_each(dames):
    from terminal_layout.timing import SPEED_LOADED_M_S, segment_time, stopping_points, travel_time

    path = dames.shortest_path(dames.quay_cranes["QC1"].transfer_points[1], dames.blocks["A2"].transfer_points[10])
    stops = stopping_points(dames)
    assert travel_time(dames, path, True, stops, junctions=False) < travel_time(dames, path, True, stops)
    # the optimistic bound is one run from rest to rest, even past other transfer points on the way
    one_run = segment_time((len(path) - 1) * dames.cell_size_m, SPEED_LOADED_M_S)
    assert travel_time(dames, path, True, stops, junctions=False) == pytest.approx(one_run)
    along_quay = dames.shortest_path((6, 34), (31, 34))          # past QC1's four transfer lanes
    assert travel_time(dames, along_quay, junctions=False) == pytest.approx(segment_time(25 * 15, 6.0))
