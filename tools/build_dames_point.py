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

The real terminal (TraPac, now Ceres) runs six post-panamax quay cranes and six rubber-tired
gantries, which is what is modelled here.

Yard cranes. Six rubber-tired gantries, each given one cluster of blocks: RTG1 to RTG3 the three
block columns of rows A to E (5 blocks each), RTG4 rows F and G, RTG5 the apron strips H1 to H4,
RTG6 the north-west yard N1 to N4. An RTG spans its block plus one truck lane, so the lane
south of each block row is where vehicles stand for the handover: one transfer point per bay,
and the crane lifts straight between the stack and the vehicle (buffer_capacity 0, no
container is set on the ground). A waiting vehicle blocks the lane behind it, as in a real
RTG lane; the parallel service lanes and the bypass lane beside the quay are what keep the
rest of the traffic moving.

Frame. The satellite image is rotated 128 degrees so the wharf runs along +x with the
river on top, which is also how the 12 x 8 demo is drawn. One cell is 15 m (about 7.3
pixels of the 2.06 m/pixel imagery). Column 0 is the south-east end of the site, column 111
the north-west end, row 0 the land side where the gates are.

Circulation (everything is one way; the arrows in render_2d show it):

* Quay (y = 34) and bypass (y = 33). Both flow east the whole length of the site and a
  vehicle may change lane at any cell (the quay lane exits east or south, the bypass lane
  east or north). Under each quay crane four quay cells are transfer points (QUAY_LANES),
  the four transfer lanes of a typical back reach; vehicles for the other cranes pass on the
  bypass lane. Two berths share the quay: B1 (x = 6 to 55, QC1 to QC3) and B2 (x = 56 to
  105, QC4 to QC6), a placeholder split until the ship arrival owner says how many berths
  the schedule needs.
* Down to the yard. From the bypass lane, forks at x = 57, 76, 90, 96 and 104 drop south:
  57 is the east column, 76, 90 and 104 feed the return lane (y = 32, flowing west to the
  east column), 96 is the exit road (also south, to row 1 and the north-west yard's way
  out), and 104 also carries on east to the north-west yard feed (x = 111).
* The east column (x = 57) flows south from the quay and forks west into each service lane
  of the south-east yard (rows A to G). Those lanes flow west. Three roads flow north out of
  the yard: the west column (x = 1) and the two aisles between block columns (x = 18 and
  36). All three reach the outbound road (y = 32), the bypass lane and the quay lane, so
  each block's containers leave by the nearest one.
* The apron strips H1 to H4 have their own lanes (y = 30 and 28), which flow east into the
  inbound road.
* Inbound: the two gate-in lanes (80, 0) and (81, 0) go north, then east to the inbound road
  (x = 86) and north. At y = 32 a truck turns west along the return lane to the east column
  and the south-east yard, or carries on north to the bypass lane and east to the
  north-west yard.
* Outbound: the outbound road (y = 32) runs east from the west column to the east column,
  then south down the column to its foot, east along the south road (y = 5) to x = 88,
  south to row 1 and west to the two gate-out lanes (84, 0) and (85, 0). Row 1 flows west,
  so a truck that misses a gate-out lane loops back into the inbound road.
* Forks and crossings are cells with two exits.
* Charging depots (schema 1.1, passing lane added for 1.2). Each depot is a loop of four
  rows beside a road: a passing lane and an access lane side by side (a vehicle in the
  passing lane drives past one waiting in the access lane and drops into it further along),
  a row of six charging stations below the access lane, and an exit lane that rejoins the
  road. A vehicle can charge in any station and the loop still carries the others past, so
  charging never blocks the road (rule 15 checks this). CH1 to CH6 hang off the west column
  (fork at (1, 28)) and CH7 to CH12 off the inbound road (fork at (86, 29)), both on roads that
  lead back to the quay; tools/charger_siting.py chose the sites (DEPOTS). A depot is a
  detour off the road, so it shortens no trip.
