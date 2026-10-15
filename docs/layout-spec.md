# Terminal Layout & Topology Module Spec

Port simulator project, CAI4105 · Author: Christopher · 2026-09-24

## Purpose and scope

This module is the map. It defines the terminal's geometry and the meaning of every cell, and the other four modules read it. It never holds runtime state: no container positions, no vehicle positions, no clock.

**Owns**

- Terminal footprint, coordinate system, cell size
- Cell types: water, berth, quay, lane (with allowed directions), yard slot, transfer point, gate, blocked
- Yard block structure and slot addressing (block, bay, row, max tier)
- Lane connectivity: which cell a vehicle may move to from any lane cell
- Closures: a lane or zone marked closed for a scenario, without rebuilding the map
- Loading, validation, JSON export, and the 2D render; the 3D render reads the same JSON

**Does not own**

- Where containers are now (yard/inventory or the kernel)
- Where vehicles are now or how they pick a route (traffic module)
- When ships and trucks arrive (demand and schedules module)
- Gate transactions, plates, tolls, damage checks (gate module)

**What the other modules get:** one JSON file plus a small Python API that answers four questions: what is at (x, y), where can a vehicle go from here, which slots belong to block A, and where are the quay, the transfer points and the gates.

## Grid model

The terminal is a rectangular grid of square cells. Every cell has exactly one type, and a vehicle moves one cell at a time, which is what the traffic module expects.

**Coordinates.** Integer (x, y). x runs east (columns), y runs north (rows), origin at the bottom-left corner. The file records `cell_size_m`; the default is 15 m, roughly one AGV length, so one cell holds one vehicle or one 40 ft ground slot. Real-world position of a cell center is (x + 0.5, y + 0.5) × cell_size_m, used only by the renderers.

**Cell types**

| Type | Traversable by vehicles | Attributes | Read by |
| --- | --- | --- | --- |
| water | no | none | viewer |
| berth | no | berth_id, crane_ids | demand |
| quay | yes | berth_id | traffic, demand |
| transfer_point | yes | serves: block_id + bay, or berth_id + crane_id | traffic, demand, yard |
| lane | yes | exits: any subset of N, E, S, W; speed_kph (optional) | traffic |
| yard_slot | no | block_id, bay, row, max_tier, cargo: general, reefer, hazmat | yard, demand |
| gate | yes | flow: in or out; lane_no | gate, traffic |
| blocked | no | reason: building, exclusion, unused | none |

**Movement rule.** From a lane cell a vehicle may move to the neighbor in any listed exit direction, if that neighbor is traversable. A one-way lane lists one exit, a two-way lane lists two, an intersection lists three or four. Quay, transfer_point and gate cells behave like lanes and carry their own `exits`. This is the whole connectivity model; there is no separate road graph in the file. The loader derives one for validation.

**Yard addressing.** A block is a rectangle of yard_slot cells. Bays run along the block's long axis (numbered from 1), rows across it (from 1), and `max_tier` is the stacking height, 4 or 5 for a rubber-tired gantry block. A ground slot is `A-03-02`; a position in the stack adds the tier: `A-03-02-1`. The loader generates slot cells from each block's corner, size and orientation, so the file lists blocks, not thousands of slots.

**Transfer points.** A vehicle never enters a yard block or a berth. It waits at a transfer_point cell on the adjacent lane, and a crane moves the container across. Each block has one transfer point per bay along its service side; each berth has one per quay crane. The demand module addresses cranes and vehicles by these cells.

**Closures.** A closure is a set of cell ids marked closed for a scenario. Closed traversable cells drop out of `neighbors()` results. The map itself is never edited during a run.

## Layout file format

There are two JSON forms. The authored file is compact (rectangles and blocks) and is what you edit. The compiled file is per-cell and is what the traffic, demand, gate and 3D modules read. The loader turns the first into the second and refuses anything invalid.

**Rules**

