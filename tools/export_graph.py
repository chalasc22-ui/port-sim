"""Export a layout as a road graph: stopping points and directed segments with lengths in metres.

Run from the repo root:

    python tools/export_graph.py                                   # layouts/dames-point.graph.json
    python tools/export_graph.py layouts/demo-12x8.json out.json

PortSim, the course's reference simulator, describes a terminal as a directed metric road graph
G = (V, E): V are the positions where a vehicle can stop (junctions and equipment transfer points),
E the road segments between them, each with a length. This turns the cell grid into that form:

* a node is every cell where something happens: a transfer point, a charging station, a gate, or a
  junction (a cell with more or fewer than one way in or out);
* a segment follows the one-way road from a node until it reaches the next node; its length is the
  number of 15 m moves times the cell size, and `cells` lists the grid cells it passes through.

The format is our own until we have PortSim's JSON schema; the fields map one to one onto its
description (positions, directed segments, lengths, equipment at a position). Positions are cell
centres in metres from the south-west corner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATION_TYPES = {"transfer_point", "charging_station", "gate"}


def graph(layout) -> dict:
    size = layout.cell_size_m
    roads = [c.pos for c in layout.cells() if layout.is_traversable(*c.pos)]
    out = {p: layout.neighbors(*p) for p in roads}
    indeg = {p: 0 for p in roads}
    for p in roads:
        for q in out[p]:
            indeg[q] += 1
    is_node = {p: layout.cell(*p).type in STATION_TYPES or len(out[p]) != 1 or indeg[p] != 1 for p in roads}
    nodes = sorted(p for p in roads if is_node[p])
    ids = {p: f"n{i}" for i, p in enumerate(nodes)}
    node_list = []
    for p in nodes:
        cell = layout.cell(*p)
        entry = {"id": ids[p], "cell": list(p), "x_m": (p[0] + 0.5) * size, "y_m": (p[1] + 0.5) * size, "type": cell.type}
        if cell.type == "transfer_point":
            entry["serves"] = dict(cell.attrs["serves"])
            entry["buffer_capacity"] = cell.attrs["buffer_capacity"]
        elif cell.type in ("charging_station", "gate"):
            entry["equipment"] = cell.attrs["id"]
            if cell.type == "gate":
                entry["flow"] = cell.attrs["flow"]
        node_list.append(entry)
    segments = []
    for start in nodes:
        for first in out[start]:
            path = [start, first]
            while not is_node[path[-1]]:
                path.append(out[path[-1]][0])
            segments.append({"from": ids[start], "to": ids[path[-1]], "length_m": (len(path) - 1) * size,
                             "cells": [list(c) for c in path]})
    return {"name": layout.name, "cell_size_m": size, "nodes": node_list, "segments": segments}


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    source = Path(argv[1]) if len(argv) > 1 else ROOT / "layouts" / "dames-point.json"
    out = Path(argv[2]) if len(argv) > 2 else source.with_name(source.stem + ".graph.json")
    g = graph(load_layout(source))
    lines = ["{", f' "name": {json.dumps(g["name"])},', f' "cell_size_m": {g["cell_size_m"]},', ' "nodes": [']
    lines += [f"  {json.dumps(n)}{',' if i < len(g['nodes']) - 1 else ''}" for i, n in enumerate(g["nodes"])]
    lines += [" ],", ' "segments": [']
    lines += [f"  {json.dumps(s)}{',' if i < len(g['segments']) - 1 else ''}" for i, s in enumerate(g["segments"])]
    lines += [" ]", "}"]
    out.write_text("\n".join(lines) + "\n")
    km = sum(s["length_m"] for s in g["segments"]) / 1000
    print(f"wrote {out.name}: {len(g['nodes'])} stopping points, {len(g['segments'])} directed segments, {km:.1f} km of road")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
