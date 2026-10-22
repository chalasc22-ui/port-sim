"""Build layouts/dames-point.json: a simplified JAXPORT Dames Point container terminal.

Run from the repo root (after `pip install -e .`):

    python tools/build_dames_point.py                 # the three files below
    python tools/build_dames_point.py other.json      # other.json and other.compiled.json only

It checks the layout with `load_layout` first and writes nothing if a rule fails (the
problem is printed with its rule number). Otherwise it writes the authored file
(layouts/dames-point.json), the compiled file (layouts/dames-point.compiled.json) and the
picture used in the README (docs/dames-point.png).

The layout was traced by eye from satellite imagery (Google Maps, Oct 2026) and then
written down here as numbers. A test fails if the files in layouts/ differ from what this
script writes, so change the numbers here and run it; do not edit the JSON by hand. A
deliberate change also turns the pinned numbers in tests/test_dames_point.py red (block,
bay and slot counts, crane and gate positions); update them with the change.

Yard cranes. Six rubber-tired gantries, each given one cluster of blocks: RTG1 to RTG3 the three
block columns of rows A to E (5 blocks each), RTG4 rows F and G, RTG5 the apron strips H1 to H4,
RTG6 the north-west yard N1 to N4.

Frame. The satellite image is rotated 128 degrees so the wharf runs along +x with the
river on top, which is also how the 12 x 8 demo is drawn. One cell is 15 m (about 7.3
pixels of the 2.06 m/pixel imagery). Column 0 is the south-east end of the site, column 111
the north-west end, row 0 the land side where the gates are.

Circulation (everything is one way; the arrows in render_2d show it):

* Inbound: the two gate-in lanes (80, 0) and (81, 0) go north, then east to the inbound road
  (x = 86), north to the apron (y = 33) and west along it to the east column (x = 57).
* The east column flows south and forks west into each service lane of the south-east yard
  (rows A to G). Those lanes flow west. Three roads flow north out of the yard: the west
  column (x = 1) and the two aisles between block columns (x = 18 and 36). All three reach
  the outbound road (y = 32) and the quay lane (y = 34), so each block's containers leave by
  the nearest one.
* The apron strips H1 to H4 have their own lanes (y = 30 and 28), which flow east into the
  inbound road.
* The quay lane flows east past the six cranes. At x = 57, 86, 96 and 104 it forks south: the
  first, second and fourth onto the apron lane (y = 33, flowing west), so a crane reaches the
  south-east yard without circling the site, and the third down the exit road (x = 96),
  which also carries the north-west yard's traffic south to row 1. At its east end the quay
  lane feeds the north-west yard (x = 111, flowing south).
* The outbound road (y = 32) runs east from the west column to x = 88, then south to the two
  gate-out lanes (84, 0) and (85, 0). A branch carries on east along y = 32 to the north-west
  yard, so gate traffic reaches it directly. Row 1 flows west, so a truck that misses a
  gate-out lane loops back into the inbound road.
* Forks and crossings are cells with two exits.
* Charging depot (schema 1.1). South of the yard, the east column forks east at (57, 8) into a
  loop of three rows: an access lane (y = 8, flowing east), a row of six charging stations
  CH1 to CH6 (y = 7) and an exit lane (y = 6, flowing west) that rejoins the column at
  (57, 6). A vehicle can charge in any bay and the loop still carries the others past, so
  charging never blocks the road (rule 15 checks this). The loop is 6 moves or more against 2
  for the column, so it shortens no existing trip.
* Transfer buffers (schema 1.1). Every transfer point holds two containers, at the quay and in
  the yard (YARD_BUFFER and QUAY_BUFFER). These are placeholders until the yard and quay
  modules say what they need; the layout only stores the number.
"""

import json
import string
import sys
from pathlib import Path

