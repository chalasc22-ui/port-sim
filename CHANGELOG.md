# Changelog: terminal layout module

Newest first. "Breaks" means code written against the previous version may need a change.

## Unreleased

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
  more exchange-crane move to be loaded (it was loaded in no time). The default run is unchanged.

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
