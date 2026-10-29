"""The compiled JSON form of a Layout, and its on-disk formatting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .model import Cell, thaw

if TYPE_CHECKING:
    from .layout import Layout

# Keys whose value is written one item per line, so diffs stay readable.
_LINE_PER_ITEM = ("grid", "cells", "blocks", "berths", "gates", "chargers", "yard_cranes", "closures")

# Written into every compiled file. 1.1 added "chargers", "yard_cranes", "buffer_capacity"
# and the "charging_station" cell type; 1.2 adds "x" and "transfer_points" (one per lane) to
# each quay crane, "container_rows" and "capacity" to each block, and lets "buffer_capacity" be 0. A reader that ignores unknown keys still
# works.
COMPILED_VERSION = "1.2"


def _is_plain(cell: Cell) -> bool:
    """Water and unused cells carry no attributes, so they are left out of ``cells``."""
    return cell.type == "water" or (
        cell.type == "blocked" and cell.attrs.get("reason") == "unused"
    )


def _cell_entry(cell: Cell) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": cell.type}
    if cell.exits:
        entry["exits"] = list(cell.exits)
    entry.update(thaw(cell.attrs))
    return entry


def compiled_dict(layout: Layout) -> dict[str, Any]:
    """Build the compiled form from the public Layout API only."""
    width, height = layout.width, layout.height
    cell_size = layout.cell_size_m
    data: dict[str, Any] = {
        "schema_version": COMPILED_VERSION,
        "name": layout.name,
        "cell_size_m": cell_size,
        "width": width,
        "height": height,
        "grid": [[layout.cell(x, y).type for x in range(width)] for y in range(height)],
        "cells": {
            f"{c.x},{c.y}": _cell_entry(c) for c in layout.cells() if not _is_plain(c)
        },
        "blocks": [
            {
                "id": block.id,
                "slots": list(block.slots),
                "transfer_points": [list(p) for p in block.transfer_points],
                "crane": block.crane,
                "buffer_capacity": block.buffer_capacity,
                "container_rows": block.container_rows,
                "capacity": block.capacity,
            }
            for block in layout.blocks.values()
        ],
        "berths": [
            {
                "id": berth_id,
                "cranes": [
                    {
                        "id": crane_id,
                        "x": layout.quay_cranes[crane_id].x,
                        "transfer_point": list(point),
                        "transfer_points": [
                            list(p) for p in layout.quay_cranes[crane_id].transfer_points
                        ],
                        "buffer_capacity": layout.quay_cranes[crane_id].buffer_capacity,
                    }
                    for crane_id, point in cranes
                ],
            }
            for berth_id, cranes in layout.berths.items()
        ],
        "gates": sorted(
            (
                {"id": g.attrs["id"], "cell": [g.x, g.y], "flow": g.attrs["flow"]}
                for g in layout.gate_cells()
            ),
            key=lambda gate: gate["id"],
        ),
        "chargers": sorted(
            ({"id": c.attrs["id"], "cell": [c.x, c.y]} for c in layout.charging_stations()),
            key=lambda charger: charger["id"],
        ),
        "yard_cranes": [
            {
                "id": crane.id,
                "blocks": list(crane.blocks),
                "reach": [list(crane.reach[0]), list(crane.reach[1])],
            }
            for crane in layout.yard_cranes.values()
        ],
    }
    if layout.closures:
        data["closures"] = [list(p) for p in sorted(layout.closures)]
    return data


def format_compiled(data: dict[str, Any]) -> str:
    """Stable, diff-friendly JSON text: grid rows, cells and list items on their own lines."""
    keys = list(data)
    lines = ["{"]
    for index, key in enumerate(keys):
        value = data[key]
        comma = "," if index < len(keys) - 1 else ""
        if key in _LINE_PER_ITEM and value:
            if isinstance(value, dict):
                items = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in value.items()]
            else:
                items = [json.dumps(item) for item in value]
            body = "[\n" if isinstance(value, list) else "{\n"
            body += ",\n".join(f"    {item}" for item in items)
            body += "\n  ]" if isinstance(value, list) else "\n  }"
        else:
            body = json.dumps(value)
        lines.append(f"  {json.dumps(key)}: {body}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"
