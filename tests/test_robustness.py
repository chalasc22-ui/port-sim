"""Bad input must give a LayoutError (never a traceback), and Layouts must copy and pickle."""

import copy
import json
import pickle

import pytest

from terminal_layout import LayoutError, load_layout
from terminal_layout.loader import layout_from_dict


def load_text(tmp_path, text, raw=False):
    path = tmp_path / "layout.json"
    path.write_bytes(text if raw else text.encode())
    return load_layout(path)


def test_not_utf8(tmp_path):
    with pytest.raises(LayoutError, match="not UTF-8"):
        load_text(tmp_path, b"\xff\xfe\x00bad", raw=True)


def test_deeply_nested_json(tmp_path):
    with pytest.raises(LayoutError, match="nested too deeply"):
        load_text(tmp_path, "[" * 100_000)


def test_not_json(tmp_path):
    with pytest.raises(LayoutError, match="not valid JSON"):
        load_text(tmp_path, "{ nope")


def test_duplicate_keys_are_an_error(tmp_path, demo_path):
    text = demo_path.read_text().rstrip().rstrip("}") + ', "lanes": []}'
    with pytest.raises(LayoutError, match="key 'lanes' appears twice"):
        load_text(tmp_path, text)


@pytest.mark.parametrize("top", [[], None, 3, "layout"])
def test_top_level_must_be_an_object(top):
    with pytest.raises(LayoutError, match="top level must be a JSON object"):
        layout_from_dict(top)


def test_grid_size_is_capped(data):
    data["width"] = 120_000
    with pytest.raises(LayoutError) as info:
        layout_from_dict(data)
    assert info.value.errors == (
        "rule 10: width: Input should be less than or equal to 500",
    )


@pytest.mark.parametrize("bad", ["15", True, float("inf"), float("nan"), 0, -3])
def test_cell_size_must_be_a_real_positive_number(data, bad):
    data["cell_size_m"] = bad
    with pytest.raises(LayoutError, match="rule 10: cell_size_m"):
        layout_from_dict(data)


def test_speed_must_be_a_number(data):
    data["lanes"][0]["speed_kph"] = True
    with pytest.raises(LayoutError, match="rule 10: lanes\\[0\\].speed_kph"):
        layout_from_dict(data)


def test_float_cell_size_is_kept(data):
    data["cell_size_m"] = 7.5
    assert layout_from_dict(data).cell_size_m == 7.5
    data["cell_size_m"] = 15.0
    assert layout_from_dict(data).cell_size_m == 15


def test_notes_are_allowed_everywhere_and_ignored(data, demo):
    data["notes"] = "traced from satellite view, Oct 2026"
    data["lanes"][0]["notes"] = "main quay road"
    data["blocks"][0]["notes"] = "block 3"
    assert layout_from_dict(data).to_json() == demo.to_json()


def test_unknown_keys_still_rejected(data):
    data["lanes"][0]["comment"] = "oops"
    with pytest.raises(LayoutError, match="rule 10: lanes\\[0\\].comment"):
        layout_from_dict(data)


def test_block_crane_id_may_not_reuse_a_quay_crane_id(data):
    data["blocks"][0]["crane"] = "QC1"
    with pytest.raises(LayoutError) as info:
        layout_from_dict(data)
    assert info.value.errors == ("rule 3: blocks[0] crane 'QC1' is also a quay crane id",)


def test_pickle_round_trip(demo):
    clone = pickle.loads(pickle.dumps(demo))
    assert clone.to_json() == demo.to_json()
    closed = demo.with_closures({(5, 1)})
    clone = pickle.loads(pickle.dumps(closed))
    assert clone.closures == {(5, 1)} and clone.warnings == closed.warnings


def test_cells_pickle(demo):
    cell = demo.cell(4, 1)
    clone = pickle.loads(pickle.dumps(cell))
    assert clone == cell and clone.attrs["serves"] == {"block": "A", "bay": 3}
    with pytest.raises(TypeError):
        clone.attrs["serves"]["bay"] = 1


def test_copy_and_deepcopy(demo):
    assert copy.copy(demo) is demo
    assert copy.deepcopy(demo) is demo
    assert copy.deepcopy(demo.cell(4, 1)) == demo.cell(4, 1)


def test_layout_error_survives_pickle(demo):
    with pytest.raises(LayoutError) as info:
        demo.with_closures({(4, 3), (0, 7)})
    clone = pickle.loads(pickle.dumps(info.value))
    assert clone.errors == info.value.errors and str(clone) == str(info.value)


def test_numpy_style_integers_are_accepted_but_floats_and_bools_are_not(demo):
    class Index:  # stands in for numpy integers: has __index__
        def __init__(self, n):
            self.n = n

        def __index__(self):
            return self.n

    assert demo.with_closures({(Index(5), Index(1))}).closures == {(5, 1)}
    assert demo.shortest_path((Index(10), Index(0)), (1, 0))[0] == (10, 0)
    for bad in [(10.0, 0), (True, 0), (10.5, 0)]:
        with pytest.raises(TypeError):
            demo.shortest_path(bad, (1, 0))


def test_slot_error_explains_tiers(demo):
    with pytest.raises(KeyError, match="ground slot"):
        demo.slot("A-03-02-1")
