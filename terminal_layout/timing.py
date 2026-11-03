"""Travel times on the map, with the same motion model as PortSim (the course's reference simulator).

A vehicle starts each road segment from rest, accelerates at ACCEL_M_S2 up to its speed limit,
cruises, and brakes to a stop at the far end. For a segment of length d (PortSim, section 3.2):

    T(d, v, a) = 2 * sqrt(d / a)      if d < v^2 / a   (too short to reach the limit)
                 d / v + v / a        otherwise

A segment is a stretch of road between two stopping points: a transfer point, a charging
station, a gate, or a junction (a cell with more or fewer than one way in or out), exactly as in
tools/export_graph.py. Empty and loaded vehicles have different limits.

The defaults are PortSim's (appendix C.1): 6 m/s empty, 4 m/s loaded, 1.2 m/s^2, and the service
times of its Jacksonville layout. They are a shared starting point so every module computes the same
times; the AGV, crane and gate owners can change them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPEED_EMPTY_M_S = 6.0
SPEED_LOADED_M_S = 4.0
ACCEL_M_S2 = 1.2
STATION_TYPES = frozenset({"transfer_point", "charging_station", "gate"})

# PortSim's Jacksonville service times (appendix C.1), seconds: one quay crane move, one yard crane
# move, one gate check, and one container onto or off a vehicle.
QUAY_SERVICE_S = 60.0
YARD_SERVICE_S = 75.0
GATE_SERVICE_S = 30.0
HANDOFF_S = 15.0


def segment_time(d: float, v: float, a: float = ACCEL_M_S2) -> float:
    """Seconds to drive d metres from rest to rest with speed limit v and acceleration a."""
    if d <= 0:
        return 0.0
    if d < v * v / a:
        return 2 * math.sqrt(d / a)
    return d / v + v / a


def stopping_points(layout) -> frozenset:
    """Cells where a segment starts or ends: stations, and cells with other than one way in or out."""
    roads = [c.pos for c in layout.cells() if layout.is_traversable(*c.pos)]
    indeg = dict.fromkeys(roads, 0)
    for p in roads:
        for q in layout.neighbors(*p):
            indeg[q] += 1
    return frozenset(
        p for p in roads
        if layout.cell(*p).type in STATION_TYPES or len(layout.neighbors(*p)) != 1 or indeg[p] != 1
    )


def travel_time(layout, path: Sequence, loaded: bool = False, stops: frozenset | None = None,
                v: float | None = None, a: float = ACCEL_M_S2, junctions: bool = True) -> float:
    """Seconds to drive a cell path (as from Layout.shortest_path).

    junctions=True (PortSim's model) stops at every stopping point on the way, the way PortSim
    drives each segment of its road graph from rest to rest. On the quay and the bypass every cell
    is a junction (a lane change is allowed anywhere), so this is pessimistic there.
    junctions=False is the optimistic bound: no stop between the two ends, so the whole path is one
    run from rest to rest, through every junction and past every station it does not use. The
    truth for a real AGV lies between the two.
    Pass `stops` (from stopping_points) when timing many paths; it is the slow part.
    """
    if not path or len(path) < 2:
        return 0.0
    speed = v if v is not None else (SPEED_LOADED_M_S if loaded else SPEED_EMPTY_M_S)
    if not junctions:
        return segment_time((len(path) - 1) * layout.cell_size_m, speed, a)
    stops = stopping_points(layout) if stops is None else stops
    size = layout.cell_size_m
    total, run = 0.0, 0
    for cell in path[1:]:
        run += 1
        if cell in stops:
            total += segment_time(run * size, speed, a)
            run = 0
    if run:
        total += segment_time(run * size, speed, a)
    return total