W, H = 112, 43
QUAY_Y = 34            # apron edge: quay lane, cranes work from here; the berth is the row above
BERTH_X0, BERTH_X1 = 6, 105          # 100 cells = 1,500 m of the 1,525 m berth
CRANES_X = [14, 31, 48, 65, 82, 99]  # six quay cranes, evenly spaced

WEST_X = 1             # one-way column north to the quay
EAST_X = 57            # one-way column south, feeds the south-east yard's service lanes
AISLES = [18, 36]      # gaps between block columns: one-way roads north to the outbound road and quay
IN_X = 86              # inbound access road (north from the gate)
OUT_X = 96             # exit road from the quay fork and the north-west yard (south)
RET_Y = 32             # outbound road, east along this row
RET_X = 88             # outbound column, south to the gate out
APRON_Y = 33           # apron lane, west from the quay forks and the inbound road to the east column
NW_FEED_X = 111        # east end of the quay, feeds the north-west yard going south
APRON_FEED_X = 104     # last quay fork onto the apron lane, so the east cranes reach the yard
QUAY_FORKS = [EAST_X, IN_X, OUT_X, APRON_FEED_X]   # quay cells that also exit south, ascending
GATES_IN = [("G_IN1", 80), ("G_IN2", 81)]     # gate cells on row 0: two lanes in, two lanes out
GATES_OUT = [("G_OUT1", 84), ("G_OUT2", 85)]
IN_LOW_X, IN_LOW_Y = 80, 6   # inbound road swings east to its column below this row

SE_ROWS = [25, 22, 19, 16, 13, 10, 7]    # south-east yard block rows: origin y, 2 rows each
SE_COLS = [(2, 16), (19, 17), (37, 18)]  # block columns: (origin x, bays)
NO_COL1_BELOW = 13     # rows with an origin y below this have no column-1 block (the I-295 ramp)
TRIMS = {"G2": (25, 11)}   # block id -> (origin x, bays), where the ramp clips a block
STRIPS = [(59, 14), (74, 12)]    # apron strips: (origin x, bays); each is two one-row blocks
STRIP_ROWS = [31, 29]            # origin y of the upper and the lower row of a strip
NW_ROWS = [30, 27, 24, 21]       # north-west yard block rows (origin y, 2 rows each)
NW_X, NW_BAYS = 99, 12
MARSH = [[59, 12], [84, 26]]           # marsh and ponds: nothing can be built or driven here
GATE_BUILDING = [[78, 7], [82, 10]]
YARD_BUFFER = 2        # containers that can wait at each yard transfer point (placeholder, see above)
QUAY_BUFFER = 2        # containers that can wait under each quay crane (placeholder, see above)
CHARGE_FORK_Y = 8      # row of the east column cell that forks east into the charging depot
CHARGE_X0 = 59         # x of the first charging station
CHARGERS = 6           # charging stations CH1 to CH6, side by side


