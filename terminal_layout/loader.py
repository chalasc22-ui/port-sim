"""Load an authored layout file, check it, and compile it into a Layout.

Stages, each reporting every problem it finds before stopping:
  1. shape and enums (schema.py, rule 10)
  2. bounds, overlaps, ids (authored.py, rules 1 to 3)
  3. compile, then rules 4 to 9, 11, 13, 14 and 15 on the compiled map

A later stage only runs when the earlier ones found nothing, because the
compiled map is meaningless if the authored file is malformed. Nothing is
ever repaired silently.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .authored import (
    authored_errors,
    block_rect,
    crane_lane_cells,
    rect_cells,
    service_pos,
    slot_pos,
)
from .layout import Layout
from .model import (
    DIRECTIONS,
    Block,
    Cell,
    Core,
    LayoutError,
    Point,
    QuayCrane,
    YardCrane,
    freeze,
    step,
    thaw,
)
from .schema import BerthEntry, BlockEntry, ChargerEntry, LayoutFile, parse_authored
from .validate import sort_by_rule

_Cells = dict[Point, Cell]


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json.loads hook: a repeated key would silently drop the earlier value."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"key {key!r} appears twice in the same object")
        seen[key] = value
    return seen


def load_layout(path: str | os.PathLike[str]) -> Layout:
    """Read, check and compile a layout file. Raises LayoutError listing every problem."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise LayoutError([f"file {file}: not UTF-8 text, so not a layout file"]) from None
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise LayoutError(
            [f"file {file}: not valid JSON ({exc.msg}, line {exc.lineno}, column {exc.colno})"]
        ) from None
    except ValueError as exc:  # the duplicate-key hook
        raise LayoutError([f"file {file}: {exc}"]) from None
    except RecursionError:
        raise LayoutError([f"file {file}: JSON is nested too deeply"]) from None
    return layout_from_dict(data)


def layout_from_dict(data: Any) -> Layout:
    model = parse_authored(data)
    errors = authored_errors(model)
    if errors:
        raise LayoutError(sort_by_rule(errors))
    core, errors = compile_core(model)
    layout = Layout._build(core, frozenset(model.closures))
    errors = errors + layout.validate()
    if errors:
        raise LayoutError(sort_by_rule(errors))
    return layout


