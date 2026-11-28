"""Step 4, the rehearsal: the guided tour (what the page needs for it, and the recorded numbers its captions are
computed from), the preflight check (tools/preflight.py) and the numbers the demo script quotes (docs/demo-script.md)."""

import importlib.util
import json
import re
import sys
from collections import deque
from pathlib import Path

import pytest

from sim import control

ROOT = Path(__file__).resolve().parent.parent
MODULES = {"Layout", "Scheduler", "AGVs", "Stacking", "Gate", "Ship arrival", "Dashboard", "Control room", "Real scale"}


def tool(name):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def page():
    return (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stops(page):
    """The part of the page that lists the tour's stops."""
    return page.split("function buildShots() {", 1)[1].split("const capKicker", 1)[0]


@pytest.fixture(scope="module")
def plan():
    return json.loads((ROOT / "viewer3d" / "terminal-replay.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def recorded():
    variants = json.loads((ROOT / "viewer3d" / "terminal-variants.json").read_text(encoding="utf-8"))["variants"]
    return {v["id"]: v["replay"] for v in variants if v["replay"]}


@pytest.fixture(scope="module")
def script():
    return (ROOT / "docs" / "demo-script.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def experiment():
    return json.loads((ROOT / "docs" / "experiments-summary.json").read_text(encoding="utf-8"))


def means(experiment, name, measure, scale="shift"):
    found = next(e for e in experiment["experiments"] if e["name"] == name and e["scale"] == scale)
    return {s["label"].split(" = ")[1]: s["measures"][measure]["mean"] for s in found["settings"]}


# ---- the tour -------------------------------------------------------------------------------------------


def test_the_tour_has_a_presenters_controls(page):
    for part in ('id="tour-prev"', 'id="tour-hold"', 'id="tour-next"', 'id="tour-full"', 'id="tour-stops"', 'id="tour-exit"',
                 'id="caption-kicker"', 'id="caption-count"'):
        assert part in page, part
    keys = page.split("const TOUR_KEYS = {", 1)[1].split("};", 1)[0]
    for key in ("ArrowRight: tourNext", "PageDown: tourNext", "ArrowLeft: tourPrev", "PageUp: tourPrev", '" ": ', "Home: ", "End: ", "f: fullScreen"):
        assert key in keys, key                                 # the arrows, and what a presenter's clicker sends
    assert "const HOLD_FOR = 180;" in page                     # a held stop goes on by itself: an unattended screen keeps looping
    # a link that starts the tour, and makes the page a screen left running: untouched for three minutes, it comes back to the tour
    assert 'const tourFromLink = () => { if (location.hash === "#tour") { unattended = true; if (!tour) startTour(); } };' in page
    assert 'window.addEventListener("hashchange", tourFromLink);' in page
    assert "if (unattended && !tour && document.visibilityState === \"visible\" && now - lastTouch > HOLD_FOR * 1000) startTour();" in page
    assert ".app.presenting.idle, .app.presenting.idle * { cursor: none !important; }" in page     # a pointer left lying on the tour is put away
    assert "navigator.wakeLock" in page and "requestFullscreen" in page                    # asked for; fine if the browser says no
    assert "dur = Math.max(s.dur, Math.round(3.5 + layShot().length / 17))" in page        # a caption stays long enough to be read


def test_the_caption_can_stand_beside_the_drawer(page):
    order = [page.index(part) for part in ('<div class="view" id="view">', 'id="tour-exit"', '<div class="caption" id="caption"', '<section class="dash" id="dash"')]
    assert order == sorted(order)                               # a child of the stage, after the view: it can take a place of its own in the band
    assert ".app.tour-dash .caption { position: static;" in page and ".app.tour-dash .view { grid-area: 1 / 1 / 2 / 3; }" in page
    assert "const roomForDrawer = () => !!dash && stageEl.clientWidth >= 1180 && stageEl.clientHeight >= 660;" in page
    assert "needsDash: true" in page and "(!shot.needsDash || roomForDrawer())" in page    # the stop about the drawer is left out where it cannot show


def test_every_stop_names_its_module(stops):
    pushed = re.findall(r"shots\.push\(\{ module: \"([^\"]+)\"", stops)
    assert len(pushed) == stops.count("shots.push(") and len(pushed) >= 18
    assert set(pushed) == MODULES


def test_the_tour_plays_runs_that_are_recorded(page, stops, recorded):
    wanted = re.findall(r'VARIANTS\.find\(v => v\.id === "([^"]+)"', stops)
    assert tuple(wanted) == tool("preflight").TOUR_RUNS == ("agv-fleet-6", "shift")
    known = {v["id"] for v in control.VARIANTS}
    for name in wanted:
        assert name in known and not recorded[name]["problems"], name
    assert "async function fetchRun(variant)" in page          # in the control room they are simulated in the background instead
    assert "The simulator did not answer." in page             # and a server that is gone is reported in words


def test_the_recorded_runs_hold_what_the_captions_quote(plan, recorded):
    run, fewer, shift = plan["replay"], recorded["agv-fleet-6"], recorded["shift"]
    # Why this AGV?: the dispatcher's reason for a trip to the yard
    sent = [w for w in run["why"] if w[1] == "agv" and w[3].startswith(w[2][0] + " is sent ")]
    assert sent and re.search(r"the nearest of the \d+ free AGVs with enough charge, a drive of [\d,]+ m \(the next, AGV\d+, [\d,]+ m\)", sent[0][3])
    # What if?
    assert (run["series"]["agvs"], fewer["series"]["agvs"]) == (12, 6)
    assert (round(run["summary"]["end_min"]), round(fewer["summary"]["end_min"])) == (115, 224)
    assert (round(run["summary"]["quay_crane_wait_min"]), round(fewer["summary"]["quay_crane_wait_min"])) == (6, 87)
    # A whole shift
    assert len(shift["ships"]) == 4 and sum(s["discharge"] + s["load"] for s in shift["ships"]) == 716
    assert shift["series"]["agvs"] == 30 and len(shift["trucks"]) == 69 and len(shift["containers"]) == 2163
    assert round((shift["t_end"] * 0.72 - 1800) / 3600) == 9 and round(shift["t_end"] / 3600) == 14
    # The gate
    assert (run["summary"]["trucks_served"], run["summary"]["trucks_turned_away"], run["summary"]["rehandles"]) == (12, 3, 4)


def test_the_tour_can_start_on_any_recorded_run(page, plan, recorded, dames):
    """A visitor may leave any run on the screen, and the tour starts on it. Its Charging stop names the station an AGV
    went to: its own, or, in a fleet that shares the stations and so has no home in the replay, the one its drive ended at."""
    stations = {(c.x, c.y): c.attrs["id"] for c in dames.charging_stations()}
    shared = 0
    for name, run in [("plan", plan["replay"]), *recorded.items()]:
        for v in run["vehicles"]:
            for start, _ in v.get("charging", []):
                if start <= 60:
                    continue
                drives = [leg for leg in v["legs"] if leg["t1"] <= start + 1]
                at = tuple(drives[-1]["path"][-1]) if drives else tuple(v["at"])
                assert v.get("home") in stations.values() or at in stations, (name, v["id"], start, at)
                shared += v.get("home") is None
    assert shared > 0 and all(v["home"] is None for v in recorded["shift"]["vehicles"] if v["type"] == "agv")
    assert "stations.find(o => o.id === stay.v.sim.home) || stations.find(o => o.cell[0] === stayAt[0] && o.cell[1] === stayAt[1])" in page
    # and a caption does not quote the reference speed limit on a run that uses the published numbers
    assert 'SIM.numbers === "calibrated" ? "On the published numbers an AGV drives 6 m/s, loaded or empty."' in page
    assert {recorded[name]["numbers"] for name in ("shift", "numbers-calibrated", "numbers-varied")} == {"calibrated"} and plan["replay"]["numbers"] == "reference"


# ---- the preflight --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def preflight():
    return tool("preflight")


def test_the_preflight_passes_on_the_repo_as_it_is(preflight, capsys):
    assert preflight.main([]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "Ready: " in out
    for part in ("Python 3.", "packages: pydantic", "its compiled file is current", "typeface files are in viewer3d/vendor/", "the tour's two among them",
                 "current, and it loads nothing from the web", "simulator: the plan in", "115.1 minutes of terminal time, every check holds, the same numbers as recorded",
                 "what-if agv-fleet-6", "learned dispatch rule: 13 weights, trained 2026-11-21, and its write-up (docs/learned-dispatch.md) is current",
                 "control room: up in", "224.1 minutes with 6 AGVs", "refuses nonsense in words; its page needs no internet"):
        assert part in out, part
    assert "python tools/control_room.py --tour" in out and "docs/demo-script.md" in out and "#tour" in out
    assert "run 2:" not in out                                  # the control room's own log lines are kept out of the report


def test_a_page_that_is_stale_or_needs_the_web_is_reported_with_what_to_run(preflight, tmp_path, monkeypatch):
    good = preflight.PAGE.read_text(encoding="utf-8")
    monkeypatch.setattr(preflight, "PAGE", tmp_path / "page.html")
    for text, said in ((None, "is not there"), (good.replace("Dames Point Terminal", "Another terminal", 1), "is older than the viewer or the recorded runs"),
                       (good + '<script src="https://example.com/x.js"></script>', "would load 1 things from the web")):
        if text is not None:
            preflight.PAGE.write_text(text, encoding="utf-8")
        report = preflight.Report(out=lambda line: None)
        preflight.check_page(report, {})
        assert [(s, fix) for s, _, fix in report.lines] == [("FAIL", "python tools/bundle_viewer.py")] and said in report.lines[0][1]
    preflight.PAGE.write_text(good, encoding="utf-8")
    report = preflight.Report(out=lambda line: None)
    preflight.check_page(report, {})
    assert [s for s, _, _ in report.lines] == ["ok"]


def test_missing_offline_files_are_reported(preflight, monkeypatch):
    bundler = tool("bundle_viewer")
    monkeypatch.setattr(bundler, "VENDOR", ROOT / "viewer3d" / "no-such-folder")
    report = preflight.Report(out=lambda line: None)
    preflight.check_vendor(report, {"bundler": bundler})
    status, text, fix = report.lines[0]
    assert status == "FAIL" and "9 missing from viewer3d/vendor/" in text and fix == "once, while online: python tools/control_room.py --vendor"


def test_a_run_that_is_not_the_recorded_one_is_reported(preflight, dames, plan):
    old = {**plan["replay"]["summary"], "end_min": 99.0, "rehandles": 7}
    report = preflight.Report(out=lambda line: None)
    preflight.check_simulator(report, {"layout": dames, "plan": {"summary": old}, "variants": {}})
    status, text, fix = report.lines[0]
    assert status == "FAIL" and "it is not the recorded run (" in text and "end_min 115.1 now, 99.0 recorded" in text and "the one-file page shows old numbers" in text
    assert fix == "python tools/build_viewer_replay.py, then python tools/bundle_viewer.py"
    report = preflight.Report(out=lambda line: None)
    preflight.check_simulator(report, {"layout": dames, "plan": None, "variants": {}})
    assert report.lines[0][0] == "WARN" and "there is no recording to compare it with" in report.lines[0][1]


def test_recorded_runs_that_are_missing_or_faulty_are_reported(preflight, tmp_path, monkeypatch, recorded):
    monkeypatch.setattr(preflight, "VARIANTS", tmp_path / "terminal-variants.json")
    report = preflight.Report(out=lambda line: None)
    preflight.check_recorded(report, {})
    assert report.lines[0][0] == "FAIL" and "not there" in report.lines[0][1] and report.lines[0][2] == "python tools/build_viewer_replay.py"
    few = [{"id": "plan", "replay": None}, {"id": "agv-fleet-6", "replay": {**recorded["agv-fleet-6"], "problems": ["AGV3 never arrives"]}}]
    preflight.VARIANTS.write_text(json.dumps({"variants": few}), encoding="utf-8")
    report = preflight.Report(out=lambda line: None)
    preflight.check_recorded(report, {})
    status, text, _ = report.lines[0]
    assert status == "FAIL" and "agv-fleet-6 recorded with failed checks" in text and "the tour's shift not recorded" in text


def test_the_preflight_stops_at_a_python_that_is_too_old_and_reports_a_check_that_cannot_run(preflight, monkeypatch):
    monkeypatch.setattr(preflight, "PYTHON", (99, 0))
    report = preflight.preflight(out=lambda line: None)
    assert [s for s, _, _ in report.lines] == ["FAIL"] and "the project needs 99.0 or later" in report.lines[0][1]
    monkeypatch.setattr(preflight, "PYTHON", (3, 11))
    monkeypatch.setattr(preflight, "LAYOUT", ROOT / "layouts" / "no-such-layout.json")
    monkeypatch.setattr(preflight, "CHECKS", preflight.CHECKS[:2])
    report = preflight.preflight(out=lambda line: None)
    assert report.lines[-1][0] == "FAIL" and report.lines[-1][1].startswith("the layout: could not be checked (")


def test_the_control_room_can_open_with_the_tour_running(monkeypatch, capsys):
    room = tool("control_room")

    class Server:
        class RequestHandlerClass:
            class room:
                offline = True

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(room, "make_server", lambda port: (Server(), f"http://127.0.0.1:{port}"))
    assert room.main(["--tour", "--no-browser", "--port", "8700"]) == 0
    out = capsys.readouterr().out
    assert "Open http://127.0.0.1:8700/#tour " in out and "The page needs no internet." in out
    assert room.main(["--no-browser", "--port", "8700"]) == 0 and "Open http://127.0.0.1:8700 " in capsys.readouterr().out


# ---- the demo script ------------------------------------------------------------------------------------


def test_the_demo_script_lists_the_tours_stops_with_their_modules(script, stops):
    rows = re.findall(r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| \d+ s \|", script, flags=re.M)
    assert [int(n) for n, _, _ in rows] == list(range(1, 19))
    for _, module, title in rows:
        assert module.strip() in MODULES, module
        assert f'"{title.strip()}"' in stops, title            # the stop's title, as the page has it
    by_module = {}
    for n, module, _ in rows:
        by_module.setdefault(module.strip(), []).append(int(n))
    table = dict(re.findall(r"^\| (Layout|Ship arrival|AGVs|Stacking|Gate|Scheduler) \| ([\d, ]+) \|", script, flags=re.M))
    assert {m: [int(x) for x in ns.split(",")] for m, ns in table.items()} == {m: by_module[m] for m in table} and len(table) == 6
    assert "| All of them | 13, 14, 15 |" in script and [by_module[m] for m in ("Dashboard", "Control room", "Real scale")] == [[13], [14], [15]]


def test_the_demo_script_quotes_the_recorded_numbers(script, plan, recorded, experiment, dames):
    run, fewer, shift = plan["replay"], recorded["agv-fleet-6"], recorded["shift"]
    assert f"the morning takes {fewer['summary']['end_min']:.0f} minutes instead of {run['summary']['end_min']:.0f}" in script
    moves = sum(s["discharge"] + s["load"] for s in shift["ships"])
    assert f"{len(shift['ships'])} ships, {moves} quay moves, {shift['series']['agvs']} AGVs" in script
    assert f"A {shift['t_end'] / 3600:.0f}-hour shift with {shift['series']['agvs']} AGVs and {len(shift['containers']):,} containers" in script
    assert f"needs {run['summary']['rehandles']} rehandles this morning" in script
    storage = [b for b in dames.blocks.values() if not b.crane.startswith("XC")]                         # the exchange's two stacks are not yard blocks
    assert f"six quay cranes, {len(storage)} yard blocks, a truck gate, on a grid of {dames.cell_size_m:g} m cells" in script and len(dames.quay_cranes) == 6
    assert f"the plan, {len(recorded) - 1} what-ifs and a shift at real scale" in script
    # the experiment
    assert experiment["reps"] == 30 and "Each setting was run 30 times" in script
    rehandles = means(experiment, "stacking", "rehandles")
    assert f"{rehandles['departure_time']:.0f} rehandles a shift against {rehandles['shortest_stack']:.0f}" in script
    assert f"needs {rehandles['departure_time']:.0f} a shift, against {rehandles['shortest_stack']:.0f} for stacking on the shortest stack" in script
    gate = means(experiment, "gate", "truck_turnaround_min")
    assert f"through in {gate['appointment']:.1f} minutes against {gate['fifo']:.1f}" in script
    fleet = means(experiment, "fleet", "quay_moves_per_crane_hour")
    assert "18, 24, 30 and 36 give " + ", ".join(f"{fleet[n]:.1f}" for n in ("18", "24", "30")) + f" and {fleet['36']:.1f} quay moves per crane-hour" in script
    assert f"From 18 to 30 AGVs the quay goes from {fleet['18']:.1f} to {fleet['30']:.1f} moves per crane-hour, and six more AGVs take it only to {fleet['36']:.1f}" in script
    trucks = means(experiment, "trucks", "truck_turnaround_min")
    assert "At 6, 10, 14 and 18 an hour a truck takes " + ", ".join(f"{trucks[n]:.1f}" for n in ("6", "10", "14")) + f" and {trucks['18']:.1f} minutes" in script
    assert f"the quay reaches {experiment['baseline']['measures']['quay_moves_per_crane_hour']['mean']:.1f} moves per crane-hour against the 28" in script


def test_the_demo_script_quotes_the_layouts_own_numbers(script, plan, dames):
    # stop 17: the charger trip the one-way roads lengthen most
    station = next(c for c in dames.charging_stations() if c.attrs["id"] == "CH7")
    start, goal = (station.x, station.y), dames.quay_cranes["QC5"].transfer_points[1]
    one_way = len(dames.shortest_path(start, goal)) - 1
    seen, queue = {start: 0}, deque([start])
    while queue:                                                # the same trip with every road two-way
        x, y = queue.popleft()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt not in seen and 0 <= nxt[0] < dames.width and 0 <= nxt[1] < dames.height and dames.is_traversable(*nxt):
                seen[nxt] = seen[(x, y)] + 1
                queue.append(nxt)
    assert f"{one_way} moves from a charger to a quay crane that would be {seen[goal]} on two-way roads" in script
    assert f"{one_way} moves on one-way roads against {seen[goal]} on two-way roads" in script
    # stop 16: where vehicles wait
    cells = plan["waiting"]["cells"]

    def on_quay(x, y):
        cell = dames.cell(x, y)
        return cell.type == "quay" or (cell.type == "transfer_point" and cell.attrs.get("serves", {}).get("crane"))

    share = round(100 * sum(s for x, y, s in cells if on_quay(x, y)) / sum(s for _, _, s in cells))
    assert f"{share} percent of all waiting is on the quay" in script and 62 <= share <= 70 and "two thirds of all waiting is there" in script


def test_the_demo_script_names_things_as_the_page_and_the_tools_do(script, page):
    for label in ("Break it down now (10 min)", "Close this road now (15 min)", "Back to the plan", "Go on"):
        assert f"**{label}**" in script and label in page, label
    for label, where in (("Control room", '<h2 id="h-control">Control room</h2>'), ("Details", '<h2 id="h-inspect">Details</h2>'), ("What if", ">What if</label>"),
                         ("AGVs", '<label for="ctl-agvs">AGVs</label>'), ("Decisions", ">Decisions</button>"), ("Table", ">Table</button>")):
        assert f"**{label}**" in script and where in page, label
    shift = next(v for v in control.VARIANTS if v["id"] == "shift")
    assert f"**{shift['title']}**" in script and "600 times faster" in shift["about"] and "plays 600 times faster" in script
    # the commands it gives are commands the tools take
    assert "python tools/preflight.py --full --tests" in script and '"--full"' in (ROOT / "tools" / "preflight.py").read_text() and '"--tests"' in (ROOT / "tools" / "preflight.py").read_text()
    assert "python tools/control_room.py --tour" in script and '"--tour"' in (ROOT / "tools" / "control_room.py").read_text()
    assert "python sim/run.py --why BRAVO" in script and '"--why"' in (ROOT / "sim" / "run.py").read_text()
    assert "viewer3d/dames-point-3d.html" in script and "`#tour`" in script
    assert "at least 1180 by 660" in script and "stageEl.clientWidth >= 1180 && stageEl.clientHeight >= 660" in page
    assert "after three minutes" in script and "nothing is touched for three minutes" in script and "const HOLD_FOR = 180;" in page
    assert "The status line says the simulator did not answer" in script and "The simulator did not answer." in page
