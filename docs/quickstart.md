# Using the layout in your module

The map of Dames Point is one Python object. Load it once and ask it questions; never read the
JSON by hand or re-derive the roads yourself.

```bash
pip install -e .            # once, from the repo root
python sim/run_demo.py      # the whole terminal running on stand-ins for every module
```

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

## Container stacking

```python
layout.blocks                              # 27 yard blocks plus the exchange stack (XA, XT): skip those two
layout.slots_in_block("C2")                # ground slots like "C2-03-02", by bay then row
block = layout.blocks["C2"]
block.max_tier, block.container_rows, block.capacity   # 5 tiers, 3 container rows per slot, containers when full
layout.transfer_point_for("C2-03-02")      # where an AGV stands for that slot's bay
layout.yard_cranes["RTG2"].blocks          # the blocks each yard crane serves (RTG1 to RTG6; XC1 is the exchange crane)
```

`sim/modules.py` has a `Stacking` stand-in with `place(container)` and `retrieve(container)`; replace it.

## Ship arrival

```python
layout.berths                              # B1 (QC1 to QC3) and B2 (QC4 to QC6)
layout.quay_cranes["QC4"]                  # berth, x, transfer lanes, buffer_capacity (2)
```

A quay crane's buffer holds 2 containers. When it is full, the crane waits for an AGV; `sim/run_demo.py`
shows QC2 doing that.

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
wait somewhere else or need more spots.

## Other files you may want

| File | What it is |
| --- | --- |
| `layouts/dames-point.compiled.json` | the map as plain JSON, if you are not using Python |
| `layouts/dames-point.graph.json` | the roads as stopping points and segments, with lengths and times |
| `layouts/variants/` | the same terminal with two-way roads, or with one aisle removed |
| `viewer3d/dames-point-3d.html` | the 3D model: open it by double-click. It plays `sim/run_demo.py`, plans routes, closes roads and has a guided tour for presenting |
| `tools/build_viewer_replay.py` | writes the run the 3D model plays (`viewer3d/demo-replay.json`); its docstring is the file format, so your module's event log can be played the same way |
| `CHANGELOG.md` | what changed in each version of the map |

Questions or a change you need: message Christopher, or open an issue on the repo.
