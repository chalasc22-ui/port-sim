"""The 12 x 8 demo layout: its numbers are the ones written in the spec."""

import json
from collections import Counter

from terminal_layout import load_layout


def test_loads_clean(demo):
    assert demo.validate() == []
    assert demo.warnings == ()
    assert (demo.name, demo.width, demo.height, demo.cell_size_m) == ("demo-12x8", 12, 8, 15)


def test_cell_counts_by_type(demo):
    counts = Counter(c.type for c in demo.cells())
    assert sum(counts.values()) == 96
    assert counts["water"] == 12
    assert counts["berth"] == 12
    assert counts["yard_slot"] == 16
    assert counts["transfer_point"] == 10  # 8 block bays + 2 quay cranes
    assert counts["gate"] == 2
    assert counts["quay"] == 9  # 11 quay cells minus the 2 under cranes
    # service lane x=10,11 (the 8 bays are transfer points) + (1,1) + column x=0 (4) + column x=11 (4)
    assert counts["lane"] == 11
    assert counts["blocked"] == 24


def test_blocks_and_slots(demo):
    block = demo.blocks["A"]
    assert (block.bays, block.rows, block.max_tier, block.cargo) == (8, 2, 5, "general")
    assert len(block.slots) == 16
    assert block.slots[0] == "A-01-01" and block.slots[-1] == "A-08-02"
    assert demo.slot("A-03-02").pos == (4, 3)
    assert demo.slots_in_block("A") == list(block.slots)


def test_transfer_points(demo):
    assert [c.pos for c in demo.transfer_points(block_id="A")] == [(x, 1) for x in range(2, 10)]
    assert [c.pos for c in demo.transfer_points(berth_id="B1")] == [(3, 5), (8, 5)]
    assert demo.transfer_point_for("A-03-02").pos == (4, 1)
    assert demo.berths == {"B1": (("QC1", (3, 5)), ("QC2", (8, 5)))}


def test_gates_and_quay(demo):
    assert [g.attrs["id"] for g in demo.gate_cells("in")] == ["G_IN"]
    assert [g.attrs["id"] for g in demo.gate_cells("out")] == ["G_OUT"]
    assert len(demo.quay_cells()) == 11
    assert len(demo.quay_cells("B1")) == 11


def test_a_truck_can_get_in_and_out(demo):
    path = demo.shortest_path((10, 0), (1, 0))
    assert path[0] == (10, 0) and path[-1] == (1, 0)
    assert len(path) == 12


def test_quay_is_reachable_from_the_yard(demo):
    assert demo.shortest_path((4, 1), (3, 5)) is not None


def test_compiled_file_is_up_to_date(demo, demo_path):
    on_disk = json.loads(demo_path.with_name("demo-12x8.compiled.json").read_text())
    assert on_disk == demo.to_json()


def test_loading_twice_gives_equal_results(demo_path):
    assert load_layout(demo_path).to_json() == load_layout(demo_path).to_json()
