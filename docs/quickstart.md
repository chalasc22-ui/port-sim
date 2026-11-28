# Using the layout in your module

The map of Dames Point is one Python object. Load it once and ask it questions; never read the
JSON by hand or re-derive the roads yourself.

```bash
pip install -e .            # once, from the repo root
python sim/run.py           # the whole terminal: ship arrival, AGVs, stacking and the gate in one run
python tools/control_room.py   # the same run in 3D, live: change a policy, break an AGV, close a road
python tools/preflight.py      # before showing it to anyone: is this computer ready?
```

`docs/integration.md` says what each module in that run does, which check-in each rule came from, and
how to put your own code in its place. Your module's file is in `sim/`: `ship_arrival.py`, `agv.py`,
`stacking.py`, `gate.py`. Each was written from the meeting notes as a working draft, so change it freely.

```python
from terminal_layout import load_layout
layout = load_layout("layouts/dames-point.json")
```

A cell is 15 m. Positions are `(x, y)`: x runs east along the quay, y runs north toward the river.
Roads are one way. Every list comes back in a fixed order, so runs repeat.

## AGVs

```python
layout.neighbors(x, y)                      # the cells one legal move away: your only movement rule
layout.shortest_path(a, b)                  # a fewest-moves route, or None
layout.quay_cranes["QC1"].transfer_points   # the crane's 4 transfer lanes: wait in any free one
layout.charging_stations()                  # 12 stations, CH1 to CH12, in two depots
layout.with_closures({(57, 20)})            # a copy of the map with roads closed, for "what if" runs

from terminal_layout.timing import travel_time, stopping_points
stops = stopping_points(layout)             # compute once
travel_time(layout, path, loaded=True, stops=stops)   # seconds, PortSim's motion model
```

* Defaults are PortSim's: 6 m/s empty, 4 m/s loaded, 1.2 m/s², and service times of 60 s (quay crane),
  75 s (yard crane), 30 s (gate) and 15 s (a container onto or off a vehicle). Change them in
  `terminal_layout/timing.py`. `travel_time` stops at every junction, as PortSim does;
  `junctions=False` gives the optimistic bound (no stops on the way).
* Each quay transfer lane sits on the quay row, and in the fleet stress test (`tools/fleet_stress.py`)
  that is where most waiting happens. `shortest_path` stays on the quay row when the bypass is no
  shorter, so a vehicle passes one being served only once it plans around it. Two other lane rules are
  there to try (`--quay-rule`; 24 AGVs, mean of three seeds): taking any free lane of the crane from
  the bypass (`crane`) changes little (98 jobs an hour against 97); sending through-traffic down the
  bypass and waiting there for a lane (`bypass`) halves the jobs (52 an hour), because a vehicle
  waiting below a busy lane blocks the bypass, and one leaving a lane and one entering it want each
  other's cell. Keep the bypass for driving past, not for waiting; the lane-change rule is yours.
* The AGV spots of the exchange stack (block XA) are on row 1, which also carries AGVs heading west to
  the inbound road, so an AGV being served there blocks the row behind it.
* `examples/agv_stub.py` is a whole job using only `neighbors()`. Swap its `route()` for your router.
* `sim/agv.py` is a full draft of the module: segment reservation so vehicles never share a cell, three
  dispatch rules, battery and charging. In the integrated run every AGV has a home charger.

## Container stacking

```python
layout.blocks                              # 27 yard blocks plus the exchange stack (XA, XT): skip those two
layout.slots_in_block("C2")                # ground slots like "C2-03-02", by bay then row
block = layout.blocks["C2"]
block.max_tier, block.container_rows, block.capacity   # 5 tiers, 3 container rows per slot, containers when full
layout.transfer_point_for("C2-03-02")      # where an AGV stands for that slot's bay
layout.yard_cranes["RTG2"].blocks          # the blocks each yard crane serves (RTG1 to RTG6; XC1 is the exchange crane)
```

`sim/stacking.py` is a full draft of the module: `shortest_stack` and `departure_time`, rehandles, and
food kept apart from hazardous cargo. The bays a run uses are `ZONES` in `sim/scenario.py`.

## Ship arrival

```python
layout.berths                              # B1 (QC1 to QC3) and B2 (QC4 to QC6)
layout.quay_cranes["QC4"]                  # berth, x, transfer lanes, buffer_capacity (2)
```

A quay crane's buffer holds 2 containers. A crane lowers a container straight onto an AGV that is standing
in its lane and uses the buffer only when no AGV is there. When the buffer is full the crane waits for an
AGV; QC2 shows that in `python sim/run.py`. `sim/ship_arrival.py` is a full draft of the module: the
anchorage, the tide, the channel, two berths, `fcfs` and `earliest_eligible`.

## Gate

```python
layout.gate_cells("in"), layout.gate_cells("out")   # G_IN1, G_IN2 and G_OUT1, G_OUT2 on row 0
layout.blocks["XT"].transfer_points          # the 4 truck spots at the exchange zone
layout.blocks["XA"].transfer_points          # the 4 AGV spots on the other side of the same stack
```

Trucks go gate in → a truck spot → gate out and never drive into the yard: 20 to 24 moves in, 8 to 12
out. They share the roads by the gate with AGVs: the column x = 80, the south road east of it, the gate-out
column x = 88 and row 1. The
exchange crane XC1 lifts containers between the truck side and the AGV side. The truck spots are one
lane, so trucks pull forward and leave in order. This zone is a proposal: tell me if trucks should
wait somewhere else or need more spots. `sim/gate.py` is a full draft of the module: `fifo` and
`appointment`, the checks in and out, the trouble desk and a holding area (none of them on the map yet).

## Other files you may want

| File | What it is |
| --- | --- |
| `layouts/dames-point.compiled.json` | the map as plain JSON, if you are not using Python |
| `layouts/dames-point.graph.json` | the roads as stopping points and segments, with lengths and times |
| `layouts/variants/` | the same terminal with two-way roads, or with one aisle removed |
| `viewer3d/dames-point-3d.html` | the 3D model: open it by double-click. It plays the integrated run (`sim/run.py`) with a dashboard under it, says why each thing was sent, stacked or held when you click it, plans routes, closes roads and has a guided tour for presenting. It needs no internet |
| `tools/build_viewer_replay.py` | writes the run the 3D model plays (`viewer3d/terminal-replay.json`); its docstring is the file format |
| `tools/experiments.py`, `docs/experiments.md` | every module's policies, the fleet size and the truck volume compared on the same workloads, 30 runs each with confidence intervals; `docs/experiments.csv` has every run |
| `tools/learned_dispatch.py`, `docs/learned-dispatch.md` | a dispatch rule learned by Q-learning in the simulator (the AGV owner's idea), tested against the others on the experiment's 30 shifts: the method, the numbers, the limits |
| `sim/params.py`, `docs/data-sheet.md` | every number the simulation runs on, with its source: PortSim's set and a set of published ones |
| `docs/validation.md` | a shift at real scale (`python sim/run.py --shift 1`) against published figures, and where the model is off |
| `tools/control_room.py`, `sim/control.py` | the live 3D control room, and the settings a what-if run takes |
| `tools/preflight.py`, `docs/demo-script.md` | before showing it: a check that a computer is ready, and the script for the symposium table |
| `sim/run_demo.py`, `sim/modules.py` | the earlier six-container walkthrough on one-line stand-ins; still runs, no longer what the viewer plays |
| `CHANGELOG.md` | what changed in each version of the map |

Questions or a change you need: message Christopher, or open an issue on the repo.
