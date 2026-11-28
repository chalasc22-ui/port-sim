"""Write the three documents that are made from the code and the experiment, so they cannot drift from them:

    docs/data-sheet.md     every number the simulation runs on and where it comes from (sim/params.py)
    docs/experiments.md    the experiment's tables (docs/experiments-summary.json, from tools/experiments.py)
    docs/validation.md     the shift at real scale against published figures, and where the model is off

    python tools/data_sheet.py            write them
    python tools/data_sheet.py --check    exit 1 if a file on disk is not what would be written (a test runs this)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim import params as P  # noqa: E402
from sim import scenario as scenarios  # noqa: E402

DOCS = ROOT / "docs"
READ_ON = "20 November 2026"
BASIS = {"source": "published", "derived": "worked out", "assumed": "assumed", "PortSim": "PortSim"}
NAMES = {  # how the documents name a measure
    "rehandles": "Rehandles", "quay_moves_per_crane_hour": "Quay moves per crane-hour", "ship_turnaround_min": "Ship: time in port (min)",
    "truck_turnaround_min": "Truck: gate to gate (min)", "ship_wait_min": "Ship: at anchor (min)", "ship_wait_max_min": "Ship: longest at anchor (min)",
    "truck_gate_wait_min": "Truck: before driving in (min)", "truck_inside_min": "Truck: inside (min)", "imports_sent_back": "Imports sent back to the yard",
    "agv_wait_for_agv_min": "A job waits for its AGV (min)", "agv_km": "AGVs drive (km)", "agv_kwh": "AGVs use (kWh)", "quay_crane_wait_min": "Quay cranes wait (min)",
    "agv_blocked_min": "AGVs held up (min)", "trucks_served": "Trucks served", "end_min": "Finished after (min)", "moves_per_hour": "Moves an hour",
    "rehandles at 50%": "Rehandles, 50% full", "rehandles at 75%": "Rehandles, 75% full", "rehandles at 90%": "Rehandles, 90% full",
    "handling_min at 75%": "Crane time at 75% (min)",
}
TITLES = {"yard alone": "Stacking alone", "stacking": "Stacking", "ship": "Ship arrival", "gate": "Gate", "dispatch": "AGV dispatch", "fleet": "Fleet size",
          "trucks": "Trucks an hour"}


def value(v) -> str:
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return f"{v:g}"


def field(params, name: str):
    obj = params
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


def link(sid: str | None) -> str:
    if sid is None:
        return ""
    title, url = P.SOURCES[sid]
    return sid if url is None else f"[{sid}]({url})"


def pm(i: dict | None, digits: int = 1) -> str:
    return "–" if i is None else f"{i['mean']:.{digits}f} ± {i['half']:.{digits}f}"


# ---- the data sheet --------------------------------------------------------------------------------
def data_sheet() -> str:
    out = ["# Data sheet: every number the simulation runs on", "",
           "Written by `python tools/data_sheet.py` from `sim/params.py`. Change a number there, not here.", "",
           "The simulation has two sets of numbers.", "",
           "* **Reference**: PortSim's numbers (the course's reference simulator) and the assumptions made in the check-ins. Every time is fixed. "
           "The demo morning, its what-ifs and the recorded 3D runs use this set, so they give the same result every time.",
           "* **Calibrated**: a number from a published source wherever one was found, crane and gate times that vary from one move to the next, "
           "and AGVs that keep their speed through a junction. The shift at real scale (`sim.scenario.shift`) uses this set.", "",
           "The column *Basis* says how far to trust a calibrated number: **published** (the source's own figure), **worked out** (from published "
           "figures, the note says how), **assumed** (our choice, no source found: these are the ones to replace first), **PortSim** (the reference "
           "value kept).", ""]
    assumed = []
    for module in dict.fromkeys(r["module"] for r in P.ROWS):
        out += [f"## {module}", "", "| What | Reference | Calibrated | Unit | Basis | Source | Note |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in (r for r in P.ROWS if r["module"] == module):
            ref, cal = (value(field(P.REFERENCE, r["field"])), value(field(P.CALIBRATED, r["field"]))) if "field" in r else r["values"]
            out.append(f"| {r['what']} | {ref} | {cal} | {r['unit']} | {BASIS[r['basis']]} | {link(r['source'])} | {r['note']} |")
            if r["basis"] == "assumed":
                assumed.append(f"{module}: {r['what'][0].lower() + r['what'][1:]}")
        out.append("")
    e = P.CALIBRATED
    room = scenarios.SHIFT_ROOM
    out += ["## How a time varies", "",
            f"A crane move or a gate check is drawn from a lognormal distribution with the mean in the tables and a coefficient of variation of "
            f"{e.move_cv:g} (cranes) or {e.gate_cv:g} (gate): about two moves in three fall within 30 percent of the mean, and none is negative. "
            "Each draw has its own random stream, named by the run's seed, the kind of move, the container and how many times it has been moved. "
            "So the same container gets the same crane time under two policies, and the difference between two runs of a workload is the policy "
            "(Dr. Wang: \"identical saved workloads and random seeds for both policies\"). Without a seed nothing varies.", "",
            "## The shift at real scale", "",
            "`sim.scenario.shift(seed)` makes the workload the experiment and the validation run. Its own numbers:", "",
            "| What | Value | Basis | Source | Note |", "| --- | --- | --- | --- | --- |",
            "| Ships in the shift | 4, two for each berth | assumed |  | The first two arrive at the start, 20 minutes apart; the others about four hours later, each up to 20 minutes early or late. |",
            "| Containers a ship discharges, loads | 90 to 150, 50 to 90 | assumed |  | 140 to 240 moves a call. JAXPORT's 1,388,841 TEU on 1,611 vessel calls of every kind in fiscal 2025 "
            f"({link('S3')}) is 862 TEU a call, so these are small calls; they are what the modelled terminal turns round in a shift. |",
            f"| Ship draft | 9.0 to {P.MAX_DRAFT_M:g} m | published (the maximum) | {link('S6')} | Uniform between the two. |",
            f"| Cargo | 88% general, 8% food, 4% hazardous | assumed |  | Food and hazardous cargo each have a zone of their own. |",
            f"| Yard at the start | 65% of the {sum(room.values()):,} places the run uses | assumed | {link('S13')} | The source puts the practical limit of a yard at 80 percent of its capacity and "
            "the average US container yard at about half. Seven of the 27 storage blocks are used for general cargo, each on a lane of its own. A bay keeps four positions free for "
            "rehandles, so this is 55 percent of every position in those blocks. |",
            "| Trucks | 10 an hour for 8 hours | assumed |  | More in the second and third hour. 55% collect an import, the others bring an export for a later ship. |",
            f"| Trucks with an appointment | 80%, for a half-hour slot | assumed |  | They come about 8 minutes after the slot starts, give or take 9; later than 15 minutes is late. |",
            f"| Booked trucks that do not come | {P.NO_SHOW_SHARE:.0%} | published | {link('S12')} | |",
            "| Trucks with a problem at the gate | 1.5% plate, 1% toll, 3% weight, 1% damaged export | assumed |  | |",
            "| AGVs | 30, sharing the 12 charging stations | assumed |  | Five to a quay crane; the source's simulations use 6.5 "
            f"({link('S8')}). 25 AGVs share 6 chargers at HHLA ({link('S10')}). |",
            "| AGV breakdowns | one in 150 AGV-hours, 15 minutes on average | assumed |  | No published failure rate was found. |",
            "| Crane outages | one in 300 crane-hours, 20 minutes on average | assumed |  | The same. |", "",
            "## Sources", "", f"Read on {READ_ON}.", ""]
    for sid, (title, url) in P.SOURCES.items():
        out.append(f"* **{sid}** {title}" + (f": <{url}>" if url else ""))
    out += ["", "## What has no source yet", "",
            "These are choices, not measurements. Each module's owner knows better than a search does:", ""]
    out += [f"* {a}" for a in assumed]
    out += ["* The shift: the number and size of the ships, the truck volume and how punctual trucks are, the failure rates, how full the yard is.", ""]
    return "\n".join(out)


# ---- the experiment's tables ---------------------------------------------------------------------------
def experiments(summary: dict) -> str:
    reps = summary["reps"]
    out = ["# The experiment: every table", "",
           "Written by `python tools/data_sheet.py` from `docs/experiments-summary.json`, which `python tools/experiments.py` writes. "
           "Every run's numbers are in `docs/experiments.csv`.", "",
           f"Each setting was run {reps} times. A cell is the mean of the runs, plus or minus half the width of its 95 percent confidence interval "
           "(Student's t): a range built this way holds the long-run mean 19 times in 20. Under every table the "
           "settings are compared run by run, each run with the run that had the same seed, so the same ships, trucks and containers and, in the "
           "shift, the same crane time for each container. A difference marked **\\*** has an interval that does not include zero: it is more than chance.", "",
           "The numbers rank the settings on this map. They are not a forecast for a real terminal.", ""]
    for scale, heading, blurb in (("shift", "A shift at real scale (calibrated numbers)",
                                   "`sim.scenario.shift(seed)`: four ships with 140 to 240 moves each, 80 trucks, 30 AGVs, the yard 65 percent full, on the calibrated "
                                   "numbers of `docs/data-sheet.md`. What varies from run to run is the workload and every random time."),
                                  ("small", "Small workloads (reference numbers)",
                                   "`sim.scenario.generate(seed)`: three ships with about ten containers each, 14 trucks, 10 AGVs, on PortSim's numbers, where "
                                   "every time is fixed. What varies from run to run is the workload.")):
        entries = [e for e in summary["experiments"] if e["scale"] == scale]
        if not entries:
            continue
        out += [f"## {heading}", "", blurb, ""]
        for e in entries:
            measures = e.get("measures") or list(e["settings"][0]["measures"])
            note = "" if "unfinished" not in e else f" Runs that did not finish: {e['unfinished']}. Failed checks: {e['problems']}."
            out += [f"### {TITLES[e['name']]}", "", f"{len(e['settings'])} settings, {e['settings'][0]['measures'][measures[0]]['n']} runs each.{note}", "",
                    "| Setting | " + " | ".join(NAMES.get(m, m) for m in measures) + " |", "| --- |" + " ---: |" * len(measures)]
            for st in e["settings"]:
                out.append(f"| `{st['label']}` | " + " | ".join(pm(st["measures"][m]) for m in measures) + " |")
            for d in e.get("differences", []):
                cells = []
                for m in measures:
                    i = d["measures"][m]
                    cells.append("–" if i is None else f"{i['mean']:+.1f} ± {i['half']:.1f}" + (" **\\***" if abs(i["mean"]) > i["half"] else ""))
                out.append(f"| *`{d['label']}` minus `{d['against']}`* | " + " | ".join(cells) + " |")
            out.append("")
    return "\n".join(out)


# ---- the validation ----------------------------------------------------------------------------------
def tide_window() -> tuple:
    """Hours of each tide the deepest ship has enough water, and hours in which it may start the passage."""
    need = P.MAX_DRAFT_M + P.CALIBRATED.keel_clearance_m - (P.TIDE_MEAN_DEPTH_M - P.MTL_M)     # tide height it needs, above the chart datum
    angle = math.acos((need - P.MTL_M) / P.TIDE_AMPLITUDE_M)
    hours = 2 * angle / (2 * math.pi) * P.TIDE_PERIOD_S / 3600.0
    return need, hours, hours - P.TRANSIT_S / 3600.0


def round_trips() -> dict:
    """Cells driven from a quay crane to the middle of each general-cargo block of the shift that serves its berth,
    and back: the shortest and longest round trip, and how many times longer the way back is for the east berth's
    first two cranes from the blocks east of the main road."""
    from terminal_layout import load_layout

    from sim.agv import Traffic
    from sim.kernel import Clock
    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    tr = Traffic(Clock(), layout)
    trips, back_out = [], []
    for berth, cranes in layout.berths.items():
        for k, (crane, _) in enumerate(cranes):
            lane = layout.quay_cranes[crane].transfer_points[1]
            for z in scenarios.SHIFT_ZONES:
                if z.cargo != "general" or z.serves not in (None, berth):
                    continue
                stands = sorted(layout.blocks[z.block].transfer_points)
                stand = stands[len(stands) // 2]
                out, back = tr.moves(lane, stand), tr.moves(stand, lane)
                trips.append(out + back)
                if berth == "B2" and k < 2 and stand[0] > 57:
                    back_out.append(back / out)
    m = layout.cell_size_m / 1000.0
    from sim.stacking import Yard
    positions = Yard(layout, scenarios.SHIFT_ZONES).capacity()             # every position, also the four a bay keeps free for rehandles
    return {"min": min(trips), "max": max(trips), "min_km": min(trips) * m, "max_km": max(trips) * m, "back_low": min(back_out), "back_high": max(back_out),
            "positions": positions}


def setting(summary: dict, name: str, label: str) -> dict:
    e = next(e for e in summary["experiments"] if e["scale"] == "shift" and e["name"] == name)
    return next(s for s in e["settings"] if s["label"] == label)["measures"]


def validation(summary: dict) -> str:
    b, reps = summary["baseline"]["measures"], summary["reps"]
    m = lambda k: b[k]["mean"]
    need, window_h, start_h = tide_window()
    fleet36, trucks18 = setting(summary, "fleet", "agvs = 36"), setting(summary, "trucks", "trucks_per_hour = 18")
    fleet18 = setting(summary, "fleet", "agvs = 18")
    per_call = m("quay_moves") / 4
    booked_share = m("truck_no_shows") / (0.8 * m("trucks"))
    pilots_h = P.TIDE_PERIOD_S / 7200.0 - 3.0 + 0.75            # from 3 h after low water to 45 min after high water
    trip = round_trips()
    rate, job, wait = m("quay_moves_per_crane_hour"), m("agv_job_min"), 100 * m("agv_wait_at_yard_share")
    rows = [
        ("Quay crane moves per crane-hour alongside", pm(b["quay_moves_per_crane_hour"]),
         f"28 sustainable, 35 the crane's best ({link('S7')}); 42 in simulated automated terminals ({link('S8')})",
         f"**{100 * rate / 28:.0f} percent of the sustainable rate.** The crane's own cycle is the source's 35 an hour; the rest is waiting for AGVs. With 36 AGVs it is "
         f"{pm(fleet36['quay_moves_per_crane_hour'])}, so more AGVs do not close the gap. The causes are below."),
        ("One AGV job, from the call to the container set down", f"{pm(b['agv_job_min'])} min",
         f"9.2 min: 6.5 boxes per AGV-hour ({link('S8')})",
         f"**{job / 9.2:.1f} times as long.** The drive is long (a round trip between a quay crane and a yard block of its berth is {trip['min_km']:.1f} to {trip['max_km']:.1f} km "
         "on this map's one-way roads) and the AGV waits its turn in a lane."),
        ("AGV speed while driving", f"{pm(b['agv_speed_m_s'], 2)} m/s", f"6 m/s top speed, 3 m/s in a curve ({link('S8')})",
         "Plausible: the mean of a top speed of 6 with a stop to turn and to queue. Not a check, since the source gives no mean."),
        ("Share of an AGV's cycle spent waiting at the yard crane", f"{wait:.0f}%",
         f"35 to 45% at peak, for AGVs at end-loaded stacking cranes ({link('S8')})",
         ("**Lower.** " if wait < 35 else "**In range.** " if wait <= 45 else "**Higher.** ")
         + "The figures are not quite the same thing: our AGV drives along the block to its bay, and the time it queues in the lane behind another AGV counts as driving held up, not as waiting at the crane."),
        ("AGV energy per hour of work", f"{pm(b['agv_kwh_per_operating_hour'])} kWh", f"13.2 ({link('S9')}), 17 ({link('S8')})",
         "In range, **by construction**: the energy per metre was set to land here (data sheet). It shows the setting worked, nothing more."),
        ("Charging", f"{m('charging_stays'):.0f} stays on 12 stations in a shift, 30 AGVs", f"25 AGVs share 6 chargers at HHLA ({link('S10')})",
         "Consistent: with a battery sized from the source the stations are never the bottleneck."),
        ("Truck turn time, gate to gate", f"{pm(b['truck_turnaround_min'])} min at 10 trucks an hour; {pm(trucks18['truck_turnaround_min'])} at 18",
         f"30 min for a single move at Savannah's Garden City Terminal ({link('S14')})",
         "**Faster, with light traffic.** A truck here never drives into the yard and there are no chassis to fetch. The one exchange crane is the limit: at 18 trucks an hour the wait before "
         f"driving in is {pm(trucks18['truck_gate_wait_min'])} min and still rising. No figure for the truck volume at Dames Point was found."),
        ("Booked trucks that did not come", f"{100 * booked_share:.0f}% ({m('truck_no_shows'):.1f} of about 64 booked)", f"14% ({link('S12')})",
         "The input, recovered: it shows the workload generator does what the data sheet says."),
        ("Yard occupancy, of every position in the blocks used", f"{100 * 0.65 * sum(scenarios.SHIFT_ROOM.values()) / trip['positions']:.0f}% at the start, {100 * m('yard_occupancy'):.0f}% at the end",
         f"about 50% on average in the US; 80% of capacity is the practical limit ({link('S13')})",
         "Inside the range the source calls workable. (The stacking module keeps four positions free in each bay for rehandles, so the data sheet's 65 percent is of the places it will fill.)"),
        ("Deepest ship: hours of each tide it may start up the river", f"{start_h:.1f} h of 12.4 (water enough for {window_h:.1f} h, less the {P.TRANSIT_S / 60:.0f} min passage)",
         f"about {pilots_h:.1f} h: the pilots' window for 36 to 40 ft tankers and bulkers ({link('S6')})",
         f"**Close, and a little stricter.** A {P.MAX_DRAFT_M:g} m ship needs the tide {need:.2f} m above the chart datum for the whole passage. The pilots' rule is for other ship types and is tied to Mayport's tide, so this is a check of size, not of detail."),
        ("Moves a vessel call", f"{per_call:.0f} ({per_call * 1.54:.0f} TEU at the 1.54 TEU a container of {link('S7')})", f"862 TEU a call: 1,388,841 TEU on 1,611 calls of every kind, fiscal 2025 ({link('S3')})",
         "**Small calls.** They are sized to what this terminal turns round in a shift, and the port's figure divides by car carriers and bulk ships too."),
    ]
    out = ["# Validation: the model against published figures", "",
           "Written by `python tools/data_sheet.py` from the experiment (`docs/experiments-summary.json`) and the data sheet (`sim/params.py`).", "",
           f"The model's column is the shift at real scale as planned (`sim.scenario.shift`, 30 AGVs, 10 trucks an hour, every module on its default policy), "
           f"{reps} runs, the mean plus or minus half its 95 percent confidence interval. {summary['baseline']['finished']} of {reps} runs finished every job, with "
           f"{summary['baseline']['problems']} failed checks. A shift is {m('quay_moves'):.0f} quay moves on four ships, {m('trucks_served'):.0f} trucks served and "
           f"{m('containers'):,.0f} containers on the terminal; the run ends {m('end_min') / 60:.1f} hours after the first ship arrives at the sea buoy.", "",
           "This is a comparison of size. It does not prove the model right: the published figures are from other terminals, and several inputs are assumptions "
           "(`docs/data-sheet.md` lists them). What it shows is where the model is in the range of a real terminal and where it is not, and why.", "",
           "| Measure | Model | Published | Reading |", "| --- | --- | --- | --- |"]
    out += [f"| {a} | {b_} | {c} | {d} |" for a, b_, c, d in rows]
    out += ["", "## Why the quay runs at half a real crane's rate", "",
            "The map is JAXPORT's Dames Point with the equipment its page lists: six quay cranes and six yard cranes "
            f"({link('S1')}). Three things on it hold the quay back, and the fleet experiment separates them from the fleet size "
            f"({pm(fleet18['quay_moves_per_crane_hour'])} moves per crane-hour with 18 AGVs, {pm(b['quay_moves_per_crane_hour'])} with 30, {pm(fleet36['quay_moves_per_crane_hour'])} with 36):", "",
            "1. **One-way loops.** The road beside the quay runs east only, and one road leads from the quay into the west yard. A round trip between a quay crane and a block "
            f"of its berth is {trip['min']} to {trip['max']} cells ({trip['min_km']:.1f} to {trip['max_km']:.1f} km). From the east berth's first two cranes the way back from the blocks east "
            f"of the main road is {trip['back_low']:.1f} to {trip['back_high']:.1f} times the way out, because the only road north on that side comes out beyond them.",
            f"2. **One yard crane to a quay crane.** Savannah's Garden City Terminal has 238 yard cranes for 34 quay cranes, seven to one ({link('S14')}). Here each yard crane serves a lane in which AGVs cannot pass each other, "
            f"and spends {100 * m('yard_crane_travel_share'):.0f} percent of its working time travelling between bays.",
            "3. **Queues that reach the main road.** A block beside the main road has room for one or two waiting AGVs before the queue blocks every lane behind it. The shift uses "
            "the far end of each lane for that reason, which lengthens the drive.", "",
            "None of this is a fault in a module. It is what this layout and this equipment give at this load, and it is the kind of answer the model is for.", "",
            "## What broke on the way to real scale, and what was done", "",
            f"The demo morning has 12 AGVs and 58 containers. A shift has 30 AGVs and about {round(m('containers'), -2):,.0f}. Four things failed at that size and were changed, "
            "each only in the calibrated set, so the demo morning runs exactly as before:", "",
            "* **PortSim's road rule.** Reserving a stretch up to the next junction lets one moving vehicle at a time onto a 285 m stretch. With 30 AGVs the fleet stood still. "
            "A vehicle now claims the road cell by cell, as far ahead as it needs to stop, and keeps its speed through a junction (`sim/agv.py`).",
            "* **Parking.** Every AGV had a charging station of its own, and there are 12. A larger fleet shares them and stands by on the quay apron before the cranes.",
            "* **The truck exchange.** Imports fetched ahead of their trucks filled its eight places, and trucks that never came left them there. The scheduler now fetches at most "
            "four ahead and sends a container back to the yard when its truck is 30 minutes overdue or is turned away.",
            "* **The yard.** One rule for the best bay sends every container to the same bay, and a crane that serves five blocks loses its time changing blocks. The scheduler now "
            "names the yard crane with the least work waiting, and a crane takes the nearest job first.", "",
            "## Limits", "",
            "* The published figures are from other terminals and from suppliers' and consultants' papers. None is a measurement at Dames Point.",
            "* The spread of a crane move, the failure rates, the truck volume and its punctuality, and the size and number of the ships are assumptions.",
            "* The tide is a sine curve with NOAA's mean range, not the predictions for a date; wind, fog and the pilots' daylight rules are not modelled.",
            "* A curve is driven as a stop, not at 3 m/s; containers are all one size; reefers, chassis, rail and empties are not modelled.", ""]
    return "\n".join(out)


def documents() -> dict:
    docs = {DOCS / "data-sheet.md": data_sheet()}
    path = DOCS / "experiments-summary.json"
    if path.exists():
        summary = json.loads(path.read_text())
        docs[DOCS / "experiments.md"] = experiments(summary)
        if "baseline" in summary:
            docs[DOCS / "validation.md"] = validation(summary)
    return docs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true", help="write nothing; exit 1 if a document is out of date")
    a = p.parse_args(argv)
    stale = []
    for path, text in documents().items():
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text + "\n":
                stale.append(path.name)
        else:
            path.write_text(text + "\n", encoding="utf-8")
            print(f"wrote docs/{path.name}")
    if stale:
        print("out of date: " + ", ".join(stale) + " (run python tools/data_sheet.py)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
