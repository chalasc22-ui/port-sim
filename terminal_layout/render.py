"""2D drawing of a compiled layout.

Reads the compiled dict (``Layout.to_json()``) and nothing else, and holds no
terminal rules: it colors the grid, draws an arrow per exit, and labels things.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Any

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import patheffects
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.collections import PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon, Rectangle

from .model import DELTAS

COLORS = MappingProxyType(
    {
        "water": "#9ecae1",
        "berth": "#6c7a89",
        "quay": "#e3dcc2",
        "lane": "#e9e9e9",
        "transfer_point": "#f2a541",
        "yard_slot": "#9ccc9c",
        "gate_in": "#4fae7b",
        "gate_out": "#d9645a",
        "charging_station": "#8e6fd1",
        "blocked_building": "#555555",
        "blocked_exclusion": "#a89f6e",
        "blocked_unused": "#fafafa",
    }
)
BUFFER_EDGE = "#8a4b00"  # hatch on transfer points that hold more than one container
CRANE_INK = "#26327a"  # yard crane reach outline and tag
BOLT = ((0.60, 0.90), (0.25, 0.48), (0.47, 0.48), (0.38, 0.10), (0.75, 0.55), (0.53, 0.55))
LEGEND = (
    ("water", "water"),
    ("berth", "berth"),
    ("quay", "quay"),
    ("lane", "lane"),
    ("transfer_point", "transfer point"),
    ("yard_slot", "yard slot"),
    ("gate_in", "gate in"),
    ("gate_out", "gate out"),
    ("charging_station", "charging station"),
    ("blocked_building", "building"),
    ("blocked_exclusion", "exclusion zone"),
    ("blocked_unused", "unused"),
)


def _kind(cell_type: str, entry: dict[str, Any] | None) -> str:
    """Which color key a cell uses."""
    if cell_type == "gate":
        return "gate_in" if (entry or {}).get("flow") == "in" else "gate_out"
    if cell_type == "blocked":
        reason = (entry or {}).get("reason")
        return {"building": "blocked_building", "exclusion": "blocked_exclusion"}.get(reason, "blocked_unused")
    return cell_type


def _split(key: str) -> tuple[int, int]:
    x, y = key.split(",")
    return int(x), int(y)


def _new_figure(width: int, height: int) -> Figure:
    """A figure sized to the grid, kept between 6 and 18 inches wide and at most 14 tall."""
    fig_w = min(18.0, max(6.0, width * 0.6))
    fig_h = min(14.0, max(3.0, fig_w * height / width + 1.0))
    fig = Figure(figsize=(fig_w, fig_h))
    FigureCanvasAgg(fig)
    return fig


def _draw_base(ax: Any, compiled: dict[str, Any]) -> tuple[int, int, float]:
    width, height = compiled["width"], compiled["height"]
    grid, cells = compiled["grid"], compiled["cells"]
    image = [
        [to_rgb(COLORS[_kind(grid[y][x], cells.get(f"{x},{y}"))]) for x in range(width)]
        for y in range(height)
    ]
    ax.imshow(image, origin="lower", extent=(0, width, 0, height), interpolation="nearest", zorder=1)
    ax.vlines(range(width + 1), 0, height, colors=(0, 0, 0, 0.12), linewidth=0.3, zorder=2)
    ax.hlines(range(height + 1), 0, width, colors=(0, 0, 0, 0.12), linewidth=0.3, zorder=2)
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    step = 1 if width <= 30 else 5
    ax.set_xticks([i + 0.5 for i in range(0, width, step)], [str(i) for i in range(0, width, step)])
    ax.set_yticks([i + 0.5 for i in range(0, height, step)], [str(i) for i in range(0, height, step)])
    ax.tick_params(labelsize=7, length=0)
    name = str(compiled["name"]).replace("$", r"\$")  # "$" would start matplotlib math text
    ax.set_title(f"{name}  ({width} x {height} cells, {compiled['cell_size_m']} m each)")
    points_per_cell = ax.figure.get_figwidth() * 72 / max(width, 1)
    return width, height, max(2.0, min(9.0, points_per_cell * 0.4))


def _draw_arrows(ax: Any, cells: dict[str, Any]) -> None:
    xs: list[float] = []
    ys: list[float] = []
    us: list[float] = []
    vs: list[float] = []
    for key, entry in cells.items():
        x, y = _split(key)
        for direction in entry.get("exits", []):
            dx, dy = DELTAS[direction]
            xs.append(x + 0.5)
            ys.append(y + 0.5)
            us.append(dx * 0.38)
            vs.append(dy * 0.38)
    if xs:
        ax.quiver(
            xs, ys, us, vs, angles="xy", scale_units="xy", scale=1, pivot="tail",
            units="xy", width=0.035, headwidth=4, headlength=4.5, color="#2b2b2b", zorder=4,
        )


def _draw_labels(ax: Any, cells: dict[str, Any], font: float) -> None:
    block_cells: dict[str, list[tuple[int, int]]] = defaultdict(list)
    gates = [_split(key) for key, entry in cells.items() if entry["type"] == "gate"]
    for key, entry in cells.items():
        x, y = _split(key)
        if entry["type"] == "yard_slot":
            block_cells[entry["block"]].append((x, y))
            if entry["row"] == 1:
                ax.text(x + 0.5, y + 0.25, str(entry["bay"]), ha="center", va="center",
                        fontsize=font * 0.8, color="#2f5f2f", zorder=5)
        elif entry["type"] == "berth" and "crane" in entry:
            ax.plot(x + 0.5, y + 0.5, marker="s", color="#222222", markersize=font * 1.1, zorder=5)
            ax.text(x + 0.5, y + 0.5, entry["crane"], ha="center", va="center",
                    fontsize=font * 0.7, color="white", zorder=6)
        elif entry["type"] == "charging_station":
            ax.add_patch(Polygon([(x + bx, y + by) for bx, by in BOLT], closed=True,
                                 facecolor="#ffe14d", edgecolor="#2a1d5c", linewidth=0.4, zorder=5))
            if font >= 5:  # big cells (small maps) have room for the id
                ax.text(x + 0.5, y + 0.1, entry["id"], ha="center", va="bottom",
                        fontsize=font * 0.55, color="white", fontweight="bold", zorder=6)
        elif entry["type"] == "gate":
            crowded = any(other != (x, y) and other[1] == y and abs(other[0] - x) <= 3 for other in gates)
            if crowded:  # neighbouring gates: stand the labels up so they do not overprint
                label = ax.text(x + 0.5, y + 1.0, entry["id"], ha="center", va="bottom", rotation=90,
                                fontsize=font * 1.3, color="black", fontweight="bold", zorder=6)
            else:
                label = ax.text(x + 0.5, y + 0.2, entry["id"], ha="center", va="center",
                                fontsize=font * 0.7, color="black", fontweight="bold", zorder=6)
            label.set_path_effects([patheffects.withStroke(linewidth=1.5, foreground="white")])
    for block_id, points in sorted(block_cells.items()):
        cx = sum(p[0] for p in points) / len(points) + 0.5
        cy = sum(p[1] for p in points) / len(points) + 0.5
        ax.text(cx, cy, block_id, ha="center", va="center", fontsize=font * 1.8,
                fontweight="bold", color="#1b4d1b", zorder=6)


def _draw_buffers(ax: Any, cells: dict[str, Any]) -> None:
    """Hatch the transfer points that hold more than one container (a transfer buffer)."""
    rects = []
    for key, entry in cells.items():
        if entry["type"] == "transfer_point" and entry.get("buffer_capacity", 1) > 1:
            x, y = _split(key)
            rects.append(Rectangle((x, y), 1, 1))
    if rects:
        ax.add_collection(PatchCollection(rects, facecolor="none", edgecolor=BUFFER_EDGE,
                                          linewidth=0, hatch="////", zorder=3))


def _draw_yard_cranes(ax: Any, compiled: dict[str, Any], font: float) -> None:
    """Outline each yard crane's reach and tag it at the top-left corner."""
    for crane in compiled.get("yard_cranes", []):
        (x0, y0), (x1, y1) = crane["reach"]
        ax.add_patch(Rectangle((x0, y0), x1 - x0 + 1, y1 - y0 + 1, fill=False, edgecolor=CRANE_INK,
                               linestyle=(0, (4, 2)), linewidth=1.0, zorder=3.5))
        ax.text(x0 + 0.25, y1 + 0.85, crane["id"], ha="left", va="top", fontsize=font * 0.8,
                color="white", fontweight="bold", zorder=8,
                bbox={"boxstyle": "round,pad=0.18", "facecolor": CRANE_INK, "edgecolor": "none"})


