# Changelog: terminal layout module

Newest first. "Breaks" means code written against the previous version may need a change.

## Unreleased

* **The 3D view and the charts redrawn (Nov 23).** Only how things are drawn: the layout, the simulator,
  every run and every number are unchanged.
  * Light and surfaces. Colours are added up as light and shown through a camera's tone curve. The sky is a
    dome with drifting clouds, the sun, and stars and a moon after dark; the same sky lights every surface
    and is what water, glass and paint reflect. Shadows are fitted to the ground in view, so they are sharp
    close up. The ground has grain and wear; the river has ripples, a swell and patches of breeze.
  * Machines and ships, each gathered into a few meshes so that detail costs little to draw. Quay cranes
    stand on bogies on rails, with stairs, a walkway, a machinery house, an operator's cab, a spreader on
    four ropes and their number; yard cranes, AGVs and trucks likewise. Ships have a shaped hull, a bridge,
    a funnel, lashing bridges, navigation lights, and mooring lines while they lie alongside. Containers
    have corrugated sides, doors with locking bars, weathered roofs.
  * Surroundings: woods, the public road, the gate with IN and OUT over its lanes, the office, high-mast
    lights, channel buoys and the cable-stayed bridge downriver. All of this is scenery, drawn by eye and
    not from data. The only lettering in the picture is ids from the layout and the run, and IN and OUT.
  * Dusk and night. The lamps come on at dusk. After dark the floodlights light the site and cast shadows,
    lamps and vehicle lights glow, windows are lit, and the land and river outside the fence are dark.
  * **Picture quality**, under View. On Automatic the page watches how long frames take and, when they run
    slow for a few seconds, draws fewer pixels and then smaller shadows. Full, Balanced and Light choose by
    hand; `window.portViewer.setQuality()` does the same from outside.
  * The two figures of the learned rule. The test figure says its finding in its title, written from the
    numbers: better than the planned rule on three of the four measures shown, and against the rule
    written by hand better on none and worse on one. An interval clear of zero has a full dot, one that
    reaches zero an open dot; the better side of zero is shaded; the learned rule's own figures are written
    over its row. The learning curve has one key and the value at the end of each line.
  * The dashboard. Charts are taller where the screen has the room, a tile that is a share of something
    carries a bar, the wash under the moves line fades downward, and the key of the waiting chart reads
    out each line's value at the run's clock.
* **A dispatch rule learned in the simulator (Nov 23).** The AGV owner's idea of Oct 22, built one way for
  the owner to adopt, change or replace. The layout, the plan's run and the experiment are unchanged, and
  the terminal still runs `nearest_feasible`.
  * Two more dispatch rules, which choose the job as well as the AGV from every pair of a waiting job and a
    free AGV (`sim/dispatch_learned.py`): `nearest_job`, the pair with the shortest drive, written by hand;
    and `learned`, the pair that a learned rule scores lowest. Under both a truck's job still goes first,
    and a job that has waited ten minutes is taken before any other.
  * `python tools/learned_dispatch.py train` learns the 13 weights by Q-learning in the simulator (batch
    form, a linear Q-function, 120 shifts at real scale) and writes `sim/dispatch_weights.json`. `test`
    runs the five rules on the experiment's 30 shifts, which training never saw, and on other fleets and
    the small workloads. `report` writes `docs/learned-dispatch.md` with its two figures, and
    `report --check` says when they are out of date.
  * The result on the 30 shifts: against the planned rule a ship is in port 5.1 ± 3.9 minutes less, a job
    waits 3 percent less for its AGV and the AGVs drive 50 ± 12 km less. `nearest_job` does as well and
    drives another 47 ± 10 km less: learning did not beat one line written by hand, and the write-up says so.
  * The control room offers both rules under **AGV jobs**, and a card quotes the learned rule's reason:
    what it expected, and which pair it passed over. The one-file page has the morning under the learned
    rule as a recorded what-if. On that small morning the rule ends 6 minutes later than the plan.
  * The preflight checks that the weights are there and that the write-up is what the numbers give.
