"""Plain data types and constants shared by the terminal_layout modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

Point = tuple[int, int]

# North is +y, east is +x, so a map drawn with y pointing up has north at the top.
DIRECTIONS: tuple[str, ...] = ("N", "E", "S", "W")
DELTAS: Mapping[str, Point] = MappingProxyType(
    {"N": (0, 1), "E": (1, 0), "S": (0, -1), "W": (-1, 0)}
)
CELL_TYPES: tuple[str, ...] = (
    "water",
    "berth",
    "quay",
    "transfer_point",
    "lane",
    "yard_slot",
    "gate",
    "charging_station",
    "blocked",
)
TRAVERSABLE_TYPES: frozenset[str] = frozenset(
    {"quay", "transfer_point", "lane", "gate", "charging_station"}
)


class LayoutError(ValueError):
    """A layout file or a closure set breaks one or more rules.

    ``errors`` holds one line per problem, each starting with ``rule N:``.
    """

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        count = len(self.errors)
        header = f"{count} problem{'s' if count != 1 else ''} found:"
        super().__init__("\n".join([header, *(f"  {line}" for line in self.errors)]))

    def __reduce__(self) -> tuple[Any, tuple[list[str]]]:
        return (LayoutError, (list(self.errors),))


def step(point: Point, direction: str) -> Point:
    """The cell one move from ``point`` in ``direction`` (may be off the grid)."""
    dx, dy = DELTAS[direction]
    return (point[0] + dx, point[1] + dy)


def freeze(value: Any) -> Any:
    """Deep-convert dicts to read-only mappings and lists to tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Inverse of :func:`freeze`: plain dicts and lists, ready for JSON."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def _no_attrs() -> Mapping[str, Any]:
    return MappingProxyType({})


def _restore_cell(x: int, y: int, type_: str, exits: tuple[str, ...], attrs: Any) -> Cell:
    return Cell(x, y, type_, exits, freeze(attrs))


@dataclass(frozen=True)
class Cell:
    """One grid cell. ``exits`` is ordered N, E, S, W."""

    x: int
    y: int
    type: str
    exits: tuple[str, ...] = ()
    attrs: Mapping[str, Any] = field(default_factory=_no_attrs, hash=False)

    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (_restore_cell, (self.x, self.y, self.type, self.exits, thaw(self.attrs)))


@dataclass(frozen=True)
class Block:
    """A yard block. Slots are ordered by bay, then row."""

    id: str
    bays: int
    rows: int
    max_tier: int
    cargo: str
    crane: str | None
    slots: tuple[str, ...]
    transfer_points: tuple[Point, ...]
    buffer_capacity: int = 0  # containers that may be set down at each of its transfer points
    container_rows: int = 1  # container rows each cell row stands for; capacity = slots x this x max_tier

    @property
    def capacity(self) -> int:
        """Containers the block holds when full: ground slots x container rows x tiers."""
        return len(self.slots) * self.container_rows * self.max_tier


@dataclass(frozen=True)
class QuayCrane:
    """A ship-to-shore crane on a berth and the transfer points under it.

    ``transfer_points`` are the quay cells, side by side along the quay and ordered by x,
    where a vehicle stands for the handover; the one at ``x`` is the crane's own column.
    ``buffer_capacity`` is the number of containers that may be set on the ground at each of
    them; 0 means the crane lifts straight between the ship and a waiting vehicle.
    """

    id: str
    berth: str
    x: int
    transfer_points: tuple[Point, ...]
    buffer_capacity: int = 0


@dataclass(frozen=True)
class YardCrane:
    """A yard crane (RTG) and the blocks it serves.

    ``reach`` is the smallest rectangle, as (low corner, high corner) with both corners
    inclusive, that holds every slot and transfer point of those blocks. It is the crane's
    envelope for drawing; where the crane stands at a given moment is not modelled.
    """

    id: str
    blocks: tuple[str, ...]
    reach: tuple[Point, Point]


@dataclass(frozen=True)
class Core:
    """The compiled, read-only map that every Layout view shares."""

    name: str
    schema_version: str
    cell_size_m: int | float
    width: int
    height: int
    cells: Mapping[Point, Cell]
    ordered: tuple[Cell, ...]
    blocks: Mapping[str, Block]
    berths: Mapping[str, tuple[tuple[str, Point], ...]]
    slot_index: Mapping[str, Point]
    source: Mapping[str, Any]  # the authored file as parsed, so a Layout can be rebuilt
    yard_cranes: Mapping[str, YardCrane] = field(default_factory=dict)
    quay_cranes: Mapping[str, QuayCrane] = field(default_factory=dict)
