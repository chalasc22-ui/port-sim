"""Integration smoke test: one vehicle drives a full job on Dames Point using only the Layout API.

Run from the repo root:

    python examples/agv_stub.py           # summary of both jobs
    python examples/agv_stub.py --trace   # also every move of each leg, cell by cell, before its stop

This is a stand-in for the AGV module, written to show the contract, not to replace it. It finds its
own routes by breadth-first search over `layout.neighbors()` (the one movement primitive), moves one
cell per step, waits at transfer points for the crane, and checks itself against
`layout.shortest_path()`. Everything a real AGV module decides is a named constant or a plain
function below, so the AGV owner can see exactly where their own code plugs in:

* Driving and handover times come from terminal_layout.timing: PortSim's motion model and its
  Jacksonville service times, so every module gets the same numbers until the AGV, crane and
  gate owners set their own.
* route(): replace with your router (A*, time windows, reservations).
* No other vehicles, no batteries, no breakdowns: those are yours.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import Layout, load_layout  # noqa: E402
from terminal_layout.timing import (  # noqa: E402
    GATE_SERVICE_S, QUAY_SERVICE_S, SPEED_EMPTY_M_S, SPEED_LOADED_M_S, YARD_SERVICE_S, stopping_points, travel_time)


def route(layout: Layout, start, goal) -> list[tuple[int, int]]:
    """Fewest-move route using only neighbors(). Ties break the same way as shortest_path."""
    previous = {start: None}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        if here == goal:
            break
        for nxt in layout.neighbors(*here):
            if nxt not in previous:
                previous[nxt] = here
                queue.append(nxt)
    if goal not in previous:
        raise RuntimeError(f"no route from {start} to {goal}")
    path = [goal]
    while previous[path[-1]] is not None:
        path.append(previous[path[-1]])
    return path[::-1]


def drive(layout: Layout, legs, trace=False):
    """legs = [(place name, cell, seconds spent there, loaded on the way there)], starting where the
    first leg is. Returns (moves, seconds, log lines)."""
    stops = stopping_points(layout)
    clock, moves, log = 0.0, 0, []
    here = legs[0][1]
    for name, cell, dwell, loaded in legs:
        path = route(layout, here, cell)
        leg_s = travel_time(layout, path, loaded=loaded, stops=stops)
        assert path == layout.shortest_path(here, cell), "router disagrees with shortest_path"
        for a, b in zip(path, path[1:]):
            assert b in layout.neighbors(*a), f"illegal move {a} -> {b}"
            moves += 1
            if trace:
                log.append(f"  {a} -> {b}  {layout.cell(*b).type}")
        clock += leg_s
        how = "loaded" if loaded else "empty"
        log.append(f"{clock:7.1f} s  at {name} {cell} after {len(path) - 1} moves ({how}), waits {dwell:g} s")
        clock += dwell
        here = cell
    return moves, clock, log


def jobs(layout: Layout):
    qc1 = layout.quay_cranes["QC1"].transfer_points[1]
    bay = layout.transfer_point_for(layout.slots_in_block("A2")[10])   # a slot in A2: its bay's transfer point
    g_in = layout.gate_cells("in")[0].pos
    g_out = layout.gate_cells("out")[0].pos
    truck_spot = layout.blocks["XT"].transfer_points[1]                # a truck spot at the exchange zone
    return {   # (place, cell, seconds there, loaded on the way there)
        "AGV: discharge QC1 -> block A2 -> back to QC1": [
            ("QC1", qc1, QUAY_SERVICE_S, False),          # the quay crane sets a container on the AGV
            ("block A2", bay.pos, YARD_SERVICE_S, True),  # the RTG lifts it off
            ("QC1", qc1, 0, False)],
        "Truck: gate in -> pick up at the exchange zone -> gate out": [
            ("gate in", g_in, GATE_SERVICE_S, False),
            ("exchange zone", truck_spot, YARD_SERVICE_S, False),   # arrives empty; XC1 loads it
            ("gate out", g_out, GATE_SERVICE_S, True)],
    }


def main(argv: list[str]) -> int:
    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    for title, legs in jobs(layout).items():
        moves, seconds, log = drive(layout, legs, trace="--trace" in argv)
        print(title)
        print("\n".join(log))
        km = moves * layout.cell_size_m / 1000
        print(f"  done: {moves} moves ({km:.2f} km), {seconds / 60:.1f} min ({SPEED_EMPTY_M_S:g} m/s empty, {SPEED_LOADED_M_S:g} m/s loaded); "
              "every move legal and every leg equal to shortest_path\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
