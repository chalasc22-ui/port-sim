"""The hand-checked numbers from the spec's verification checklist, written out one by one."""

import inspect
import json

from terminal_layout import Layout, load_layout
from terminal_layout.authored import rect_cells
from terminal_layout.render import render_2d

API_NAMES = (
    "cell cells is_traversable exits neighbors quay_cells gate_cells transfer_points "
    "transfer_point_for slot slots_in_block with_closures shortest_path validate to_json render_2d "
    "charging_stations"
).split()
API_PROPERTIES = "width height cell_size_m name blocks berths yard_cranes closures warnings".split()


def test_every_api_member_exists(demo):
    for name in API_NAMES:
        assert callable(getattr(Layout, name)), name
    for name in API_PROPERTIES:
        assert isinstance(inspect.getattr_static(Layout, name), property), name


def test_signatures_match_the_spec():
    params = lambda name: list(inspect.signature(getattr(Layout, name)).parameters)[1:]  # noqa: E731
    assert params("cell") == ["x", "y"]
    assert params("neighbors") == ["x", "y"]
    assert params("transfer_points") == ["block_id", "berth_id"]
    assert params("shortest_path") == ["a", "b"]
    assert params("to_json") == ["path"]
    assert params("render_2d") == ["path", "show_ids", "ax"]


def test_16_slots_a_01_01_to_a_08_02(demo):
    slots = demo.slots_in_block("A")
    assert len(slots) == 16 and slots[0] == "A-01-01" and slots[-1] == "A-08-02"


def test_block_transfer_points_in_bay_order(demo):
    points = demo.transfer_points(block_id="A")
    assert [p.pos for p in points] == [(x, 1) for x in range(2, 10)]
    assert [p.attrs["serves"]["bay"] for p in points] == list(range(1, 9))


def test_quay_transfer_points(demo):
    points = demo.transfer_points(berth_id="B1")
    assert [(p.pos, p.attrs["serves"]["crane"]) for p in points] == [((3, 5), "QC1"), ((8, 5), "QC2")]


def test_neighbor_order(demo):
    assert demo.neighbors(5, 1) == [(4, 1)]
    assert demo.neighbors(1, 1) == [(1, 0), (0, 1)]  # S before W


def test_gate_to_gate_is_11_moves(demo):
    assert len(demo.shortest_path((10, 0), (1, 0))) - 1 == 11


def test_quay_to_first_bay_is_21_moves(demo):
    expected = [(x, 5) for x in range(3, 12)] + [(11, y) for y in (4, 3, 2, 1)]
    expected += [(x, 1) for x in range(10, 1, -1)]
    assert demo.shortest_path((3, 5), (2, 1)) == expected
    assert len(expected) - 1 == 21


def test_closing_6_1_cuts_off_bay_1_and_warns(demo):
    closed = demo.with_closures({(6, 1)})
    assert closed.shortest_path((3, 5), (2, 1)) is None
    assert closed.warnings and all(w.startswith("rule 12:") for w in closed.warnings)
    assert demo.shortest_path((3, 5), (2, 1)) is not None
    assert demo.closures == frozenset() and demo.warnings == ()


def test_to_json_identical_across_runs_and_to_checked_in_file(demo_path):
    first, second = load_layout(demo_path).to_json(), load_layout(demo_path).to_json()
    assert first == second
    checked_in = demo_path.with_name("demo-12x8.compiled.json")
    assert json.loads(checked_in.read_text()) == first


def test_compiled_file_text_is_what_to_json_writes(demo, demo_path, tmp_path):
    out = tmp_path / "again.json"
    demo.to_json(out)
    assert out.read_text() == demo_path.with_name("demo-12x8.compiled.json").read_text()


def test_corner_cells_use_x_then_y(demo):
    assert demo.cell(0, 0).type == "blocked" and demo.cell(11, 0).type == "blocked"
    assert demo.cell(0, 7).type == "water" and demo.cell(11, 7).type == "water"
    assert (demo.cell(1, 0).type, demo.cell(1, 0).attrs["flow"]) == ("gate", "out")
    assert (demo.cell(10, 0).type, demo.cell(10, 0).attrs["flow"]) == ("gate", "in")
    assert demo.cell(11, 5).type == "lane" and demo.cell(0, 5).type == "quay"
    assert demo.cell(2, 2).attrs["slot"] == "A-01-01" and demo.cell(9, 3).attrs["slot"] == "A-08-02"


def test_inclusive_rectangles():
    assert len(list(rect_cells(((2, 1), (11, 1))))) == 10
    assert len(list(rect_cells(((2, 2), (9, 3))))) == 16


def test_one_transfer_point_per_bay_not_per_row(demo):
    assert len(demo.transfer_points(block_id="A")) == demo.blocks["A"].bays == 8


def test_renderer_needs_only_the_compiled_json(demo, demo_path, tmp_path):
    compiled = json.loads(demo_path.with_name("demo-12x8.compiled.json").read_text())
    render_2d(compiled, tmp_path / "from_json.png")
    assert (tmp_path / "from_json.png").stat().st_size > 5_000


def test_grid_matches_the_spec_ascii_map(demo):
    """The picture is drawn from `grid`, so check the types against the spec's map."""
    symbols = {"water": "~", "berth": "B", "quay": "Q", "transfer_point": "P", "lane": "L",
               "yard_slot": "A", "gate": "G", "blocked": "."}
    drawn = {y: "".join(symbols[demo.cell(x, y).type] for x in range(12)) for y in range(8)}
    assert drawn == {
        7: "~~~~~~~~~~~~",
        6: "BBBBBBBBBBBB",
        5: "QQQPQQQQPQQL",
        4: "L..........L",
        3: "L.AAAAAAAA.L",
        2: "L.AAAAAAAA.L",
        1: "LLPPPPPPPPLL",
        0: ".G........G.",
    }
    arrows = {(x, 5): ["E"] for x in range(11)} | {(11, y): ["S"] for y in range(2, 6)}
    arrows |= {(x, 1): ["W"] for x in range(2, 12)} | {(1, 1): ["S", "W"]}
    arrows |= {(0, y): ["N"] for y in range(1, 5)} | {(10, 0): ["N"]}
    for pos, exits in arrows.items():
        assert demo.exits(*pos) == exits, pos
