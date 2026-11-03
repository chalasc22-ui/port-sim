"""Integration smoke test: one vehicle drives a full job on Dames Point using only the Layout API.

Run from the repo root:

    python examples/agv_stub.py           # summary of both jobs
    python examples/agv_stub.py --trace   # every move with its clock time

This is a stand-in for the AGV module, written to show the contract, not to replace it. It finds its
own routes by breadth-first search over `layout.neighbors()` (the one movement primitive), moves one
cell per step, waits at transfer points for the crane, and checks itself against
`layout.shortest_path()`. Everything a real AGV module decides is a named constant or a plain
function below, so the AGV owner can see exactly where their own code plugs in:

* The handover times: placeholders until the AGV and crane owners set them. Driving times use
  terminal_layout.timing, PortSim's model, so every module gets the same numbers.
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
from terminal_layout.timing import SPEED_EMPTY_M_S, SPEED_LOADED_M_S, stopping_points, travel_time  # noqa: E402

# Driving times come from terminal_layout.timing (PortSim's model: 6 m/s empty, 4 m/s loaded).
QUAY_HANDOVER_S = 90     # placeholder: one quay crane move, vehicle waiting under the crane
YARD_HANDOVER_S = 120    # placeholder: one RTG move, vehicle waiting in the truck lane
GATE_S = 60              # placeholder: a truck's check-in or check-out at a gate lane


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
    """legs = [(place name, cell, seconds spent there)]. Returns (moves, seconds, log lines)."""
    stops = stopping_points(layout)
    clock, moves, log = 0.0, 0, []
    here = legs[0][1]
    loaded = False
    for name, cell, dwell in legs:
        path = route(layout, here, cell)
        leg_s = travel_time(layout, path, loaded=loaded, stops=stops)
        assert path == layout.shortest_path(here, cell), "router disagrees with shortest_path"
        for a, b in zip(path, path[1:]):
            assert b in layout.neighbors(*a), f"illegal move {a} -> {b}"
            moves += 1
            if trace:
                log.append(f"  {a} -> {b}  {layout.cell(*b).type}")
        clock += leg_s
        log.append(f"{clock:7.1f} s  at {name} {cell} after {len(path) - 1} moves, waits {dwell} s")
        clock += dwell
        here = cell
        loaded = not loaded                      # each stop is a pickup or a drop-off, in turn
    return moves, clock, log


def jobs(layout: Layout):
    qc1 = layout.quay_cranes["QC1"].transfer_points[1]
    bay = layout.transfer_point_for(layout.slots_in_block("A2")[10])   # a slot in A2: its bay's transfer point
    g_in = layout.gate_cells("in")[0].pos
    g_out = layout.gate_cells("out")[0].pos
    truck_spot = layout.blocks["XT"].transfer_points[1]                # a truck spot at the exchange zone
    return {
        "AGV: discharge QC1 -> block A2 -> back to QC1": [
            ("QC1", qc1, QUAY_HANDOVER_S), ("block A2", bay.pos, YARD_HANDOVER_S), ("QC1", qc1, 0)],
        "Truck: gate in -> pick up at the exchange zone -> gate out": [
            ("gate in", g_in, GATE_S), ("exchange zone", truck_spot, YARD_HANDOVER_S), ("gate out", g_out, GATE_S)],
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
