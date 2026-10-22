"""Checks on the authored file itself: rules 1 (bounds), 2 (overlaps) and 3 (ids).

Also holds the block geometry helpers that the loader shares with these checks.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import NamedTuple

from .model import Point
from .schema import BlockEntry, LayoutFile

Rect = tuple[Point, Point]


class Footprint(NamedTuple):
    label: str  # how messages name the entry, e.g. "lanes[2]" or "block A"
    desc: str  # "rect", "cell" or "footprint"
    rect: Rect
    claims: bool  # True if the cells it covers may not be shared (rule 2)


def rect_cells(rect: Rect) -> Iterator[Point]:
    (x0, y0), (x1, y1) = rect
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield (x, y)


def block_rect(block: BlockEntry) -> Rect:
    ox, oy = block.origin
    if block.orientation == "EW":
        return ((ox, oy), (ox + block.bays - 1, oy + block.rows - 1))
    return ((ox, oy), (ox + block.rows - 1, oy + block.bays - 1))


def slot_pos(block: BlockEntry, bay: int, row: int) -> Point:
    """Cell of a ground slot; bay and row count from 1 at the origin corner."""
    ox, oy = block.origin
    if block.orientation == "EW":
        return (ox + bay - 1, oy + row - 1)
    return (ox + row - 1, oy + bay - 1)


def service_pos(block: BlockEntry, bay: int) -> Point:
    """Cell on the service side that is beside this bay."""
    ox, oy = block.origin
    if block.orientation == "EW":
        y = oy - 1 if block.service_side == "S" else oy + block.rows
        return (ox + bay - 1, y)
    x = ox - 1 if block.service_side == "W" else ox + block.rows
    return (x, oy + bay - 1)


def footprints(model: LayoutFile) -> list[Footprint]:
    prints = []
    prints += [Footprint(f"water[{i}]", "rect", e.rect, True) for i, e in enumerate(model.water)]
    prints += [
        Footprint(f"blocked[{i}]", "rect", e.rect, True) for i, e in enumerate(model.blocked)
    ]
    prints += [Footprint(f"berth {e.id}", "rect", e.rect, True) for e in model.berths]
    prints += [Footprint(f"lanes[{i}]", "rect", e.rect, True) for i, e in enumerate(model.lanes)]
    prints += [Footprint(f"block {e.id}", "footprint", block_rect(e), True) for e in model.blocks]
    prints += [Footprint(f"gate {e.id}", "cell", (e.cell, e.cell), True) for e in model.gates]
    # A charger turns an existing lane cell into a station, like a transfer point, so it
    # shares the cell with a lane entry and does not claim it.
    prints += [
        Footprint(f"charger {e.id}", "cell", (e.cell, e.cell), False) for e in model.chargers
    ]
    # Closures may sit on any road cell, so they never count as claiming a cell.
    prints += [
        Footprint(f"closures[{i}]", "cell", (c, c), False) for i, c in enumerate(model.closures)
    ]
    return prints


def _text(fp: Footprint) -> str:
    (x0, y0), (x1, y1) = fp.rect
    if fp.desc == "cell":
        return f"({x0}, {y0})"
    return f"[[{x0}, {y0}], [{x1}, {y1}]]"


def _problem(fp: Footprint, width: int, height: int) -> str | None:
    (x0, y0), (x1, y1) = fp.rect
    if x0 > x1 or y0 > y1:
        return "must list the low corner first"
    if x0 < 0 or y0 < 0 or x1 >= width or y1 >= height:
        return f"lies outside the {width} x {height} grid"
    return None


def rule_1(model: LayoutFile, prints: list[Footprint]) -> list[str]:
    lines = []
    for fp in prints:
        problem = _problem(fp, model.width, model.height)
        if problem:
            lines.append(f"rule 1: {fp.label} {fp.desc} {_text(fp)} {problem}")
    for berth in model.berths:
        if not 0 <= berth.quay_y < model.height:
            lines.append(
                f"rule 1: berth {berth.id} quay_y {berth.quay_y} lies outside "
                f"the {model.width} x {model.height} grid"
            )
        for crane in berth.cranes:
            if not 0 <= crane.x < model.width:
                lines.append(
                    f"rule 1: berth {berth.id} crane {crane.id} x {crane.x} lies outside "
                    f"the {model.width} x {model.height} grid"
                )
    return lines


def rule_2(model: LayoutFile, prints: list[Footprint]) -> list[str]:
    owner: dict[Point, str] = {}
    clashes: dict[tuple[str, str], list[Point]] = defaultdict(list)
    for fp in prints:
        if not fp.claims or _problem(fp, model.width, model.height):
            continue
        for cell in rect_cells(fp.rect):
            first = owner.setdefault(cell, fp.label)
            if first != fp.label:
                clashes[(first, fp.label)].append(cell)
    lines = []
    for (first, second), cells in sorted(clashes.items(), key=lambda item: min(item[1])):
        more = f" (and {len(cells) - 1} more)" if len(cells) > 1 else ""
        lines.append(f"rule 2: {first} and {second} both cover cell {min(cells)}{more}")
    return lines


def _duplicates(kind: str, entries: list[tuple[str, str]]) -> list[str]:
    where: dict[str, list[str]] = defaultdict(list)
    for ident, place in entries:
        where[ident].append(place)
    return [
        f"rule 3: {kind} id {ident!r} is used {len(places)} times ({', '.join(places)})"
        for ident, places in where.items()
        if len(places) > 1
    ]


def rule_3(model: LayoutFile) -> list[str]:
    lines = _duplicates("berth", [(b.id, f"berths[{i}]") for i, b in enumerate(model.berths)])
    lines += _duplicates("block", [(b.id, f"blocks[{i}]") for i, b in enumerate(model.blocks)])
    lines += _duplicates("gate", [(g.id, f"gates[{i}]") for i, g in enumerate(model.gates)])
    lines += _duplicates(
        "charger", [(c.id, f"chargers[{i}]") for i, c in enumerate(model.chargers)]
    )
    cranes = [
        (c.id, f"berths[{i}].cranes[{j}]")
        for i, b in enumerate(model.berths)
        for j, c in enumerate(b.cranes)
    ]
    lines += _duplicates("crane", cranes)
    crane_ids = {c.id for b in model.berths for c in b.cranes}
    for index, block in enumerate(model.blocks):
        if block.crane in crane_ids:
            lines.append(
                f"rule 3: blocks[{index}] crane {block.crane!r} is also a quay crane id"
            )
    berth_ids = {b.id for b in model.berths}
    for index, lane in enumerate(model.lanes):
        if lane.berth is not None and lane.berth not in berth_ids:
            lines.append(f"rule 3: lanes[{index}] refers to unknown berth {lane.berth!r}")
    return lines


def authored_errors(model: LayoutFile) -> list[str]:
    prints = footprints(model)
    return rule_1(model, prints) + rule_2(model, prints) + rule_3(model)
