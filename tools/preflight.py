"""Preflight: is this computer ready to show the terminal? Run it the evening before, and again at the table.

Run from the repo root:

    python tools/preflight.py             # about ten seconds
    python tools/preflight.py --full      # also simulates the shift the tour shows (about ten seconds more)
    python tools/preflight.py --tests     # also runs the test suite (about two minutes more)

It checks, in the order a demo depends on them:

    Python and the packages the project needs
    the layout and its compiled file
    the files the 3D page needs to work without the internet (viewer3d/vendor/)
    the recorded runs the one-file page plays, the two the guided tour shows among them
    the one-file page: there, current, and asking the network for nothing
    the simulator: the plan gives the recorded numbers and passes its own checks
    the learned dispatch rule: its weights are there and whole, and its write-up is what the numbers give
    the control room: it starts, serves the page, simulates a change and refuses nonsense

and then says what to open. A line that is not "ok" says what to do about it. The exit status is 0 when
nothing failed. It changes no file and needs no internet.

What it cannot check is the browser: whether the 3D view draws smoothly on this computer's graphics, and
whether the screen stays awake. docs/demo-script.md lists those few things to try by hand.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VIEWER = ROOT / "viewer3d"
LAYOUT = ROOT / "layouts" / "dames-point.json"
PAGE = VIEWER / "dames-point-3d.html"
REPLAY = VIEWER / "terminal-replay.json"
VARIANTS = VIEWER / "terminal-variants.json"
PYTHON = (3, 11)                              # pyproject.toml: requires-python
PACKAGES = ("pydantic", "networkx")           # what the simulator and the control room import; matplotlib only draws the 2D maps
TOUR_RUNS = ("agv-fleet-6", "shift")          # the other runs the guided tour plays (viewer3d/index.html, buildShots)
PORT = 8642                                   # the control room's own


class Report:
    """The lines of a preflight: each ok, a warning or a failure, the last two with what to do."""

    def __init__(self, out=print):
        self.out, self.lines = out, []

    def _say(self, status: str, text: str, fix: str | None = None) -> None:
        self.lines.append((status, text, fix))
        self.out(f"  {status:<5} {text}")
        if fix:
            self.out(f"        -> {fix}")

    def ok(self, text: str) -> None:
        self._say("ok", text)

    def warn(self, text: str, fix: str) -> None:
        self._say("WARN", text, fix)

    def fail(self, text: str, fix: str) -> None:
        self._say("FAIL", text, fix)

    def count(self, status: str) -> int:
        return sum(1 for s, _, _ in self.lines if s == status)


def load(name: str):
    """Another tool of this folder, as a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mb(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:.1f} MB"


def shown(path: Path) -> str:
    """A path as the report names it: from the project folder, where it is inside it."""
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def differences(terminal, recorded: dict) -> list[str]:
    """Where a run made now differs from the summary a recording of it carries: nowhere, when the recording is current."""
    from sim.metrics import summary

    now = summary(terminal)
    out = []
    for key, was in recorded.items():
        mine = len(now[key]) if key == "unfinished" else now.get(key)
        if mine != was:
            out.append(f"{key} {mine} now, {was} recorded")
    return out


# ---- the checks: each takes the report, and what earlier checks loaded ---------------------------------


def check_python(r: Report, have: dict) -> None:
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info[:2] < PYTHON:
        r.fail(f"Python {version}: the project needs {PYTHON[0]}.{PYTHON[1]} or later",
               "activate the project's conda environment (conda activate ...), then run this again")
        return
    r.ok(f"Python {version} on {platform.system() or 'this system'} {platform.release()}")
    missing, found = [], []
    for name in PACKAGES:
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        r.fail(f"packages: {' and '.join(missing)} not installed in this Python",
               "in the project folder, with its environment active: pip install -e .")
    else:
        r.ok("packages: " + ", ".join(found))


def check_layout(r: Report, have: dict) -> None:
    from terminal_layout import load_layout

    layout = have["layout"] = load_layout(LAYOUT)
    compiled = LAYOUT.with_name(LAYOUT.stem + ".compiled.json")
    text = f"layout: {layout.name}, {layout.width} x {layout.height} cells of {layout.cell_size_m:g} m"
    if not compiled.exists() or json.loads(compiled.read_text(encoding="utf-8")) != layout.to_json():
        r.fail(f"{text}; {shown(compiled)} is missing or not what the layout compiles to", "python tools/build_dames_point.py")
    else:
        r.ok(f"{text}; its compiled file is current")


def check_vendor(r: Report, have: dict) -> None:
    bundler = have["bundler"] = have.get("bundler") or load("bundle_viewer")
    gone = bundler.missing()
    if gone:
        r.fail(f"offline files: {len(gone)} missing from viewer3d/vendor/ ({', '.join(Path(g).name for g in gone[:3])}{', ...' if len(gone) > 3 else ''}), "
               "so the page would need the internet", "once, while online: python tools/control_room.py --vendor")
    else:
        r.ok(f"offline files: Three.js and {len(bundler.FONTS)} typeface files are in viewer3d/vendor/")