1. A rectangle is `[[x0, y0], [x1, y1]]`, both corners inclusive.
2. Any cell no entry covers becomes `blocked` with reason `unused`.
3. Two entries covering the same cell is an error, with one exception: the loader converts lane cells on a block's service side, and quay cells under a crane, into `transfer_point` cells. They keep their `exits`.
4. `exits` lists the directions a vehicle may leave by: any of `N`, `E`, `S`, `W`.
5. Ids are strings of letters, digits and `_`, unique within their kind (`B1`, `A`, `QC1`, `G_IN`). Crane ids are unique across quay cranes and block cranes together.
6. Closures are exempt from rule 3: they name existing lane cells.
7. Unknown keys are errors, so a typo like `"exist"` cannot pass silently. The one exception is `notes`: free text allowed on the file and on any entry, never read. Use it for tracing remarks.

**Limits in v0.1**

- Width and height are at most 500 cells each.
- A quay runs east-west only: a berth has one `quay_y` row and cranes sit at an `x` on it.
- A rect lists its low corner first.
- One service-lane cell is the transfer point for one block bay. Two blocks cannot share one lane row; give each block its own lane row (two adjacent rows is fine). The loader reports it as a rule 8 conflict.
- `service_side` must run parallel to the bays: `N` or `S` for an `EW` block, `E` or `W` for an `NS` block.

**Worked example: a 12 × 8 demo terminal**

```
y7  ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~    water
y6  B B B C B B B B C B B B    berth B1; C = quay crane QC1 (x=3), QC2 (x=8)
y5  > > > P > > > > P > > v    quay lane, one way east; P = transfer point under a crane
y4  ^ . . . . . . . . . . v    . = unused
y3  ^ . A A A A A A A A . v    block A: 8 bays (x=2..9), 2 rows (y=2..3), max tier 5
y2  ^ . A A A A A A A A . v
y1  ^ * T T T T T T T T < <    service lane, one way west; T = transfer point, bays 1..8
y0  . O . . . . . . . . I .    I = gate in (10,0), O = gate out (1,0)
                               * = (1,1), a lane with two exits: W (on round the loop) and S (to gate out)
    x0 1 2 3 4 5 6 7 8 9 10 11
```

Authored file for that map:

```json
{
  "schema_version": "1.0",
  "name": "demo-12x8",
  "cell_size_m": 15,
  "width": 12,
  "height": 8,
  "water": [{"rect": [[0, 7], [11, 7]]}],
  "berths": [
    {"id": "B1", "rect": [[0, 6], [11, 6]], "quay_y": 5,
     "cranes": [{"id": "QC1", "x": 3}, {"id": "QC2", "x": 8}]}
  ],
  "lanes": [
    {"rect": [[0, 5], [10, 5]], "exits": ["E"], "kind": "quay", "berth": "B1"},
    {"rect": [[11, 5], [11, 5]], "exits": ["S"]},
    {"rect": [[11, 2], [11, 4]], "exits": ["S"]},
    {"rect": [[2, 1], [11, 1]], "exits": ["W"]},
    {"rect": [[1, 1], [1, 1]], "exits": ["W", "S"]},
    {"rect": [[0, 1], [0, 4]], "exits": ["N"]}
  ],
  "blocks": [
    {"id": "A", "origin": [2, 2], "bays": 8, "rows": 2, "orientation": "EW",
     "max_tier": 5, "cargo": "general", "service_side": "S", "crane": "RTG1"}
  ],
  "gates": [
    {"id": "G_IN", "cell": [10, 0], "flow": "in", "exits": ["N"]},
    {"id": "G_OUT", "cell": [1, 0], "flow": "out", "exits": []}
  ],
  "closures": []
}
```

`origin` is the block's bottom-left cell; `orientation` `EW` means bays run along x. `service_side` names which side the service lane is on; the loader marks the lane cells on that side, one per bay, as transfer points. Quay transfer points are the quay cells at each crane's `x`.

**Compiled file** (`to_json()` output, what the other modules read):

