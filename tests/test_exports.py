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
