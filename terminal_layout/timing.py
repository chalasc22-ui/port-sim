"""Travel times on the map, with the same motion model as PortSim (the course's reference simulator).

A vehicle starts each road segment from rest, accelerates at ACCEL_M_S2 up to its speed limit,
cruises, and brakes to a stop at the far end. For a segment of length d (PortSim, section 3.2):

    T(d, v, a) = 2 * sqrt(d / a)      if d < v^2 / a   (too short to reach the limit)
                 d / v + v / a        otherwise

A segment is a stretch of road between two stopping points: a transfer point, a charging
station, a gate, or a junction (a cell with more or fewer than one way in or out), exactly as in
tools/export_graph.py. Empty and loaded vehicles have different limits.

The defaults are PortSim's (appendix C.1): 6 m/s empty, 4 m/s loaded, 1.2 m/s^2. They are a
shared starting point so every module computes the same times; the AGV owner can change them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPEED_EMPTY_M_S = 6.0
SPEED_LOADED_M_S = 4.0
ACCEL_M_S2 = 1.2
STATION_TYPES = frozenset({"transfer_point", "charging_station", "gate"})


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

    junctions=True (PortSim's model) stops at every stopping point, junctions included. On the
    quay and the bypass every cell is a junction (a lane change is allowed anywhere), so that is
    pessimistic there; junctions=False stops only at stations (transfer points, chargers, gates)
    and drives through junctions at speed. The truth for a real AGV lies between the two.
    Pass `stops` (from stopping_points) when timing many paths; it is the slow part.
    """
    if not path or len(path) < 2:
        return 0.0
    stops = stopping_points(layout) if stops is None else stops
    if not junctions:
        stops = frozenset(p for p in path if layout.cell(*p).type in STATION_TYPES)
    speed = v if v is not None else (SPEED_LOADED_M_S if loaded else SPEED_EMPTY_M_S)
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
