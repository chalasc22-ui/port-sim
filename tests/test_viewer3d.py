"""The 3D viewer's demo routes: generated from the layout, and every step a legal move."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTES = ROOT / "viewer3d" / "demo-routes.json"


@pytest.fixture(scope="module")
def routes():
    return json.loads(ROUTES.read_text())["routes"]


def test_every_step_of_every_route_is_a_legal_move(dames, routes):
    assert len(routes) == 7
    for r in routes:
        path = [tuple(p) for p in r["path"]]
        steps = list(zip(path, path[1:])) + ([(path[-1], path[0])] if r["loop"] else [])
        bad = [(a, b) for a, b in steps if b not in dames.neighbors(*a)]
        assert not bad, (r["id"], bad[:3])


def test_stops_sit_where_the_route_says(dames, routes):
    for r in routes:
        for stop in r["stops"]:
            cell = dames.cell(*r["path"][stop["at"]])
            assert cell.type in ("transfer_point", "charging_station", "gate"), (r["id"], stop)


def test_routes_file_is_what_the_generator_writes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bvr", ROOT / "tools" / "build_viewer_routes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = ROUTES.read_text()
    assert module.main() == 0
    same = ROUTES.read_text() == before         # compared first: pytest's diff of two long files takes minutes
    assert same, "viewer3d/demo-routes.json is out of date: run tools/build_viewer_routes.py"


def test_the_viewer_reads_only_the_compiled_file_the_routes_and_the_replay():
    page = (ROOT / "viewer3d" / "index.html").read_text()
    assert "../layouts/dames-point.compiled.json" in page and "demo-routes.json" in page and "demo-replay.json" in page
    assert "dames-point.json\"" not in page  # never the authored file
    # the layout, the routes, one helper for the replay files, and the control room's two requests to its own
    # simulator: a change made in its panel, and the other runs the guided tour plays
    assert page.count("fetch(") == 5 and page.count('fetch(CONTROL.api + "/run"') == 2
    assert "terminal-replay.json" in page and "terminal-variants.json" in page


def test_the_lettering_in_the_picture_is_only_ids_and_in_and_out():
    """No real name or mark is drawn in the scene: every sign is an id from the layout or the run, or says IN or OUT."""
    page = (ROOT / "viewer3d" / "index.html").read_text()
    assert page.count("fillText(") == 1                                    # one place draws letters: signMat
    calls = [page[i + len("signMat("):i + 60] for i in range(len(page)) if page.startswith("signMat(", i) and not page.startswith("const signMat", i - 6)]
    allowed = ('"IN"', '"OUT"', "c.id)", "yc.id)", 'String(id).replace(/[^0-9A-Za-z]/g, "")')
    assert len(calls) == 5 and all(call.startswith(allowed) for call in calls), calls


def test_the_picture_quality_can_be_chosen_in_the_panel_and_from_outside():
    page = (ROOT / "viewer3d" / "index.html").read_text()
    menu = page.split('<select id="quality">')[1].split("</select>")[0]
    assert [o.split('"')[0] for o in menu.split('<option value="')[1:]] == ["auto", "0", "2", "4"]
    levels = page.split("const QUALITY = [")[1].split("];")[0]
    assert levels.startswith("[1, 4096]") and levels.count("[") == 5 and levels.rstrip().endswith("[0.5, 1024]")      # full first, lightest last
    assert "setQuality: chooseQuality" in page and "MeshLambertMaterial" not in page


def bundler():
    spec = importlib.util.spec_from_file_location("bundle_viewer", ROOT / "tools" / "bundle_viewer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bundle_is_the_page_with_its_data(tmp_path):
    module = bundler()
    out = tmp_path / "bundle.html"
    assert module.main(["bundle_viewer.py", str(out)]) == 0
    same = out.read_text(encoding="utf-8") == (ROOT / "viewer3d" / "dames-point-3d.html").read_text(encoding="utf-8")
    assert same, "viewer3d/dames-point-3d.html is out of date: run tools/bundle_viewer.py"


def test_the_one_file_page_asks_the_network_for_nothing(tmp_path):
    module = bundler()
    assert module.missing() == []                                           # Three.js and seven font files are kept in viewer3d/vendor/
    page = (ROOT / "viewer3d" / "dames-point-3d.html").read_text(encoding="utf-8")
    assert module.loads_from_the_web(page) == []
    for host in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com"):
        assert host not in page, host
    assert "<script>/* three.min.js, from viewer3d/vendor/" in page and "<script>/* OrbitControls.js, from viewer3d/vendor/" in page
    assert page.count("@font-face{") == 7 and page.count("data:font/woff2;base64,") == 7
    for family, weight, _ in module.FONTS:                                  # every face the page's style sheet asks Google Fonts for
        assert f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};" in page
    source = (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")
    asked = source.split("fonts.googleapis.com/css2?")[1].split('"')[0]
    wanted = set()
    for part in asked.split("&"):
        if part.startswith("family="):
            family, weights = part[7:].split(":wght@")
            wanted |= {(family.replace("+", " "), int(w)) for w in weights.split(";")}
    assert wanted == {(family, weight) for family, weight, _ in module.FONTS} and len(wanted) == 7
    licences = sorted(p.name for p in (ROOT / "viewer3d" / "vendor").rglob("LICENSE*"))
    assert licences == ["LICENSE-barlow.txt", "LICENSE-big-shoulders-stencil-display.txt", "LICENSE-ibm-plex-mono.txt", "LICENSE-three.txt"]
    # the same page for the web: its addresses are kept, and it is a megabyte smaller
    cdn, fragment = tmp_path / "cdn.html", tmp_path / "fragment.html"
    assert module.main(["bundle_viewer.py", str(cdn), "--cdn"]) == 0 and module.main(["bundle_viewer.py", str(fragment), "--fragment"]) == 0
    assert len(module.loads_from_the_web(cdn.read_text(encoding="utf-8"))) == 5 and len(module.loads_from_the_web(fragment.read_text(encoding="utf-8"))) == 5
    assert len(page) - len(cdn.read_text(encoding="utf-8")) > 700_000 and not fragment.read_text(encoding="utf-8").lstrip().startswith("<!doctype")


def test_a_page_that_loads_anything_else_from_the_web_is_refused():
    module = bundler()
    source = (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="would still load from the web"):
        module.self_contained(source.replace("<!-- BUNDLE:DATA -->", '<script src="https://example.com/extra.js"></script>'))
    with pytest.raises(ValueError, match="does not load"):
        module.self_contained(source.replace("three@0.147.0/build", "three@0.150.0/build"))


def test_the_agv_stub_completes_both_jobs_using_only_the_api(dames):
    """The integration smoke test from the timeline: a job driven with neighbors() alone."""
    spec = importlib.util.spec_from_file_location("agv_stub", ROOT / "examples" / "agv_stub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for legs in module.jobs(dames).values():
        moves, seconds, _ = module.drive(dames, legs)
        assert moves > 0 and seconds > 0
    agv, truck = module.jobs(dames).values()
    assert [leg[3] for leg in agv] == [False, True, False]     # loaded from the quay crane to the yard
    assert [leg[3] for leg in truck] == [False, False, True]   # arrives empty, leaves with its container
