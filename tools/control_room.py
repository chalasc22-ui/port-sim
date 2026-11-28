"""The control room: the 3D model with the simulator behind it.

Run from the repo root (after `pip install -e .`):

    python tools/control_room.py                 # opens http://127.0.0.1:8642 in the browser
    python tools/control_room.py --tour          # the same, with the guided tour running: for an unattended screen
    python tools/control_room.py --port 9000 --no-browser
    python tools/control_room.py --vendor        # online: fetch Three.js and the typefaces again into viewer3d/vendor/

The page is viewer3d/index.html with the layout, the plan's run and the list of what-ifs embedded.
Every change made in its Control room panel (another policy, fewer AGVs, a rush of trucks, an AGV broken
down or a road closed at the moment you click) is sent here as one settings object (sim/control.py),
simulated by the Python modules, checked, and sent back as a replay that the model plays from the same
moment. Nothing is recorded in advance: change a module's code, start this again, and the model shows it.

    GET  /               the page
    POST /api/run        settings as JSON -> {"replay": ..., "seconds": ..., "problems": [...]}, or
                         400 {"error": "a sentence"} for settings that make no sense
    GET  /api/options    what may be changed (sim.control.options)

Only this computer can reach it: it listens on 127.0.0.1. It needs no internet: Three.js 0.147.0 and
the page's three typefaces are put inside the page from viewer3d/vendor/ (their licences are beside
them), as tools/bundle_viewer.py does for the one-file page. Without that folder the page loads them
from jsDelivr and Google Fonts, as viewer3d/index.html does.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402

from sim import control  # noqa: E402
from sim.metrics import check  # noqa: E402

VIEWER = ROOT / "viewer3d"
FONT_PACKAGES = {"barlow": "barlow", "ibm-plex-mono": "ibm-plex-mono", "big-shoulders-stencil-display": "big-shoulders-stencil-display"}
MAX_BODY = 1_000_000


def vendor_sources(bundler) -> dict:
    """Where each file of viewer3d/vendor/ comes from: the page's own script addresses, and the typefaces'
    packages on the same host (Fontsource 5.3.0, the Latin subsets), each with its licence."""
    sources = {name: url for url, name in bundler.SCRIPTS.items()}
    base = "https://cdn.jsdelivr.net/npm/@fontsource/{package}@5.3.0/{path}"
    for _, _, name in bundler.FONTS:
        package = next(p for p in FONT_PACKAGES if name.startswith(p + "-latin-"))
        sources[f"fonts/{name}"] = base.format(package=package, path=f"files/{name}")
        sources[f"fonts/LICENSE-{package}.txt"] = base.format(package=package, path="LICENSE")
    return sources


def load(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControlRoom:
    """The layout, the page, and one simulation at a time."""

    def __init__(self):
        self.layout = load_layout(ROOT / "layouts" / "dames-point.json")
        self.replays = load("build_viewer_replay")
        self.lock = threading.Lock()
        self.runs = 0
        self.page = self._page()

    def simulate(self, settings: dict) -> dict:
        """Run the morning with these settings. ValueError for settings that make no sense."""
        with self.lock:
            started = time.perf_counter()
            terminal = control.run(settings, self.layout)
            problems = check(terminal)
            replay = self.replays.replay_from_run(terminal, self.layout, settings=settings, problems=problems)
            self.runs += 1
            return {"replay": replay, "seconds": round(time.perf_counter() - started, 2), "problems": problems}

    def _waiting(self) -> dict:
        """The stress test's waiting map: read from the recorded run if it is there, since it takes a while to make."""
        recorded = VIEWER / "terminal-replay.json"
        if recorded.exists():
            return json.loads(recorded.read_text(encoding="utf-8"))["waiting"]
        return self.replays.waiting(self.layout)

    def _page(self) -> str:
        page = (VIEWER / "index.html").read_text(encoding="utf-8")
        data = {
            "layout": json.loads((ROOT / "layouts" / "dames-point.compiled.json").read_text(encoding="utf-8")),
            "routes": json.loads((VIEWER / "demo-routes.json").read_text(encoding="utf-8")),
            "sim": {"layout": self.layout.name, "replay": self.simulate({})["replay"], "waiting": self._waiting()},
            "variants": {"variants": [{**v, "replay": None} for v in control.VARIANTS]},      # simulated when one is chosen
            "control": {"api": "/api", "options": control.options(self.layout)},
        }
        blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
        page = page.replace("<!-- BUNDLE:DATA -->", f"<script>window.PORT_DATA = {blob};</script>")
        bundler = load("bundle_viewer")
        self.offline = not bundler.missing()                 # with viewer3d/vendor/ the page asks the network for nothing
        return bundler.self_contained(page) if self.offline else page


