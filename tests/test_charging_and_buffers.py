"""Schema 1.1 and 1.2: charging stations, transfer buffers, quay transfer lanes and yard crane reach
(rules 14 and 15)."""

import copy
import dataclasses
import json

import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle

from terminal_layout import LayoutError, QuayCrane, YardCrane
from terminal_layout.export import format_compiled
from terminal_layout.loader import layout_from_dict
from terminal_layout.render import _legend, render_2d


def add_depot(d):
    """Two charging stations on a short loop beside the east column, in the space the demo leaves free.

    Block A shrinks to six bays so x = 8..10 is free. At the fork (11, 4) a vehicle either goes on
    down the column or west along y = 4, steps south into a station (CH1 at (10, 3), CH2 at (9, 3)),
    and rejoins the column at (11, 2) along y = 2. Column x = 8 is the loop: it lets a vehicle go
    round the stations, so a parked vehicle never blocks the way.
    """
    d["schema_version"] = "1.1"
    d["blocks"][0]["bays"] = 6
    lanes = d["lanes"]
    i = lanes.index({"rect": [[11, 2], [11, 4]], "exits": ["S"]})
    lanes[i : i + 1] = [
        {"rect": [[11, 2], [11, 3]], "exits": ["S"]},
        {"rect": [[11, 4], [11, 4]], "exits": ["S", "W"]},  # the fork
        {"rect": [[9, 4], [10, 4]], "exits": ["W", "S"]},  # access row: on west, or down into a station
        {"rect": [[8, 4], [8, 4]], "exits": ["S"]},  # the loop turns south here
        {"rect": [[9, 3], [10, 3]], "exits": ["S"]},  # the two stations sit on these cells
        {"rect": [[8, 3], [8, 3]], "exits": ["S"]},
        {"rect": [[8, 2], [10, 2]], "exits": ["E"]},  # exit row back to the column
    ]
    d["chargers"] = [{"id": "CH1", "cell": [10, 3]}, {"id": "CH2", "cell": [9, 3]}]


def add_chargers(d, *cells):
    """Schema 1.1 chargers on the plain demo, ids CH1, CH2, ..."""
    d["schema_version"] = "1.1"
    d["chargers"] = [{"id": f"CH{n}", "cell": list(c)} for n, c in enumerate(cells, 1)]


@pytest.fixture
def depot(data):
    add_depot(data)
    return layout_from_dict(data)


@pytest.fixture
def broken_depot(data):
    """Error lines for a mutated copy of the demo-plus-depot ([] if it loads)."""

    def run(mutate):
        edited = copy.deepcopy(data)
        add_depot(edited)
        mutate(edited)
        try:
            layout_from_dict(edited)
        except LayoutError as exc:
            return list(exc.errors)
        return []

    return run


# ---- charging stations on the map -----------------------------------------------


def test_a_charger_turns_the_lane_cell_under_it_into_a_charging_station(depot):
    cell = depot.cell(10, 3)
    assert (cell.type, cell.attrs["id"], cell.exits) == ("charging_station", "CH1", ("S",))
    assert [(c.pos, c.attrs["id"]) for c in depot.charging_stations()] == [((9, 3), "CH2"), ((10, 3), "CH1")]
    assert depot.cells("charging_station") == depot.charging_stations()


def test_the_depot_map_is_valid_and_has_no_warnings(depot):
    assert depot.validate() == []
    assert depot.warnings == ()


def test_a_charging_station_is_part_of_the_road(depot):
    assert depot.is_traversable(10, 3)
    assert depot.neighbors(10, 3) == [(10, 2)]
    # Shut the column and the loop: a vehicle that wants to get from the fork to the exit row must use CH1.
    closed = depot.with_closures({(11, 3), (8, 3)})
    assert closed.shortest_path((11, 4), (11, 2)) == [(11, 4), (10, 4), (10, 3), (10, 2), (11, 2)]


def test_the_road_is_shorter_than_the_loop_so_vehicles_pass_the_depot_by(depot):
    assert depot.shortest_path((11, 4), (11, 2)) == [(11, 4), (11, 3), (11, 2)]
    forced_round = depot.with_closures({(11, 3), (10, 3), (9, 3)})  # column and both stations shut
    assert len(forced_round.shortest_path((11, 4), (11, 2))) - 1 == 8