def check_recorded(r: Report, have: dict) -> None:
    absent = [shown(p) for p in (REPLAY, VARIANTS) if not p.exists()]
    if absent:
        r.fail("recorded runs: " + " and ".join(absent) + " not there", "python tools/build_viewer_replay.py")
        return
    plan = have["plan"] = json.loads(REPLAY.read_text(encoding="utf-8"))["replay"]
    variants = have["variants"] = {v["id"]: v for v in json.loads(VARIANTS.read_text(encoding="utf-8"))["variants"]}
    runs = [v for v in variants.values() if v["replay"]]
    faulty = [v["id"] for v in runs if v["replay"]["problems"]] + (["plan"] if plan.get("problems") else [])
    no_tour = [i for i in TOUR_RUNS if not (variants.get(i) or {}).get("replay")]
    if faulty or no_tour:
        what = [f"{', '.join(faulty)} recorded with failed checks"] if faulty else []
        what += [f"the tour's {' and '.join(no_tour)} not recorded"] if no_tour else []
        r.fail("recorded runs: " + "; ".join(what), "python tools/build_viewer_replay.py, then python tools/bundle_viewer.py")
    else:
        r.ok(f"recorded runs: the plan and {len(runs)} others ({mb(REPLAY)} and {mb(VARIANTS)}), the tour's two among them")


def check_page(r: Report, have: dict) -> None:
    bundler = have.get("bundler") or load("bundle_viewer")
    name = shown(PAGE)
    if not PAGE.exists():
        r.fail(f"one-file page: {name} is not there", "python tools/bundle_viewer.py")
        return
    page = PAGE.read_text(encoding="utf-8")
    web = bundler.loads_from_the_web(page)
    if web:
        r.fail(f"one-file page: {name} would load {len(web)} things from the web, so it needs the internet", "python tools/bundle_viewer.py")
    elif bundler.missing() or not REPLAY.exists() or page != bundler.bundle(REPLAY, VARIANTS):
        r.fail(f"one-file page: {name} is older than the viewer or the recorded runs", "python tools/bundle_viewer.py")
    else:
        r.ok(f"one-file page: {name}, {mb(PAGE)}, current, and it loads nothing from the web")


def check_simulator(r: Report, have: dict, full: bool = False) -> None:
    from sim import control
    from sim.metrics import check

    layout, plan, variants = have["layout"], have.get("plan"), have.get("variants", {})
    runs = [("the plan", {}, plan and plan["summary"])]
    runs += [(f"what-if {i}", variants[i]["settings"], variants[i]["replay"]["summary"])
             for i in TOUR_RUNS if (variants.get(i) or {}).get("replay") and (full or i != "shift")]
    for name, settings, recorded in runs:
        started = time.perf_counter()
        terminal = control.run(settings, layout)
        problems = check(terminal)
        seconds = time.perf_counter() - started
        text = f"simulator: {name} in {seconds:.1f} s, {terminal.end_s / 60:.1f} minutes of terminal time"
        if problems:
            r.fail(f"{text}, but {len(problems)} of its own checks fail, the first: {problems[0]}", "python sim/run.py --quiet shows them; a module has changed")
        elif recorded is None:
            r.warn(f"{text}, every check holds; there is no recording to compare it with", "python tools/build_viewer_replay.py")
        else:
            off = differences(terminal, recorded)
            if off:
                r.fail(f"{text}, every check holds, but it is not the recorded run ({off[0]}{', ...' if len(off) > 1 else ''}): the one-file page shows old numbers",
                       "python tools/build_viewer_replay.py, then python tools/bundle_viewer.py")
            else:
                r.ok(f"{text}, every check holds, the same numbers as recorded")
    if not full and (variants.get("shift") or {}).get("replay"):
        r.ok("simulator: the shift at real scale was not run (--full runs it: the tour's stop for it waits that long in the control room)")


def check_learned(r: Report, have: dict) -> None:
    """The dispatch rule that was learned (sim/dispatch_learned.py): the control room offers it, so its weights must be there."""
    from sim import dispatch_learned

    try:
        learned = dispatch_learned.load_weights()
    except ValueError as problem:
        r.fail(f"learned dispatch rule: {problem}".split(": python ")[0],
               "python tools/learned_dispatch.py train, then test and report; until then do not pick \"Learned (Q-learning)\" in the control room")
        return
    text = f"learned dispatch rule: {len(learned['weights'])} weights, trained {learned.get('made', 'on an unknown day')}"
    try:
        stale = load("learned_dispatch").report(True, say=lambda line: None)
    except (OSError, SystemExit, KeyError, ValueError) as problem:
        r.warn(f"{text}, but its write-up cannot be checked ({problem})", "python tools/learned_dispatch.py test, then python tools/learned_dispatch.py report")
        return
    if stale:
        r.warn(f"{text}, but docs/learned-dispatch.md is not what the numbers give now", "python tools/learned_dispatch.py report")
    else:
        r.ok(f"{text}, and its write-up (docs/learned-dispatch.md) is current")


