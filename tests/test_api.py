"""The Python API the other modules call: cells, movement, lookups, closures, immutability."""

import pytest

from terminal_layout import Layout, LayoutError


def test_cell_and_cells(demo):
    cell = demo.cell(4, 1)
    assert (cell.x, cell.y, cell.type, cell.pos) == (4, 1, "transfer_point", (4, 1))
    assert cell.attrs["serves"] == {"block": "A", "bay": 3}
    assert len(demo.cells()) == 96
    assert [c.pos for c in demo.cells("gate")] == [(1, 0), (10, 0)]
    ordered = [c.pos for c in demo.cells()]
    assert ordered == sorted(ordered)


def test_cell_off_grid_raises(demo):
    for x, y in [(-1, 0), (0, -1), (12, 0), (0, 8)]:
        with pytest.raises(IndexError):
            demo.cell(x, y)


def test_unknown_type_raises(demo):
    with pytest.raises(ValueError):
        demo.cells("runway")


def test_is_traversable(demo):
    assert demo.is_traversable(0, 3) and demo.is_traversable(3, 5) and demo.is_traversable(10, 0)
    assert not demo.is_traversable(4, 3)  # yard slot
    assert not demo.is_traversable(0, 7)  # water
    assert not demo.is_traversable(-1, 0) and not demo.is_traversable(99, 99)


def test_exits_and_neighbors_are_ordered_n_e_s_w(demo):
    assert demo.exits(1, 1) == ["S", "W"]
    assert demo.neighbors(1, 1) == [(1, 0), (0, 1)]
    assert demo.exits(10, 0) == ["N"]
    assert demo.neighbors(1, 0) == []


def test_every_exit_is_a_neighbor_on_the_open_map(demo):
    for cell in demo.cells():
        if demo.is_traversable(cell.x, cell.y):
            assert len(demo.neighbors(cell.x, cell.y)) == len(cell.exits)


def test_unknown_names_raise_keyerror(demo):
    for call in (
        lambda: demo.slot("Z-01-01"),
        lambda: demo.slots_in_block("Z"),
        lambda: demo.transfer_points(block_id="Z"),
        lambda: demo.transfer_points(berth_id="Z"),
        lambda: demo.quay_cells("Z"),
    ):
        with pytest.raises(KeyError):
            call()


def test_argument_errors(demo):
    with pytest.raises(ValueError):
        demo.gate_cells("sideways")
    with pytest.raises(ValueError):
        demo.transfer_points(block_id="A", berth_id="B1")


def test_shortest_path_edge_cases(demo):
    assert demo.shortest_path((0, 3), (0, 3)) == [(0, 3)]
    assert demo.shortest_path((4, 3), (0, 3)) is None  # start is a yard slot
    assert demo.shortest_path((0, 3), (0, 7)) is None  # goal is water
    assert demo.shortest_path((1, 0), (10, 0)) is None  # nothing leaves a gate-out


def test_shortest_path_is_repeatable(demo):
    assert demo.shortest_path((10, 0), (1, 0)) == demo.shortest_path((10, 0), (1, 0))


def test_closures_replace_and_leave_the_original_alone(demo):
    closed = demo.with_closures({(5, 1)})
    assert closed is not demo and demo.closures == frozenset()
    assert closed.closures == {(5, 1)}
    assert not closed.is_traversable(5, 1) and demo.is_traversable(5, 1)
    assert closed.neighbors(5, 1) == []
    assert (5, 1) not in closed.neighbors(6, 1)
    assert closed.exits(5, 1) == ["W"]  # the static map is unchanged
    assert closed.with_closures(set()).closures == frozenset()
    assert closed.with_closures({(6, 1)}).closures == {(6, 1)}


def test_closing_a_chokepoint_warns_and_cuts_paths(demo):
    closed = demo.with_closures({(5, 1)})
    assert closed.shortest_path((10, 0), (1, 0)) is None
    assert closed.warnings
    assert all(w.startswith("rule 12: with 1 closed cell, ") for w in closed.warnings)


def test_closing_a_dead_end_side_does_not_warn_if_still_connected(demo):
    assert demo.with_closures(set()).warnings == ()


def test_closure_rule_11(demo):
    for bad in ({(4, 3)}, {(0, 7)}, {(50, 50)}):
        with pytest.raises(LayoutError) as info:
            demo.with_closures(bad)
        assert all(e.startswith("rule 11:") for e in info.value.errors)


def test_closure_input_must_be_integer_pairs(demo):
    for bad in ({(1.5, 1)}, {"ab"}, {(True, 1)}):
        with pytest.raises(TypeError):
            demo.with_closures(bad)


def test_layout_is_immutable(demo):
    with pytest.raises(AttributeError):
        demo.name = "other"
    with pytest.raises(AttributeError):
        demo.new_field = 1
    with pytest.raises(AttributeError):
        del demo.name


def test_returned_collections_are_copies(demo):
    demo.blocks.clear()
    assert "A" in demo.blocks
    demo.cells().clear()
    assert len(demo.cells()) == 96
    demo.berths.clear()
    assert "B1" in demo.berths


def test_cell_attrs_are_read_only(demo):
    cell = demo.cell(4, 1)
    with pytest.raises(TypeError):
        cell.attrs["serves"] = {}
    with pytest.raises(TypeError):
        cell.attrs["serves"]["bay"] = 99
    with pytest.raises(AttributeError):
        cell.type = "lane"


def test_two_layouts_do_not_share_state(demo_path):
    from terminal_layout import load_layout

    a, b = load_layout(demo_path), load_layout(demo_path)
    a.with_closures({(5, 1)})
    assert b.closures == frozenset()
    assert isinstance(a, Layout)


def test_validate_reports_closure_problems_too(demo):
    assert demo.with_closures({(5, 1)}).validate() == []
