"""Dames Point, the poster layout: what it holds, that every journey completes, that nothing drifts."""

import importlib.util
import json
import statistics
import struct
from pathlib import Path

import networkx as nx
import pytest
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle
from matplotlib.quiver import Quiver

from terminal_layout.graph import build_graph
from terminal_layout.render import COLORS, _legend, render_2d

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BLOCK_IDS = (
    [f"{row}{col}" for row in "ABCDE" for col in "123"]  # south-east yard, full rows
    + ["F2", "F3", "G2", "G3"]  # the bottom-left corner is cut by the highway ramp
    + [f"H{n}" for n in range(1, 5)]  # strip yard along the apron
    + [f"N{n}" for n in range(1, 5)]  # north-west yard
)
RTG_CLUSTERS = {
    "RTG1": ["A1", "B1", "C1", "D1", "E1"],
    "RTG2": ["A2", "B2", "C2", "D2", "E2"],
    "RTG3": ["A3", "B3", "C3", "D3", "E3"],
    "RTG4": ["F2", "F3", "G2", "G3"],
    "RTG5": ["H1", "H2", "H3", "H4"],
    "RTG6": ["N1", "N2", "N3", "N4"],
}
CHARGERS = (  # by (x, y): west-column depot CH1 at (3, 29) ... CH6 at (8, 29); inbound-road depot CH7 at (88, 30) ... CH12 at (93, 30)
    [(f"CH{n}", (2 + n, 29)) for n in range(1, 7)] + [(f"CH{n}", (81 + n, 30)) for n in range(7, 13)]
)
QUAY_CRANES = {"B1": {"QC1": 14, "QC2": 31, "QC3": 48}, "B2": {"QC4": 65, "QC5": 82, "QC6": 99}}
BYPASS_FORKS = (57, 76, 90, 96, 104)  # bypass-lane cells that also drop south
YARD_REACH = {  # bounding box of each crane's slots and transfer points, low corner then high corner
    "RTG1": ((2, 12), (17, 26)),
    "RTG2": ((19, 12), (35, 26)),
    "RTG3": ((37, 12), (54, 26)),
    "RTG4": ((19, 6), (54, 11)),
    "RTG5": ((59, 28), (85, 31)),
    "RTG6": ((99, 20), (110, 31)),
}
MAX_LEG = 250  # moves; a gross detour (a closed road, a loop gone wrong) shows up far above this