```json
{
  "schema_version": "1.0",
  "name": "demo-12x8",
  "cell_size_m": 15,
  "width": 12,
  "height": 8,
  "grid": [["blocked", "gate", "blocked", "..."], ["lane", "lane", "transfer_point", "..."], ["..."]],
  "cells": {
    "3,5": {"type": "transfer_point", "exits": ["E"], "serves": {"berth": "B1", "crane": "QC1"}},
    "2,1": {"type": "transfer_point", "exits": ["W"], "serves": {"block": "A", "bay": 1}},
    "2,2": {"type": "yard_slot", "slot": "A-01-01", "block": "A", "bay": 1, "row": 1, "max_tier": 5, "cargo": "general"},
    "10,0": {"type": "gate", "id": "G_IN", "flow": "in", "exits": ["N"]}
  },
  "blocks": [{"id": "A", "slots": ["A-01-01", "A-01-02", "..."], "transfer_points": [[2, 1], [3, 1], "..."], "crane": "RTG1"}],
  "berths": [{"id": "B1", "cranes": [{"id": "QC1", "transfer_point": [3, 5]}, {"id": "QC2", "transfer_point": [8, 5]}]}],
  "gates": [{"id": "G_IN", "cell": [10, 0], "flow": "in"}, {"id": "G_OUT", "cell": [1, 0], "flow": "out"}]
}
```

`grid[y][x]` gives the type of every cell, so a renderer needs nothing else to draw the map. `cells` holds attributes only for cells that have them; water and unused cells are absent. `exits` is omitted when empty, and `closures` appears only when the layout has closed cells. The file is written in a fixed order, one cell and one list item per line, so diffs between versions are readable. The `...` entries above stand for the rest of the rows and lists.

## Python API

One package, `terminal_layout`, exposing one class, `Layout`. It is immutable after loading; closures return a new `Layout` view rather than editing the map. Python 3.11 or newer; dependencies are pydantic, networkx and matplotlib only.

```python
from terminal_layout import load_layout, Layout, Cell, Block, LayoutError

layout: Layout = load_layout("layouts/demo-12x8.json")
# raises LayoutError listing every problem found, not just the first

# Dimensions
layout.width, layout.height, layout.cell_size_m

# Cells
layout.cell(x, y) -> Cell            # frozen dataclass: x, y, type, exits, attrs; cell.pos is (x, y)
layout.cells(type=None) -> list[Cell]
layout.is_traversable(x, y) -> bool
layout.exits(x, y) -> list[str]      # subset of ["N", "E", "S", "W"], in that order
layout.neighbors(x, y) -> list[tuple[int, int]]
# open, traversable cells reachable in one move, always ordered N, E, S, W

# Named places
layout.quay_cells(berth_id=None) -> list[Cell]
layout.gate_cells(flow=None) -> list[Cell]           # flow: "in" | "out"
layout.transfer_points(block_id=None, berth_id=None) -> list[Cell]
layout.transfer_point_for(slot_id: str) -> Cell     # the one at that slot's bay

# Yard
layout.blocks -> dict[str, Block]     # Block: id, bays, rows, max_tier, cargo, crane, slots, transfer_points
layout.berths -> dict[str, tuple[tuple[str, tuple[int, int]], ...]]   # berth id -> ((crane id, transfer point), ...)
layout.slot(slot_id: str) -> Cell
layout.slots_in_block(block_id: str) -> list[str]

# Closures (scenario support)
layout.with_closures(cells: set[tuple[int, int]]) -> Layout   # new view, original untouched
layout.closures -> frozenset[tuple[int, int]]
layout.warnings -> tuple[str, ...]   # rule 12 warnings for the current closures

# Convenience
layout.shortest_path(a, b) -> list[tuple[int, int]] | None     # BFS over neighbors()
layout.validate() -> list[str]        # empty when valid; load_layout calls it
layout.to_json(path=None) -> dict     # compiled form; writes the file when path is given
layout.render_2d(path="layout.png", show_ids=False, ax=None) -> None   # ax: draw onto the caller's Axes instead of saving
```

**Contract details**