def _draw_extras(ax: Any, compiled: dict[str, Any], show_ids: bool, font: float) -> None:
    _draw_buffers(ax, compiled["cells"])
    _draw_yard_cranes(ax, compiled, font)
    for x, y in compiled.get("closures", []):
        ax.add_patch(Rectangle((x, y), 1, 1, fill=False, hatch="///", edgecolor="#c0392b",
                               linewidth=0.8, zorder=7))
    if show_ids:
        for x in range(compiled["width"]):
            for y in range(compiled["height"]):
                ax.text(x + 0.5, y + 0.14, f"{x},{y}", ha="center", va="center",
                        fontsize=font * 0.5, color="#666666", zorder=3)


def _legend(ax: Any, compiled: dict[str, Any]) -> None:
    grid, cells = compiled["grid"], compiled["cells"]
    present = {
        _kind(grid[y][x], cells.get(f"{x},{y}"))
        for y in range(compiled["height"])
        for x in range(compiled["width"])
    }
    capacities = [e.get("buffer_capacity", 1) for e in cells.values() if e["type"] == "transfer_point"]
    handles = []
    for key, label in LEGEND:
        if key not in present:
            continue
        if key == "transfer_point":
            if any(c <= 1 for c in capacities):
                handles.append(Patch(facecolor=COLORS[key], edgecolor="#888888", label=label))
            if any(c > 1 for c in capacities):
                handles.append(Patch(facecolor=COLORS[key], edgecolor=BUFFER_EDGE, hatch="////",
                                     label="transfer buffer"))
        else:
            handles.append(Patch(facecolor=COLORS[key], edgecolor="#888888", label=label))
    if compiled.get("yard_cranes"):
        handles.append(Line2D([0], [0], color=CRANE_INK, linestyle=(0, (4, 2)), label="yard crane reach"))
    if compiled.get("closures"):
        handles.append(Patch(facecolor="white", edgecolor="#c0392b", hatch="///", label="closed"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)


def render_2d(
    compiled: dict[str, Any],
    path: str | Path | None = "layout.png",
    show_ids: bool = False,
    ax: Any = None,
) -> None:
    """Draw a compiled layout. Saves a PNG to ``path``, or draws onto ``ax`` if given.

    With ``ax`` the caller owns the figure: nothing is saved, no legend is added, and
    the title, limits and ticks of ``ax`` are replaced. Pass a fresh Axes each time.
    """
    owns_figure = ax is None
    if owns_figure and path is None:
        raise ValueError("render_2d needs a path to save to or an ax to draw on")
    if owns_figure:
        fig = _new_figure(compiled["width"], compiled["height"])
        ax = fig.add_subplot(111)
    _, _, font = _draw_base(ax, compiled)
    _draw_arrows(ax, compiled["cells"])
    _draw_labels(ax, compiled["cells"], font)
    _draw_extras(ax, compiled, show_ids, font)
    if owns_figure:
        _legend(ax, compiled)
        if path is not None:
            fig.savefig(path, dpi=150, bbox_inches="tight")