def handler_for(room_: ControlRoom):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PortControlRoom/1"
        room = room_

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, separators=(",", ":")).encode("utf-8"), "application/json")

        def do_GET(self) -> None:      # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, room_.page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/options":
                self._json(200, control.options(room_.layout))
            else:
                self._json(404, {"error": f"nothing at {path}"})

        def do_POST(self) -> None:     # noqa: N802
            if self.path.split("?")[0] != "/api/run":
                self._json(404, {"error": f"nothing at {self.path}"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    raise ValueError("the settings must be sent as JSON, at most 1 MB")
                try:
                    settings = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    raise ValueError("the settings are not valid JSON") from None
                self._json(200, room_.simulate(settings))
            except ValueError as problem:                 # settings that make no sense, or not JSON at all
                self._json(400, {"error": str(problem)})
            except Exception as crash:                    # a fault in a module: say which, and keep serving
                traceback.print_exc()
                self._json(500, {"error": f"the simulator stopped: {type(crash).__name__}: {crash}"})

        def log_message(self, fmt: str, *args) -> None:
            if self.command == "POST":
                print(f"  run {room_.runs}: {fmt % args}")

    return Handler


def make_server(port: int = 8642, tries: int = 10):
    """A server on 127.0.0.1, on this port or one of the next few if it is taken. Port 0 picks a free one."""
    room = ControlRoom()
    for candidate in ([0] if port == 0 else range(port, port + tries)):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler_for(room))
            break
        except OSError:
            continue
    else:
        raise OSError(f"ports {port} to {port + tries - 1} are all in use: pass --port")
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def vendor() -> int:
    """Download Three.js and the page's typefaces into viewer3d/vendor/, so the page needs no internet."""
    import urllib.request

    files = {}
    for name, url in vendor_sources(load("bundle_viewer")).items():
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                files[name] = r.read()
        except OSError as problem:
            print(f"could not download {url}: {problem}")
            return 1
    for name, body in files.items():                         # only when everything came: never half a folder
        (VIEWER / "vendor" / name).parent.mkdir(parents=True, exist_ok=True)
        (VIEWER / "vendor" / name).write_bytes(body)
        print(f"saved viewer3d/vendor/{name} ({len(body) / 1024:.0f} KB)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Serve the 3D model with the simulator behind it.")
    p.add_argument("--port", type=int, default=8642)
    p.add_argument("--no-browser", action="store_true", help="do not open the page")
    p.add_argument("--tour", action="store_true", help="open the page with the guided tour running (the address ends in #tour)")
    p.add_argument("--vendor", action="store_true", help="download Three.js and the typefaces into viewer3d/vendor/ and stop")
    a = p.parse_args(argv)
    if a.vendor:
        return vendor()
    print("Starting the control room: simulating the plan...")
    server, url = make_server(a.port)
    if a.tour:
        url += "/#tour"
    print(f"Open {url}   (Ctrl+C here to stop)")
    print("The page needs no internet." if server.RequestHandlerClass.room.offline else
          "viewer3d/vendor/ is incomplete, so the page loads Three.js and its typefaces from the web: run with --vendor once while online.")
    if not a.no_browser:
        threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