def check_control_room(r: Report, have: dict) -> None:
    started = time.perf_counter()
    server, url = load("control_room").make_server(0)
    server.RequestHandlerClass.log_message = lambda *args: None            # its own log lines do not belong in this report
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # this computer only: never through a proxy

    def ask(path: str, body: bytes | None = None):
        request = urllib.request.Request(url + path, data=body, headers={"Content-Type": "application/json"} if body else {})
        try:
            with opener.open(request, timeout=120) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as refused:
            return refused.code, refused.read()

    try:
        status, page = ask("/")
        up = time.perf_counter() - started
        page = page.decode("utf-8")
        bundler = have.get("bundler") or load("bundle_viewer")
        if status != 200 or "window.PORT_DATA = {" not in page:
            r.fail(f"control room: it started, but its page is not right (status {status})", "python tools/control_room.py --no-browser shows the error")
            return
        asked = time.perf_counter()
        status, body = ask("/api/run", json.dumps({"agvs": 6}).encode())
        seconds = time.perf_counter() - asked
        answer = json.loads(body)
        if status != 200 or answer.get("problems"):
            r.fail(f"control room: a change (6 AGVs) was not simulated cleanly (status {status}): {answer.get('error') or answer.get('problems')}",
                   "python sim/run.py --agvs 6 --quiet shows the same run")
            return
        status, body = ask("/api/run", json.dumps({"agvs": 99}).encode())
        sentence = json.loads(body).get("error", "") if status == 400 else ""
        if not sentence:
            r.fail(f"control room: settings that make no sense (99 AGVs) were not refused with a sentence (status {status})", "see tools/control_room.py, do_POST")
            return
        offline = not bundler.loads_from_the_web(page)
        text = (f"control room: up in {up:.1f} s, serves the page, simulates a change in {seconds:.1f} s "
                f"({answer['replay']['summary']['end_min']:g} minutes with 6 AGVs) and refuses nonsense in words")
        if offline:
            r.ok(text + "; its page needs no internet")
        else:
            r.warn(text + "; but its page loads Three.js and its typefaces from the web", "once, while online: python tools/control_room.py --vendor")
    finally:
        server.shutdown()
        server.server_close()


def check_port(r: Report, have: dict) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", PORT))
        except OSError:
            r.warn(f"port {PORT} is in use: a control room may be running already",
                   "use the one that is open, or close it (Ctrl+C in its terminal); a new one takes the next free port and prints its address")
            return
    r.ok(f"port {PORT}, the control room's, is free")


def check_tests(r: Report, have: dict) -> None:
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x"], cwd=ROOT, capture_output=True, text=True)
    last = (done.stdout.strip().splitlines() or ["no output"])[-1].strip("= ")
    if done.returncode == 0:
        r.ok(f"tests: {last} (in {time.perf_counter() - started:.0f} s here)")
    elif "No module named pytest" in done.stderr:
        r.warn("tests: pytest is not installed in this Python", 'pip install -e ".[dev]"')
    else:
        r.fail(f"tests: {last}", "python -m pytest -q -x shows the first failure")


CHECKS = [("Python", check_python), ("the layout", check_layout), ("the offline files", check_vendor), ("the recorded runs", check_recorded),
          ("the one-file page", check_page), ("the simulator", check_simulator), ("the learned dispatch rule", check_learned),
          ("the control room", check_control_room), ("the port", check_port)]


def preflight(full: bool = False, tests: bool = False, out=print) -> Report:
    """Run every check in order. One that cannot run at all is reported and the rest go on."""
    report, have = Report(out), {}
    out(f"Preflight for the Dames Point terminal demo, {time.strftime('%a %d %b %Y, %H:%M')}")
    for name, check in CHECKS + ([("the tests", check_tests)] if tests else []):
        try:
            if check is check_simulator:
                check(report, have, full=full)
            else:
                check(report, have)
        except Exception as crash:                                # a check that cannot run is itself a finding
            report.fail(f"{name}: could not be checked ({type(crash).__name__}: {crash})",
                        "fix the lines above first; if there are none, python -m pytest -q -x shows more")
        if report.count("FAIL") and check is check_python:
            break                                                  # nothing below can run without Python and its packages
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check that this computer is ready to show the terminal.")
    p.add_argument("--full", action="store_true", help="also simulate the shift at real scale that the tour shows")
    p.add_argument("--tests", action="store_true", help="also run the test suite")
    a = p.parse_args(argv)
    report = preflight(full=a.full, tests=a.tests)
    failed, warned = report.count("FAIL"), report.count("WARN")
    if failed:
        print(f"\nNot ready: {failed} to fix" + (f", {warned} to look at" if warned else "") + ". The arrows say how; then run this again.")
        return 1
    print(f"\nReady: {len(report.lines)} checks" + (f", {warned} to look at (above)." if warned else ", nothing to fix."))
    print("\nTo present")
    print("  live, the simulator behind the page:   python tools/control_room.py --tour")
    print(f"  one file, no Python needed:            open {shown(PAGE)}, and add #tour to the address")
    print("  the script for the day:                docs/demo-script.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
