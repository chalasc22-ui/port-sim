# The integrated terminal: all five modules in one run

`sim/terminal.py` runs ship arrival, AGVs, container stacking and the gate together on the Dames Point
layout. A ship waits for the tide, berths, and is discharged onto AGVs; the yard stacks its containers;
trucks are checked at the gate and loaded at the exchange zone; exports go the other way and leave by ship.

**Who wrote what.** Only the layout is its owner's finished module. The other four modules here were
written by Christopher (with Claude) from what each owner said in the check-ins of Sep 24, Oct 15, Oct 22
and Oct 29, so the group has something that runs end to end. Each one is a working draft for its owner to
replace with their own code or to adopt and change. Every rule below names the meeting it came from, and
every number that was guessed is marked as an assumption.

```bash
pip install -e .                  # once, from the repo root
python sim/run.py                 # the demo morning: the event log, the summary, the checks
python sim/run.py --quiet         # only the summary and the checks
python sim/run.py --follow A-03   # one container, truck, ship or AGV through the whole run
python sim/run.py --gate fifo --agvs 6          # the same morning with another policy and fleet
python sim/run.py --seed 7        # a generated workload instead of the demo
python sim/run.py --shift 1 --quiet             # a shift at real scale on published numbers (ten seconds)
python tools/control_room.py      # the 3D model with the simulator behind it: change things and watch
python tools/preflight.py         # before showing it: is this computer ready? (docs/demo-script.md is the script)
python tools/experiments.py       # the experiment: 30 runs a setting (25 minutes; --scale small for two)
python tools/data_sheet.py        # write docs/data-sheet.md, experiments.md and validation.md again
python -m pytest                  # every test; tests/test_*_module.py and test_terminal_integration.py are this simulator's
```

## The files