* **Ready for the table: no internet, a tour that runs itself, a preflight (Nov 21).** The layout and every
  run are unchanged.
  * The 3D page needs no internet. `viewer3d/dames-point-3d.html` and the control room's page carry Three.js
    and the page's typefaces inside them, from `viewer3d/vendor/` (licences beside them), and
    `tools/bundle_viewer.py` refuses to write a page that would still load something from the web. `--cdn`
    keeps the web addresses, as `--fragment` does.
  * The guided tour is 18 stops and names the module of each. New stops: **Why this AGV?** (the
    dispatcher's own reason, with the AGV marked), **The run in numbers** (the dashboard), **What if?** (the
    morning with 6 AGVs against the plan) and **A whole shift** (real scale). A stop can show the dashboard,
    with its caption beside it, and can play another run and come back.
  * For a presenter: arrow keys, a clicker's Page Down and Page Up, Space to hold, F for the full screen, dots
    to any stop. A held stop goes on after three minutes. A caption stays long enough to be read.
  * For a screen left running: a link that ends in `#tour` starts the tour (`python tools/control_room.py
    --tour` opens it), and the tour comes back by itself when a visitor has left it and nothing is touched
    for three minutes.
  * `python tools/preflight.py`: is this computer ready to show the terminal? It checks the packages, the
    layout, the offline files, the recorded runs, the one-file page, the simulator against its recording and
    the control room, and says what to run for anything that is not right.
  * `docs/demo-script.md`: the evening before, at the table, the tour stop by stop, three ways to tell it,
    the questions to expect with their numbers, and what to do when something goes wrong. A test holds
    every number in it against the recorded runs and the experiment.
  * Fixed in the tour: on **Exports go aboard** the camera stayed at the crane's lane while the crane took
    the container along the quay to the ship, so the picture showed neither. It follows the crane now.
  * Fixed in the tour: it could not be started while the shift at real scale was on the screen. Its AGVs
    share the chargers and have no home station, which the **Charging** stop looked for; the stop now takes
    the station the AGV drove to. Found by starting the tour on every recorded run.
  * Fixed in the tour, on runs with the published numbers: **AGV to the yard** quoted the reference set's
    limit for a loaded AGV (4 m/s; it is 6), and **Into the stack** could show the exchange crane setting a
    truck's export down instead of the yard crane stacking it.
  * The tour's caption keeps a margin on the right on a phone, and what was selected before a tour is
    selected again after it.
  * When the control room's server is gone, the page says so in words instead of the browser's own
    "Failed to fetch".
* **A dashboard, and a reason for every decision (Nov 20).** The layout is unchanged, and every run is the
  same as before: the recorded runs differ only by what was added to them.
  * Every module says why. `EventLog.explain` (`sim/kernel.py`) keeps a sentence for each decision, built
    from the numbers the rule went by: why this AGV of the free ones, why this bay and this stack, why a
    container is moved aside, why a yard crane serves one AGV before another, why a ship waits and when the
    water is enough, why a truck is called, held or turned away, why a quay crane uses its buffer or stops.
    The morning has 210 of them, a shift about 3,000. `terminal.log.why("AGV3")` lists those about one thing,
    and `python sim/run.py --why AGV3` prints them.
  * `sim.metrics.timeline(terminal)`: the run in equal steps of time. Totals so far, the queues, what the
    AGVs are doing and how busy each crane is. The tests hold it against the summary.
  * 3D model: a **Dashboard** under the view with six tiles, four charts that follow the run's clock (click one to
    go to that moment), the decisions as a list and the same numbers as a table. A run of the morning is drawn
    against the plan. Every card ends with **Why**; a held-up vehicle says whom it waits behind; ships can be
    clicked and have a card.
  * `tools/build_viewer_replay.py`: a run's replay also carries `series`, `why`, `ships`, `trucks`, each AGV's
    `jobs` and each vehicle's `waits`, and a container's ship, truck and due time.
  * Fixed in the 3D model: a vehicle that moved a single cell and stopped (in a queue, mostly) was not drawn
    until its next move. Its speed over one cell came out as zero, so its position could not be worked out.
    It is drawn now. In the morning this hid an AGV for a few minutes in all.
  * The model's clock shows hours once a run lasts an hour or more (0:34:10), as the log and the reasons do.
    An AGV of a fleet that shares the stations has no home charger in the replay (the shift named one).
    A thing stays pinned when the control room simulates the run again.
  * Tests that compare a recorded file with a fresh one fail at once with what to run (a stale file used to
    make pytest work for minutes on the difference).