def build() -> dict:
    lanes: list[dict] = []
    blocks: list[dict] = []

    def lane(x0, y0, x1, y1, exits, **extra):
        lanes.append({"rect": [[x0, y0], [x1, y1]], "exits": exits, **extra})

    def block(bid, x, y, bays, rows, rtg):
        blocks.append({"id": bid, "origin": [x, y], "bays": bays, "rows": rows, "orientation": "EW",
                       "max_tier": 5, "cargo": "general", "service_side": "S", "crane": f"RTG{rtg}",
                       "buffer_capacity": YARD_BUFFER})

    # ---- quay and apron -----------------------------------------------------
    lane(1, QUAY_Y, BERTH_X0 - 1, QUAY_Y, ["E"])
    x = BERTH_X0                                   # the quay lane flows east; forks drop south onto the apron
    for fork in QUAY_FORKS + [BERTH_X1 + 1]:
        if fork > x:
            lane(x, QUAY_Y, fork - 1, QUAY_Y, ["E"], kind="quay", berth="B1")
        if fork <= BERTH_X1:
            lane(fork, QUAY_Y, fork, QUAY_Y, ["E", "S"], kind="quay", berth="B1")
        x = fork + 1
    lane(BERTH_X1 + 1, QUAY_Y, NW_FEED_X - 1, QUAY_Y, ["E"])
    lane(EAST_X + 1, APRON_Y, IN_X - 1, APRON_Y, ["W"])
    lane(EAST_X, APRON_Y, EAST_X, APRON_Y, ["S"])

    # ---- south-east yard: 7 block rows x 3 block columns --------------------
    service_ys = []                                # service lane rows, top to bottom; a lane sits just south of its blocks
    for i, y0 in enumerate(SE_ROWS):
        has_col1 = y0 >= NO_COL1_BELOW
        for j, (x0, bays) in enumerate(SE_COLS):
            if j == 0 and not has_col1:
                continue
            bid = f"{string.ascii_uppercase[i]}{j + 1}"
            x0, bays = TRIMS.get(bid, (x0, bays))
            block(bid, x0, y0, bays, 2, j + 1 if has_col1 else 4)
        service_ys.append(y0 - 1)
    for ys, y0 in zip(service_ys, SE_ROWS):        # lanes flow west, fed from the east column
        has_col1 = y0 >= NO_COL1_BELOW
        x = 2 if has_col1 else SE_COLS[1][0]       # no column-1 block: the lane ends at the aisle before column 2
        for aisle in AISLES + [EAST_X]:
            if x < aisle:
                lane(x, ys, aisle - 1, ys, ["W"])
            if aisle == EAST_X:
                exits = ["S", "W"] if ys != service_ys[-1] else ["W"]   # the lowest lane ends the column
            elif aisle == SE_COLS[1][0] - 1 and not has_col1:
                exits = ["N"]
            else:
                exits = ["N", "W"]
            lane(aisle, ys, aisle, ys, exits)
            x = aisle + 1
    strip_lane_ys = [y - 1 for y in STRIP_ROWS]
    for aisle in AISLES:                           # aisles flow north, collecting from the lanes they cross
        for y in range(service_ys[-1] + 1, QUAY_Y):
            if y not in service_ys and y != RET_Y:
                lane(aisle, y, aisle, y, ["N"])
    taken = {RET_Y, *service_ys, *strip_lane_ys, CHARGE_FORK_Y}   # east column: south from the apron, skipping cells set elsewhere
    for y in range(service_ys[-1] + 1, APRON_Y):
        if y not in taken:
            lane(EAST_X, y, EAST_X, y, ["S"])
    lane(EAST_X, CHARGE_FORK_Y, EAST_X, CHARGE_FORK_Y, ["S", "E"])   # fork: on south, or east into the charging depot
    west_top = min(ys for ys, y0 in zip(service_ys, SE_ROWS) if y0 >= NO_COL1_BELOW)
    for y in range(west_top, QUAY_Y):              # west column: north from the lowest lane that reaches it
        lane(WEST_X, y, WEST_X, y, ["N", "E"] if y == RET_Y else ["N"])

    # ---- charging depot: a loop beside the east column (access lane, bays, exit lane) ---------
    last_bay = CHARGE_X0 + CHARGERS - 1
    loop_x = last_bay + 1                                   # east side of the loop, flowing south
    access_y, bay_y, exit_y = CHARGE_FORK_Y, CHARGE_FORK_Y - 1, CHARGE_FORK_Y - 2
    lane(EAST_X + 1, access_y, CHARGE_X0 - 1, access_y, ["E"])        # from the fork to the first bay
    lane(CHARGE_X0, access_y, last_bay, access_y, ["E", "S"])         # on east, or down into a bay
    lane(loop_x, bay_y, loop_x, access_y, ["S"])                      # around the east end
    lane(CHARGE_X0, bay_y, last_bay, bay_y, ["S"])                    # the bays; the loader makes them stations
    lane(EAST_X + 1, exit_y, loop_x, exit_y, ["W"])                   # exit lane, west into the east column

    # ---- apron strip yard: two pairs of one-row blocks, lanes flow east into the inbound road
    for k, (x0, bays) in enumerate(STRIPS):
        block(f"H{2 * k + 1}", x0, STRIP_ROWS[0], bays, 1, 5)
        block(f"H{2 * k + 2}", x0, STRIP_ROWS[1], bays, 1, 5)
    for ys in strip_lane_ys:
        lane(EAST_X + 1, ys, IN_X - 1, ys, ["E"])
        lane(EAST_X, ys, EAST_X, ys, ["S", "E"])

    # ---- inbound road: north from the gate, then along the apron -------------
    for y in range(IN_LOW_Y, APRON_Y):
        lane(IN_X, y, IN_X, y, ["N", "E"] if y == RET_Y else ["N"])
    lane(IN_X, APRON_Y, IN_X, APRON_Y, ["W"])
    lane(IN_X + 1, APRON_Y, OUT_X - 1, APRON_Y, ["W"])   # apron lane: quay forks and the inbound road feed it
    lane(OUT_X, APRON_Y, OUT_X, APRON_Y, ["S", "W"])     # exit road goes on south; the apron lane goes on west
    lane(OUT_X + 1, APRON_Y, APRON_FEED_X, APRON_Y, ["W"])
    for y in range(1, IN_LOW_Y):                       # gate-in column, then east along row IN_LOW_Y
        lane(IN_LOW_X, y, IN_LOW_X, y, ["N"])
    lane(IN_LOW_X, IN_LOW_Y, IN_X - 1, IN_LOW_Y, ["E"])

    # ---- north-west yard: 4 block rows, fed from the quay's east end ---------
    for i, y0 in enumerate(NW_ROWS):
        block(f"N{i + 1}", NW_X, y0, NW_BAYS, 2, 6)
    nw_service = [y0 - 1 for y0 in NW_ROWS]
    lane(NW_FEED_X, QUAY_Y, NW_FEED_X, QUAY_Y, ["S"])
    for y in range(nw_service[-1] + 1, QUAY_Y):
        lane(NW_FEED_X, y, NW_FEED_X, y, ["S", "W"] if y in nw_service else ["S"])
    lane(NW_FEED_X, nw_service[-1], NW_FEED_X, nw_service[-1], ["W"])
    for ys in nw_service:
        lane(OUT_X + 1, ys, NW_FEED_X - 1, ys, ["W"])
    for y in range(2, RET_Y):                           # exit road: from the quay fork down to row 1
        lane(OUT_X, y, OUT_X, y, ["S"])
    lane(OUT_X, RET_Y, OUT_X, RET_Y, ["S", "E"])        # crossing with the east lane to the north-west yard

    # ---- outbound road: east along RET_Y from the west column, then south to the gate out
    x = WEST_X + 1
    for aisle in AISLES:                                      # crossings with the northbound aisles
        lane(x, RET_Y, aisle - 1, RET_Y, ["E"])
        lane(aisle, RET_Y, aisle, RET_Y, ["N", "E"])
        x = aisle + 1
    lane(x, RET_Y, EAST_X - 1, RET_Y, ["E"])
    lane(EAST_X, RET_Y, EAST_X, RET_Y, ["S", "E"])           # crossing with the east column
    lane(EAST_X + 1, RET_Y, IN_X - 1, RET_Y, ["E"])
    lane(IN_X + 1, RET_Y, RET_X - 1, RET_Y, ["E"])
    for y in range(2, RET_Y):
        lane(RET_X, y, RET_X, y, ["S"])
    lane(RET_X, RET_Y, RET_X, RET_Y, ["S", "E"])        # south to the gate out, or on east to the north-west yard
    lane(RET_X + 1, RET_Y, OUT_X - 1, RET_Y, ["E"])     # east lane: gate traffic reaches the north-west yard directly
    lane(OUT_X + 1, RET_Y, NW_FEED_X - 1, RET_Y, ["E"])

    # ---- row 1 flows west past the gate-out lanes and loops back into the inbound road
    out_xs = [x for _, x in GATES_OUT]
    lane(OUT_X, 1, OUT_X, 1, ["W"])
    lane(max(out_xs) + 1, 1, OUT_X - 1, 1, ["W"])
    for x in out_xs:
        lane(x, 1, x, 1, ["S", "W"])                    # fork: down into the gate, or on west
    lane(GATES_IN[-1][1], 1, min(out_xs) - 1, 1, ["W"])  # gate-in lanes feed row 1 and the inbound road

    return {
        "schema_version": "1.1",
        "name": "dames-point",
        "notes": "Simplified JAXPORT Dames Point container terminal, traced by eye from satellite "
                 "imagery (Imagery (c) 2026 Airbus, Maxar Technologies; Map data (c) 2026 Google; Oct 2026), "
                 "rotated so the wharf runs along +x with the river on top. 15 m cells; the berth is "
                 "100 cells = 1,500 m of the real 1,525 m. Generated by tools/build_dames_point.py.",
        "cell_size_m": 15,
        "width": W,
        "height": H,
        "water": [
            {"rect": [[0, QUAY_Y + 1], [BERTH_X0 - 1, H - 1]]},
            {"rect": [[BERTH_X0, QUAY_Y + 2], [BERTH_X1, H - 1]]},
            {"rect": [[BERTH_X1 + 1, QUAY_Y + 1], [W - 1, H - 1]]},
        ],
        "blocked": [
            {"rect": MARSH, "reason": "exclusion", "notes": "marsh and ponds"},
            {"rect": GATE_BUILDING, "reason": "building", "notes": "gate complex"},
        ],
        "berths": [{"id": "B1", "rect": [[BERTH_X0, QUAY_Y + 1], [BERTH_X1, QUAY_Y + 1]], "quay_y": QUAY_Y,
                    "cranes": [{"id": f"QC{i + 1}", "x": x, "buffer_capacity": QUAY_BUFFER}
                               for i, x in enumerate(CRANES_X)]}],
        "lanes": lanes,
        "blocks": blocks,
        "gates": [{"id": gid, "cell": [x, 0], "flow": "in", "exits": ["N"]} for gid, x in GATES_IN]
        + [{"id": gid, "cell": [x, 0], "flow": "out", "exits": []} for gid, x in GATES_OUT],
        "chargers": [{"id": f"CH{i + 1}", "cell": [CHARGE_X0 + i, bay_y]} for i in range(CHARGERS)],
        "closures": [],
    }