def test_a_closed_station_is_a_closed_cell(depot):
    closed = depot.with_closures({(10, 3)})
    assert not closed.is_traversable(10, 3)
    assert closed.neighbors(10, 4) == [(9, 4)]
    assert [c.pos for c in closed.charging_stations()] == [(9, 3), (10, 3)]  # lists the static map


def test_the_demo_has_no_stations(demo):
    assert demo.charging_stations() == []
    assert demo.to_json()["chargers"] == []
    assert not [e for e in demo.validate() if e.startswith("rule 15")]


# ---- schema versions ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
def test_every_authored_version_loads_and_compiles_as_1_2(data, version):
    data["schema_version"] = version
    assert layout_from_dict(data).to_json()["schema_version"] == "1.2"


def test_an_unknown_schema_version_is_rule_10(broken):
    errors = broken(lambda d: d.__setitem__("schema_version", "2.0"))
    assert len(errors) == 1 and errors[0].startswith("rule 10: schema_version")


# ---- rules 1, 3, 10 and 14: the chargers in the authored file ------------------------


@pytest.mark.parametrize(
    "cell, found",
    [
        ((5, 3), "a yard slot cell"),
        ((5, 5), "a quay cell"),
        ((0, 7), "a water cell"),
        ((4, 1), "a transfer point cell"),
        ((10, 0), "a gate cell"),
        ((3, 0), "a blocked cell"),
    ],
)
def test_rule_14_a_charger_needs_a_plain_lane_cell(broken_depot, cell, found):
    errors = broken_depot(lambda d: d["chargers"][0].__setitem__("cell", list(cell)))
    assert errors == [f"rule 14: charger CH1 has no lane cell under it (expected at {cell}, found {found})"]


def test_rule_14_two_chargers_on_one_cell(broken_depot):
    errors = broken_depot(lambda d: d["chargers"][1].__setitem__("cell", [10, 3]))
    assert errors == ["rule 14: chargers CH1 and CH2 are both on cell (10, 3)"]


def test_rule_3_duplicate_charger_ids(broken_depot):
    errors = broken_depot(lambda d: d["chargers"][1].__setitem__("id", "CH1"))
    assert errors == ["rule 3: charger id 'CH1' is used 2 times (chargers[0], chargers[1])"]


def test_rule_1_charger_outside_the_grid(broken_depot):
    errors = broken_depot(lambda d: d["chargers"][0].__setitem__("cell", [12, 3]))
    assert errors == ["rule 1: charger CH1 cell (12, 3) lies outside the 12 x 8 grid"]


def test_rule_10_charger_fields(broken_depot):
    def edit(d):
        d["chargers"][0]["id"] = "bad id"
        d["chargers"][1]["extra"] = 1
        d["chargers"].append({"id": "CH3"})

    errors = broken_depot(edit)
    assert any(e.startswith("rule 10: chargers[0].id") for e in errors)
    assert any(e.startswith("rule 10: chargers[1].extra") for e in errors)
    assert any(e.startswith("rule 10: chargers[2].cell") for e in errors)


# ---- rule 15: an occupied station must not cut the network ------------------------


def test_rule_15_the_loop_keeps_the_road_open_with_every_station_occupied(depot):
    assert depot.with_closures({(10, 3), (9, 3)}).warnings == ()


def test_rule_15_a_station_on_the_loop_itself_is_refused(broken_depot):
    errors = broken_depot(lambda d: d["chargers"].append({"id": "CH3", "cell": [8, 3]}))
    assert errors == [
        "rule 15: with every charging station occupied, the internal network is not strongly "
        "connected (no path from (0, 1) to (8, 2))",
        "rule 15: with every charging station occupied, 3 internal cells cannot reach any gate-out "
        "(first: (8, 4))",
    ]


def test_rule_15_one_station_on_the_through_road_is_refused(broken):
    errors = broken(lambda d: add_chargers(d, (11, 3)))
    assert errors == [
        "rule 15: with the charging station occupied, the internal network is not strongly "
        "connected (no path from (0, 1) to (1, 1))",
        "rule 15: with the charging station occupied, 17 internal cells cannot reach any gate-out "
        "(first: (0, 1))",
    ]