- Every list the API returns is in a fixed order (coordinates ascending, or N, E, S, W for directions). Reproducible runs depend on this.
- `neighbors()` is the only movement primitive. The traffic module decides which neighbor to take and when; this module never moves anything.
- `shortest_path()` exists for validation and for the demand module to estimate distances. The traffic module may ignore it.
- Slot ids are strings everywhere (`"A-03-02"`). Cell coordinates are `(x, y)` tuples everywhere. Nothing takes both.
- `LayoutError` messages name the cell or id at fault: `"block A: transfer point (5, 1) is not a lane cell"`.
- **Closures.** `with_closures(cells)` replaces the closed set (it does not add to it); pass an empty set to reopen everything. A closed cell has no neighbors and is no one's neighbor, and `is_traversable` is False for it. `exits`, `quay_cells`, `gate_cells` and `transfer_points` describe the static map and still list closed cells.
- **Off the grid.** `cell`, `exits` and `neighbors` raise `IndexError`. `is_traversable` returns False. `shortest_path` returns `None` if either end is not traversable.
- **Coordinates are integers.** Python and numpy ints are accepted; floats and bools raise `TypeError` (`with_closures`, `shortest_path`).
- **Unknown ids** (`slot`, `slots_in_block`, `transfer_points`, `quay_cells`) raise `KeyError`. Slot ids name ground slots only; a tier (`A-03-02-1`) is a position in the stack, not a cell.
- `Layout`, `Cell` and `LayoutError` can be copied and pickled, so a layout can cross a process boundary. Everything the API returns is a copy or read-only.

## Reference terminal

Two layouts ship with the module: the 12 × 8 demo above as the test fixture, and a simplified Dames Point container terminal as the poster layout. Dames Point is JAXPORT's container terminal, it uses the quay-crane plus rubber-tired-gantry setup the grid model assumes, and it is local.