def _ordered(exits: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(d for d in DIRECTIONS if d in exits)


def _put(cells: _Cells, point: Point, type_: str, exits: Any = (), **attrs: Any) -> None:
    cells[point] = Cell(point[0], point[1], type_, _ordered(exits), freeze(attrs))


def _paint_simple(cells: _Cells, model: LayoutFile) -> None:
    for entry in model.water:
        for point in rect_cells(entry.rect):
            _put(cells, point, "water")
    for entry in model.blocked:
        for point in rect_cells(entry.rect):
            _put(cells, point, "blocked", reason=entry.reason)
    for berth in model.berths:
        for point in rect_cells(berth.rect):
            _put(cells, point, "berth", berth=berth.id)
    for gate in model.gates:
        attrs: dict[str, Any] = {"id": gate.id, "flow": gate.flow}
        if gate.lane_no is not None:
            attrs["lane_no"] = gate.lane_no
        _put(cells, gate.cell, "gate", gate.exits, **attrs)


def _paint_lanes(cells: _Cells, model: LayoutFile) -> None:
    for lane in model.lanes:
        attrs: dict[str, Any] = {}
        if lane.kind == "quay":
            attrs["berth"] = lane.berth
        if lane.speed_kph is not None:
            attrs["speed_kph"] = lane.speed_kph
        for point in rect_cells(lane.rect):
            _put(cells, point, lane.kind, lane.exits, **attrs)


def _paint_blocks(cells: _Cells, model: LayoutFile) -> dict[str, list[str]]:
    slots: dict[str, list[str]] = {}
    for block in model.blocks:
        slots[block.id] = []
        for bay in range(1, block.bays + 1):
            for row in range(1, block.rows + 1):
                slot = f"{block.id}-{bay:02d}-{row:02d}"
                slots[block.id].append(slot)
                _put(
                    cells,
                    slot_pos(block, bay, row),
                    "yard_slot",
                    slot=slot,
                    block=block.id,
                    bay=bay,
                    row=row,
                    max_tier=block.max_tier,
                    cargo=block.cargo,
                )
    return slots


def _serves_text(serves: Any) -> str:
    if "block" in serves:
        return f"block {serves['block']} bay {serves['bay']}"
    return f"berth {serves['berth']} crane {serves['crane']}"


def _found(cell: Cell | None) -> str:
    """What sits at a cell that was expected to be something else, for error messages."""
    if cell is None:
        return "which is outside the grid"
    return f"found a {cell.type.replace('_', ' ')} cell"


def _make_transfer_point(
    cells: _Cells, point: Point, serves: dict[str, Any], buffer_capacity: int
) -> None:
    old = cells[point]
    attrs = {**thaw(old.attrs), "serves": serves, "buffer_capacity": buffer_capacity}
    cells[point] = replace(old, type="transfer_point", attrs=freeze(attrs))


def _convert_block_transfers(
    cells: _Cells, blocks: list[BlockEntry], errors: list[str]
) -> dict[str, list[Point]]:
    found: dict[str, list[Point]] = {block.id: [] for block in blocks}
    for block in blocks:
        for bay in range(1, block.bays + 1):
            point = service_pos(block, bay)
            cell = cells.get(point)
            if cell is not None and cell.type == "transfer_point":
                errors.append(
                    f"rule 8: block {block.id} bay {bay}: service cell {point} is already "
                    f"the transfer point for {_serves_text(cell.attrs['serves'])}"
                )
            elif cell is None or cell.type != "lane":
                errors.append(
                    f"rule 8: block {block.id} bay {bay} has no lane cell on service side "
                    f"{block.service_side} (expected at {point}, {_found(cell)})"
                )
            else:
                _make_transfer_point(
                    cells, point, {"block": block.id, "bay": bay}, block.buffer_capacity
                )
                found[block.id].append(point)
    return found


def _convert_berth_transfers(
    cells: _Cells, berths: list[BerthEntry], errors: list[str]
) -> dict[str, QuayCrane]:
    """Turn the quay cells under each crane into its transfer points (one per lane)."""
    cranes: dict[str, QuayCrane] = {}
    for berth in berths:
        for crane in berth.cranes:
            lanes = crane_lane_cells(crane, berth.quay_y)
            made: list[Point] = []
            for number, point in enumerate(lanes, 1):
                which = f"lane {number} of {len(lanes)}" if len(lanes) > 1 else "it"
                cell = cells.get(point)
                if cell is not None and cell.type == "transfer_point":
                    errors.append(
                        f"rule 8: berth {berth.id} crane {crane.id} shares its quay cell {point} "
                        f"with {_serves_text(cell.attrs['serves'])}"
                        + (f" ({which})" if len(lanes) > 1 else "")
                    )
                elif cell is None or cell.type != "quay" or cell.attrs["berth"] != berth.id:
                    errors.append(
                        f"rule 8: berth {berth.id} crane {crane.id} has no quay cell of berth "
                        f"{berth.id} under {which} (expected at {point}, {_found(cell)})"
                    )
                else:
                    _make_transfer_point(
                        cells, point, {"berth": berth.id, "crane": crane.id}, crane.buffer_capacity
                    )
                    made.append(point)
            cranes[crane.id] = QuayCrane(
                id=crane.id,
                berth=berth.id,
                x=crane.x,
                transfer_points=tuple(made),
                buffer_capacity=crane.buffer_capacity,
            )
    return cranes


def _convert_chargers(cells: _Cells, chargers: list[ChargerEntry], errors: list[str]) -> None:
    """Rule 14: each charger sits on a plain lane cell; the cell becomes a charging station."""
    for charger in chargers:
        point = charger.cell
        cell = cells.get(point)
        if cell is not None and cell.type == "charging_station":
            errors.append(
                f"rule 14: chargers {cell.attrs['id']} and {charger.id} are both on cell {point}"
            )
        elif cell is None or cell.type != "lane":
            errors.append(
                f"rule 14: charger {charger.id} has no lane cell under it "
                f"(expected at {point}, {_found(cell)})"
            )
        else:
            attrs = {**thaw(cell.attrs), "id": charger.id}
            cells[point] = replace(cell, type="charging_station", attrs=freeze(attrs))


def _yard_cranes(
    blocks: list[BlockEntry], block_points: dict[str, list[Point]]
) -> dict[str, YardCrane]:
    """One entry per crane id named on a block: its blocks and the rectangle that holds them."""
    members: dict[str, list[BlockEntry]] = {}
    for block in blocks:
        if block.crane is not None:
            members.setdefault(block.crane, []).append(block)
    cranes: dict[str, YardCrane] = {}
    for crane_id, group in sorted(members.items()):
        xs: list[int] = []
        ys: list[int] = []
        for block in group:
            (x0, y0), (x1, y1) = block_rect(block)
            xs += [x0, x1]
            ys += [y0, y1]
            for px, py in block_points[block.id]:
                xs.append(px)
                ys.append(py)
        cranes[crane_id] = YardCrane(
            id=crane_id,
            blocks=tuple(sorted(b.id for b in group)),
            reach=((min(xs), min(ys)), (max(xs), max(ys))),
        )
    return cranes


def _mark_cranes(cells: _Cells, cranes: dict[str, QuayCrane], quay_ys: dict[str, int]) -> None:
    """Put the crane id on the berth cell directly behind each crane's own quay column."""
    for crane in cranes.values():
        point = (crane.x, quay_ys[crane.berth])
        for direction in ("N", "S"):
            target = step(point, direction)
            cell = cells.get(target)
            if cell is not None and cell.type == "berth" and cell.attrs["berth"] == crane.berth:
                attrs = {**thaw(cell.attrs), "crane": crane.id}
                cells[target] = replace(cell, attrs=freeze(attrs))
                break


def compile_core(model: LayoutFile) -> tuple[Core, list[str]]:
    """Turn the authored model into the read-only map. Returns it with any rule 8 errors."""
    cells: _Cells = {}
    for x in range(model.width):
        for y in range(model.height):
            _put(cells, (x, y), "blocked", reason="unused")
    _paint_simple(cells, model)
    _paint_lanes(cells, model)
    slots = _paint_blocks(cells, model)
    errors: list[str] = []
    block_points = _convert_block_transfers(cells, model.blocks, errors)
    quay_cranes = _convert_berth_transfers(cells, model.berths, errors)
    _convert_chargers(cells, model.chargers, errors)
    quay_ys = {b.id: b.quay_y for b in model.berths}
    _mark_cranes(cells, quay_cranes, quay_ys)
    blocks = {
        b.id: Block(
            id=b.id,
            bays=b.bays,
            rows=b.rows,
            max_tier=b.max_tier,
            cargo=b.cargo,
            crane=b.crane,
            slots=tuple(slots[b.id]),
            transfer_points=tuple(sorted(block_points[b.id])),
            buffer_capacity=b.buffer_capacity,
        )
        for b in sorted(model.blocks, key=lambda b: b.id)
    }
    berths = {
        b.id: tuple(
            sorted(
                ((c.id, (c.x, b.quay_y)) for c in quay_cranes.values() if c.berth == b.id),
                key=lambda item: item[1],
            )
        )
        for b in sorted(model.berths, key=lambda b: b.id)
    }
    ordered_cells = dict(sorted(cells.items()))
    slot_index = {
        cell.attrs["slot"]: cell.pos for cell in ordered_cells.values() if cell.type == "yard_slot"
    }
    size = model.cell_size_m
    core = Core(
        name=model.name,
        schema_version=model.schema_version,
        cell_size_m=int(size) if float(size).is_integer() else size,
        width=model.width,
        height=model.height,
        cells=MappingProxyType(ordered_cells),
        ordered=tuple(ordered_cells.values()),
        blocks=MappingProxyType(blocks),
        berths=MappingProxyType(berths),
        slot_index=MappingProxyType(slot_index),
        source=freeze(model.model_dump(mode="json")),
        yard_cranes=MappingProxyType(_yard_cranes(model.blocks, block_points)),
        quay_cranes=MappingProxyType(dict(sorted(quay_cranes.items()))),
    )
    return core, errors
