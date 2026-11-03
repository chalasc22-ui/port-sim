"""networkx view of a Layout, used for the connectivity rules (6, 7 and 12).

The graph is built from ``Layout.neighbors()``, so it always agrees with the
movement rule the traffic module sees, closures included.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from .model import Point

if TYPE_CHECKING:
    from .layout import Layout


def build_graph(layout: Layout, exclude: frozenset[Point] = frozenset()) -> nx.DiGraph:
    """Directed graph: one node per open traversable cell, one edge per legal move.

    ``exclude`` leaves cells out as if a vehicle were standing in them (rule 15).
    """
    graph: nx.DiGraph = nx.DiGraph()
    nodes = [
        cell.pos
        for cell in layout.cells()
        if layout.is_traversable(cell.x, cell.y) and cell.pos not in exclude
    ]
    graph.add_nodes_from(nodes)
    for point in nodes:
        for target in layout.neighbors(*point):
            if target not in exclude:
                graph.add_edge(point, target)
    return graph


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _gate_points(graph: nx.DiGraph, layout: Layout, flow: str) -> list[Point]:
    return sorted(
        p
        for p in graph
        if layout.cell(*p).type == "gate" and layout.cell(*p).attrs["flow"] == flow
    )


def _internal_problems(graph: nx.DiGraph, internal: list[Point]) -> list[tuple[int, str]]:
    if not internal:
        return [(6, "the internal network has no open lane cells")]
    sub = graph.subgraph(internal)
    if nx.is_strongly_connected(sub):
        return []
    first = internal[0]
    forward = nx.descendants(sub, first) | {first}
    unreached = [p for p in internal if p not in forward]
    if unreached:
        example = f"no path from {first} to {unreached[0]}"
    else:
        backward = nx.ancestors(sub, first) | {first}
        example = f"no path from {[p for p in internal if p not in backward][0]} to {first}"
    return [(6, f"the internal network is not strongly connected ({example})")]


def _gate_problems(
    graph: nx.DiGraph, layout: Layout, internal: list[Point]
) -> list[tuple[int, str]]:
    problems: list[tuple[int, str]] = []
    inside = set(internal)
    gates_in = _gate_points(graph, layout, "in")
    gates_out = _gate_points(graph, layout, "out")
    if layout.gate_cells("in") and not gates_in:
        problems.append((7, "no gate-in is open"))
    for point in gates_in:
        if not inside & nx.descendants(graph, point):
            gate_id = layout.cell(*point).attrs["id"]
            problems.append((7, f"gate {gate_id} at {point} cannot reach the internal network"))
    if not gates_out:
        problems.append((7, "there is no open gate-out, so no internal cell can reach one"))
        return problems
    for point in gates_out:
        if not inside & nx.ancestors(graph, point):
            gate_id = layout.cell(*point).attrs["id"]
            problems.append((7, f"gate {gate_id} at {point} cannot be reached from the internal network"))
    reach: set[Point] = set(gates_out)
    for point in gates_out:
        reach |= nx.ancestors(graph, point)
    stuck = [p for p in internal if p not in reach]
    if stuck:
        problems.append(
            (7, f"{_plural(len(stuck), 'internal cell')} cannot reach any gate-out (first: {stuck[0]})")
        )
    return problems


def connectivity_problems(
    layout: Layout, exclude: frozenset[Point] = frozenset()
) -> list[tuple[int, str]]:
    """(rule number, text) pairs for rule 6 and rule 7 problems in this view.

    With ``exclude`` the named cells are treated as blocked by a standing vehicle.
    """
    graph = build_graph(layout, exclude)
    internal = sorted(p for p in graph if layout.cell(*p).type != "gate")
    return _internal_problems(graph, internal) + _gate_problems(graph, layout, internal)