Per [JAXPORT's terminal page](https://www.jaxport.com/cargo/facilities/dames-point-marine-terminal/), Dames Point has 5,002 ft (1,525 m) of deepwater berth, six post-Panamax quay cranes and six 40-ton rubber-tired gantry cranes. The container terminal is at 9834 New Berlin Road, Jacksonville; in satellite view it is at about 30.404 N, 81.565 W, on the north bank just north-west of the Dames Point Bridge.

**What to trace from the satellite view**

1. The quay line: one berth entry the full length of the wharf, with six cranes spaced evenly. At 15 m cells that is about 100 cells of quay.
2. The yard: count the RTG blocks and their orientation. Each becomes one `blocks` entry; estimate bays and rows from the footprint (a typical block is about 6 rows wide and 30 to 40 bays long). Set `max_tier` to 5.
3. The internal roads: reduce them to one loop, one way, with a service lane along each block's service side and one cross lane every few blocks.
4. The gate complex on the New Berlin Road side: two gate-in lanes and two gate-out lanes.
5. Everything else (buildings, the rail yard, the auto terminal) as `blocked` rectangles.

**Simplification rules**

- Straight lines and grid-aligned rectangles only. No curves, no diagonal roads.
- Keep the outline, the berth, the block count and the gate position; drop everything that does not move a container.
- Aim for a grid of about 100 × 40 cells. Bigger than that slows the 3D demo without adding anything to the poster.
- Nobody will check the model against the real terminal. It has to look like Dames Point on the poster and produce a complete container journey.

File names: `layouts/demo-12x8.json` and `layouts/dames-point.json`. A traced screenshot with the grid overlaid goes in `layouts/dames-point-trace.png` so the group can see where the cells came from.

**As built (v0.1)**

`layouts/dames-point.json` is written by `tools/build_dames_point.py`, which checks the layout with `load_layout` (writing nothing if a rule fails), then writes the compiled file and `docs/dames-point.png`. Edit the script, not the JSON; a test fails if the files differ from what the script writes. The grid is 112 × 43 cells; the berth is 100 cells (1,500 m of the real 1,525 m) with `QC1` to `QC6` at x = 14, 31, 48, 65, 82 and 99; there are 27 blocks with 419 bays and 786 ground slots; the gates are `G_IN1`, `G_IN2`, `G_OUT1` and `G_OUT2` on row 0. Where it differs from the plan above:

| Plan | Built | Why |
| --- | --- | --- |
| About 100 × 40 cells | 112 × 43 | The site is about 1.7 km long at 15 m cells, and 8 of the 43 rows are river |
| Blocks about 6 rows by 30 to 40 bays | 1 to 2 rows by 11 to 18 bays | A cell is 15 m, so one cell stands for several container slots. The block count and where the blocks sit are kept |
| One loop, one way | One-way roads with forks and crossings | With only the west and east columns linking quay and yard, a crane at the west end needed over 250 moves to reach the block beside it. Forks from the quay lane onto the apron lane cut that to 108 (166 without the fork at x = 57), and two northbound aisles between block columns (the plan's "cross lane every few blocks") shorten trips from the yard to the quay and the gate |
| Everything else as `blocked` rectangles | Only the marsh (`exclusion`) and the gate buildings (`building`) are drawn; everything else is unused | The rail yard, auto terminal and car parks do not move a container |

The longest leg between a gate, a block and a crane is about 230 moves (3.4 km) because every road is one way; a test fails above 250.

Known simplifications, so nobody is surprised on the poster:

- The south-west corner is where the model departs most from the photo. The I-295 ramp crosses it, so blocks F1 and G1 are left out and G2 is shortened to 11 bays; the lanes of rows F and G end at the aisle (x = 18) and the west column starts at row 12.
- A cell with two exits lets a vehicle turn onto either road. Crossings are not modelled; whether a vehicle may take a turn is the traffic module's rule.
- A gate is one cell. Queues, scanning time and lane choice belong to the gate module.
- A transfer point is a cell of a one-way service lane, so a vehicle being served stands in the road and blocks the ones behind it, and a route to a bay passes over the transfer points of the bays before it. The traffic module decides whether service blocks the lane or is a delay while the vehicle keeps moving.
- Yard cranes are labels (`RTG1` to `RTG6`, one cluster of blocks each). Where they travel between blocks is not modelled here.

## Validation rules

`load_layout()` checks in three stages and raises `LayoutError` with every failure found in the first stage that has any: (1) file shape and enums, rule 10; (2) bounds, overlaps and ids on the authored file, rules 1 to 3; (3) the compiled map, rules 4 to 9 and 13. Later stages are skipped when an earlier one fails, because the compiled map of a malformed file means nothing; fix the list, reload, and the next stage appears. An error means the file cannot be used; a warning is reported on `layout.warnings` and loading continues. The graph checks run on a networkx `DiGraph` built from `neighbors()`.

| # | Rule | How it is checked | Severity |
| --- | --- | --- | --- |
| 1 | Every rect and cell lies inside width × height | bounds check per entry | error |
| 2 | No two entries cover the same cell | count entries per cell; the transfer-point conversion is the only allowed overlap | error |
| 3 | Every id is unique within its kind and every reference (crane, berth, block) resolves; a block's crane id is not a quay crane id | set membership | error |
| 4 | Every exit of a traversable cell leads to a traversable cell | for each exit, look up the neighbor's type | error |
| 5 | No dead ends: every traversable cell except gate-out has at least one usable exit | count usable exits | error |
| 6 | The internal network is strongly connected: every traversable non-gate cell can reach every other | `nx.is_strongly_connected` on the subgraph without gate cells | error |
| 7 | Every gate-in cell reaches the internal network, every gate-out can be reached from it, and every internal cell reaches some gate-out | `nx.has_path` | error |
| 8 | Every block has a lane cell beside every bay on its service side, and every berth has a quay cell of that berth under every crane; no cell is the transfer point for two things | neighbor lookup per bay and per crane | error |
| 9 | Every transfer point is adjacent to the block or berth it serves | neighbor lookup | error |
| 10 | Shapes and enum values are valid: integers where integers are required, numbers finite and positive, exits in N/E/S/W, cargo in general/reefer/hazmat, flow in in/out, `max_tier` at least 1, no unknown or repeated keys | pydantic, plus a duplicate-key check on read | error |
| 11 | Closures name traversable cells only | set membership | error |
| 12 | A closure set does not disconnect the network or cut off a gate | rules 6 and 7 rerun under the closure | warning |
| 13 | Every block and berth has at least one transfer point | count | error |

Rule 12 is a warning because a scenario may close a lane on purpose to test recovery. The traffic module decides what to do about it; this module only reports it.

**Error format.** One line per failure, naming the rule, the entity and the cell: `rule 8: block A bay 5 has no lane cell on service side S (expected at (6, 1), found a blocked cell)`. A teammate reading the message should be able to open the JSON and fix the line without reading the code.

## 2D and 3D rendering

Both views draw from the compiled JSON and nothing else. Neither view holds any terminal rule of its own; if the 3D page needs to know where a block is, it reads `grid` and `cells`.

**2D (this module, Python).** `render_2d()` writes a PNG with matplotlib: one colored square per cell type, an arrow per lane exit, block ids and bay numbers, crane markers on the quay, gate labels, and closed cells hatched. `show_ids=True` prints coordinates in every cell for debugging. This is the verification view: if the picture is wrong, the file is wrong. It is also the figure for the one-page report. The traffic module can draw vehicles on top of the same axes by calling `render_2d(ax=...)`.

**3D (web page, Three.js).** `viewer3d/index.html` is a single file that loads Three.js from a CDN and fetches `layouts/dames-point.compiled.json`. It draws a flat plane per cell colored by type, a box per yard slot stacked to `max_tier` in a translucent outline (occupied tiers get filled in later by the inventory feed), a simple gantry frame at each quay crane and yard crane, and a water plane. Orbit camera, zoom, and a top-down button. No build step: it runs from `python -m http.server` in the repo root.

**What the poster demo needs from this module:** the static Dames Point map in 3D with an orbiting camera. AGVs moving and containers lifting are the traffic and demand modules' state drawn onto this map; the boundary is that the layout page renders the map and exposes a `world.cellCenter(x, y)` helper, and the animation layer places moving objects with it.

**Decisions deferred until the professor's demo later this month:** whether to stay on Three.js or move to Unity, and how live state reaches the page (events file replay first, websocket later). The JSON format does not change either way, so those decisions do not block anything else.

## Integration contract

Every other module talks to this one through `Layout` or the compiled JSON, never by reading the authored file or importing internals. The layout module imports nothing from the others.

| Module | Needs from layout | Calls |
| --- | --- | --- |
| Traffic | traversable cells, one-move neighbors under closures, transfer points, gate cells, cell centers for drawing | `neighbors`, `is_traversable`, `transfer_points`, `gate_cells`, `with_closures`, `shortest_path` (optional) |
| Demand and schedules | berths and crane ids, blocks and slot ids, the transfer point for a slot, distances for scheduling | `blocks`, `slots_in_block`, `transfer_point_for`, `quay_cells`, `shortest_path` |
| Gate | gate cells and their flow, the lane cells leading in and out | `gate_cells`, `neighbors` |
| Yard or inventory (if split out) | slot ids, `max_tier`, cargo class | `slot`, `slots_in_block`, `blocks` |
| 3D viewer | the compiled JSON | `to_json` |

**What this module needs from them, once, this week:** the naming they want for berths, cranes, blocks and gates (so the Dames Point file uses ids their code expects), and confirmation from the traffic owner that four-neighbor, cell-to-cell movement on a 15 m grid is what they are building. Nothing is needed from them at runtime.

**Repo shape**

```
port-sim/
  terminal_layout/     this module
  demand/
  traffic/
  gate/
  viewer3d/
  layouts/             demo-12x8.json, dames-point.json, compiled outputs, dames-point-trace.png
  tools/               build_dames_point.py
  tests/
  README.md
```

**Merge order.** Layout merges to `main` first, tagged `layout-v0.1`, with the two layout files and passing tests. Traffic and demand build against that tag. Gate is mostly independent and merges when ready. Any change to the JSON format bumps `schema_version` and is announced in the group chat before it merges; the old version keeps loading for one week.

**Working rule.** Each module lives on its own branch and merges by pull request. Conflicts are resolved by whoever opened the PR, with the AI agent's help, as the professor described.

## Verification checklist for AI-generated code

The module passes when every box below is checked and `pytest` is green. Keep the pytest output and the two rendered PNGs; they go in the one-page report as evidence.

**Completeness**

- [ ] Every function in the Python API section exists with the stated signature. Check with a script that imports and calls each one once on the demo layout.
- [ ] Both layout files load, compile, and render without warnings.
- [ ] Every validation rule has a test that breaks it on purpose in a copy of `demo-12x8.json` and asserts the error names that rule.
- [ ] The README explains how to load, validate, render and test in under a page.

**Correctness on the demo layout (hand-checked numbers)**

- [ ] 16 yard slots, `A-01-01` through `A-08-02`.
- [ ] 8 block transfer points at (2, 1) through (9, 1), each serving bay 1 through 8 in order.
- [ ] 2 quay transfer points at (3, 5) and (8, 5), serving QC1 and QC2.
- [ ] `neighbors(5, 1)` returns `[(4, 1)]`; `neighbors(1, 1)` returns `[(1, 0), (0, 1)]` in that order (S before W).
- [ ] `shortest_path((10, 0), (1, 0))` is 11 moves along the service lane.
- [ ] `shortest_path((3, 5), (2, 1))` is 21 moves: east along the quay, south, then west along the service lane. There is no shorter route on a one-way loop.
- [ ] `with_closures({(6, 1)})` makes (2, 1) unreachable from the quay and records a rule 12 warning; the original layout is unchanged.
- [ ] `to_json()` output is identical across two runs and identical to the checked-in compiled file.

**Behavior (look at it)**

- [ ] `render_2d` of the demo matches the ASCII map in this spec cell for cell, including arrow directions.
- [ ] `render_2d` of Dames Point matches the traced screenshot.
- [ ] The 3D page opens from `python -m http.server`, shows the map, orbits, and its top-down view matches the 2D PNG.

**Code reading**

- [ ] Read every file. No function over about 50 lines; no comment claiming behavior the code does not have.
- [ ] The renderers contain no terminal rules. They read `grid` and `cells` and draw.
- [ ] `Layout` is frozen; nothing mutates it after load; no module-level mutable state.
- [ ] Dependencies are exactly pydantic, networkx, matplotlib, plus pytest for tests.
- [ ] `LayoutError` reports every failure, not only the first.

**Where generated code usually goes wrong on this kind of module**

- Silently repairing an invalid file instead of raising. Check by feeding it a broken file.
- Mixing `(x, y)` with `(row, col)` or `grid[x][y]`. Check the corner cells of the demo.
- Off-by-one on inclusive rectangles. Check that `[[2, 1], [11, 1]]` produces 10 cells.
- Generating transfer points on the wrong side of a block, or one per row instead of one per bay.
- Returning neighbors in dictionary or set order, which changes between runs.
- A second, slightly different copy of the movement rule inside the renderer or the 3D page.

## Timeline

Generation is front-loaded into the first two weeks so the remaining six are verification, integration and the 3D view. Week 3 and week 6 are kept light: the midterm lands in the week 3 window (date not announced) and drill takes Oct 17 to 18 and Nov 5 to 8. The poster date is not announced yet; when it is, weeks 7 and 8 shift to end one week before it.

| Week ends | Deliverable | Done when |
| --- | --- | --- |
| Oct 2, 2026 | Spec agreed with the group (ids, cell size, movement rule). Repo skeleton on main. `demo-12x8.json`. Loader and validation generated and verified. | `pytest` green on the demo; every rule test passes |
| Oct 9, 2026 | `render_2d`. Dames Point traced and loading. Compiled JSON checked in. Tag `layout-v0.1`. | Group can `pip install -e .` and call the API |
| Oct 16, 2026 | Closures and warnings. API changes from traffic and demand feedback. Light week: midterm window, drill Oct 17 to 18. | Feedback folded in without breaking `layout-v0.1` callers |
| Oct 23, 2026 | Integration smoke test with traffic: one vehicle runs gate to quay to block to gate on Dames Point using only the API. | The run completes and matches `shortest_path` |
| Oct 30, 2026 | 3D static map: `viewer3d/index.html` shows Dames Point with an orbit camera. | Top-down 3D view matches the 2D PNG |
| Nov 6, 2026 | 3D cranes, block outlines, `cellCenter` helper; animation hook agreed with traffic. Finish by Nov 4 (drill Nov 5 to 8). | Traffic can place a moving box on the map |
| Nov 13, 2026 | Full demo run with all modules. Fix layout issues found. Layout frozen. | No layout changes after this date |
| Nov 20, 2026 | One-page module report, poster figures, final merge. | Report submitted; poster figure approved by the group |

## Prompt for Codex or Claude Code

Save this spec in the repo as `docs/layout-spec.md`, then paste the prompt below into the agent from the repo root. It works the same in either tool. Run it once for the package and tests; give the 3D viewer its own run afterwards so the two do not get tangled.

```
Implement the `terminal_layout` Python package described in docs/layout-spec.md.
Read the whole spec first. Sections 2, 3, 4 and 6 are the contract; do not
deviate from them, and do not add features they do not name.

Constraints:
- Python 3.11+. Dependencies: pydantic, networkx, matplotlib. pytest for tests.
  Nothing else.
- Package layout: terminal_layout/{__init__,schema,loader,graph,validate,
  render}.py, layouts/demo-12x8.json, tests/, examples/demo.py, README.md,
  pyproject.toml.
- Layout is immutable after load. with_closures() returns a new Layout.
- Every list the API returns is in a fixed order: coordinates ascending, or
  N, E, S, W for directions. Never return set or dict iteration order.
- load_layout() raises LayoutError listing every failure, one line each,
  in the format in section 6. Never repair an invalid file silently.
- Rectangles are inclusive on both corners. Cells not covered by any entry
  are blocked/unused.
- Transfer points are generated by the loader as described in section 3.
- The renderer reads grid and cells only; it contains no terminal rules.

Order of work:
1. schema.py: pydantic models for the authored file.
2. loader.py: compile to per-cell form, generate slots and transfer points,
   build the networkx DiGraph, produce to_json().
3. validate.py: all 13 rules from section 6.
4. The Layout class and every function in section 4, with type hints.
5. render.py: render_2d() as described in section 7.
6. tests/: one test per validation rule that breaks a copy of demo-12x8.json
   and asserts the error names that rule; plus every hand-checked number in
   section 9 as its own test; plus a determinism test that runs to_json()
   twice and compares.
7. examples/demo.py: loads layouts/demo-12x8.json, prints counts of each
   cell type, prints the two shortest paths from section 9, writes
   demo.png.
8. README.md: how to install, load, validate, render, and run tests.

When finished, run pytest and examples/demo.py and show me the output.
If any part of the spec is ambiguous or impossible, stop and ask instead of
guessing. List anything you did not implement.
```

Second run, after the first is verified:

```
Read docs/layout-spec.md section 7. Build viewer3d/index.html: a single
file that loads Three.js from a CDN, fetches ../layouts/demo-12x8.compiled.json,
and draws the map as described: a plane per cell colored by type, translucent
stacked boxes per yard slot up to max_tier, a simple gantry frame at each
crane, a water plane, orbit controls, and a top-down button. Expose
window.world.cellCenter(x, y) returning the 3D center of a cell. No build
step; it must run from `python -m http.server` in the repo root. Do not
embed any layout data in the HTML.
```
