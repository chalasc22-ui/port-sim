"""Where should the charging depots go? A p-median optimization on the Dames Point map.

Run from the repo root:

    python tools/charger_siting.py            # prints the table and writes docs/charger-siting.csv

The question the professor framed on Oct 22 as a resource allocation problem is, in operations
research terms, the p-median problem: choose k sites so that the total distance from every
demand point to its nearest site is as small as possible.

* Demand points: every transfer point (443): where a vehicle finishes a job and may head off to charge.
* Candidate sites: every plain lane cell with a 9 x 4 block of open ground beside it, room for a
  depot like the built ones (six stations, passing lane, access lane, exit lane).
* Cost of charging at site j after a job at i: moves from i to j, plus moves from j back to the
  nearest quay transfer point (the next job usually starts at a quay crane). Same metric as the
  four-site comparison in docs/layout-spec.md.
* Solved exactly as an integer program (scipy.optimize.milp, HiGHS) for k = 1 to 4.

A site here is a single road cell, so the optimum leaves out the few extra moves of a real depot's
loop (4 to 8); the built depots are scored by their actual station cells, which include them.
"""

from __future__ import annotations

import csv
import sys
from collections import deque
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

ROOT = Path(__file__).resolve().parent.parent
DEPOT_W, DEPOT_D = 9, 4   # open ground a depot needs: six stations plus its loop, four rows deep


def bfs(layout, sources, reverse=False):
    """Moves from any source (or to any source, with reverse) to every reachable cell."""
    if reverse:
        back = {}
        for c in layout.cells():
            if layout.is_traversable(*c.pos):
                for n in layout.neighbors(*c.pos):
                    back.setdefault(n, []).append(c.pos)
        step = lambda p: back.get(p, [])  # noqa: E731
    else:
        step = lambda p: layout.neighbors(*p)  # noqa: E731
    dist = {s: 0 for s in sources}
    queue = deque(sources)
    while queue:
        here = queue.popleft()
        for nxt in step(here):
            if nxt not in dist:
                dist[nxt] = dist[here] + 1
                queue.append(nxt)
    return dist


def problem(layout):
    demand = [c.pos for c in layout.cells("transfer_point")]
    quay = [c.pos for c in layout.cells("transfer_point") if "berth" in c.attrs["serves"]]
    to_quay = bfs(layout, quay, reverse=True)          # cell -> moves to the nearest quay transfer point

    def is_open(x, y):
        if not (0 <= x < layout.width and 0 <= y < layout.height):
            return False
        c = layout.cell(x, y)
        return c.type == "blocked" and c.attrs.get("reason") == "unused"

    def room_for_a_depot(x, y):
        """A DEPOT_W x DEPOT_D block of open ground touching this road cell on one side."""
        for w, d in ((DEPOT_W, DEPOT_D), (DEPOT_D, DEPOT_W)):
            for x0 in range(x - w, x + 2):
                for y0 in range(y - d, y + 2):
                    touches = (x0 <= x < x0 + w and (y0 == y + 1 or y0 + d == y)) or \
                              (y0 <= y < y0 + d and (x0 == x + 1 or x0 + w == x))
                    if touches and all(is_open(a, b) for a in range(x0, x0 + w) for b in range(y0, y0 + d)):
                        return True
        return False

    candidates = [c.pos for c in layout.cells("lane") if room_for_a_depot(*c.pos)]
    from_demand = {i: bfs(layout, [i]) for i in demand}
    cost = np.array([[from_demand[i][j] + to_quay[j] for j in candidates] for i in demand], dtype=float)
    return demand, candidates, cost, from_demand, to_quay


def solve(cost: np.ndarray, k: int):
    """Exact p-median: y_j = site open, x_ij = demand i served by site j."""
    n, m = cost.shape
    nv = m + n * m
    c = np.concatenate([np.zeros(m), cost.ravel()])
    rows, cols, vals, lo, hi = [], [], [], [], []
    r = 0
    for i in range(n):                                 # each demand point served once
        rows += [r] * m; cols += list(range(m + i * m, m + (i + 1) * m)); vals += [1] * m
        lo.append(1); hi.append(1); r += 1
    rows += [r] * m; cols += list(range(m)); vals += [1] * m   # exactly k sites
    lo.append(k); hi.append(k); r += 1
    for i in range(n):                                 # x_ij <= y_j
        for j in range(m):
            rows += [r, r]; cols += [m + i * m + j, j]; vals += [1, -1]
            lo.append(-np.inf); hi.append(0); r += 1
    a = coo_array((vals, (rows, cols)), shape=(r, nv)).tocsr()
    res = milp(c, constraints=LinearConstraint(a, lo, hi), integrality=np.r_[np.ones(m), np.zeros(n * m)],
               bounds=Bounds(0, 1), options={"time_limit": 300})
    if res.x is None:
        raise RuntimeError(res.message)
    sites = [j for j in range(m) if res.x[j] > 0.5]
    return sites, cost[:, sites].min(axis=1)


def stations_of(layout):
    return layout.charging_stations()


def built(layout, demand, from_demand, to_quay):
    """Score the depots as built: best station for each demand point, real station cells."""
    stations = {c.attrs["id"]: c.pos for c in layout.charging_stations()}
    def score(ids):
        return np.array([min(from_demand[i][stations[s]] + to_quay[stations[s]] for s in ids) for i in demand])
    first = [f"CH{n}" for n in range(1, 7)]
    return {"built, west-column depot only": score(first), "built, both depots": score(list(stations))}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from terminal_layout import load_layout

    import importlib.util
    from terminal_layout.loader import layout_from_dict

    layout = load_layout(ROOT / "layouts" / "dames-point.json")
    spec = importlib.util.spec_from_file_location("bdp", ROOT / "tools" / "build_dames_point.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.DEPOTS = []                       # candidates come from the map with no depots built yet
    bare = layout_from_dict(builder.build())
    demand, candidates, cost, _, _ = problem(bare)
    _, _, _, from_demand, to_quay = problem(layout)
    print(f"{len(demand)} demand points, {len(candidates)} candidate sites")
    rows = []
    for name, d in built(layout, demand, from_demand, to_quay).items():
        rows.append({"layout": name, "depots": 1 if "only" in name else len(stations_of(layout)) // 6, "sites": "",
                     "mean": round(d.mean(), 1), "median": float(np.median(d)), "worst": int(d.max())})
    for k in range(1, 5):
        sites, d = solve(cost, k)
        rows.append({"layout": f"optimal, {k} depot{'s' if k > 1 else ''}", "depots": k,
                     "sites": " ".join(f"({candidates[j][0]},{candidates[j][1]})" for j in sites),
                     "mean": round(d.mean(), 1), "median": float(np.median(d)), "worst": int(d.max())})
    for r in rows:
        print(f"{r['layout']:42s} mean {r['mean']:6.1f}  median {r['median']:6.1f}  worst {r['worst']:4d}  {r['sites']}")
    out = ROOT / "docs" / "charger-siting.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
