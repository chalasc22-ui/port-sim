"""render_2d on awkward inputs."""

import pytest

from terminal_layout.loader import layout_from_dict


def test_dollar_sign_in_the_name(data, tmp_path):
    data["name"] = "$a_$ terminal"
    layout_from_dict(data).render_2d(tmp_path / "x.png")
    assert (tmp_path / "x.png").stat().st_size > 5_000


def test_needs_a_path_or_an_axes(demo):
    with pytest.raises(ValueError, match="path to save to or an ax"):
        demo.render_2d(path=None)


def test_narrow_layouts_get_a_sane_figure_size(tmp_path):
    from terminal_layout.render import _new_figure

    fig = _new_figure(1, 400)
    assert fig.get_figheight() <= 14 and 6 <= fig.get_figwidth() <= 18
    wide = _new_figure(500, 2)
    assert wide.get_figheight() >= 3
