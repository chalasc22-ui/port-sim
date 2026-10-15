"""Validation rules that run on a compiled Layout (rules 4 to 7, 9, 11, 12, 13).

Rules 1 to 3 and 10 are about the authored file and live in loader.py and
schema.py; rule 8 needs the authored block geometry and lives in loader.py.
Every function returns a list of lines in the form ``rule N: ...``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from .graph import connectivity_problems
from .model import DIRECTIONS, TRAVERSABLE_TYPES, Cell, Point, step

if TYPE_CHECKING:
    from .layout import Layout


def describe(cell: Cell) -> str:
    return f"{cell.type.replace('_', ' ')} cell {cell.pos}"


def _inside(layout: Layout, point: Point) -> bool:
    return 0 <= point[0] < layout.width and 0 <= point[1] < layout.height


def rule_4(layout: Layout) -> list[str]:
    """Every exit of a traversable cell leads to a traversable cell."""
    lines = []
    for cell in layout.cells():
        if cell.type not in TRAVERSABLE_TYPES:
            continue
        for direction in cell.exits:
            target = step(cell.pos, direction)
            if not _inside(layout, target):
                lines.append(
                    f"rule 4: {describe(cell)} exit {direction} leads outside the grid"
                )
            elif not layout.is_traversable(*target):
                kind = layout.cell(*target).type.replace("_", " ")
                lines.append(
                    f"rule 4: {describe(cell)} exit {direction} leads to {target}, "
                    f"which is not traversable (it is a {kind} cell)"
                )
    return lines


def rule_5(layout: Layout) -> list[str]:
    """No dead ends: every traversable cell except a gate-out has a usable exit."""
    lines = []
    for cell in layout.cells():
        if cell.type not in TRAVERSABLE_TYPES:
            continue
        if cell.type == "gate" and cell.attrs["flow"] == "out":
            continue
        if not layout.neighbors(*cell.pos):
            lines.append(f"rule 5: {describe(cell)} has no usable exit (dead end)")
    return lines


def rules_6_and_7(layout: Layout) -> list[str]:
    """Strong connectivity of the internal network, and gate reachability."""
    return [f"rule {number}: {text}" for number, text in connectivity_problems(layout)]


def _adjacent(layout: Layout, cell: Cell, wanted: Callable[[Cell], bool]) -> bool:
    for direction in DIRECTIONS:
        point = step(cell.pos, direction)
        if _inside(layout, point) and wanted(layout.cell(*point)):
            return True
    return False


def rule_9(layout: Layout) -> list[str]:
    """Every transfer point is adjacent to the block bay or berth it serves."""
    lines = []
    for cell in layout.cells("transfer_point"):
        serves = cell.attrs["serves"]
        if "block" in serves:
            block, bay = serves["block"], serves["bay"]
            if not _adjacent(
                layout,
                cell,
                lambda c, b=block, n=bay: c.type == "yard_slot"
                and c.attrs["block"] == b
                and c.attrs["bay"] == n,
            ):
                lines.append(
                    f"rule 9: {describe(cell)} serves block {block} bay {bay} "
                    "but is not adjacent to a slot in that bay"
                )
        else:
            berth, crane = serves["berth"], serves["crane"]
            if not _adjacent(
                layout,
                cell,
                lambda c, b=berth: c.type == "berth" and c.attrs["berth"] == b,
            ):
                lines.append(
                    f"rule 9: {describe(cell)} serves berth {berth} crane {crane} "
                    f"but is not adjacent to a cell of berth {berth}"
                )
    return lines


def rule_13(layout: Layout) -> list[str]:
    """Every block and every berth has at least one transfer point."""
    lines = []
    for block_id, block in layout.blocks.items():
        if not block.transfer_points:
            lines.append(f"rule 13: block {block_id} has no transfer point")
    for berth_id, cranes in layout.berths.items():
        if not cranes:
            lines.append(f"rule 13: berth {berth_id} has no transfer point (no crane is placed on it)")
    return lines


def closure_errors(layout: Layout, closures: Iterable[Point]) -> list[str]:
    """Rule 11: closures may name traversable cells only."""
    lines = []
    for point in sorted(closures):
        if not _inside(layout, point):
            lines.append(
                f"rule 11: closure cell {point} is outside the "
                f"{layout.width} x {layout.height} grid"
            )
            continue
        cell = layout.cell(*point)
        if cell.type not in TRAVERSABLE_TYPES:
            lines.append(
                f"rule 11: closure cell {point} is not traversable "
                f"(it is a {cell.type.replace('_', ' ')} cell)"
            )
    return lines


def closure_warnings(view: Layout) -> list[str]:
    """Rule 12: re-run rules 6 and 7 with the view's closures applied."""
    count = len(view.closures)
    if not count:
        return []
    noun = "closed cell" if count == 1 else "closed cells"
    return [
        f"rule 12: with {count} {noun}, {text}" for _, text in connectivity_problems(view)
    ]


def validate_layout(base: Layout) -> list[str]:
    """Rules 4 to 7, 9 and 13 on a view that has no closures."""
    return rule_4(base) + rule_5(base) + rules_6_and_7(base) + rule_9(base) + rule_13(base)


def sort_by_rule(lines: Iterable[str]) -> list[str]:
    """Order lines by rule number, keeping the original order within a rule."""
    return sorted(lines, key=lambda line: int(line.split(":", 1)[0].split()[1]))
