"""Rules 1, 2, 3 and 10: problems in the authored file itself."""


def only(lines, rule):
    return [line for line in lines if line.startswith(f"rule {rule}:")]


def test_rule_1_lane_outside_grid(broken):
    errors = broken(lambda d: d["lanes"].append({"rect": [[10, 3], [12, 3]], "exits": ["E"]}))
    assert errors == ["rule 1: lanes[6] rect [[10, 3], [12, 3]] lies outside the 12 x 8 grid"]


def test_rule_1_negative_coordinate(broken):
    errors = broken(lambda d: d.__setitem__("blocked", [{"rect": [[-1, 0], [0, 0]]}]))
    assert errors == ["rule 1: blocked[0] rect [[-1, 0], [0, 0]] lies outside the 12 x 8 grid"]


def test_rule_1_reversed_rect(broken):
    errors = broken(lambda d: d["lanes"].__setitem__(3, {"rect": [[11, 1], [2, 1]], "exits": ["W"]}))
    assert "rule 1: lanes[3] rect [[11, 1], [2, 1]] must list the low corner first" in errors


def test_rule_1_gate_and_crane_outside(broken):
    def edit(d):
        d["gates"][0]["cell"] = [10, -1]
        d["berths"][0]["cranes"][1]["x"] = 20

    errors = broken(edit)
    assert "rule 1: gate G_IN cell (10, -1) lies outside the 12 x 8 grid" in errors
    assert "rule 1: berth B1 crane QC2 x 20 lies outside the 12 x 8 grid" in errors


def test_rule_2_block_overlaps_lane(broken):
    errors = broken(lambda d: d["lanes"].append({"rect": [[4, 3], [4, 3]], "exits": ["N"]}))
    assert errors == ["rule 2: lanes[6] and block A both cover cell (4, 3)"]


def test_rule_2_many_cells_reported_once_per_pair(broken):
    errors = broken(lambda d: d["lanes"].append({"rect": [[2, 2], [9, 3]], "exits": ["N"]}))
    assert errors == ["rule 2: lanes[6] and block A both cover cell (2, 2) (and 15 more)"]


def test_rule_2_gate_on_lane(broken):
    errors = broken(lambda d: d["gates"][1].__setitem__("cell", [1, 1]))
    assert "rule 2: lanes[4] and gate G_OUT both cover cell (1, 1)" in errors


def test_closure_on_a_lane_is_not_an_overlap(broken):
    errors = broken(lambda d: d.__setitem__("closures", [[0, 3]]))
    assert only(errors, 2) == []


def test_rule_3_duplicate_ids(broken):
    def edit(d):
        d["gates"][1]["id"] = "G_IN"
        d["berths"][0]["cranes"][1]["id"] = "QC1"

    errors = broken(edit)
    assert "rule 3: gate id 'G_IN' is used 2 times (gates[0], gates[1])" in errors
    assert "rule 3: crane id 'QC1' is used 2 times (berths[0].cranes[0], berths[0].cranes[1])" in errors


def test_rule_3_unknown_berth(broken):
    errors = broken(lambda d: d["lanes"][0].__setitem__("berth", "B9"))
    assert "rule 3: lanes[0] refers to unknown berth 'B9'" in errors


def test_rule_10_bad_enum_and_typo(broken):
    def edit(d):
        d["gates"][0]["flow"] = "sideways"
        d["lanes"][0]["exist"] = ["E"]

    errors = broken(edit)
    assert any(e.startswith("rule 10: gates[0].flow") for e in errors)
    assert any(e.startswith("rule 10: lanes[0].exist") for e in errors)


def test_rule_10_float_coordinate_and_wrong_service_side(broken):
    def edit(d):
        d["gates"][0]["cell"] = [10.5, 0]
        d["blocks"][0]["service_side"] = "E"

    errors = broken(edit)
    assert any(e.startswith("rule 10: gates[0].cell") for e in errors)
    assert any("orientation EW needs service_side N or S" in e for e in errors)


def test_rule_10_quay_lane_needs_berth(broken):
    errors = broken(lambda d: d["lanes"][0].pop("berth"))
    assert any("a lane of kind 'quay' needs a berth id" in e for e in errors)


def test_every_problem_in_a_stage_is_reported_together(broken):
    def edit(d):
        d["lanes"].append({"rect": [[10, 3], [12, 3]], "exits": ["E"]})
        d["gates"][1]["id"] = "G_IN"

    errors = broken(edit)
    assert len(errors) == 2
    assert errors[0].startswith("rule 1:") and errors[1].startswith("rule 3:")


def test_later_stages_wait_for_earlier_ones(broken):
    def edit(d):
        d["gates"][0]["flow"] = "sideways"
        d["lanes"].pop(3)  # would be a rule 4/5/6/7 problem

    assert all(e.startswith("rule 10:") for e in broken(edit))
