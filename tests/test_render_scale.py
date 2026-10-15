"""render_2d, JSON export, and a larger generated layout."""

import json
import time

from matplotlib.figure import Figure

from terminal_layout import load_layout
from terminal_layout.export import format_compiled
from terminal_layout.loader import layout_from_dict


def test_render_writes_a_png(demo, tmp_path):
    out = tmp_path / "demo.png"
    demo.render_2d(out)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out.stat().st_size > 5_000


def test_render_with_ids_and_closures(demo, tmp_path):
    out = tmp_path / "ids.png"
    demo.with_closures({(5, 1)}).render_2d(out, show_ids=True)
    assert out.stat().st_size > 5_000


def test_render_onto_a_given_axes_saves_nothing(demo, tmp_path):
    fig = Figure()
    ax = fig.add_subplot(111)
    demo.render_2d(path=tmp_path / "never.png", ax=ax)
    assert not (tmp_path / "never.png").exists()
    assert ax.images and ax.get_title().startswith("demo-12x8")


def test_render_does_not_change_the_layout(demo, tmp_path):
    before = demo.to_json()
    demo.render_2d(tmp_path / "x.png")
    assert demo.to_json() == before


def test_compiled_json_shape(demo):
    data = demo.to_json()
    assert list(data)[:6] == ["schema_version", "name", "cell_size_m", "width", "height", "grid"]
    assert len(data["grid"]) == 8 and all(len(row) == 12 for row in data["grid"])
    assert data["grid"][0][4] == "transfer_point" or data["grid"][1][4] == "transfer_point"
    assert "closures" not in data
    assert data["cells"]["4,1"]["serves"] == {"block": "A", "bay": 3}
    assert data["cells"]["1,1"]["exits"] == ["S", "W"]
    assert "0,7" not in data["cells"]  # water carries no attributes


def test_compiled_json_text_round_trips(demo):
    text = format_compiled(demo.to_json())
    assert json.loads(text) == demo.to_json()
    assert text.endswith("}\n")


def test_to_json_writes_the_file(demo, tmp_path):
    out = tmp_path / "out.json"
    returned = demo.to_json(out)
    assert json.loads(out.read_text()) == returned


def test_closures_appear_in_the_compiled_form(demo):
    data = demo.with_closures({(6, 1), (5, 1)}).to_json()
    assert data["closures"] == [[5, 1], [6, 1]]


def big_layout(width=100, height=40):
    """A comb: a service lane per row of blocks, joined by a ring road, one berth, two gates."""
    bays, rows, quay_y = 8, 2, height - 3
    row_ys = list(range(4, height - 6, 4))
    service_ys = [1] + [y - 1 for y in row_ys]
    lanes = [
        {"rect": [[0, quay_y], [width - 2, quay_y]], "exits": ["E"], "kind": "quay", "berth": "B1"},
        {"rect": [[0, 1], [0, quay_y - 1]], "exits": ["N"]},
        {"rect": [[1, 1], [1, 1]], "exits": ["W", "S"]},
        {"rect": [[2, 1], [width - 2, 1]], "exits": ["W"]},
    ]
    for y in range(1, quay_y + 1):
        exits = ["S", "W"] if y in service_ys[1:] else (["W"] if y == 1 else ["S"])
        lanes.append({"rect": [[width - 1, y], [width - 1, y]], "exits": exits})
    lanes += [{"rect": [[1, y], [width - 2, y]], "exits": ["W"]} for y in service_ys[1:]]
    blocks = [
        {"id": f"Y{i}_{j}", "origin": [x, y], "bays": bays, "rows": rows, "orientation": "EW",
         "max_tier": 4, "service_side": "S"}
        for i, y in enumerate(row_ys)
        for j, x in enumerate(range(3, width - 12, 12))
    ]
    return {
        "schema_version": "1.0", "name": "big", "cell_size_m": 15, "width": width, "height": height,
        "water": [{"rect": [[0, height - 1], [width - 1, height - 1]]}],
        "berths": [{"id": "B1", "rect": [[0, height - 2], [width - 1, height - 2]], "quay_y": quay_y,
                    "cranes": [{"id": f"QC{i}", "x": 10 + 15 * i} for i in range(5)]}],
        "lanes": lanes, "blocks": blocks,
        "gates": [{"id": "G_IN", "cell": [width - 2, 0], "flow": "in", "exits": ["N"]},
                  {"id": "G_OUT", "cell": [1, 0], "flow": "out", "exits": []}],
    }


def test_a_100_by_40_layout_loads_quickly():
    data = big_layout()
    start = time.perf_counter()
    layout = layout_from_dict(data)
    elapsed = time.perf_counter() - start
    assert layout.width == 100 and layout.height == 40
    assert len(layout.blocks) > 20
    assert elapsed < 5.0
    assert layout.shortest_path((98, 0), (1, 0)) is not None