def load_generator():
    path = Path(__file__).resolve().parent.parent / "tools" / "build_dames_point.py"
    spec = importlib.util.spec_from_file_location("build_dames_point", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, path
    return struct.unpack(">II", data[16:24])


# ---- what it holds -----------------------------------------------------------


def test_size_and_name(dames):
    assert (dames.name, dames.width, dames.height, dames.cell_size_m) == ("dames-point", 112, 43, 15)
    assert dames.warnings == ()


def test_six_quay_cranes_evenly_spaced_on_two_berths(dames):
    assert list(dames.berths) == ["B1", "B2"]
    for berth_id, cranes in QUAY_CRANES.items():
        assert [(c, pos) for c, pos in dames.berths[berth_id]] == [(c, (x, 34)) for c, x in cranes.items()]
    xs = [pos[0] for berth in dames.berths.values() for _, pos in berth]
    assert len({b - a for a, b in zip(xs, xs[1:])}) == 1  # one gap, 17 cells = 255 m


def test_each_quay_crane_has_four_transfer_lanes_beside_the_berth(dames):
    """A typical back reach has four transfer lanes; the vehicle waits in one and the crane lifts to it."""
    for crane in dames.quay_cranes.values():
        assert crane.transfer_points == tuple((crane.x + d, 34) for d in (-1, 0, 1, 2)), crane.id
        assert crane.buffer_capacity == 2
        assert all(dames.cell(x, 35).attrs["berth"] == crane.berth for x, _ in crane.transfer_points)
    assert len(dames.transfer_points(berth_id="B1")) == 12 and len(dames.transfer_points(berth_id="B2")) == 12


def test_berth_is_100_cells_of_the_real_1525_m_split_in_two(dames):
    """B1 and B2 are a placeholder split at x = 55/56 until the ship arrival owner says how many berths."""
    assert len(dames.cells("berth")) == 100  # 1,500 m of the real 1,525 m
    assert len(dames.quay_cells("B1")) == 50 and len(dames.quay_cells("B2")) == 50
    assert dames.cell(55, 35).attrs["berth"] == "B1" and dames.cell(56, 35).attrs["berth"] == "B2"


def test_blocks_slots_and_bays(dames):
    assert sorted(dames.blocks) == sorted(BLOCK_IDS) and len(BLOCK_IDS) == 27
    assert sum(len(dames.slots_in_block(b)) for b in dames.blocks) == 786
    assert len(dames.cells("yard_slot")) == 786
    bays = sum(block.bays for block in dames.blocks.values())
    assert bays == 419 == len(dames.cells("transfer_point")) - 24  # one transfer point per bay, four per crane
    assert {block.max_tier for block in dames.blocks.values()} == {5}


def test_six_yard_cranes_each_serve_a_cluster_of_blocks(dames):
    served = {}
    for block in dames.blocks.values():
        served.setdefault(block.crane, []).append(block.id)
    assert {crane: sorted(ids) for crane, ids in served.items()} == RTG_CLUSTERS


def test_two_gate_in_lanes_and_two_gate_out_lanes(dames):
    assert [(c.attrs["id"], c.pos) for c in dames.gate_cells("in")] == [("G_IN1", (80, 0)), ("G_IN2", (81, 0))]
    assert [(c.attrs["id"], c.pos) for c in dames.gate_cells("out")] == [("G_OUT1", (84, 0)), ("G_OUT2", (85, 0))]


def test_marsh_and_gate_complex_are_blocked_with_a_reason(dames):
    assert (dames.cell(70, 20).type, dames.cell(70, 20).attrs["reason"]) == ("blocked", "exclusion")
    assert (dames.cell(80, 8).type, dames.cell(80, 8).attrs["reason"]) == ("blocked", "building")


def test_the_forks_and_aisles_the_trip_lengths_depend_on(dames):
    """Design pins: the quay and bypass pair, the forks south, the return lane, the aisles, the south road."""
    assert dames.exits(40, 34) == ["E", "S"] and dames.exits(40, 33) == ["N", "E"]  # change lane anywhere
    assert dames.exits(1, 34) == ["E"] and dames.exits(1, 33) == ["N", "E"]        # but not back into the west column
    for x in BYPASS_FORKS:
        assert dames.exits(x, 33) == ["E", "S"], x
    assert dames.exits(57, 32) == ["S"]                   # the outbound road ends at the east column
    assert dames.exits(70, 32) == ["W"]                   # the return lane
    assert dames.exits(86, 32) == ["N", "W"]              # the inbound road meets it
    assert dames.exits(96, 32) == ["S", "W"]              # the exit road crosses it
    assert dames.exits(104, 32) == ["E", "W"]             # and its east branch feeds the north-west yard
    for x in (18, 36):
        assert dames.exits(x, 20) == ["N"], x
        assert dames.exits(x, 32) == ["N", "E"] and dames.exits(x, 33) == ["N", "E"], x
    assert dames.exits(57, 6) == ["S", "W"] and dames.exits(57, 5) == ["E"]   # foot of the east column, south road
    assert dames.exits(80, 5) == ["N", "E"] and dames.exits(88, 5) == ["S"]  # gate-in column crossing; gate-out column
    assert dames.cell(88, 20).type == "blocked"           # the old gate-out column is gone


def test_a_vehicle_waiting_under_a_crane_does_not_block_the_quay(dames):
    """With all four of QC1's lanes occupied, traffic to QC2 still gets past on the bypass lane."""
    busy = dames.with_closures(set(dames.quay_cranes["QC1"].transfer_points))
    assert busy.warnings == ()
    assert len(dames.shortest_path((6, 34), (31, 34))) - 1 == 25
    assert len(busy.shortest_path((6, 34), (31, 34))) - 1 == 27  # two moves more: down to the bypass and back


def required_trips(dames):
    """The four kinds of trip the other modules make, as move counts (None if there is no route).

    Gate in -> bay and crane -> bay, then bay -> gate out and bay -> crane: 8 x 419 each way.
    """
    graph = build_graph(dames)
    backward = graph.reverse(copy=False)
    cranes = [pos for berth in dames.berths.values() for _, pos in berth]
    gates_in = [c.pos for c in dames.gate_cells("in")]
    gates_out = [c.pos for c in dames.gate_cells("out")]
    bays = [p for block in dames.blocks.values() for p in block.transfer_points]
    trips = []
    for source in gates_in + cranes:
        reach = nx.single_source_shortest_path_length(graph, source)
        trips += [reach.get(bay) for bay in bays]
    for target in gates_out + cranes:
        reach = nx.single_source_shortest_path_length(backward, target)
        trips += [reach.get(bay) for bay in bays]
    return trips


# ---- journeys ---------------------------------------------------------------


def test_no_trip_between_a_gate_a_crane_and_a_bay_is_longer_than_250_moves(dames):
    """Every leg in both directions: gate in -> bay, bay -> gate out, crane <-> bay, gate <-> crane."""
    graph = build_graph(dames)
    cranes = [pos for berth in dames.berths.values() for _, pos in berth]
    gates_in = [c.pos for c in dames.gate_cells("in")]
    gates_out = [c.pos for c in dames.gate_cells("out")]
    bays = [p for block in dames.blocks.values() for p in block.transfer_points]
    assert len(bays) == 419
    legs = []  # (from, to, moves)
    for source in gates_in + cranes:
        reach = nx.single_source_shortest_path_length(graph, source)
        legs += [(source, t, reach.get(t)) for t in bays + cranes + gates_out if t != source]
    backward = graph.reverse(copy=False)
    for target in gates_out + cranes:
        reach = nx.single_source_shortest_path_length(backward, target)
        legs += [(s, target, reach.get(s)) for s in bays if s != target]
    missing = [leg for leg in legs if leg[2] is None]
    assert not missing, missing[:3]
    longest = max(legs, key=lambda leg: leg[2])
    assert longest[2] <= MAX_LEG, longest


def test_a_crane_reaches_the_block_beside_it_without_circling_the_site(dames):
    """The quay flows east, so the way down to the yard is the fork at x = 57 into the east column.

    Without it this trip is 166 moves instead of 108.
    """
    qc1 = dict(dames.berths["B1"])["QC1"]
    a1 = dames.blocks["A1"].transfer_points
    assert len(dames.shortest_path(qc1, a1[0])) - 1 < 150
    assert len(dames.shortest_path(a1[0], qc1)) - 1 < 60  # back by the west column


def test_the_east_cranes_reach_the_yard_by_the_return_lane(dames):
    """QC4 to QC6 drop onto the return lane (y = 32) at x = 76, 90 or 104 and ride it west to the east column."""
    qc4 = dict(dames.berths["B2"])["QC4"]
    path = dames.shortest_path(qc4, (57, 32))
    assert len(path) - 1 == 32 and (76, 33) in path and (76, 32) in path
    qc6 = dict(dames.berths["B2"])["QC6"]
    assert len(dames.shortest_path(qc6, (57, 32))) - 1 == 54


def test_gate_in_to_gate_out_is_a_short_hop(dames):
    assert len(dames.shortest_path((80, 0), (84, 0))) - 1 < 100


def test_north_west_yard_is_reached_without_circling_the_site(dames):
    """The return lane's east branch (x = 104 on, y = 32) feeds it; without it the way in is over 200 moves."""
    for block_id in ("N1", "N4"):
        point = dames.blocks[block_id].transfer_points[0]
        assert len(dames.shortest_path((80, 0), point)) - 1 < 100


@pytest.mark.parametrize("cell", [(80, 3), (57, 20)])  # the gate-in column, the east column
def test_closing_a_road_cell_is_reported_as_a_warning(dames, cell):
    closed = dames.with_closures({cell})
    assert closed.warnings and all(w.startswith("rule 12:") for w in closed.warnings)
    assert dames.warnings == ()


def test_trip_lengths_are_the_ones_measured_for_the_v0_2_circulation(dames):
    """Recomputed independently from the compiled file (a separate script that imports nothing from here).

    v0.1 (one quay lane, no return lane) measured 15 / 86 / 228, mean 90.1.
    """
    trips = required_trips(dames)
    assert len(trips) == 6704 and None not in trips
    assert (min(trips), statistics.median(trips), max(trips)) == (15, 84, 211)
    assert round(statistics.mean(trips), 1) == 87.6
    qc1 = dict(dames.berths["B1"])["QC1"]
    assert len(dames.shortest_path(qc1, dames.blocks["A1"].transfer_points[0])) - 1 == 108


# ---- charging, buffers and yard cranes ------------------------------------------------


def test_twelve_charging_stations_in_two_depots(dames):
    assert [(c.attrs["id"], c.pos) for c in dames.charging_stations()] == CHARGERS
    assert {c.exits for c in dames.charging_stations()} == {("N",)}  # in from the access lane, out to the exit lane
    assert dames.validate() == []


def test_each_depot_is_a_loop_so_twelve_parked_vehicles_cut_nothing_off(dames):
    """Rule 15 on the real map: close every station as if each held a vehicle and nothing is stranded."""
    stations = {c.pos for c in dames.charging_stations()}
    assert dames.with_closures(stations).warnings == ()


@pytest.mark.parametrize("x, fork_y", [(1, 28), (86, 29)])  # west column, inbound road
def test_a_depot_is_not_a_shortcut(dames, x, fork_y):
    """From the fork to the cell where the exit lane rejoins: 2 moves on the road, 6 through the depot."""
    assert len(dames.shortest_path((x, fork_y), (x, fork_y + 2))) - 1 == 2
    assert len(dames.with_closures({(x, fork_y + 1)}).shortest_path((x, fork_y), (x, fork_y + 2))) - 1 == 6


def test_the_depots_sit_where_the_p_median_solve_put_them(dames):
    """tools/charger_siting.py: the best two sites are beside the west column and the inbound road."""
    assert dames.exits(1, 28) == ["N", "E"] and dames.exits(86, 29) == ["N", "E"]


def test_the_passing_lane_gets_a_vehicle_past_a_queue(dames):
    """With vehicles waiting in front of CH1 to CH3, the passing lane (y = 27) still reaches CH4."""
    queue = {(3, 28), (4, 28), (5, 28)}
    path = dames.with_closures(queue).shortest_path((1, 28), (6, 29))
    assert path is not None and (4, 27) in path and (6, 28) in path
    access = [x for x in range(3, 10) if dames.cell(x, 28).type == "lane"]   # beyond the entrance cell (2, 28)
    passing = [x for x in range(2, 10) if dames.cell(x, 27).type == "lane"]
    assert len(access) + len(passing) == 15  # cells a queue can stand on before it blocks the entrance (7 in v0.1)
    assert dames.exits(2, 28) == ["E", "S"] and dames.exits(5, 27) == ["N", "E"]


def test_every_transfer_point_has_a_finite_buffer_of_two(dames):
    """PortSim (the course's reference simulator) models a finite transfer buffer of 2 containers; a full
    buffer makes the next vehicle wait. 0 stays available for a coupled handover."""
    assert {c.attrs["buffer_capacity"] for c in dames.cells("transfer_point")} == {2}
    assert {b.buffer_capacity for b in dames.blocks.values()} == {2}
    assert {c.buffer_capacity for c in dames.quay_cranes.values()} == {2}


def test_each_yard_crane_has_its_blocks_and_its_reach(dames):
    assert {k: v.reach for k, v in dames.yard_cranes.items()} == YARD_REACH
    assert {k: list(v.blocks) for k, v in dames.yard_cranes.items()} == RTG_CLUSTERS


def test_a_crane_reach_box_holds_every_slot_and_transfer_point_of_its_blocks(dames):
    for crane in dames.yard_cranes.values():
        (x0, y0), (x1, y1) = crane.reach
        for block_id in crane.blocks:
            points = [dames.slot(s).pos for s in dames.slots_in_block(block_id)]
            points += [p.pos for p in dames.transfer_points(block_id=block_id)]
            assert all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points), (crane.id, block_id)