def test_rule_15_says_every_when_there_are_several_stations(broken):
    errors = broken(lambda d: add_chargers(d, (11, 3), (11, 4)))
    assert errors and all(e.startswith("rule 15: with every charging station occupied, ") for e in errors)


def test_rule_15_waits_for_rule_14(broken):
    errors = broken(lambda d: add_chargers(d, (4, 1)))  # a transfer point: not a lane cell
    assert [e.split(":")[0] for e in errors] == ["rule 14"]


# ---- transfer-buffer capacity ----------------------------------------------------------


def test_buffer_capacity_is_zero_everywhere_unless_the_file_says_otherwise(demo):
    """0 is a coupled handover: the crane lifts straight between the vehicle and the stack or ship."""
    assert {c.attrs["buffer_capacity"] for c in demo.cells("transfer_point")} == {0}
    assert {b.buffer_capacity for b in demo.blocks.values()} == {0}
    assert {c.buffer_capacity for c in demo.quay_cranes.values()} == {0}
    compiled = demo.to_json()
    assert compiled["blocks"][0]["buffer_capacity"] == 0
    assert [c["buffer_capacity"] for c in compiled["berths"][0]["cranes"]] == [0, 0]


def test_a_block_and_a_crane_can_set_their_own_capacity(data):
    data["schema_version"] = "1.1"
    data["blocks"][0]["buffer_capacity"] = 3
    data["berths"][0]["cranes"][0]["buffer_capacity"] = 2
    layout = layout_from_dict(data)
    assert layout.blocks["A"].buffer_capacity == 3
    assert {p.attrs["buffer_capacity"] for p in layout.transfer_points(block_id="A")} == {3}
    assert [p.attrs["buffer_capacity"] for p in layout.transfer_points(berth_id="B1")] == [2, 0]  # QC1, QC2
    compiled = layout.to_json()
    assert compiled["blocks"][0]["buffer_capacity"] == 3
    assert [c["buffer_capacity"] for c in compiled["berths"][0]["cranes"]] == [2, 0]
    assert compiled["cells"]["4,1"]["buffer_capacity"] == 3


@pytest.mark.parametrize("value", [-1, 1.5, "2", True])
def test_rule_10_block_buffer_capacity_is_a_whole_number_of_at_least_zero(broken, value):
    errors = broken(lambda d: d["blocks"][0].__setitem__("buffer_capacity", value))
    assert len(errors) == 1 and errors[0].startswith("rule 10: blocks[0].buffer_capacity")


def test_rule_10_crane_buffer_capacity(broken):
    errors = broken(lambda d: d["berths"][0]["cranes"][0].__setitem__("buffer_capacity", -1))
    assert len(errors) == 1 and errors[0].startswith("rule 10: berths[0].cranes[0].buffer_capacity")


def test_a_buffer_capacity_of_zero_is_allowed_and_means_coupled(data):
    data["blocks"][0]["buffer_capacity"] = 0
    data["berths"][0]["cranes"][0]["buffer_capacity"] = 0
    assert {c.attrs["buffer_capacity"] for c in layout_from_dict(data).cells("transfer_point")} == {0}


# ---- quay transfer lanes (schema 1.2) --------------------------------------------------------


def test_one_transfer_lane_per_crane_unless_the_file_says_otherwise(demo):
    assert demo.quay_cranes == {
        "QC1": QuayCrane("QC1", "B1", 3, ((3, 5),), 0),
        "QC2": QuayCrane("QC2", "B1", 8, ((8, 5),), 0),
    }
    assert demo.berths == {"B1": (("QC1", (3, 5)), ("QC2", (8, 5)))}