* **Published numbers, random variation, and a shift at real scale (Nov 20).** The layout is unchanged, and the
  demo morning gives the same run as before, to the byte.
  * `sim/params.py`: every number the modules run on, in one place and in two sets. `REFERENCE` is PortSim's,
    with every time fixed. `CALIBRATED` has a published figure wherever one was found (crane rates, AGV speed
    and acceleration, the tide at NOAA's Dames Point station, the pilots' clearance under the keel, the share
    of trucks that miss an appointment) and crane and gate times that vary around their means, each from a
    random stream named by what is timed, so two policies give the same container the same crane time.
    `docs/data-sheet.md` lists every number with its source and marks what is still assumed.
  * `sim.scenario.shift(seed)`, `python sim/run.py --shift 1`: four ships with 140 to 240 moves each, 80
    trucks, 30 AGVs, the yard 65 percent full, no-shows, breakdowns.
  * What real scale needed, each only with the calibrated numbers. `sim/agv.py`: a vehicle claims the road
    cell by cell, as far ahead as it needs to stop, and keeps its speed through a junction (PortSim's
    stretch-by-stretch reservation brought 30 AGVs to a standstill); a fleet larger than the 12 charging
    stations shares them and stands by on the quay apron. `sim/terminal.py`: a yard crane takes time to
    travel and takes the nearest job first; the scheduler names the least busy yard crane; at most four
    imports are fetched to the exchange ahead of their trucks, and one whose truck does not come goes back
    to the yard. `sim/stacking.py`, `sim/gate.py` and `sim/ship_arrival.py` take their numbers from the set.
  * `tools/experiments.py`: 30 runs for every setting, the mean with its 95 percent confidence interval,
    and the difference between two settings run by run with its own interval. A sweep of trucks an hour is
    new. `docs/experiments.md` has every table, `docs/experiments.csv` every run.
  * `docs/validation.md`: what the shift produces against published figures, where the model is off, and why.
  * `tools/data_sheet.py` writes the three documents from the code and the experiment; a test fails if one
    is out of date.
  * 3D model and control room: three more runs to choose (the morning on published numbers, the same with
    times that vary, a shift at real scale), settings `numbers`, `seed` and `workload` in `sim/control.py`,
    playback up to 600 times faster.
* **The control room (Nov 20).** `python tools/control_room.py` serves the 3D model with the simulator
  behind it, on this computer and without the internet (Three.js 0.147.0 is kept in `viewer3d/vendor/`).
  Change the fleet size or a module's policy, send a rush of walk-in trucks, or click an AGV, a crane, a
  road cell or a gate lane to break it down or close it at that moment: the Python modules simulate the
  morning again in about half a second and the model carries on from the same moment, with a table
  comparing the run with the plan. The shared page and the double-click file offer ten recorded what-ifs
  in the same panel.
* **Disruptions in the simulator** (`scenario.events`): an AGV breakdown and a closed road (`sim/agv.py`:
  vehicles replan, queue, turn off early round a breakdown, and are reported when they have no way), a
  crane out of service (`sim/terminal.py`), a closed gate lane (`sim/gate.py`). `sim/control.py` turns
  one settings object into a run, for the control room, the recorded what-ifs and notebooks alike.
* Viewer: a run can be swapped in place (`loadRun`); disruptions show in the model (a red ring on a
  broken AGV, barriers on a closed road, a crane marked out of service) and as marks on the timeline;
  a slot the run uses lists the containers standing in it. Replay additions: `disruptions`, `settings`,
  `problems`, and more numbers in `summary`.
