"""The compiled JSON form of a Layout, and its on-disk formatting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .model import Cell, thaw

if TYPE_CHECKING:
    from .layout import Layout

# Keys whose value is written one item per line, so diffs stay readable.
_LINE_PER_ITEM = ("grid", "cells", "blocks", "berths", "gates", "closures")


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
        "schema_version": "1.0",
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
            }
            for block in layout.blocks.values()
        ],
        "berths": [
            {
                "id": berth_id,
                "cranes": [
                    {"id": crane_id, "transfer_point": list(point)}
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
