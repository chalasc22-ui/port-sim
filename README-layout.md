# terminal_layout

The terminal layout and topology module of the port simulator. It loads a terminal
description from a JSON file, checks it, and gives every other module one read-only
`Layout` object to ask questions of: what is at this cell, where can a truck go from
here, which cell serves this yard slot.

Python 3.11+. Depends on `pydantic`, `networkx` and `matplotlib` (plus `pytest` for tests).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                             # 140 tests, a few seconds
python examples/demo.py            # prints a tour and writes layout.png
```

## Using it

```python
from terminal_layout import load_layout, LayoutError

layout = load_layout("layouts/demo-12x8.json")     # raises LayoutError listing every problem

layout.cell(4, 1)                         # Cell(x, y, type, exits, attrs)
layout.neighbors(1, 1)                    # [(1, 0), (0, 1)]  one move away, ordered N, E, S, W
layout.shortest_path((10, 0), (1, 0))     # list of (x, y), or None
layout.transfer_point_for("A-03-02")      # the cell a crane works that slot from
layout.render_2d("layout.png")            # picture with arrows, ids, cranes, gates

closed = layout.with_closures({(5, 1)})   # a new Layout; `layout` is untouched
closed.warnings                           # rule 12: what the closure breaks
```

The `Layout` is immutable. Closures are a scenario you apply to a copy, never a change
to the map. Everything returned from it is a copy or read-only.

### API

| Call | Returns |
|---|---|
| `layout.name`, `.width`, `.height`, `.cell_size_m` | metadata |
| `cell(x, y)` | the `Cell`; `IndexError` off the grid |
| `cells(type=None)` | all cells, or one type, ordered by (x, y) |
| `is_traversable(x, y)` | True for an open lane, quay, transfer point or gate cell |
| `exits(x, y)` | directions the static map allows, ordered N, E, S, W |
| `neighbors(x, y)` | cells one legal move away (closures applied), ordered N, E, S, W |
| `quay_cells(berth_id=None)` | quay strip cells, including transfer points under cranes |
| `gate_cells(flow=None)` | gate cells, or only `"in"` / `"out"` |
| `transfer_points(block_id=None, berth_id=None)` | transfer-point cells |
| `transfer_point_for(slot_id)` | the transfer point serving that slot's bay |
| `slot(slot_id)`, `slots_in_block(block_id)` | slot cell; slot ids ordered by bay, then row |
| `blocks` | `{block_id: Block}` |
| `berths` | `{berth_id: ((crane_id, (x, y)), ...)}` |
| `with_closures(cells)` | new `Layout` with exactly those cells closed (replace, not add) |
| `closures`, `warnings` | current closed cells; rule 12 warnings for them |
| `shortest_path(a, b)` | fewest-move path, both ends included, or `None` |
| `validate()` | list of problems, empty when valid |
| `to_json(path=None)` | compiled dict for other modules; also written to `path` |
| `render_2d(path="layout.png", show_ids=False, ax=None)` | PNG, or draws onto your `ax` |

Cell types: `water`, `berth`, `quay`, `lane`, `transfer_point`, `yard_slot`, `gate`, `blocked`.
Traversable ones: `quay`, `lane`, `transfer_point`, `gate`.

### Coordinates

Integer `(x, y)`, x east, y north, origin at the bottom-left cell. One cell is
`cell_size_m` metres square (15 by default). A cell's `exits` are the directions a
vehicle may leave it in: any of `N`, `E`, `S`, `W`, always listed in that order.
A one-way road is a run of cells with a single exit each.

## Layout files

Two forms:

- **Authored** (`layouts/demo-12x8.json`): what a person writes and edits. Rectangles
  of water, lanes with exits, yard blocks with a size and origin, berths with cranes, gates.
  (`layouts/dames-point.json` is authored too, but a script writes it; see below.)
- **Compiled** (`layouts/demo-12x8.compiled.json`): what `layout.to_json()` produces. A
  full grid plus every cell's attributes. Other modules read this; nobody edits it.
  It is regenerated, and a test fails if it is out of date:

  ```bash
  python -c "from terminal_layout import load_layout; load_layout('layouts/demo-12x8.json').to_json('layouts/demo-12x8.compiled.json')"
  ```

To add a terminal, copy `demo-12x8.json`, edit it, and run `load_layout` on it. Errors
come back as a list, every one tagged with a rule number, for example:

```
2 problems found:
  rule 4: lane cell (11, 5) exit N leads to (11, 6), which is not traversable (it is a berth cell)
  rule 8: block A bay 4 has no lane cell on service side S (expected at (5, 1))