* **The other four modules, integrated (Nov 20).** `python sim/run.py` runs ship arrival, AGVs, container
  stacking and the gate together on the map: three ships, 12 AGVs and 15 trucks in the demo morning, or a
  generated workload from a seed. The layout is unchanged. Each module is a working draft written from
  what its owner said in the check-ins, for the owner to replace or adopt; `docs/integration.md` lists
  every rule with the meeting it came from, what was assumed, what is not built, and the calls a
  replacement has to keep.
  * `sim/ship_arrival.py`: anchorage, tide, a two-ship channel, two berths, radio contact; `fcfs` and
    `earliest_eligible`.
  * `sim/agv.py`: PortSim's segment reservation (no shared cell, no head-on swap, no entry into an
    occupied service position), three dispatch rules, battery and charging at a home charger, reports
    of long waits and unreachable destinations.
  * `sim/stacking.py`: `shortest_stack` and `departure_time`, rehandles, food and hazardous cargo in
    separate zones.
  * `sim/gate.py`: `fifo` and `appointment`, the checks in and out, the trouble desk, a holding area.
  * `sim/terminal.py`: the scheduler nobody owned (Dr. Wang, Oct 22). It keeps the Nov 15 handover rule.
  * `sim/metrics.py`: the summary and the checks from Dr. Wang's evaluation list; `sim/scenario.py`:
    the demo, generated workloads, save and load.
* **Experiment** (`tools/experiments.py`, `docs/experiments.csv`): each module's policies and four fleet
  sizes on the same ten workloads. All 130 runs finish with every check holding.
* **The 3D viewer plays the integrated run** (`viewer3d/terminal-replay.json`): several ships one after
  the other at a berth, containers at their exact stack position, AGVs that queue and charge (battery
  on the card), trucks through the gate, tinted yard zones, and tour stops for the gate, a rehandle,
  the ship that waited for the tide and an AGV charging. The six-container walkthrough still plays with
  `?run=walkthrough`, or bundled with `tools/bundle_viewer.py out.html --replay viewer3d/demo-replay.json`.
  Replay format additions are listed at the top of `tools/build_viewer_replay.py`; old files play unchanged.
* 3D viewer only, the map is unchanged: the river has moving ripples, sky reflections, a quay wall and a far
  bank; the marsh shows its ponds, reeds and shrubs; open ground is pavement; the legend lists water.
* **The 3D viewer plays a simulation run.** `sim/run_demo.py` records every drive and lift
  (`Terminal.trace`), `tools/build_viewer_replay.py` writes them to `viewer3d/demo-replay.json`, and the
  viewer plays that file with play, pause, speed and a timeline to scrub: the ship comes alongside, quay
  cranes lift containers into their buffers, AGVs carry them, yard cranes stack them, the exchange crane
  loads a truck. Any module can be played the same way by writing the same file from its own event log;
  the format is at the top of `tools/build_viewer_replay.py`.
* Viewer tools: click two road cells for the shortest legal path with its moves, metres and travel times;
  close road cells; compare the two-way variant; a layer for the stress test's waiting map; click a crane,
  vehicle or container for what it is doing; follow or ride along with a vehicle; a guided tour with
  captions for presenting.
* Viewer look: quay cranes and RTGs with working trolleys and spreaders, vehicles that speed up, brake
  and turn through corners, a time-of-day slider with night lighting, light masts, lane markings, a
  fence, the gate canopy, 20 and 40 ft containers, and a feeder ship on the river.
* The route tool repeats `neighbors`, `shortest_path` and `travel_time` in JavaScript, the only copy of a
  layout rule outside the package. `tests/test_viewer_roads.py` runs it in Node against the Python on
  480 trips (one-way, two-way, with closures) and is skipped where Node is not installed.
* `sim/run_demo.py`: a truck that arrives after its container is in the exchange stack now waits one
  more exchange-crane move to be loaded (it was loaded in no time).