def test_four_lanes_sit_one_west_and_two_east_of_the_crane_column(data):
    data["schema_version"] = "1.2"
    data["berths"][0]["cranes"][0]["transfer_lanes"] = 4
    layout = layout_from_dict(data)
    crane = layout.quay_cranes["QC1"]
    assert crane.transfer_points == ((2, 5), (3, 5), (4, 5), (5, 5))
    assert layout.berths["B1"] == (("QC1", (3, 5)), ("QC2", (8, 5)))  # berths keep the crane's own column
    assert [p.pos for p in layout.transfer_points(berth_id="B1")] == [(2, 5), (3, 5), (4, 5), (5, 5), (8, 5)]
    assert all(p.attrs["serves"] == {"berth": "B1", "crane": "QC1"} for p in layout.transfer_points(berth_id="B1")[:4])
    assert not [e for e in layout.validate() if e.startswith("rule 9")]  # each lane is beside the berth
    compiled = layout.to_json()["berths"][0]["cranes"][0]
    assert compiled == {"id": "QC1", "x": 3, "transfer_point": [3, 5],
                        "transfer_points": [[2, 5], [3, 5], [4, 5], [5, 5]], "buffer_capacity": 0}


@pytest.mark.parametrize("lanes, xs", [(1, [3]), (2, [3, 4]), (3, [2, 3, 4]), (5, [1, 2, 3, 4, 5])])
def test_an_odd_lane_count_is_centred_and_an_even_one_leans_east(data, lanes, xs):
    data["berths"][0]["cranes"][0]["transfer_lanes"] = lanes
    assert [p[0] for p in layout_from_dict(data).quay_cranes["QC1"].transfer_points] == xs


def test_rule_8_lanes_of_two_cranes_may_not_overlap(broken):
    def edit(d):
        d["berths"][0]["cranes"][0]["transfer_lanes"] = 4   # QC1 at 3: x 2..5
        d["berths"][0]["cranes"][1] = {"id": "QC2", "x": 6, "transfer_lanes": 4}  # x 5..8

    errors = broken(edit)
    assert errors == [
        "rule 8: berth B1 crane QC2 shares its quay cell (5, 5) with berth B1 crane QC1 (lane 1 of 4)"
    ]


def test_rule_8_a_lane_must_be_a_quay_cell_of_the_berth(broken):
    assert broken(lambda d: d["berths"][0]["cranes"][1].__setitem__("transfer_lanes", 4)) == []  # x 7..10: quay
    errors = broken(lambda d: d["berths"][0]["cranes"][1].update({"x": 9, "transfer_lanes": 4}))  # x 8..11
    assert errors == [
        "rule 8: berth B1 crane QC2 has no quay cell of berth B1 under lane 4 of 4 "
        "(expected at (11, 5), found a lane cell)"
    ]


@pytest.mark.parametrize("value", [0, -2, 1.5, "4"])
def test_rule_10_transfer_lanes_is_a_whole_number_of_at_least_one(broken, value):
    errors = broken(lambda d: d["berths"][0]["cranes"][0].__setitem__("transfer_lanes", value))
    assert len(errors) == 1 and errors[0].startswith("rule 10: berths[0].cranes[0].transfer_lanes")


def test_a_quay_crane_cannot_be_edited_and_the_dict_is_a_copy(demo):
    with pytest.raises(dataclasses.FrozenInstanceError):
        demo.quay_cranes["QC1"].x = 4
    demo.quay_cranes.clear()
    assert list(demo.quay_cranes) == ["QC1", "QC2"]


# ---- yard cranes -------------------------------------------------------------------------


def test_a_yard_crane_is_derived_from_the_blocks_that_name_it(demo):
    assert demo.yard_cranes == {"RTG1": YardCrane("RTG1", ("A",), ((2, 1), (9, 3)))}


def test_reach_covers_the_slots_and_the_transfer_points(depot):
    assert depot.yard_cranes["RTG1"].reach == ((2, 1), (7, 3))  # six bays: x 2..7, service row y 1, rows y 2..3


def test_a_block_with_no_crane_has_no_yard_crane(data):
    data["blocks"][0]["crane"] = None
    assert layout_from_dict(data).yard_cranes == {}


def test_a_yard_crane_cannot_be_edited_and_the_dict_is_a_copy(demo):
    with pytest.raises(dataclasses.FrozenInstanceError):
        demo.yard_cranes["RTG1"].reach = ((0, 0), (1, 1))
    demo.yard_cranes.clear()
    assert list(demo.yard_cranes) == ["RTG1"]


