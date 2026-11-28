"""The control room server (tools/control_room.py): the 3D page with the simulator behind it."""

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(path, name):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def room():
    server, url = load("tools/control_room.py", "control_room").make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield url
    server.shutdown()
    server.server_close()


def post(url, payload, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    request = urllib.request.Request(url + "/api/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_page_carries_the_plan_the_what_ifs_and_the_controls(room):
    with urllib.request.urlopen(room + "/", timeout=60) as r:
        page = r.read().decode()
    assert r.status == 200 and "window.PORT_DATA = {" in page and "<!-- BUNDLE:DATA -->" not in page
    data = json.loads(page.split("window.PORT_DATA = ", 1)[1].split(";</script>", 1)[0].replace("<\\/", "</"))
    assert data["control"]["api"] == "/api" and data["control"]["options"]["defaults"]["agvs"] == 12
    assert data["sim"]["replay"]["summary"]["end_min"] == 115.1 and data["sim"]["waiting"]["cells"]
    recorded = json.loads((ROOT / "viewer3d" / "terminal-replay.json").read_text())["replay"]
    same = data["sim"]["replay"] == recorded
    assert same, "the plan the control room simulates on starting is not the recorded one: run tools/build_viewer_replay.py"
    assert [v["id"] for v in data["variants"]["variants"]][:2] == ["plan", "ship-fcfs"] and all(v["replay"] is None for v in data["variants"]["variants"])
    # it works without the internet: Three.js and the typefaces are inside the page, from viewer3d/vendor/
    assert "cdn.jsdelivr.net" not in page and "fonts.googleapis.com" not in page and "fonts.gstatic.com" not in page
    assert "<script>/* three.min.js, from viewer3d/vendor/" in page and page.count("@font-face{font-family:'Barlow'") == 3
    with urllib.request.urlopen(room + "/api/options", timeout=60) as r:
        assert json.loads(r.read()) == data["control"]["options"]


def test_a_run_comes_back_as_a_replay_with_its_settings_its_numbers_and_its_checks(room):
    settings = {"agvs": 8, "policies": {"gate": "fifo"}, "events": [{"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"}]}
    status, body = post(room, settings)
    replay = body["replay"]
    assert status == 200 and body["problems"] == [] and replay["problems"] == [] and 0 < body["seconds"] < 30
    assert replay["settings"] == settings and replay["agvs"] == 8 and replay["policies"]["gate"] == "fifo"
    assert sum(1 for v in replay["vehicles"] if v["type"] == "agv") == 8
    assert replay["disruptions"] == [{"kind": "agv", "id": "AGV5", "at": replay["disruptions"][0]["at"], "t0": 1900.0, "t1": 2500.0}]
    assert replay["summary"]["end_min"] > 150 and any("AGV5 breaks down" in line[1] for line in replay["log"])
    again = post(room, settings)[1]["replay"]
    assert again == replay                                                 # the same settings give the same run


def test_settings_that_make_no_sense_are_answered_with_a_sentence_and_the_server_carries_on(room):
    assert post(room, {"agvs": 99}) == (400, {"error": "the fleet must be a whole number of AGVs from 2 to 12, not 99"})
    assert post(room, {"events": [{"kind": "crane_down", "t": 60, "seconds": 600, "crane": "QC9"}]})[0] == 400
    assert post(room, None, raw=b"not json") == (400, {"error": "the settings are not valid JSON"})
    assert post(room, None, raw=b"") [0] == 400
    for path in ("/nope", "/vendor/three.min.js", "/vendor/fonts/barlow-latin-400-normal.woff2", "/../pyproject.toml"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(room + path, timeout=60)
        assert e.value.code == 404
    assert post(room, {})[1]["replay"]["summary"]["end_min"] == 115.1      # still serving, and {} is the plan


def test_a_road_closed_for_good_is_reported_not_refused(room):
    status, body = post(room, {"events": [{"kind": "road_closed", "t": 1200, "seconds": None, "cells": [[57, 28]]}]})
    assert status == 200 and body["problems"] and all("behind a closed road" in p or "still waits" in p or "hanging" in p for p in body["problems"][:3])
    assert body["replay"]["disruptions"][0] == {"kind": "road", "cells": [[57, 28]], "t0": 1200.0, "t1": None}