* Transfer buffers. buffer_capacity is 2 everywhere, matching PortSim's finite buffer (0 would be a coupled
  handover). Was 0 on Oct 22: an RTG lane and a quay crane with plain
  AGVs are coupled handovers, nothing waits on the ground. The field stays so a Lift-AGV
  rack terminal can be described (set it per block or crane).
"""

import json
import string
import sys
from pathlib import Path

W, H = 112, 43
QUAY_Y = 34            # apron edge: quay lane, cranes work from here; the berth is the row above
BYPASS_Y = 33          # drive-through lane beside the quay lane, east the whole length
BERTHS = [("B1", 6, 55), ("B2", 56, 105)]   # 100 cells = 1,500 m of the 1,525 m berth, split at 55/56
CRANES_X = {"B1": [14, 31, 48], "B2": [65, 82, 99]}  # six quay cranes, evenly spaced
QUAY_LANES = 4         # transfer lanes under each quay crane (a typical back reach has four)

WEST_X = 1             # one-way column north to the quay
EAST_X = 57            # one-way column south, feeds the south-east yard's service lanes
AISLES = [18, 36]      # gaps between block columns: one-way roads north to the outbound road and quay
IN_X = 86              # inbound access road (north from the gate)
OUT_X = 96             # exit road from the quay fork and the north-west yard (south)
RET_Y = 32             # outbound road west of the east column (east); return lane east of it (west)
RET_X = 88             # gate-out column, from the south road down to row 1
SOUTH_Y = 5            # south road: from the foot of the east column east to the gate-out column
NW_FEED_X = 111        # east end of the quay, feeds the north-west yard going south
NW_SPLIT_X = 104       # return-lane fork that also carries on east to the north-west yard feed
RETURN_FORKS = [76, 90, OUT_X, NW_SPLIT_X]   # bypass cells that also drop south onto the return lane
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
CONTAINER_ROWS = 3     # container rows per cell row: a two-row block is 30 m = 6 containers wide, a 6+1 RTG span
YARD_BUFFER = 2        # containers that can wait on the ground at a yard transfer point (PortSim uses 2; 3 at its Jacksonville layout)
QUAY_BUFFER = 2        # same under each quay crane, per transfer lane
CHARGERS = 6           # charging stations per depot, side by side
# Charging depots, each ("north", column x, bottom row y, side) beside a northbound road, or
# ("south", column x, top row y, side) beside a southbound one, or ("lane", lane_y, fork_x,
# road_x, climb) below a westbound lane. Placed by tools/charger_siting.py: an exact p-median
# solve over every road cell with room for a depot puts the best two beside the two northbound roads
# that lead back to the quay: the west column, and the inbound road (x = 86). Vehicles take those
# roads to the quay anyway, so a charge costs little detour (see docs/layout-spec.md).
DEPOTS = [("north", WEST_X, 27, "E"), ("north", IN_X, 28, "E")]


def build() -> dict:
    lanes: list[dict] = []
    blocks: list[dict] = []
    chargers: list[dict] = []

    def lane(x0, y0, x1, y1, exits, **extra):
        lanes.append({"rect": [[x0, y0], [x1, y1]], "exits": exits, **extra})

    def block(bid, x, y, bays, rows, rtg):
        blocks.append({"id": bid, "origin": [x, y], "bays": bays, "rows": rows, "orientation": "EW",
                       "max_tier": 5, "cargo": "general", "service_side": "S", "crane": f"RTG{rtg}",
                       "buffer_capacity": YARD_BUFFER, "container_rows": CONTAINER_ROWS})

    def stations(cells):
        first = len(chargers)
        chargers.extend({"id": f"CH{first + i + 1}", "cell": [x, y]} for i, (x, y) in enumerate(cells))

    def depot_beside_column(col_x, top_y, flow):
        """A depot east (flow "E") or west (flow "W") of a column that flows south.

        Rows from the top: passing lane (top_y), access lane, stations, exit lane. The column
        cell on the access row forks into the depot; the first depot cell climbs into the
        passing lane or carries on along the access lane; the exit lane rejoins the column
        two rows below the fork.
        """
        passing_y, access_y, bay_y, exit_y = top_y, top_y - 1, top_y - 2, top_y - 3
        sign = 1 if flow == "E" else -1
        back = "W" if flow == "E" else "E"                    # the exit lane flows back toward the column
        x_first = col_x + sign * 2                            # first station, one cell clear of the column
        x_last = col_x + sign * (CHARGERS + 1)
        loop_x = col_x + sign * (CHARGERS + 2)                # beyond the last station, flowing south
        lo, hi = min(x_first, x_last), max(x_first, x_last)
        near = col_x + sign                                   # the cell next to the column on each row
        lane(col_x, access_y, col_x, access_y, ["S", flow])   # the fork cell
        lane(near, access_y, near, access_y, [flow, "N"])     # into the access lane, or up to the passing lane
        lane(near, passing_y, near, passing_y, [flow])
        lane(lo, passing_y, hi, passing_y, [flow, "S"])       # pass a waiting vehicle, drop in further along
        lane(loop_x, passing_y, loop_x, passing_y, ["S"])
        lane(lo, access_y, hi, access_y, [flow, "S"])         # on along the lane, or down into a station
        lane(loop_x, access_y, loop_x, access_y, ["S"])
        lane(lo, bay_y, hi, bay_y, ["S"])                     # the stations; the loader makes them stations
        lane(loop_x, bay_y, loop_x, bay_y, ["S"])
        lane(min(near, loop_x), exit_y, max(near, loop_x), exit_y, [back])
        stations([(x, bay_y) for x in (range(lo, hi + 1) if flow == "E" else range(hi, lo - 1, -1))])
        return exit_y

    def depot_beside_north_column(col_x, bottom_y, flow):
        """A depot east (flow "E") or west ("W") of a column that flows north: the mirror of
        depot_beside_column. Rows from the bottom: passing lane, access lane, stations, exit lane.
        """
        passing_y, access_y, bay_y, exit_y = bottom_y, bottom_y + 1, bottom_y + 2, bottom_y + 3
        sign = 1 if flow == "E" else -1
        back = "W" if flow == "E" else "E"
        x_first, x_last = col_x + sign * 2, col_x + sign * (CHARGERS + 1)
        loop_x = col_x + sign * (CHARGERS + 2)
        lo, hi = min(x_first, x_last), max(x_first, x_last)
        near = col_x + sign
        lane(col_x, access_y, col_x, access_y, ["N", flow])   # the fork cell
        lane(near, access_y, near, access_y, [flow, "S"])     # into the access lane, or down to the passing lane
        lane(near, passing_y, near, passing_y, [flow])
        lane(lo, passing_y, hi, passing_y, [flow, "N"])       # pass a waiting vehicle, climb in further along
        lane(loop_x, passing_y, loop_x, passing_y, ["N"])
        lane(lo, access_y, hi, access_y, [flow, "N"])         # on along the lane, or up into a station
        lane(loop_x, access_y, loop_x, access_y, ["N"])
        lane(lo, bay_y, hi, bay_y, ["N"])                     # the stations
        lane(loop_x, bay_y, loop_x, bay_y, ["N"])
        lane(min(near, loop_x), exit_y, max(near, loop_x), exit_y, [back])
        stations([(x, bay_y) for x in (range(lo, hi + 1) if flow == "E" else range(hi, lo - 1, -1))])

    def depot_below_lane(lane_y, fork_x, aisle_x, climb=True):
        """A depot south of a lane that flows west, draining into a northbound road at aisle_x.

        The lane cell at fork_x drops south into the passing lane; passing and access lanes
        flow west; the exit lane flows west to the road at aisle_x. With climb, the cells of
        that road between the exit lane and the lane are built here (an aisle that starts at
        the lane); without it the road already runs past the exit lane.
        """
        passing_y, access_y, bay_y, exit_y = lane_y - 1, lane_y - 2, lane_y - 3, lane_y - 4
        hi, lo = fork_x - 2, fork_x - 1 - CHARGERS            # stations, east to west
        loop_x = lo - 1
        lane(fork_x, passing_y, fork_x, passing_y, ["W", "S"])
        lane(lo, passing_y, fork_x - 1, passing_y, ["W", "S"])
        lane(loop_x, passing_y, loop_x, passing_y, ["S"])
        lane(fork_x, access_y, fork_x, access_y, ["W"])
        lane(hi + 1, access_y, hi + 1, access_y, ["W", "S"])  # also straight down to the exit lane, so a
        lane(hi + 1, bay_y, hi + 1, bay_y, ["S"])             # full depot still drains (rule 15)
        lane(lo, access_y, hi, access_y, ["W", "S"])
        lane(loop_x, access_y, loop_x, access_y, ["S"])
        lane(lo, bay_y, hi, bay_y, ["S"])
        lane(loop_x, bay_y, loop_x, bay_y, ["S"])
        lane(aisle_x + 1, exit_y, hi + 1, exit_y, ["W"])
        if climb:
            lane(aisle_x, exit_y, aisle_x, lane_y - 1, ["N"])  # back up to the lane cell, which flows north
        stations([(x, bay_y) for x in range(hi, lo - 1, -1)])

    # ---- quay lane and bypass lane: both east, lane changes anywhere ---------------------
    lane(WEST_X, QUAY_Y, WEST_X, QUAY_Y, ["E"])
    lane(WEST_X + 1, QUAY_Y, BERTHS[0][1] - 1, QUAY_Y, ["E", "S"])
    for berth_id, x0, x1 in BERTHS:
        lane(x0, QUAY_Y, x1, QUAY_Y, ["E", "S"], kind="quay", berth=berth_id)
    lane(BERTHS[-1][2] + 1, QUAY_Y, NW_FEED_X - 1, QUAY_Y, ["E", "S"])
    x = WEST_X + 1
    for fork in sorted(AISLES + [EAST_X] + RETURN_FORKS):     # aisle tops and forks are set below
        if x < fork:
            lane(x, BYPASS_Y, fork - 1, BYPASS_Y, ["E", "N"])
        x = fork + 1
    lane(x, BYPASS_Y, NW_FEED_X - 1, BYPASS_Y, ["E", "N"])
    for fork in [EAST_X] + RETURN_FORKS:
        lane(fork, BYPASS_Y, fork, BYPASS_Y, ["E", "S"])

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
    lane_forks: dict[int, set[int]] = {}           # westbound lane row -> cells that also drop south (a depot fork)
    column_forks: set[tuple[int, int]] = set()     # column cells a depot sets itself (its fork)
    for kind, *spec in DEPOTS:
        if kind == "lane":
            lane_forks.setdefault(spec[0], set()).add(spec[1])
        elif kind == "north":
            column_forks.add((spec[0], spec[1] + 1))
        else:
            column_forks.add((spec[0], spec[1] - 1))

    def west_lane(x0, x1, y):
        """A lane from x0 to x1 flowing west, split around any depot fork on it."""
        for fork in sorted(f for f in lane_forks.get(y, ()) if x0 <= f <= x1):
            if x0 < fork:
                lane(x0, y, fork - 1, y, ["W"])
            lane(fork, y, fork, y, ["W", "S"])
            x0 = fork + 1
        if x0 <= x1:
            lane(x0, y, x1, y, ["W"])

    for ys, y0 in zip(service_ys, SE_ROWS):        # lanes flow west, fed from the east column
        has_col1 = y0 >= NO_COL1_BELOW
        x = 2 if has_col1 else SE_COLS[1][0]       # no column-1 block: the lane ends at the aisle before column 2
        for aisle in AISLES + [EAST_X]:
            if x < aisle:
                west_lane(x, aisle - 1, ys)
            if aisle == EAST_X:
                exits = ["S", "W"]                 # the lowest lane also drops onto the south road
            elif aisle == SE_COLS[1][0] - 1 and not has_col1:
                exits = ["N"]
            else:
                exits = ["N", "W"]
            lane(aisle, ys, aisle, ys, exits)
            x = aisle + 1
    strip_lane_ys = [y - 1 for y in STRIP_ROWS]
    for aisle in AISLES:                           # aisles flow north, collecting from the lanes they cross
        for y in range(service_ys[-1] + 1, QUAY_Y):
            if y in service_ys or (aisle, y) in column_forks:
                continue
            lane(aisle, y, aisle, y, ["N", "E"] if y in (RET_Y, BYPASS_Y) else ["N"])
    taken = {RET_Y, BYPASS_Y, *service_ys, *strip_lane_ys}   # east column cells set elsewhere
    for y in range(service_ys[-1] + 1, QUAY_Y):
        if y not in taken and (EAST_X, y) not in column_forks:
            lane(EAST_X, y, EAST_X, y, ["S"])
    lane(EAST_X, RET_Y, EAST_X, RET_Y, ["S"])      # the outbound road ends here and turns south
    west_top = min(ys for ys, y0 in zip(service_ys, SE_ROWS) if y0 >= NO_COL1_BELOW)
    for y in range(west_top, QUAY_Y):              # west column: north from the lowest lane that reaches it
        if (WEST_X, y) not in column_forks:
            lane(WEST_X, y, WEST_X, y, ["N", "E"] if y in (RET_Y, BYPASS_Y) else ["N"])

    # ---- charging depots -----------------------------------------------------------------
    for kind, *spec in DEPOTS:                                # CH1 to CH6, then CH7 to CH12
        {"north": depot_beside_north_column, "south": depot_beside_column, "lane": depot_below_lane}[kind](*spec)

    # ---- south road: foot of the east column east to the gate-out column ----------------
    lane(EAST_X, SOUTH_Y, IN_LOW_X - 1, SOUTH_Y, ["E"])     # the gate-in column crosses it at IN_LOW_X
    lane(IN_LOW_X + 1, SOUTH_Y, RET_X - 1, SOUTH_Y, ["E"])
    lane(RET_X, 2, RET_X, SOUTH_Y, ["S"])

    # ---- apron strip yard: two pairs of one-row blocks, lanes flow east into the inbound road
    for k, (x0, bays) in enumerate(STRIPS):
        block(f"H{2 * k + 1}", x0, STRIP_ROWS[0], bays, 1, 5)
        block(f"H{2 * k + 2}", x0, STRIP_ROWS[1], bays, 1, 5)
    for ys in strip_lane_ys:
        lane(EAST_X + 1, ys, IN_X - 1, ys, ["E"])
        lane(EAST_X, ys, EAST_X, ys, ["S", "E"])

    # ---- inbound road: north from the gate to the return lane and the bypass lane ---------
    for y in range(IN_LOW_Y, BYPASS_Y):
        if (IN_X, y) not in column_forks:
            lane(IN_X, y, IN_X, y, ["N", "W"] if y == RET_Y else ["N"])
    for y in range(1, IN_LOW_Y):                       # gate-in column, then east along row IN_LOW_Y
        lane(IN_LOW_X, y, IN_LOW_X, y, ["N", "E"] if y == SOUTH_Y else ["N"])
    lane(IN_LOW_X, IN_LOW_Y, IN_X - 1, IN_LOW_Y, ["E"])

    # ---- return lane: west from the forks to the east column; east beyond 104 to the north-west yard
    west_lane(EAST_X + 1, IN_X - 1, RET_Y)
    west_lane(IN_X + 1, OUT_X - 1, RET_Y)
    lane(OUT_X, RET_Y, OUT_X, RET_Y, ["S", "W"])        # exit road goes on south; the return lane goes on west
    west_lane(OUT_X + 1, NW_SPLIT_X - 1, RET_Y)
    lane(NW_SPLIT_X, RET_Y, NW_SPLIT_X, RET_Y, ["E", "W"])
    lane(NW_SPLIT_X + 1, RET_Y, NW_FEED_X - 1, RET_Y, ["E"])

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
        if (OUT_X, y) not in column_forks:
            lane(OUT_X, y, OUT_X, y, ["S"])

    # ---- outbound road: east along RET_Y from the west column to the east column
    x = WEST_X + 1
    for aisle in AISLES:                                      # crossings with the northbound aisles
        lane(x, RET_Y, aisle - 1, RET_Y, ["E"])
        x = aisle + 1
    lane(x, RET_Y, EAST_X - 1, RET_Y, ["E"])

    # ---- row 1 flows west past the gate-out lanes and loops back into the inbound road
    out_xs = [x for _, x in GATES_OUT]
    lane(OUT_X, 1, OUT_X, 1, ["W"])
    lane(max(out_xs) + 1, 1, OUT_X - 1, 1, ["W"])
    for x in out_xs:
        lane(x, 1, x, 1, ["S", "W"])                    # fork: down into the gate, or on west
    lane(GATES_IN[-1][1], 1, min(out_xs) - 1, 1, ["W"])  # gate-in lanes feed row 1 and the inbound road

    return {
        "schema_version": "1.2",
        "name": "dames-point",
        "notes": "Simplified JAXPORT Dames Point container terminal, traced by eye from satellite "
                 "imagery (Imagery (c) 2026 Airbus, Maxar Technologies; Map data (c) 2026 Google; Oct 2026), "
                 "rotated so the wharf runs along +x with the river on top. 15 m cells; the two berths "
                 "are 100 cells = 1,500 m of the real 1,525 m. Generated by tools/build_dames_point.py.",
        "cell_size_m": 15,
        "width": W,
        "height": H,
        "water": [
            {"rect": [[0, QUAY_Y + 1], [BERTHS[0][1] - 1, H - 1]]},
            {"rect": [[BERTHS[0][1], QUAY_Y + 2], [BERTHS[-1][2], H - 1]]},
            {"rect": [[BERTHS[-1][2] + 1, QUAY_Y + 1], [W - 1, H - 1]]},
        ],
        "blocked": [
            {"rect": MARSH, "reason": "exclusion", "notes": "marsh and ponds"},
            {"rect": GATE_BUILDING, "reason": "building", "notes": "gate complex"},
        ],
        "berths": [
            {"id": berth_id, "rect": [[x0, QUAY_Y + 1], [x1, QUAY_Y + 1]], "quay_y": QUAY_Y,
             "cranes": [{"id": f"QC{number}", "x": cx, "transfer_lanes": QUAY_LANES,
                         "buffer_capacity": QUAY_BUFFER}
                        for number, cx in zip(crane_numbers(berth_id), CRANES_X[berth_id])]}
            for berth_id, x0, x1 in BERTHS
        ],
        "lanes": lanes,
        "blocks": blocks,
        "gates": [{"id": gid, "cell": [x, 0], "flow": "in", "exits": ["N"]} for gid, x in GATES_IN]
        + [{"id": gid, "cell": [x, 0], "flow": "out", "exits": []} for gid, x in GATES_OUT],
        "chargers": chargers,
        "closures": [],
    }


def crane_numbers(berth_id: str) -> range:
    """QC1 to QC6 numbered along the quay, so B2's cranes carry on from B1's."""
    before = sum(len(CRANES_X[b]) for b, _, _ in BERTHS if b < berth_id)
    return range(before + 1, before + 1 + len(CRANES_X[berth_id]))


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
