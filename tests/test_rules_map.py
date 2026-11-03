"""Rules 4 to 9 and 13: problems found on the compiled map."""


def rules(errors):
    return sorted({int(e.split(":")[0].split()[1]) for e in errors})


def test_rule_4_exit_leads_off_the_grid(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(5, {"rect": [[0, 1], [0, 4]], "exits": ["W"]}))
    assert "rule 4: lane cell (0, 1) exit W leads outside the grid" in errors


def test_rule_4_exit_leads_to_unusable_cell(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(1, {"rect": [[11, 5], [11, 5]], "exits": ["N"]}))
    assert (
        "rule 4: lane cell (11, 5) exit N leads to (11, 6), "
        "which is not traversable (it is a berth cell)"
    ) in errors


def test_rule_4_exit_into_a_yard_slot(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(3, {"rect": [[2, 1], [11, 1]], "exits": ["N"]}))
    assert (
        "rule 4: transfer point cell (2, 1) exit N leads to (2, 2), "
        "which is not traversable (it is a yard slot cell)"
    ) in errors


def test_rule_5_dead_end(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(1, {"rect": [[11, 5], [11, 5]], "exits": []}))
    assert "rule 5: lane cell (11, 5) has no usable exit (dead end)" in errors


def test_rule_5_gate_out_is_exempt(demo):
    assert not [e for e in demo.validate() if e.startswith("rule 5")]


def test_rule_5_gate_in_with_no_exit_is_a_dead_end(broken):
    errors = broken(lambda d: d["gates"][0].__setitem__("exits", []))
    assert "rule 5: gate cell (10, 0) has no usable exit (dead end)" in errors


def test_rule_6_internal_network_split(broken):
    # Cut the loop: the service lane no longer reaches column x=0.
    errors = broken(lambda d: d["lanes"].__setitem__(4, {"rect": [[1, 1], [1, 1]], "exits": ["S"]}))
    assert any(e.startswith("rule 6: the internal network is not strongly connected") for e in errors)


def test_rule_6_names_an_unreachable_pair(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(4, {"rect": [[1, 1], [1, 1]], "exits": ["S"]}))
    assert errors == [
        "rule 6: the internal network is not strongly connected (no path from (0, 2) to (0, 1))"
    ]


def test_rule_7_gate_in_cannot_reach_network(broken):
    errors = broken(lambda d: d["gates"][0].__setitem__("exits", ["E"]))
    assert "rule 7: gate G_IN at (10, 0) cannot reach the internal network" in errors
    assert rules(errors) == [4, 5, 7]


def test_rule_7_no_gate_out(broken):
    errors = broken(lambda d: d.__setitem__("gates", d["gates"][:1]))
    assert "rule 7: there is no open gate-out, so no internal cell can reach one" in errors


def test_rule_7_cells_cannot_leave(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(4, {"rect": [[1, 1], [1, 1]], "exits": ["W"]}))
    assert any(e.startswith("rule 7:") and "cannot reach any gate-out" in e for e in errors)


def test_rule_7_gate_out_nothing_leads_into(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(4, {"rect": [[1, 1], [1, 1]], "exits": ["W"]}))
    assert "rule 7: gate G_OUT at (1, 0) cannot be reached from the internal network" in errors


def test_rule_7_names_only_the_gate_out_that_is_cut_off(broken):
    def add_second_gate_out(d):
        d["gates"].append({"id": "G_OUT2", "cell": [11, 0], "flow": "out", "exits": []})

    errors = broken(add_second_gate_out)
    assert errors == ["rule 7: gate G_OUT2 at (11, 0) cannot be reached from the internal network"]


def test_rule_8_no_lane_on_service_side(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(3, {"rect": [[2, 1], [4, 1]], "exits": ["W"]}))
    assert (
        "rule 8: block A bay 4 has no lane cell on service side S "
        "(expected at (5, 1), found a blocked cell)"
    ) in errors


def test_rule_8_crane_not_over_a_quay_cell(broken):
    errors = broken(lambda d: d["berths"][0]["cranes"].__setitem__(0, {"id": "QC1", "x": 11}))
    assert (
        "rule 8: berth B1 crane QC1 has no quay cell of berth B1 under it "
        "(expected at (11, 5), found a lane cell)"
    ) in errors


def test_rule_8_two_cranes_on_one_cell(broken):
    errors = broken(lambda d: d["berths"][0]["cranes"].__setitem__(1, {"id": "QC2", "x": 3}))
    assert any(e.startswith("rule 8: berth B1 crane QC2 shares its quay cell (3, 5)") for e in errors)


def test_rule_9_holds_for_every_demo_transfer_point(demo):
    assert not [e for e in demo.validate() if e.startswith("rule 9")]


def test_rule_13_berth_without_cranes(broken):
    errors = broken(lambda d: d["berths"][0].__setitem__("cranes", []))
    assert "rule 13: berth B1 has no transfer point (no crane is placed on it)" in errors


def test_rule_13_block_without_transfer_point(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(3, {"rect": [[10, 1], [11, 1]], "exits": ["W"]}))
    assert "rule 13: block A has no transfer point" in errors
    assert sum(e.startswith("rule 8: block A bay") for e in errors) == 8


def test_errors_come_out_in_rule_order(broken):
    def edit(d):
        d["berths"][0]["cranes"] = []
        d["lanes"].__setitem__(1, {"rect": [[11, 5], [11, 5]], "exits": []})

    numbers = [int(e.split(":")[0].split()[1]) for e in broken(edit)]
    assert numbers == sorted(numbers)


def test_rule_8_service_cell_off_the_grid_says_so(broken):
    def edit(d):
        d["blocks"][0]["origin"] = [2, 0]  # service row would be y = -1
        d["lanes"] = [lane for lane in d["lanes"] if lane["rect"][0] != [2, 1]]

    errors = broken(edit)
    assert any("(expected at (2, -1), which is outside the grid)" in e for e in errors)