def dumps(data: dict) -> str:
    """Stable text: one top-level key per line, and one list entry per line."""
    lines = ["{"]
    for n, (key, value) in enumerate(data.items()):
        comma = "," if n < len(data) - 1 else ""
        if isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f" {json.dumps(key)}: [")
            lines += [f"  {json.dumps(entry)}{',' if i < len(value) - 1 else ''}" for i, entry in enumerate(value)]
            lines.append(f" ]{comma}")
        else:
            lines.append(f" {json.dumps(key)}: {json.dumps(value)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from terminal_layout import LayoutError
    from terminal_layout.loader import layout_from_dict

    data = build()
    try:
        layout = layout_from_dict(data)
    except LayoutError as exc:
        print(f"nothing written, the layout does not load:\n{exc}")
        return 1
    default = len(argv) < 2
    out = root / "layouts" / "dames-point.json" if default else Path(argv[1])
    compiled = out.with_name(out.stem + ".compiled.json")
    out.write_text(dumps(data), encoding="utf-8", newline="\n")
    layout.to_json(compiled)
    written = [out.name, compiled.name]
    if default:
        layout.render_2d(root / "docs" / "dames-point.png")
        written.append("docs/dames-point.png")
    slots = sum(len(b.slots) for b in layout.blocks.values())
    print(f"wrote {', '.join(written)}: {layout}, {len(layout.blocks)} blocks, {slots} slots, "
          f"{len(layout.warnings)} warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
