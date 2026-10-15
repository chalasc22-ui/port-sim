"""The Layout class: the read-only map the other modules call."""

from __future__ import annotations

import operator
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import export, render, validate
from .model import (
    CELL_TYPES,
    TRAVERSABLE_TYPES,
    Block,
    Cell,
    Core,
    LayoutError,
    Point,
    step,
    thaw,
)


def _as_int(value: Any) -> int:
    """An int from a Python or numpy integer. Rejects bools and floats."""
    if isinstance(value, bool):
        raise TypeError(f"coordinates must be integers, got {value!r}")
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(f"coordinates must be integers, got {value!r}") from None


def _as_point(value: Any) -> Point:
    try:
        x, y = value
    except (TypeError, ValueError):
        raise TypeError(f"cells must be (x, y) pairs, got {value!r}") from None
    return (_as_int(x), _as_int(y))


def _restore(source: Any, closures: tuple[Point, ...]) -> Layout:
    """Rebuild a Layout from its authored source. Used by pickle."""
    from .loader import layout_from_dict

    layout = layout_from_dict(source)
    return layout.with_closures(closures) if closures else layout


class Layout:
    """An immutable terminal map plus an optional set of closed cells.

    Closed cells are out of the graph: they are never returned by ``neighbors``
    and ``is_traversable`` is False for them. ``with_closures`` returns a new
    Layout and leaves this one unchanged.
    """

    __slots__ = ("_core", "_closures", "_warnings")

    def __init__(
        self,
        core: Core,
        closures: Iterable[Point] = frozenset(),
        warnings: Iterable[str] = (),
    ) -> None:
        object.__setattr__(self, "_core", core)
        object.__setattr__(self, "_closures", frozenset(closures))
        object.__setattr__(self, "_warnings", tuple(warnings))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Layout is immutable; use with_closures() for scenarios")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Layout is immutable")

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (_restore, (thaw(self._core.source), tuple(sorted(self._closures))))

    def __copy__(self) -> Layout:
        return self  # immutable, so sharing is safe

    def __deepcopy__(self, memo: dict[int, Any]) -> Layout:
        return self

    def __repr__(self) -> str:
        core = self._core
        return (
            f"Layout({core.name!r}, {core.width}x{core.height} cells, "
            f"{len(self._closures)} closed)"
        )

    @classmethod
    def _build(cls, core: Core, closures: frozenset[Point]) -> Layout:
        view = cls(core, closures)
        return cls(core, closures, validate.closure_warnings(view))

    # ----- dimensions and metadata -----

    @property
    def name(self) -> str:
        return self._core.name

    @property
    def width(self) -> int:
        return self._core.width

    @property
    def height(self) -> int:
        return self._core.height

    @property
    def cell_size_m(self) -> int | float:
        return self._core.cell_size_m

    @property
    def warnings(self) -> tuple[str, ...]:
        """Rule 12 warnings for the current closures. Empty when nothing is closed."""
        return self._warnings

    # ----- cells -----

    def cell(self, x: int, y: int) -> Cell:
        """The cell at (x, y). Raises IndexError outside the grid."""
        try:
            return self._core.cells[(x, y)]
        except KeyError:
            raise IndexError(
                f"cell ({x}, {y}) is outside the {self.width} x {self.height} grid"
            ) from None

    def cells(self, type: str | None = None) -> list[Cell]:
        """All cells, or all of one type, ordered by (x, y)."""
        if type is not None and type not in CELL_TYPES:
            raise ValueError(f"unknown cell type {type!r}; expected one of {CELL_TYPES}")
        return [c for c in self._core.ordered if type is None or c.type == type]

    def is_traversable(self, x: int, y: int) -> bool:
        """True if a vehicle may be on this cell: a road-type cell that is not closed.

        Cells outside the grid are not traversable.
        """
        cell = self._core.cells.get((x, y))
        return (
            cell is not None
            and cell.type in TRAVERSABLE_TYPES
            and (x, y) not in self._closures
        )

    def exits(self, x: int, y: int) -> list[str]:
        """The directions the map allows out of this cell, ordered N, E, S, W.

        This is the static map and ignores closures; use ``neighbors`` for movement.
        """
        return list(self.cell(x, y).exits)

    def neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        """Cells reachable in one move, ordered N, E, S, W.

        A closed cell has no neighbors and is never returned as one.
        """
        cell = self.cell(x, y)
        if not self.is_traversable(x, y):
            return []
        targets = (step(cell.pos, d) for d in cell.exits)
        return [t for t in targets if self.is_traversable(*t)]

    # ----- named places -----

    def _require_berth(self, berth_id: str) -> None:
        if berth_id not in self._core.berths:
            raise KeyError(f"unknown berth {berth_id!r}")

    def _require_block(self, block_id: str) -> Block:
        try:
            return self._core.blocks[block_id]
        except KeyError:
            raise KeyError(f"unknown block {block_id!r}") from None

    def quay_cells(self, berth_id: str | None = None) -> list[Cell]:
        """Quay strip cells (including the transfer points under cranes), by (x, y).

        Lists the static map: closed cells are still listed.
        """
        if berth_id is not None:
            self._require_berth(berth_id)
        found = []
        for cell in self._core.ordered:
            if cell.type == "quay":
                owner = cell.attrs["berth"]
            elif cell.type == "transfer_point" and "berth" in cell.attrs["serves"]:
                owner = cell.attrs["serves"]["berth"]
            else:
                continue
            if berth_id is None or owner == berth_id:
                found.append(cell)
        return found

    def gate_cells(self, flow: str | None = None) -> list[Cell]:
        """Gate cells, optionally only ``"in"`` or only ``"out"``, by (x, y).

        Lists the static map: closed cells are still listed.
        """
        if flow not in (None, "in", "out"):
            raise ValueError(f"flow must be 'in' or 'out', got {flow!r}")
        gates = self.cells("gate")
        return [g for g in gates if flow is None or g.attrs["flow"] == flow]

    def transfer_points(
        self, block_id: str | None = None, berth_id: str | None = None
    ) -> list[Cell]:
        """Transfer points, all or those serving one block or one berth, by (x, y).

        Lists the static map: closed cells are still listed.
        """
        if block_id is not None and berth_id is not None:
            raise ValueError("pass block_id or berth_id, not both")
        points = self.cells("transfer_point")
        if block_id is not None:
            self._require_block(block_id)
            return [c for c in points if c.attrs["serves"].get("block") == block_id]
        if berth_id is not None:
            self._require_berth(berth_id)
            return [c for c in points if c.attrs["serves"].get("berth") == berth_id]
        return points

    def transfer_point_for(self, slot_id: str) -> Cell:
        """The transfer point at the bay of this slot."""
        slot = self.slot(slot_id)
        for point in self.transfer_points(block_id=slot.attrs["block"]):
            if point.attrs["serves"]["bay"] == slot.attrs["bay"]:
                return point
        raise LookupError(f"slot {slot_id!r} has no transfer point")

    # ----- yard -----

    @property
    def blocks(self) -> dict[str, Block]:
        """Yard blocks by id, ordered by id."""
        return dict(self._core.blocks)

    @property
    def berths(self) -> dict[str, tuple[tuple[str, Point], ...]]:
        """For each berth id, its cranes as (crane id, transfer point (x, y))."""
        return dict(self._core.berths)

    def slot(self, slot_id: str) -> Cell:
        """The yard_slot cell with this ground slot id, e.g. ``"A-03-02"``."""
        try:
            return self.cell(*self._core.slot_index[slot_id])
        except KeyError:
            raise KeyError(
                f"unknown slot id {slot_id!r}; ids name a ground slot like 'A-03-02' "
                "(a tier is a position in the stack, not a cell)"
            ) from None

    def slots_in_block(self, block_id: str) -> list[str]:
        """Slot ids of a block, ordered by bay, then row."""
        return list(self._require_block(block_id).slots)

    # ----- closures -----

    @property
    def closures(self) -> frozenset[Point]:
        return self._closures

    def with_closures(self, cells: Iterable[Point]) -> Layout:
        """A new view in which exactly ``cells`` are closed. This Layout is unchanged.

        Raises LayoutError (rule 11) if a cell is outside the grid or not traversable.
        Pass an empty set to get the fully open map back. Rule 12 warnings for the
        new view are on its ``warnings``.
        """
        closed = frozenset(_as_point(c) for c in cells)
        problems = validate.closure_errors(self, closed)
        if problems:
            raise LayoutError(problems)
        return Layout._build(self._core, closed)

    # ----- convenience -----

    def shortest_path(self, a: Point, b: Point) -> list[tuple[int, int]] | None:
        """Fewest-move path from a to b, both included, or None.

        Breadth-first over ``neighbors`` (so closures apply). Ties break in N, E, S, W
        order. Returns None if either end is not traversable or b is unreachable.
        Raises TypeError if a or b is not an (int, int) pair.
        """
        a, b = _as_point(a), _as_point(b)
        if not (self.is_traversable(*a) and self.is_traversable(*b)):
            return None
        previous: dict[Point, Point | None] = {a: None}
        queue = deque([a])
        while queue:
            current = queue.popleft()
            if current == b:
                break
            for target in self.neighbors(*current):
                if target not in previous:
                    previous[target] = current
                    queue.append(target)
        if b not in previous:
            return None
        path = [b]
        while previous[path[-1]] is not None:
            path.append(previous[path[-1]])
        return path[::-1]

    def validate(self) -> list[str]:
        """Problems found in the map and its closures; empty when valid.

        Checks rules 4 to 7, 9 and 13 on the open map and rule 11 on the closures.
        """
        base = self if not self._closures else Layout(self._core)
        lines = validate.validate_layout(base)
        lines += validate.closure_errors(base, self._closures)
        return validate.sort_by_rule(lines)

    def to_json(self, path: str | Path | None = None) -> dict[str, Any]:
        """The compiled form for the other modules; also written to ``path`` if given."""
        data = export.compiled_dict(self)
        if path is not None:
            Path(path).write_text(export.format_compiled(data), encoding="utf-8", newline="\n")
        return data

    def render_2d(
        self, path: str | Path | None = "layout.png", show_ids: bool = False, ax: Any = None
    ) -> None:
        """Draw the map to a PNG, or onto ``ax`` (a matplotlib Axes) if one is given.

        When ``ax`` is given nothing is saved; the caller owns the figure and ``ax``
        gets this layout's title, limits and ticks. Raises ValueError if both ``path``
        and ``ax`` are None.
        """
        render.render_2d(self.to_json(), path=path, show_ids=show_ids, ax=ax)