```

Problems are reported in stages (file shape, then bounds/overlaps/ids, then the compiled
map), and a later stage waits until the earlier one is clean. Nothing is repaired silently.

## Dames Point

`layouts/dames-point.json` is the poster layout: JAXPORT's Dames Point container terminal in
Jacksonville, simplified to straight roads on a 15 m grid. It was traced by eye from satellite
imagery and written down as numbers in `tools/build_dames_point.py`.

![Dames Point, drawn by render_2d](docs/dames-point.png)

| | |
|---|---|
| Grid | 112 x 43 cells (1,680 m by 645 m). The wharf runs along x with the river on top; column 0 is the south-east end of the site and row 0 the land side |
| Berth | `B1`, 100 cells = 1,500 m of the real 1,525 m. Six quay cranes `QC1` to `QC6`, 17 cells (255 m) apart |
| Yard | 27 blocks, 419 bays, 786 ground slots, max tier 5: the south-east yard `A1` to `G3`, strip blocks `H1` to `H4` along the apron, the north-west yard `N1` to `N4` |
| Yard cranes | `RTG1` to `RTG3` take the three block columns of rows A to E, `RTG4` rows F and G, `RTG5` the strips, `RTG6` the north-west yard |
| Gates | in: `G_IN1` (80, 0), `G_IN2` (81, 0). out: `G_OUT1` (84, 0), `G_OUT2` (85, 0) |
| Not roads | the marsh and ponds in the middle (`blocked`, reason `exclusion`) and the gate buildings (reason `building`) |

`layouts/dames-point-trace.png` is the grid laid over the satellite photo, so anyone can see
where the cells came from. It carries the imagery credit (Airbus, Maxar Technologies, Google).

**How vehicles move.** Every road is one way, so the arrows in the picture are the whole
traffic plan. Trucks come in at a gate-in lane, go north to the apron road and west to the east
column, which feeds the service lane under each block. The service lanes of the south-east
yard flow west. Three roads carry traffic north out of the yard: the west column and two aisles
between block columns. They join the outbound road, which runs east and south to the gate-out
lanes, and the quay lane, which runs east past all six cranes. The quay lane forks south in four
places (x = 57, 86, 96 and 104) so a crane reaches the yard without circling the site. Where two
roads meet, the cell has two exits.

One-way roads make trips long. The longest leg between a gate, a block and a crane is about
230 moves (3.4 km); a test fails if any leg goes over 250.

**Changing it.** Edit the numbers in `tools/build_dames_point.py` and run it. It checks the
layout with `load_layout` first and writes nothing if a rule fails; otherwise it rewrites both
layout files and `docs/dames-point.png`. A test fails if the files in `layouts/` were edited by
hand or are out of date. A deliberate change also turns the pinned numbers in
`tests/test_dames_point.py` red (block, bay and slot counts, crane and gate positions); update
them in the same change.

```bash
python tools/build_dames_point.py
```

## Limits in v0.1

- Width and height are at most 500 cells each.
- A quay runs east-west only (a berth has one `quay_y` row; cranes sit at an `x` on it).
- One service-lane cell is the transfer point for one block bay, so two blocks cannot share
  one lane row. Give each block its own lane row (two adjacent rows is fine). The loader
  says so as a rule 8 conflict.
- `service_side` runs parallel to the bays: `N`/`S` for an `EW` block, `E`/`W` for an `NS` block.
- Unknown keys are errors. For remarks while tracing a real terminal, use `"notes": "..."`,
  which is allowed on the file and on every entry and is never read.
- `with_closures` replaces the closed set; it does not add to it.

## The rules

| # | Checked | Where |
|---|---|---|
| 1 | Every rectangle, cell, crane and quay row is inside the grid, low corner first | authored |
| 2 | Nothing shares a cell (closures may sit on lanes) | authored |
| 3 | Ids are unique; lanes only name berths that exist | authored |
| 4 | Every exit leads to a traversable cell inside the grid | map |
| 5 | No dead ends (a gate-out is allowed to have no exit) | map |
| 6 | The internal network is strongly connected | map |
| 7 | Gate-ins reach the network; every gate-out can be reached from it; every internal cell can reach a gate-out | map |
| 8 | Each bay has a lane cell on its service side; each crane has a quay cell under it | authored + map |
| 9 | Each transfer point touches the bay or berth it serves | map |
| 10 | Shapes and enums are right; unknown keys are errors (so `"exist"` is caught) | file |
| 11 | Closures name traversable cells only | `with_closures` |
| 12 | Warn if a closure breaks rule 6 or 7 (not an error: closing a road is allowed) | `with_closures` |
| 13 | Every block and berth has at least one transfer point | map |

## Layout of the repo

```
terminal_layout/
  model.py      Cell, Block, LayoutError, directions, frozen data
  schema.py     pydantic models for the authored file (rule 10)
  authored.py   rules 1-3 and the block geometry
  loader.py     load_layout: stages, painting the grid, rule 8
  validate.py   rules 4-9, 11-13 on the compiled map
  graph.py      networkx graph, rules 6, 7 and 12
  layout.py     the Layout class (the API above)
  export.py     compiled JSON and its on-disk formatting
  render.py     render_2d
layouts/        demo-12x8.json, dames-point.json, their compiled forms, dames-point-trace.png
tools/          build_dames_point.py: writes and checks the Dames Point files
tests/          140 tests: every rule, the API, bad input, rendering, a 100 x 40 layout, Dames Point
examples/demo.py
docs/           layout-spec.md (the spec this was built from), demo-12x8.png, dames-point.png
```

## What the other modules should rely on

- Call `neighbors`, `is_traversable` and `shortest_path`; do not re-derive movement from `exits`.
- Read `to_json()` if you want plain data; read `Layout` if you want the API.
- Cell order, slot order and exit order are fixed, so runs with the same seed repeat.
- A transfer point is an ordinary lane cell on a one-way road (the service lane under each
  bay). A vehicle being served there stands in that road and blocks the ones behind it, unless
  the traffic module lets it wait elsewhere or treats service as a delay while it keeps moving.
  A route to a bay passes over the transfer points of the bays before it on the same lane.
- Do not mutate anything you get back. You can't: it is frozen.