| File | What it is | Owner |
| --- | --- | --- |
| `sim/ship_arrival.py` | anchorage, tide, channel, berths; when a ship may enter and leave | ship arrival |
| `sim/agv.py` | how vehicles move without colliding, which AGV takes a job, battery and charging | AGVs |
| `sim/dispatch_learned.py`, `sim/dispatch_weights.json` | two more dispatch rules that choose the job as well as the AGV: one written by hand, one learned by Q-learning, with its weights | AGVs (the owner's idea, built here for the owner to adopt, change or replace) |
| `sim/stacking.py` | where a container is stacked, what it costs to get one out, cargo kept apart | container stacking |
| `sim/gate.py` | the truck queue, the checks in and out, the trouble desk, the holding area | gate |
| `sim/terminal.py` | the scheduler that joins them: who is asked to do what, and when | nobody yet (Dr. Wang, Oct 22) |
| `sim/kernel.py` | the clock, the event log with the reasons, and the records `Container`, `Vessel`, `Truck` | shared |
| `sim/scenario.py` | workloads: the demo morning, generated ones from a seed, a shift at real scale, save and load | shared |
| `sim/params.py` | every number the modules run on, in two sets (PortSim's, and published ones), with its source | shared |
| `sim/metrics.py` | the summary numbers, the checks, and the run in equal steps of time for the dashboard | shared |
| `sim/control.py` | what-if runs: one settings object in, a finished run out | shared |
| `tools/control_room.py` | serves the 3D model and re-runs the simulator when something is changed in it | shared |
| `tools/preflight.py` | checks that a computer is ready to show the terminal, and says what to fix | shared |
| `tools/experiments.py` | each module's policies, the fleet size and the truck volume compared on the same workloads, 30 runs each | shared |
| `tools/learned_dispatch.py` | trains the learned dispatch rule in the simulator, tests it on the experiment's shifts and writes `docs/learned-dispatch.md` | AGVs, as above |
| `tools/data_sheet.py` | writes `docs/data-sheet.md`, `docs/experiments.md` and `docs/validation.md` from the code and the experiment | shared |

`sim/run_demo.py` and `sim/modules.py` are the earlier six-container walkthrough with one-line stand-ins.
They still run and are not used by anything above.

## The demo morning

`python sim/run.py` runs one hand-written morning so that every module has something to show.

* **Three ships.** ALPHA berths at B1 and is discharged by QC1 to QC3. BRAVO is deep (11.9 m) and waits at
  the anchorage for the tide, then for ALPHA's berth. CHARLIE arrives after BRAVO but is ready, so under
  `earliest_eligible` it passes BRAVO and takes B2. Under `fcfs` it would wait behind BRAVO.
* **Quay.** AGVs are called while a ship is in the channel, so the first containers go straight onto
  them. QC2 has six containers and only two AGVs at a time: its buffer of 2 fills and the crane waits.
* **Yard.** 29 containers are in the yard at the start. Food goes to block D3 and hazardous cargo to N4,
  62 cells (930 m) apart. Four containers have to be rehandled to reach one underneath.
* **Gate.** 15 trucks. T03 is late and goes to the trouble desk; T05 has no appointment and waits its
  turn; T11's weight does not match its papers and is weighed again; T07 (wrong plate), T12 (damaged
  container) and T15 (cannot pay) are turned away. EXP-D1 was on T12, so it misses its ship.
* **AGVs.** 12 AGVs start at their chargers, six in each depot, take jobs by the nearest-feasible rule
  and drive home to charge when there is nothing to do.

The run ends after 115 minutes: 26 containers discharged, 10 loaded onto ships, 10 gone by truck, 3 ships
and 12 trucks served, 50 AGV jobs, no vehicle stuck, and every check holds.

## See it in 3D

Open `viewer3d/dames-point-3d.html` by double-click (or start the control room, next section, for the
live version). It plays this run instead of the six-container walkthrough: the three ships come and go, cranes lift each container to the exact stack position the
stacking module chose, AGVs queue behind each other and go home to charge (a green ring while charging,
and the battery level on the AGV's card), trucks come through the gate to the exchange zone. The bays
the run uses are tinted and the food and hazardous zones are named; the rest of the yard keeps its
example fill. **Start the guided tour** has a stop for each module: the gate and who was turned away,
a buried container and its rehandle, the ship that waited for the tide, an AGV coming home to charge,
and then the dashboard, a what-if and the shift at real scale (the section on showing it, below).
Under the view is the dashboard, and every card says why (the section after next).

```bash
python tools/build_viewer_replay.py   # writes the run (terminal-replay.json), the recorded what-ifs and the walkthrough
python tools/bundle_viewer.py         # rebuilds viewer3d/dames-point-3d.html
python tools/bundle_viewer.py walkthrough.html --replay viewer3d/demo-replay.json   # the old walkthrough, if wanted
```

The viewer knows no rule of the simulation. It plays the run's trace: every drive, every lift, and the
event log. So a module that is replaced shows up in 3D as soon as the two commands above are run again.

## The control room: change something and watch

```bash
python tools/control_room.py      # opens http://127.0.0.1:8642 on this computer; no internet needed
```

This is the 3D model with the simulator behind it. In its **Control room** panel you can:

* set the fleet size (2 to 12 AGVs) and each module's policy;
* send a rush of walk-in trucks at the moment you choose;
* click an AGV, a crane, a road cell or a gate lane and use the button on its card to break it down or
  close it **now**.

Each change is sent to the Python modules as one settings object. The morning is simulated again (about
half a second) and the model carries on from the same moment, so you see the consequence unfold. A table
compares the run with the plan, and the run's own checks are reported: close the main road for good and
the page tells you which AGVs never moved again. Nothing is recorded in advance, so a module that has
been changed shows up as soon as the control room is started again.

| Disruption | Handled by | What happens |
| --- | --- | --- |
| An AGV breaks down | AGVs | It holds its cell until it is repaired on the spot (the owner, Oct 22). Others queue behind it, or turn off early if a way round costs less time than the repair. |
| A road cell is closed | AGVs | Nobody is routed over it. A vehicle on its way replans at its next stop; one with no way left waits and is reported. |
| A crane is out of service | scheduler | It finishes the lift it is making and starts no other until it is back. Its ship waits. |
| A gate lane is closed | gate | A truck being checked in it is finished; no other is called to it or driven through it. |
| A rush of walk-in trucks | gate | Each comes for a container in the yard nobody had booked. Booked trucks keep their turn. |
| A ship is late, deeper, or loses radio contact | ship arrival | Set per ship in the settings (`vessels`); the page has no control for it yet. |

The page that is shared by link, and `viewer3d/dames-point-3d.html`, have no simulator behind them. Their
Control room offers fourteen recorded runs instead (`VARIANTS` in `sim/control.py`): eleven what-ifs with one
change each, the morning on published numbers with and without times that vary, and a shift at real scale,
which has a workload of its own and plays 600 times faster. With the control room running the same list is
simulated when you choose from it (the shift takes about 15 seconds).
What the what-ifs show:

* **The main road is a single point of failure.** More than half of the trips from the quay to the yard
  cross cell (57, 28) on the column x = 57. Closing that cell for 15 minutes holds seven AGVs for 11 to
  15 minutes each and delays the ships by 22 minutes on average. Closed for good, it stops the terminal.
* **Fleet size dominates.** With 8 AGVs the morning takes 161 minutes instead of 115, with 6 it takes 224.
* **In a yard this empty, the simplest stacking rule wins.** `shortest_stack` gives every container a
  stack of its own: no rehandles, against 4 for `departure_time`. The cleverer rule pays off only when
  the yard is full (the experiment below).

The same runs in code, for a notebook or a test:

```python
from sim import control
terminal = control.run({"agvs": 8, "events": [{"kind": "agv_breakdown", "t": 1900, "seconds": 600, "agv": "AGV5"}]}, layout)
varied = control.run({"numbers": "calibrated", "seed": 3}, layout)       # the morning on published numbers, times varying
shift = control.run({"workload": "shift", "seed": 1, "agvs": 24}, layout)   # a shift at real scale with 24 AGVs
```

The settings are listed at the top of `sim/control.py`. A setting that makes no sense is refused with a
sentence, before anything runs.

## The dashboard and the reasons

Under the 3D view is a drawer, **Dashboard**. Closed, it is one line: quay moves so far, ships alongside
and at anchor, AGVs at work, trucks inside. Open, it has three views.

* **Charts.** Six tiles (quay moves, moves in the last 30 minutes, trucks inside, AGVs at work, the
  lowest battery, rehandles) and the ships, each with where it is in its call. Under them, four charts on the run's own
  clock: containers over the quay so far; what the AGVs are doing (driving loaded, driving empty, at a
  crane, held up, at a charger, standing by); how many are waiting (trucks before the gate, jobs with no
  AGV yet, quay cranes); and the share of the last 30 minutes each crane was at work. A line marks the
  moment being played, and what is still to come is drawn faintly. Point at a chart for the numbers;
  click or drag on it to move the run's clock there. A run of the morning is drawn against the plan: the
  plan's line in grey, and "3 behind the plan" on the tiles.
* **Decisions.** Every reason the modules gave, the latest first, for all of them or for one module.
  Click a time to go to that moment, a name to open that thing's card.
* **Table.** The same numbers as the charts, for reading and for a screen reader.

Click a crane, a vehicle, a container or a ship (in the model, in the list of ships, or its name in a
reason) and its card ends with **Why**: the latest decisions about it, with the numbers they went by.

> AGV1 is sent to QC1, to take a container of ALPHA to the yard: the nearest of the 12 free AGVs with
> enough charge, a drive of 300 m (the next, AGV3, 315 m).
>
> BRAVO waits for the tide: it draws 11.9 m and needs 12.9 m of water for the whole 10 minutes of the
> passage. The channel has 12.7 m now, and enough from 0:34.
>
> RTG1 moves IMP-05 to A1-02-01, tier 2: it stands on EXP-A4, which is wanted for ALPHA. IMP-05 is set on
> STAY-06: nothing in that stack has a time to leave, and it leaves at 0:55.

A vehicle that is held up says whom it waits behind, and whom that one waits behind, to the head of the
queue. A ship's card gives its draft, its moves done and when it reached the anchorage, was cleared and
berthed.

Where each part comes from, for a module's owner:

* **Reasons.** A module calls `self.log.explain(module, [names], sentence)` at the moment it decides
  (`EventLog.explain` in `sim/kernel.py`). Reasons are kept apart from the event log, so noting one
  changes nothing in a run, and each sentence is built from the numbers the rule itself used. If your
  module decides differently, change the sentence beside the rule: `_why` in `sim/agv.py`, `_why` and
  `_why_bay` in `sim/stacking.py`, `_detail` in `sim/ship_arrival.py`, `_why_called` in `sim/gate.py`;
  the scheduler's are in `sim/terminal.py`. `terminal.log.why("AGV3")` lists what was decided about one
  thing, and so does `python sim/run.py --why AGV3` (`--why all` prints every reason of the run).
* **Series.** `sim.metrics.timeline(terminal)` gives the run in equal steps of time, at most 200 of
  them: the totals so far, and for each step the average number of ships at anchor, trucks before the
  gate, jobs waiting, and AGVs in each of the six states, which add up to the fleet. It is worked out
  from the same records as the summary, and the tests hold one against the other: the time AGVs are held
  up, the waits of ships, trucks and quay cranes, and each crane's working time.
* **Replay.** `tools/build_viewer_replay.py` writes both into the run's replay as `series` and `why`,
  with `ships`, `trucks`, each AGV's `jobs` and each vehicle's `waits` (who held it up, and where). The
  page reads these and works nothing out about the simulation itself.

The clock shows hours as well once a run lasts an hour or more (0:34:10), as the run's log and the
reasons write their times.

## Showing it: the tour, the copy that needs no internet, the preflight

For the symposium table. `docs/demo-script.md` is the script for the day: what to do the evening before
and at the table, what to say in 30 seconds, 3 minutes and 7, and what to do when something goes wrong.

```bash
python tools/preflight.py              # is this computer ready? ten seconds, and it says what to fix
python tools/control_room.py --tour    # the live model with the guided tour running
```

**Nothing needs the internet.** `viewer3d/dames-point-3d.html` and the control room's page carry
everything inside them: Three.js 0.147.0 and the Latin subsets of the page's three typefaces (Barlow, IBM
Plex Mono, Big Shoulders Stencil Display), copied from `viewer3d/vendor/`, where each has its licence
beside it. `tools/bundle_viewer.py` refuses to write a page that would still load something from the web.
`python tools/control_room.py --vendor` downloads the folder again; `--cdn` and `--fragment` on the
bundler keep the web addresses instead, for a host that serves those itself.

**The guided tour** has 18 stops and a loop of a little over four minutes. Each stop names its module, and
a caption stays long enough to be read. The stops are worked out from the run, so a module that changes
changes the captions.

* A stop may mark one thing in the model (**Why this AGV?** quotes the dispatcher's reason for a trip).
* A stop may show the dashboard: the picture keeps the top and the caption stands beside the numbers under
  it. That needs a window of 1180 by 660; in a smaller one the stop about the dashboard is left out.
* A stop may play another run and come back: **What if?** plays the morning with 6 AGVs against the plan,
  **A whole shift** the shift at real scale. In the control room these two are simulated in the background
  when the tour starts, and their dots are grey until they are there (about 20 seconds for the shift).
* For a presenter: the arrow keys, Page Down and Page Up (a clicker) and N and P move a stop and hold
  there; Space or K holds and lets go; Home and End; F for the full screen; Esc to leave. The dots under
  the caption go to any stop. A held stop goes on by itself after three minutes.
* A link that ends in `#tour` starts the tour, and makes the page a screen left running: when a visitor
  has left the tour and nothing is touched for three minutes, the tour starts again. A pointer left lying
  on the tour is put away, and the page asks the browser to keep the screen awake.

**The preflight** checks, in the order a demo depends on them: Python and the packages; the layout and
its compiled file; the files in `viewer3d/vendor/`; the recorded runs, the tour's two among them; that
the one-file page is there, current and loads nothing from the web; that the plan, simulated now, gives
the recorded numbers and passes its own checks; and that the control room starts, serves its page,
simulates a change and refuses nonsense in words. `--full` also simulates the shift, `--tests` runs the
test suite. It changes no file. What it cannot check is the browser's graphics: the demo script lists
what to try by hand.

## What each module does, and where each rule came from

### Ship arrival (`sim/ship_arrival.py`)

* A ship arrives at its time and waits at the anchorage until it is cleared (Oct 15).
* It is cleared when radio contact is up, the water is deep enough for its draft for the whole passage,
  a berth is free and the channel has room: the `try_entry` steps (Oct 29).
* The channel takes two ships at a time (Oct 29; it was one on Oct 22). A ship leaving needs a slot too.
  Requests are served in the order they were made, so a ship that asked to leave at 9:50 goes before one
  that asked to enter at 9:55 (Oct 22).
* Two berths; with both taken a third ship waits until one leaves (Oct 22).
* Policies (Oct 15): `fcfs`, strictly in arrival order, so a ship that is not ready holds up the ones
  behind it; `earliest_eligible`, where a ship that is not ready is passed over until it is.
* A ship out of radio contact is not cleared until contact is back, by itself or over the backup link
  (raised Oct 22, answered Oct 29).
* A ship leaves when its cargo work is done and the channel has a slot (Oct 15).

Assumptions for the owner to set: the tide (12.5 m mean depth, 0.9 m either way, high water every 12 h
25 min), 1.0 m under the keel, 10 minutes through the channel, 10 minutes to reach a ship over the backup
link. Not built: a ship stuck in the channel, ship length against berth length, pilots and tugs.

**Differs from what the owner described:** on Oct 29 there was a second "gantry" crane moving containers from the
buffer onto AGVs. Here the quay crane does that lift itself (15 s), which is Christopher's handover rule
of Nov 15. If the owner wants the second crane, it is one more crane id in `sim/terminal.py`.

### AGVs (`sim/agv.py`)

* A vehicle moves cell to cell along `layout.neighbors()`, the only movement rule (Dr. Wang, Sep 24).
  Path planning for many agents at once is not required (Sep 24), and is not attempted.
* A move is admitted one road segment at a time, PortSim's rule: a vehicle reserves every cell up to
  the next junction or its destination and keeps the cell it leaves until it arrives. So two vehicles
  are never in one cell, cannot swap head-on, and never enter an occupied service position. If the way
  is partly free it drives up behind the vehicle in front and waits there.
* The scheduler sends a job ("an AGV is needed at this cell"); the fleet picks the AGV. Policies:
  `nearest_feasible` (Oct 15 rule 1, Oct 22), `longest_idle` (Oct 22), `balanced`: drive time, less
  credit for idle time and state of charge (Oct 15 rule 2). The job that has waited longest is served first.
* Two more policies choose the job as well as the AGV, from every pair of a waiting job and a free AGV
  that could take it (`sim/dispatch_learned.py`): `nearest_job`, the pair with the shortest drive, and
  `learned`, the pair that a rule learned by Q-learning in the simulator scores lowest (the owner's idea
  of Oct 22). Under both a truck's job still goes before a ship's, and a job that has waited ten minutes
  is taken before any other. "A dispatch rule learned in the simulator", below, has how it was learned
  and what it is worth. The terminal runs `nearest_feasible` as planned.
* Battery (the owner's Oct 15 request): PortSim's numbers, 40 kWh, start at 75 percent, 3 kWh per km empty and
  1.35 times that loaded, 100 kW charging up to 90 percent. An AGV takes a job only if its charge covers
  the drive to the pickup, the loaded drive, and the drive home afterwards, so a loaded AGV never runs
  flat (Oct 22).
* Every AGV has a home charger, half the fleet in each depot: the owner's two regions, logical and not fenced
  (Oct 22, Oct 29). An AGV with no job drives home and charges.
* A wait of ten minutes or more is reported with the cell the vehicle was waiting for, and so is a
  destination no road leads to. A vehicle still waiting when the run ends is listed with what blocks it.

Assumptions: the ten-minute report threshold; a way round a standing vehicle is taken if it adds at most
12 moves; the fleet is at most 12 because an idle AGV parks at a charger and there are 12. A vehicle
stops at every junction (PortSim), and on the quay bypass every cell is one, since a lane change is
possible at each: vehicles move along it one cell at a time, about 2 m/s. The ten minutes after which
a waiting job goes first under the two rules that choose the job is the same threshold. Not built:
towing a dead AGV to a recovery bay, battery swap, weather and wear. A dispatch rule is one function in
`POLICIES`, or in `PAIR_POLICIES` if it chooses the job too.

### Container stacking (`sim/stacking.py`)

* The goal is the fewest rehandles (Oct 15).
* Policies (Oct 22, Oct 29): `shortest_stack`, the lowest stack with room; `departure_time`, on a stack
  whose containers all leave later, so this one is on top when its turn comes; failing that an empty
  stack; failing that the stack whose first leaver leaves latest. Weight is left out for now (Dr. Wang,
  Oct 22).
* Hazardous cargo and food are kept apart (Dr. Wang's first priority, Oct 22): each zone of the yard
  takes one kind of cargo, and a food zone and a hazmat zone must be at least 4 cells (60 m) apart or
  the yard refuses to start.
* Containers arrive and leave at the same time (Dr. Wang, Oct 29).
* A container buried under others costs one crane move for each container on top of it. Each bay keeps
  4 positions free so the lowest container of a full stack can always be dug out.

Assumptions: the 60 m gap; which bays a run uses (`ZONES` in `sim/scenario.py`: 13 bays at the exit end
of seven blocks, 345 positions, since the owner's tests use 50 to 200 containers); a rehandled container stays in
its own bay.

### Gate (`sim/gate.py`)

* Every step is written to the shared event log, so a truck and its container can be followed (Oct 15).
* Policies (Oct 15, Oct 22): `fifo`; `appointment`, where booked trucks go first once their time has
  come, an early truck waits for its time, and a truck with no appointment goes after them but is not
  passed over for more than 20 minutes (Oct 29).
* At the lane (Oct 22, Oct 29): the plate is compared with the booking; the container is looked up in
  the terminal's records (Dr. Wang, Oct 15); more than 15 minutes late means the trouble desk, and the
  truck comes back without its appointment; the truck is weighed and a weight that does not match its
  papers is weighed again at the help desk; the toll is paid.
* A truck is weighed again on the way out and its container is checked against its booking (Oct 22, Oct 29).
* A checked truck waits in a holding area (the owner's Oct 15 idea) until a truck spot is free and, for a
  pick-up, its container has reached the exchange zone.

Assumptions: 10 minutes at the trouble desk, a 25 dollar toll, 5 percent weight tolerance, 30 seconds
per check, two lanes in and two out. **Differs from what the owner described:** the owner sends a wrong plate, a missing
booking and a damaged container to the trouble desk; what the desk then decides was not said, so here
those trucks are turned away, like one that cannot pay (Oct 29). The trouble desk, the holding area and
the scales take time but have no cells on the map, because the layout was frozen on Nov 13 before their
places were decided.

### The scheduler (`sim/terminal.py`)

Dr. Wang said on Oct 22 that a high-level scheduling system must send the requests and connect the
modules, and that nobody owned it. These are its decisions; all of them are the group's to change.

* A crane does every lift, one at a time. A quay crane lowers a container straight onto an AGV waiting
  in its lane and uses its buffer of 2 only when no AGV is there; a full buffer stops the crane
  (Christopher, Nov 15; Dr. Wang, Oct 15).
* Two AGVs are called to each working quay crane, from the moment the ship is cleared, so they are in
  the lanes when it berths (Dr. Wang, Sep 24: the vehicle arrives first, beneath the crane).
* A ship's exports are loaded at each crane after it has finished discharging, the easiest to reach in
  the yard first. A ship waits for an export that is already inside the terminal; one whose truck never
  brought it in misses the ship.
* Trucks never drive into the yard (the group, Oct 29). A truck waits at a truck spot of the exchange
  zone, an AGV comes to the other side, and the exchange crane lifts across. An import is fetched from
  the yard 30 minutes before its truck's appointment. A job for a truck goes before a job for a ship.
* Yard cranes retrieve before they store (PortSim).

## What is checked

Dr. Wang's evaluation list, and where each item is covered. `python sim/run.py` prints "checks: all hold"
or lists what failed; the same checks run in the tests on the demo and on generated workloads.

| Dr. Wang's item | How it is covered |
| --- | --- |
| conflicting destinations | a job goes to one AGV; a quay lane, a truck spot and an AGV spot are claimed by one vehicle at a time; the check that a vehicle, a buffer and a stack position never hold more than their limit |
| head-on swaps, entry into occupied service locations | segment reservation in `sim/agv.py`; `metrics.cell_conflicts` rebuilds who held which cell from every admitted move and reports any cell two vehicles shared |
| correct tracking of containers and completed jobs | every container keeps its history; the checks that a container changes place only by a crane, that no crane does two lifts at once, and that the yard's stacks agree with the containers' records; `--follow` prints one container's story |
| unreachable destinations, prolonged lack of progress | a destination no road leads to is reported and refused; a wait of 10 minutes or more is reported with the blocking cell; a vehicle still waiting at the end is listed (`stalls`, `stuck_at_end`) |
| identical saved workloads and seeds for both policies | `scenario.generate(seed)`, `scenario.save` and `load`; the simulator copies a workload before running it, and a run is the same every time |
| completed jobs, throughput, job completion time, waiting time, unfinished work | `discharged`, `loaded_on_ships`, `left_by_truck`, `agv_jobs`; `moves_per_hour`; `agv_job_min`, `ship_turnaround_min`, `truck_turnaround_min`; ship, truck, AGV and quay crane waits; `unfinished`, `missed_ship`, `agv_jobs_open` |

## Published numbers and real scale

The demo morning runs on PortSim's numbers with every time fixed, 12 AGVs and 58 containers. Two additions
take the same modules to the size of a working terminal. Neither changes the demo morning: it gives the same
run as before, to the byte, and a test checks that.

**The numbers** (`sim/params.py`, `docs/data-sheet.md`). Every number the modules use is in one place, in
two sets. The reference set is PortSim's. The calibrated set has a published figure wherever one was found
(a quay crane's 35 moves an hour, an AGV's speed and acceleration, the tide at NOAA's Dames Point station,
the pilots' 2.5 ft under the keel, the 14 percent of trucks that miss an appointment), marks every other
number as an assumption, and lets crane and gate times vary around their means. The data sheet lists each
number with its source.

**A shift at real scale** (`sim.scenario.shift(seed)`, `python sim/run.py --shift 1 --quiet`): four ships
with 140 to 240 moves each, 80 trucks, 30 AGVs, about 2,200 containers in a yard 65 percent full, no-shows
and breakdowns; some 15 hours of terminal time in about ten seconds.

What that size needed, each only with the calibrated numbers:

| Part | Reference (the demo morning) | Calibrated (the shift) |
| --- | --- | --- |
| AGVs: the road | a vehicle reserves the stretch up to the next junction and drives it from rest to rest (PortSim) | it claims the road cell by cell, as far ahead as it needs to stop, and keeps its speed through a junction; it stops to turn |
| AGVs: parking | every AGV has a charging station of its own, so 12 at most | the fleet shares the 12 stations and stands by on the quay apron before the cranes; below 35 percent an AGV takes a free station |
| Stacking | a yard crane is at every bay at once; the best bay in the yard wins | a yard crane takes time to travel and takes the nearest job first; the scheduler names the yard crane with the least work waiting, and the policy picks the bay among that crane's |
| Gate | an import fetched for a truck that does not come stays in the exchange stack | at most four imports are fetched ahead of their trucks; one whose truck is 30 minutes overdue or is turned away goes back to the yard |
| Ship arrival | a tide of 0.9 m either way, 1.0 m under the keel, ten minutes from the anchorage | NOAA's 0.52 m either way on 40 ft at low water, 2.5 ft under the keel, 85 minutes from the sea buoy |
| Scheduler | two AGVs called to a crane, from the moment the ship is cleared | three, from ten minutes before it berths |

The first version of each of these failed at real scale: with PortSim's road rule 30 AGVs stood still, and
the truck exchange locked up when its eight places filled. `docs/validation.md` tells that story and
compares what the shift produces with published figures. In short: the quay cranes make 15.7 moves an
hour each where a real terminal sustains 28, an AGV job takes 17 minutes where a supplier's simulations
take 9, and trucks turn in 13 minutes at ten an hour. The gaps come from the map and the equipment, not
from the fleet: 36 AGVs give 16.0.

**What real scale showed about the layout.** These are properties of the map as it was frozen on Nov 13,
found by loading it, and worth a line in the layout report:

* Every trip from the quay to the west yard goes down the one road at x = 57.
* The road beside the quay runs east only, and the one road north on the east side comes out beyond quay
  cranes QC4 and QC5. A round trip between a quay crane and a block of its berth is 74 to 244 cells (1.1
  to 3.7 km), and from QC4 and QC5 the way back from the east blocks is two to five times the way out.
* The only way out of the N blocks leads through the lane of the truck exchange.
* A block beside the main road (column 3, H1, H2) has room for one or two waiting AGVs before the queue
  blocks the road, so the shift uses the far end of each lane.
* Six yard cranes for six quay cranes, each serving a column of four or five blocks: a yard crane spends
  27 percent of its working time travelling.

## The experiment

`python tools/experiments.py` runs every comparison 30 times: 660 runs of the whole terminal, every one
finished, no failed check. `docs/experiments.md` has every table and `docs/experiments.csv` every run.

Each setting of a comparison runs the same 30 seeds, so run k under one policy and run k under the other
have the same ships, trucks and containers and, in the shift, the same crane time for each container. A
number below is the mean of 30 runs, plus or minus half its 95 percent confidence interval; a difference is
the mean of the 30 run-by-run differences with its own interval. The numbers rank the policies on this map
with the inputs of the data sheet; they are not a forecast for a real terminal.

**Stacking alone**, a test like the owner's of Oct 29 (the owner's: 30 workloads of 120 containers on 8 stacks of
5 tiers). Here 120 containers arrive and leave all the time through two bays of block A1, 12 stacks of 5
tiers. Rehandles needed to get them all out, 90 workloads:

| Policy | 50% full | 75% full | 90% full | Crane time at 75% |
| --- | --- | --- | --- | --- |
| `shortest_stack` | 75.3 ± 1.3 | 125.7 ± 1.8 | 151.2 ± 2.4 | 307 ± 2 min |
| `departure_time` | 2.4 ± 0.5 | 29.7 ± 2.0 | 64.0 ± 2.5 | 187 ± 3 min |

Departure-time stacking saves two hours of crane time at 75 percent: the same direction and size as
the "about 100 minutes less" the owner reported.

**A shift at real scale** (calibrated numbers; 30 AGVs and 10 trucks an hour unless that is what varies):

| Compared | Setting | Result | Difference, run by run |
| --- | --- | --- | --- |
| Stacking | `shortest_stack` | 400 ± 12 rehandles; 13.0 ± 0.2 quay moves per crane-hour; ships 520 ± 25 min in port | |
| | `departure_time` | 21 ± 4 rehandles; 15.7 ± 0.3; 442 ± 27 min | 379 ± 11 fewer rehandles; ships 77 ± 11 min sooner |
| Ship arrival | `fcfs` | ships wait 149 ± 62 min at anchor | |
| | `earliest_eligible` | 107 ± 30 min | 42 ± 40 min less |
| Gate | `fifo` | trucks 16.2 ± 1.2 min gate to gate | |
| | `appointment` | 13.4 ± 0.5 min; 5.1 ± 0.8 imports sent back for trucks that did not come | 2.8 ± 1.2 min less |
| AGV dispatch | `nearest_feasible` | a job waits 5.7 ± 0.2 min for its AGV; 2,033 ± 34 km driven | |
| | `longest_idle` | 6.3 ± 0.2 min; 2,224 ± 34 km | 0.6 ± 0.1 min more; 191 ± 13 km more |
| | `balanced` | 5.8 ± 0.2 min; 2,058 ± 35 km | 0.1 ± 0.1 min more (no clear difference); 25 ± 9 km more |
| Fleet size | 18 AGVs | 13.4 ± 0.3 quay moves per crane-hour; ships 512 ± 23 min in port; AGVs held up 1,063 ± 53 min | |
| | 24 AGVs | 15.0 ± 0.3; 462 ± 26 min; 1,706 ± 128 min | +1.6 ± 0.1 moves; 50 ± 6 min sooner |
| | 30 AGVs | 15.7 ± 0.3; 442 ± 27 min; 2,489 ± 200 min | +2.3 ± 0.1; 70 ± 8 min sooner |
| | 36 AGVs | 16.0 ± 0.3; 435 ± 28 min; 3,325 ± 256 min | +2.6 ± 0.2; 78 ± 10 min sooner |
| Trucks an hour | 6 | trucks 11.1 ± 0.6 min gate to gate, 5.9 ± 0.6 of them before driving in | |
| | 10 | 13.4 ± 0.5 min, 7.8 ± 0.5 | 2.3 ± 0.7 min more |
| | 14 | 17.1 ± 1.1 min, 11.0 ± 1.1 | 6.1 ± 1.2 min more |
| | 18 | 24.5 ± 2.3 min, 17.8 ± 2.4 | 13.5 ± 2.1 min more |

**Small workloads** (reference numbers, 10 AGVs unless the fleet varies; the yard is about 10 percent full):

| Compared | Setting | Result | Difference, run by run |
| --- | --- | --- | --- |
| Stacking | `shortest_stack` | 3.4 ± 1.0 rehandles; trucks 21.9 ± 2.1 min | |
| | `departure_time` | 1.8 ± 0.5 rehandles; trucks 27.7 ± 3.0 min | 1.6 ± 1.2 fewer rehandles; trucks 5.8 ± 2.0 min longer |
| Ship arrival, first ship needs the tide | `fcfs` | ships wait 61 ± 4 min, 126 ± 6 min in port | |
| | `earliest_eligible` | 38 ± 3 min, 105 ± 6 min | 23 ± 4 min less at anchor |
| Gate | `fifo` | trucks 44.8 ± 3.4 min gate to gate | |
| | `appointment` | 27.7 ± 3.0 min | 17.2 ± 2.9 min less |
| AGV dispatch | `nearest_feasible` | a job waits 17.4 ± 1.2 min for its AGV; 171 ± 6 km | |
| | `longest_idle` | 18.1 ± 1.2 min; 175 ± 6 km | 0.7 ± 0.4 min more |
| | `balanced` | 17.6 ± 1.2 min; 172 ± 6 km | 0.2 ± 0.1 min more |
| Fleet size | 6 AGVs | done after 233 ± 8 min; trucks 84 ± 6 min; quay cranes wait 149 ± 21 min | |
| | 8 AGVs | 180 ± 6 min; 48 ± 4 min; 91 ± 17 min | 53 ± 4 min sooner |
| | 10 AGVs | 149 ± 5 min; 28 ± 3 min; 57 ± 14 min | 84 ± 4 min sooner |
| | 12 AGVs | 135 ± 4 min; 18 ± 2 min; 30 ± 10 min | 98 ± 6 min sooner |

What the experiment shows:

* **Stacking is the largest effect, and it needs a full yard to show.** In the shift, with the yard 65
  percent full, stacking on the shortest stack costs 400 rehandles where stacking by departure time costs
  21, and that is worth 2.7 quay moves per crane-hour and 77 minutes of every ship's time in port. In the
  small workloads the yard is nearly empty and the two differ by under two rehandles; there the cleverer
  rule even costs trucks six minutes, because it puts containers that leave together into the same bays
  and their AGVs queue at the same crane.
* **More AGVs help less and less.** From 18 to 24 AGVs a quay crane gains 1.6 moves an hour, from 30 to 36
  it gains 0.3, and the time AGVs spend held up behind each other triples from 18 to 36. Past about 30 the
  roads and the yard cranes set the pace (`docs/validation.md`).
* **The truck exchange keeps up with about ten trucks an hour.** At 18 a truck waits 18 minutes before it
  may drive in, three times as long as at 6, and the wait is still rising. One exchange crane and four
  truck spots are the limit, not the gate lanes.
* **Appointments help, by less than the small runs suggested.** In the shift they save a truck 2.8 minutes
  of 16; in the small workloads 17 of 45, where trucks wait far longer for an AGV. Fetching ahead has a
  price the small runs did not have: five imports a shift go back to the yard because their trucks never came.
* **Letting a ready ship pass a waiting one saves about 40 minutes at anchor in the shift**, but the
  interval (42 ± 40) nearly reaches zero: with berths planned ahead and the real tide range, a ship is
  seldom held up behind another. In the small workloads, built so that the first ship needs the tide, the
  saving is a clear 23 ± 4 minutes.
* **The three dispatch rules differ by a few percent.** `longest_idle` drives 9 percent further for
  nothing; `nearest_feasible` and `balanced` cannot be told apart on waiting time.
* **A dispatch rule that was learned does better than the planned one, and no better than one line
  written by hand.** Trained by Q-learning in the simulator and tested on these same 30 shifts, which it
  never saw, `learned` leaves a ship in port 5.1 ± 3.9 minutes less than `nearest_feasible` and drives
  50 ± 12 km less a shift. `nearest_job`, the pair of a waiting job and a free AGV with the shortest
  drive, does as well and drives another 47 ± 10 km less. Both choose the job as well as the AGV, and
  the gain comes with that. The section below and `docs/learned-dispatch.md` have the rest.

## A dispatch rule learned in the simulator

The AGV owner's idea of Oct 22, built one way so that it can be tried in the whole terminal. It is the
owner's to adopt, change or replace, and nothing else depends on it. `docs/learned-dispatch.md` has the
method, every number and the limits; this is the short form.

* **What it is.** `sim/dispatch_learned.py`. Whenever an AGV is free and jobs are waiting, the rule scores
  every pair of a waiting job and a free AGV and starts the pair with the lowest score. A score is a sum
  of 13 weights (`sim/dispatch_weights.json`): for the drive to the pickup, for how long the job has
  waited, and for the kind of job.
* **How it learned.** `python tools/learned_dispatch.py train`: Q-learning in its batch form with a linear
  Q-function, on 120 shifts at real scale. The cost it learned to keep low is the minutes jobs wait for an
  AGV. Part of the choices are made by lot, and the weights are fitted on those alone: where the lot
  chose, the pair differs from the average pair on offer by chance, so what the choice changed can be told
  from how busy the terminal was.
* **What it is worth.** `python tools/learned_dispatch.py test` runs the five dispatch rules on the
  experiment's 30 shifts, which training never saw, then other fleets and the small workloads.
  `python tools/learned_dispatch.py report` writes the write-up and its two figures from the numbers.
* **Where to see it.** `python sim/run.py --dispatch learned`. In the control room, **AGV jobs**; click an
  AGV to read what the rule expected and which pair it passed over. In the one-file page, the what-if
  "AGV jobs given out by a learned rule".
* **When the simulator changes.** The weights and the test belong to the simulator as it was. If a module
  or a calibrated number changes what a shift does, `tests/test_learned_dispatch.py` fails and says what
  to run: `test` and `report` again, and `train` first if the rule should learn from the change. All
  three take about an hour on two cores.

## Replacing a module with your own

The scheduler talks to each module through a few calls, listed at the top of `sim/terminal.py`. Keep
those and the rest of the file is yours.

| Module | The scheduler calls | The module calls back |
| --- | --- | --- |
| Ship arrival | `expect(vessel)`, `discharge_plan(vessel)`, `work_done(vessel)` | `on_cleared(vessel)`, `on_berthed(vessel)` |
| Stacking | `assign(container, berth)`, `stand(bay)`, `crane(bay)`, `settle(id)`, `set_down(id, pos)`, `blockers(id)`, `rehandle_target(id, keep_clear)`, `lift_off(id)`, `relocate(id, pos)`, `gone(id)` | nothing |
| AGVs | `fleet.submit(Job)`, `fleet.carry(agv, container, cell, on_drop)`, `fleet.unloaded(agv)`, `fleet.release(agv)`; trucks use `traffic.add(vehicle, cell)`, `traffic.send(vehicle, cell, on_arrive)` and `traffic.remove(vehicle)` | the job's `on_pickup(agv, job)`, then `on_drop(agv)` |
| Gate | `expect(truck)`, `exit(truck, container, done)`, `lane_clear(lane)`, `recheck()` | `find(container_id)`, `spot_for(truck)`, `on_cleared(truck)`, `on_enter(truck, lane, spot)` |

1. Read your module's file. Its docstring lists the rules it follows and the numbers that were guessed.
2. Change it, or put your own class behind the same calls.
3. Run `python -m pytest tests/test_<your module>_module.py`, then `python sim/run.py`. If "checks: all
   hold" still prints and the run finishes, the other modules still work with yours.
4. A new policy is a new name in `POLICIES` in `sim/scenario.py` and a branch (or function) in your module.
   Add it to `EXPERIMENTS` in `tools/experiments.py` to compare it with the others on the same workloads.

## Questions for each owner

* **Ship arrival:** the tide, the depth and the clearance now have published numbers (`docs/data-sheet.md`):
  are they the ones you want; how fast does a ship come up the river, and how many may be in the channel;
  should a ship's draft change as it is unloaded; how many cranes work one ship.
* **AGVs:** is 12 the fleet (one charger each), or do AGVs share chargers as in the shift at real scale; is
  cell-by-cell following the traffic rule you want, or PortSim's stretch by stretch; where is the recovery
  bay, and who tows; should a low AGV finish its job first or go to charge at once; how often does one fail.
  The learned dispatch rule was your idea and is built one way (`docs/learned-dispatch.md`): do you want it
  in your module as it is, changed, or replaced by your own; what should it be trained to keep low (it is
  the wait of a job now); and may a rule pass over the job that has waited longest at all.
* **Stacking:** which blocks are for food and for hazardous cargo, and how far apart must they be; may
  a rehandled container go to another bay; when does weight come in; which yard crane should take a
  container when several could (the scheduler now names the least busy one).
* **Gate:** what does the trouble desk decide for a wrong plate, a missing booking or a damaged
  container; where on the map are the scales, the holding area and the trouble desk; the toll; how long
  does an import wait for a truck before it goes back to the yard; how many trucks an hour should the
  exchange take (one crane keeps up with about ten).
* **Everyone:** who owns the scheduler; and every row of the data sheet marked "assumed".