* **`sim/run_demo.py` hands containers over differently (Nov 15).** The map and its buffers of 2 are
  unchanged. Before, every container went crane to ground to AGV, and an AGV picked a container up off
  the ground by itself. Now:
  * a crane does every lift, and one lift at a time (each crane keeps a calendar);
  * an AGV standing in the crane's lane is served directly: the quay crane lowers a container straight
    onto it, and a yard, quay or exchange crane lifts a delivered container straight off it;
  * the buffer is used only when no AGV is there. When an AGV comes for a buffered container, the crane
    lifts it onto the AGV (15 s). A full buffer still makes the crane wait.

  In the default run AGV3 starts in QC3's lane, so QC3's first container goes straight onto it; QC2 has
  no AGV yet, fills its buffer and waits 109 s as before. The run now takes 29 minutes (it was 30),
  with 32 log lines. New call on the AGV stand-in: `waiting_at(cells, now)`. The stacking stand-in
  fills block C2 one row at a time, so each container goes to the next bay along the lane.
* Replay file: a leg may carry `marks`, the times the vehicle passes the stopping points on its way.
  The viewer keeps to them, so two vehicles on one road stay as far apart as they are in the run.

## v0.2.1, Nov 3 (schema 1.2, unchanged)

* **Truck exchange zone** by the gate: 4 truck spots (block XT, along y = 4) and 4 AGV spots (block XA,
  along row 1) on either side of one small stack, served by the exchange crane XC1. Trucks go gate in →
  truck spot → gate out and no longer enter the yard. A proposal until the gate and AGV owners confirm
  it. Breaks: code that assumed every block is a storage block or every yard crane an RTG; skip blocks
  XA and XT and crane XC1 by id (their `cargo` is `general`, like the rest).
* **Travel times**: `terminal_layout.timing` with PortSim's motion model (6 m/s empty, 4 m/s loaded,
  1.2 m/s²) and its Jacksonville service times (quay crane 60 s, yard crane 75 s, gate 30 s, handoff
  15 s). The road graph export now has `time_empty_s` and `time_loaded_s` per segment.
* **Fleet stress test** (`tools/fleet_stress.py`): 6 to 36 AGVs, one per cell, an hour each, no gridlock;
  about 4 jobs per AGV per hour, and with 12 or more AGVs most of the waiting (57 to 78 percent over four
  seeds) is on the quay row. No road changes needed. `--quay-rule` tries other ways to use the quay lanes;
  keeping through-traffic on the bypass and waiting there for a lane halves the jobs.
* **Integration demo** (`sim/run_demo.py`, `sim/modules.py`): one ship, five imports, one export and two
  trucks, with a stand-in for each module. QC2's buffer fills and the crane waits for an AGV.
* Docs: `docs/quickstart.md`.

## v0.2, Oct 29 (schema 1.2)

* Transfer buffers hold **2** containers at every transfer point (PortSim's finite buffer), as in
  v0.1.1. The first Oct 29 update and the PR note set them to 0 after the buffer research; 2 came back the
  same afternoon. `buffer_capacity` now means containers on the ground beside the transfer point, may be
  0 (the vehicle waits under the crane), and defaults to 0 when a file leaves it out (it was 1). Breaks:
  code that read 0, or relied on the default of 1.
* Road graph export (`tools/export_graph.py`) and road variants (`layouts/variants/`: two-way, one aisle).
* Charging depots moved to the sites an exact p-median solve chose: beside the west column (CH1 to CH6)
  and the inbound road (CH7 to CH12), each with a passing lane.
* `container_rows` on blocks and `Block.capacity`: 3 container rows per slot, 11,790 containers.
* Each quay crane has **4 transfer lanes** (`transfer_lanes`), a bypass lane beside the quay, and a
  return lane. Breaks: code that assumed one transfer point per crane; read `quay_cranes[...].transfer_points`.
* Berth B1 split into B1 (QC1 to QC3) and B2 (QC4 to QC6).
* 3D viewer (`viewer3d/`), and an AGV stub (`examples/agv_stub.py`).

## v0.1.1, Oct 22 (schema 1.1)

* Charging stations (new cell type `charging_station`), yard crane reach boxes, `buffer_capacity` (2 at
  every Dames Point transfer point).
  Breaks: readers with their own list of drivable cell types must add `charging_station`.

## v0.1, Oct 15 (schema 1.0)

* First version: loader, 13 validation rules, the Layout API, the 12 × 8 demo and Dames Point.