# ---- the files ----------------------------------------------------------------


def test_compiled_file_is_up_to_date(dames, dames_path, tmp_path):
    compiled_path = dames_path.with_name("dames-point.compiled.json")
    assert json.loads(compiled_path.read_text()) == dames.to_json()
    again = tmp_path / "again.json"
    dames.to_json(again)
    assert again.read_text() == compiled_path.read_text()


def test_files_are_what_the_generator_writes(dames_path, tmp_path, capsys):
    """The layout files are generated; a hand edit would be lost the next time the script runs."""
    module = load_generator()
    assert module.main(["build_dames_point.py", str(tmp_path / "dames-point.json")]) == 0
    assert capsys.readouterr().out.strip().endswith(", 0 warnings")
    for name in ("dames-point.json", "dames-point.compiled.json"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (dames_path.parent / name).read_text(
            encoding="utf-8"
        ), f"layouts/{name} differs from tools/build_dames_point.py: edit the script and run it"


def test_a_layout_that_does_not_load_writes_nothing(tmp_path, capsys):
    module = load_generator()
    module.RETURN_FORKS = []  # leaves the return lane east of the exit road with nothing feeding it: rule 6
    out = tmp_path / "broken.json"
    assert module.main(["build_dames_point.py", str(out)]) == 1
    assert list(tmp_path.iterdir()) == []
    assert "rule 6" in capsys.readouterr().out


# ---- the pictures -------------------------------------------------------------


def test_render_draws_every_block_crane_gate_and_exit(dames):
    fig = Figure()
    ax = fig.add_subplot(111)
    render_2d(dames.to_json(), ax=ax)
    labels = {t.get_text() for t in ax.texts}
    assert set(BLOCK_IDS) <= labels
    assert {f"QC{n}" for n in range(1, 7)} <= labels
    assert {"G_IN1", "G_IN2", "G_OUT1", "G_OUT2"} <= labels
    exits = sum(len(dames.exits(x, y)) for x in range(dames.width) for y in range(dames.height))
    arrows = [c for c in ax.collections if isinstance(c, Quiver)]
    assert len(arrows) == 1 and len(arrows[0].get_offsets()) == exits


def test_render_draws_the_stations_the_buffers_and_each_crane_reach(dames):
    compiled = dames.to_json()
    fig = Figure()
    ax = fig.add_subplot(111)
    render_2d(compiled, ax=ax)
    assert {f"RTG{n}" for n in range(1, 7)} <= {t.get_text() for t in ax.texts}
    assert len([p for p in ax.patches if isinstance(p, Polygon)]) == 12  # one bolt per station
    assert len([p for p in ax.patches if isinstance(p, Rectangle)]) == 6  # one reach box per yard crane
    hatched = [c for c in ax.collections if c.get_hatch() == "////"]
    assert len(hatched) == 1 and len(hatched[0].get_paths()) == 443  # every transfer point has a buffer of 2
    _legend(ax, compiled)
    legend = {t.get_text() for t in ax.get_legend().get_texts()}
    assert legend >= {"charging station", "transfer point with ground buffer", "yard crane reach"}


def test_render_shows_the_marsh_in_its_own_colour(dames):
    compiled = dames.to_json()
    fig = Figure()
    ax = fig.add_subplot(111)
    render_2d(compiled, ax=ax)
    pixels = ax.images[0].get_array()
    marsh, building = tuple(pixels[20][70]), tuple(pixels[8][80])
    unused, slot = tuple(pixels[10][100]), tuple(pixels[25][10])  # (100, 10) is open ground; (10, 25) a yard slot
    assert dames.cell(100, 10).type == "blocked" and dames.cell(10, 25).type == "yard_slot"
    assert marsh == pytest.approx(to_rgb(COLORS["blocked_exclusion"]))
    assert building == pytest.approx(to_rgb(COLORS["blocked_building"]))
    assert unused == pytest.approx(to_rgb(COLORS["blocked_unused"]))
    assert len({marsh, building, unused, slot}) == 4
    _legend(ax, compiled)
    assert {t.get_text() for t in ax.get_legend().get_texts()} >= {"exclusion zone", "building", "unused"}


def test_render_to_a_file_matches_the_picture_in_the_docs(dames, dames_path, tmp_path):
    out = tmp_path / "dames.png"
    dames.render_2d(out)
    width, height = png_size(out)
    assert width > 1500 and height > 500
    docs_width, docs_height = png_size(dames_path.parent.parent / "docs" / "dames-point.png")
    assert abs(docs_width - width) <= 0.03 * width and abs(docs_height - height) <= 0.03 * height  # same picture


def test_the_trace_picture_is_there(dames_path):
    width, height = png_size(dames_path.parent / "dames-point-trace.png")
    assert width > 1000 and height > 400


def test_yard_capacity_counts_three_container_rows_per_cell_row(dames):
    """A two-row block is 30 m across: six containers wide, a common 6 + 1 RTG span."""
    assert {b.container_rows for b in dames.blocks.values()} == {3}
    assert sum(b.capacity for b in dames.blocks.values()) == 786 * 3 * 5 == 11790