# ---- the compiled form ---------------------------------------------------------------------


def test_compiled_form_carries_the_new_keys(depot):
    data = depot.to_json()
    assert data["schema_version"] == "1.2"
    assert list(data)[-2:] == ["chargers", "yard_cranes"]
    assert data["chargers"] == [{"id": "CH1", "cell": [10, 3]}, {"id": "CH2", "cell": [9, 3]}]
    assert data["cells"]["10,3"] == {"type": "charging_station", "exits": ["S"], "id": "CH1"}
    assert data["grid"][3][9:11] == ["charging_station", "charging_station"]
    assert data["yard_cranes"] == [{"id": "RTG1", "blocks": ["A"], "reach": [[2, 1], [7, 3]]}]


def test_compiled_text_puts_each_charger_and_crane_on_its_own_line(depot):
    text = format_compiled(depot.to_json())
    assert '  "chargers": [\n    {"id": "CH1", "cell": [10, 3]},\n    {"id": "CH2", "cell": [9, 3]}\n  ],\n' in text
    assert '  "yard_cranes": [\n    {"id": "RTG1", "blocks": ["A"], "reach": [[2, 1], [7, 3]]}\n  ]\n}\n' in text
    assert json.loads(text) == depot.to_json()


def test_closures_still_come_last_in_the_compiled_form(depot):
    assert list(depot.with_closures({(10, 3)}).to_json())[-1] == "closures"


def test_a_layout_with_stations_survives_a_pickle_round_trip(depot):
    import pickle

    again = pickle.loads(pickle.dumps(depot))
    assert again.to_json() == depot.to_json()
    assert [c.pos for c in again.charging_stations()] == [(9, 3), (10, 3)]


# ---- the picture -----------------------------------------------------------------------------


def draw(layout):
    fig = Figure()
    ax = fig.add_subplot(111)
    render_2d(layout.to_json(), ax=ax)
    return ax


def test_stations_are_drawn_as_bolts_with_their_ids(depot):
    ax = draw(depot)
    assert len([p for p in ax.patches if isinstance(p, Polygon)]) == 2
    assert {"CH1", "CH2"} <= {t.get_text() for t in ax.texts}


def test_the_crane_reach_is_a_dashed_box_with_a_tag(depot):
    ax = draw(depot)
    boxes = [p for p in ax.patches if isinstance(p, Rectangle)]
    assert len(boxes) == 1
    assert (boxes[0].get_xy(), boxes[0].get_width(), boxes[0].get_height()) == ((2, 1), 6, 3)
    assert boxes[0].get_linestyle() == (0, (4, 2))  # dashed
    assert "RTG1" in {t.get_text() for t in ax.texts}


def test_only_transfer_points_with_a_ground_buffer_are_hatched(data):
    ax = draw(layout_from_dict(copy.deepcopy(data)))
    assert [c for c in ax.collections if c.get_hatch() == "////"] == []
    data["schema_version"] = "1.1"
    data["blocks"][0]["buffer_capacity"] = 2
    ax = draw(layout_from_dict(data))
    hatched = [c for c in ax.collections if c.get_hatch() == "////"]
    assert len(hatched) == 1 and len(hatched[0].get_paths()) == 8  # the eight bays of block A, not the cranes


def test_legend_names_only_what_is_on_the_map(data):
    plain = {t.get_text() for t in _legend_of(layout_from_dict(copy.deepcopy(data)))}
    assert "transfer point (vehicle stands here under the crane)" in plain and "yard crane reach" in plain
    assert "charging station" not in plain and "transfer point with ground buffer" not in plain

    add_depot(data)
    data["blocks"][0]["buffer_capacity"] = 2
    for crane in data["berths"][0]["cranes"]:
        crane["buffer_capacity"] = 2
    full = {t.get_text() for t in _legend_of(layout_from_dict(data))}
    assert {"charging station", "transfer point with ground buffer", "yard crane reach"} <= full
    assert not any(t.startswith("transfer point (") for t in full)  # every transfer point has a buffer here


def _legend_of(layout):
    ax = draw(layout)
    _legend(ax, layout.to_json())
    return ax.get_legend().get_texts()
