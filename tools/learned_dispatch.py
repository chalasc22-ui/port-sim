"""Learn the dispatch rule of sim/dispatch_learned.py by Q-learning in the simulator, test it, and write it up.

Run from the repo root:

    python tools/learned_dispatch.py train     # learns the weights: writes sim/dispatch_weights.json and the learning curve
    python tools/learned_dispatch.py test      # the learned rule against the others on the experiment's 30 shifts
    python tools/learned_dispatch.py report    # docs/learned-dispatch.md and its two figures, from what the first two wrote
    python tools/learned_dispatch.py report --check     # exit 1 if a written file is out of date (a test runs this)

What is learned. Whenever the fleet can start a job it has a set of choices: every (waiting job, free AGV)
pair. The rule gives each pair a score, a sum of weights for what sim/dispatch_learned.py looks at, and
starts the pair with the lowest. Training finds the weights.

How. Q-learning in its batch form, with a linear Q-function and n-step targets (fitted Q iteration):

  * An episode is one shift at real scale (sim.scenario.shift), on a seed the experiment never uses.
  * The cost to keep low is read off the running terminal every ten seconds of terminal time. The reading
    only reads: a run with it is the same run as without. COSTS below says what counts.
  * For each choice made, the target is the cost that followed: the cost of the next fifteen minutes as it
    happened, each minute counting less than the one before (half as much after five), plus the rule's own
    estimate of what comes after that, for the best choice open at that later moment. That last part is the
    Q-learning step. With these numbers seven eighths of a target is cost that was seen.
  * Exploring: in the first round every choice is made by lot, so the rule starts knowing nothing. In later
    rounds it follows the weights fitted so far, except for a share of choices still made by lot.
  * The fit uses the choices made by lot and only those. Most of the cost that follows a choice has nothing
    to do with the choice: it is how busy the terminal is. Where the rule itself chose, what it chose
    depends on the situation, and a plain fit would credit the choice with the situation. Where the lot
    chose, the chosen pair differs from the average pair on offer by chance alone, so the fit can tell the
    two apart: it explains the target by the situation (six numbers, and the average pair on offer) and by
    the pair chosen, and the weights of the pair chosen are the rule. Least squares, on every such choice so
    far, after each round.

After every round the rule as it then stands, with no choices by lot, runs eight development shifts (seeds
5001 to 5008): that is the learning curve, beside three rules written by hand on the same shifts. Training
ends with two checks: how far each weight moves when one training shift is left out (a jackknife), and what
the plain fit would have learned from the same shifts. The test uses the experiment's own seeds, 1 to 30,
which training and development never touch.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402

from sim import agv as fleets  # noqa: E402
from sim import dispatch_learned as rules  # noqa: E402
from sim import scenario as scenarios  # noqa: E402
from sim.metrics import check, summary  # noqa: E402
from sim.terminal import Terminal  # noqa: E402

DOCS = ROOT / "docs"
TRAINING = DOCS / "learned-dispatch-training.json"
RESULTS = DOCS / "learned-dispatch.json"
REPORT = DOCS / "learned-dispatch.md"
CURVE_SVG = DOCS / "learned-dispatch-curve.svg"
TEST_SVG = DOCS / "learned-dispatch-test.svg"
TRAIN_FROM = 1001                                   # training shifts are seeds 1001, 1002, ...
DEV_SEEDS = tuple(range(5001, 5009))                # the learning curve, and every choice made while building this
TEST_SEEDS = tuple(range(1, 31))                    # the experiment's own (tools/experiments.py): never trained or tuned on
EPSILON = (1.0, 0.5, 0.4, 0.3, 0.3, 0.2, 0.2, 0.2)  # the share of choices made by lot, round by round
EPISODES = (8, 16, 16, 16, 16, 16, 16, 16)          # shifts in each round
TICK_S = 10.0                                       # the cost is read this often
HALF_S = 300.0                                      # a minute of cost counts half as much five minutes later
WINDOW_S = 900.0                                    # the cost is followed this long before the rule's own estimate takes over
RIDGE = 1e-4                                        # keeps the fit steady where a feature is rare
CONTEXT = ("level", "jobs_waiting", "agvs_coming", "agvs_free", "cost_now", "cranes_at_work")
EMPTY_DRIVE = 0.2                                   # cost "blocked": a minute of an AGV driving to a pickup, against one of a blocked crane (assumption)
COSTS = {                                           # what a rule can be trained to keep low; "expects" is how a noted reason names it
    "jobs": {"what": "the minutes jobs wait for an AGV: at every reading, one for each job with no AGV at its pickup yet", "expects": "waiting"},
    "blocked": {"what": f"what a late AGV holds up: at every reading, one for each quay crane with a container to hand over and no AGV there, one for each "
                        f"export or truck job with no AGV at its pickup yet, and {EMPTY_DRIVE:g} for each AGV driving to a pickup", "expects": "delay"},
}
DEFAULT_COST = "jobs"
MEASURES = ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "truck_turnaround_min", "quay_crane_wait_min", "agv_km", "end_min")
CURVE = (*MEASURES, "cost_min", "job_wait_max_min")
RULES = ("nearest_feasible", "longest_idle", "balanced", "nearest_job", "learned")
YARDSTICKS = (("nearest_feasible", "nearest_feasible", None), ("nearest_job", "nearest_job", None),
              ("nearest_job_without_guard", "nearest_job", {"guard": False}))       # rules written by hand, on the development shifts
TABLE = ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "truck_turnaround_min", "quay_crane_wait_min", "agv_km",
         "job_wait_max_min", "cost_min")
PAIRS = (("learned", "nearest_feasible"), ("learned", "nearest_job"), ("nearest_job", "nearest_feasible"), ("longest_idle", "nearest_feasible"),
         ("balanced", "nearest_feasible"))                # each rule against another, shift by shift
CARRY = ({"agvs": 18}, {"agvs": 36}, {"scale": "small"})   # what the rule was not trained on: other fleets (it learned with 30), and the small workloads
PLAN = {"scale": "plan"}                            # and the one morning of the plan (sim.scenario.demo), which has no seed
CARRY_RULES = ("nearest_feasible", "nearest_job", "learned")

_layout = None


def shown(path: Path) -> str:
    """A path as a reader should see it: from the repo root where it is inside the repo."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def layout():
    global _layout
    if _layout is None:
        _layout = load_layout(ROOT / "layouts" / "dames-point.json")
    return _layout


# ---- the cost, read from a running terminal --------------------------------------------------------------


def waiting_jobs(t) -> list:
    """The jobs that have no AGV at their pickup yet: not given to one, or given and the AGV still on its way."""
    return [*t.fleet.pending, *(v.job for v in t.fleet.agvs.values() if v.state == "to_pickup" and v.job is not None)]


def cost_rate(t, cost: str) -> float:
    """What is being paid at this moment, per minute."""
    jobs = waiting_jobs(t)
    if cost == "jobs":
        return float(len(jobs))
    blocked = sum(1 for q in t.quay.values() if q.vessel is not None and q.alongside and q.id not in t.down and not t.busy[q.id]
                  and (q.ship or q.buffer))                      # a crane with a container to hand over and no AGV to take it
    coming = sum(1 for v in t.fleet.agvs.values() if v.state == "to_pickup")
    return blocked + sum(1 for j in jobs if j.kind == "load" or j.priority > 0) + EMPTY_DRIVE * coming


def watch(t, cost: str) -> list:
    """Read the cost every TICK_S of terminal time while anything is still to happen. Returns the list it fills: (time, cost a minute)."""
    ticks: list = []

    def tick():
        ticks.append((t.clock.now, cost_rate(t, cost)))
        if t.clock.pending:
            t.clock.after(TICK_S, tick)
    t.clock.after(TICK_S, tick)
    return ticks


def context(t, cost: str) -> tuple:
    """The situation a choice is made in, the same for every pair on offer, in the order of CONTEXT."""
    agvs = t.fleet.agvs.values()
    return (1.0, len(t.fleet.pending) / 10.0, sum(1 for v in agvs if v.state == "to_pickup") / 10.0,
            sum(1 for v in agvs if v.state in ("parked", "homing", "idle") and v.broken_until is None) / 10.0,
            cost_rate(t, cost) / 5.0, sum(1 for q in t.quay.values() if q.vessel is not None and q.alongside) / 6.0)


def measures(t, ticks: list) -> dict:
    s = summary(t)
    out = {m: s[m] for m in MEASURES}
    out["cost_min"] = round(sum(rate for _, rate in ticks) * TICK_S / 60.0, 1)
    out["job_wait_max_min"] = round(max(((j.times["at_pickup"] - j.released) / 60.0 for j in t.fleet.jobs if "at_pickup" in j.times), default=0.0), 1)
    out["problems"] = len(check(t))
    out["finished"] = int(s["ships_served"] == len(t.scenario.vessels) and not s["agv_jobs_open"] and not s["stuck_at_end"])
    return out


# ---- one shift ---------------------------------------------------------------------------------------------


def shift(seed: int, setting: dict | None = None):
    """The workload of a seed, as the experiment makes it: a shift at real scale, or (scale small) one of the small workloads;
    or (scale plan) the plan's own morning, whatever the seed."""
    setting = dict(setting or {})
    scale = setting.pop("scale", "shift")
    if scale == "plan":
        return scenarios.demo().using(**setting)
    if scale == "small":
        return scenarios.generate(seed, agvs=10).using(**setting)
    made = {k: setting.pop(k) for k in ("agvs", "trucks_per_hour") if k in setting}
    return scenarios.shift(seed, **made).using(**setting)


def episode(task: tuple) -> dict:
    """A training shift: the rule follows `weights` except for a share `epsilon` of choices made by lot, and every choice
    is noted with the cost that followed it. weights None: every score is zero (the rule knows nothing).

    A row is (situation, average pair on offer, pair chosen, target, chosen by lot?, pairs on offer, seed, jobs on offer)."""
    seed, weights, level, epsilon, cost, tag = task
    w = tuple(weights) if weights else (0.0,) * len(rules.FEATURES)
    lv = tuple(level) if level else (0.0,) * (len(CONTEXT) + len(rules.FEATURES))
    rng = random.Random(f"explore|{tag}|{seed}")
    t = Terminal(layout(), shift(seed).using(dispatch="nearest_job"))
    t.fleet.policy = "learning"                      # no reasons are noted for a learner's choices
    made: list = []

    def rule(fleet, pairs):
        served = rules.at_work(fleet)
        feats = [rules.features(fleet, job, moves, served) for job, _, moves in pairs]
        scores = [rules.score(w, f) for f in feats]
        best = min(range(len(pairs)), key=lambda i: (round(scores[i], 9), *rules.order(pairs[i][0], pairs[i][1])))
        by_lot = len(pairs) > 1 and rng.random() < epsilon
        k = rng.randrange(len(pairs)) if by_lot else best
        ctx = context(t, cost)
        mean = tuple(sum(f[i] for f in feats) / len(feats) for i in range(len(rules.FEATURES)))
        made.append((fleet.clock.now, ctx, mean, feats[k], by_lot, len(pairs), rules.score(lv, (*ctx, *mean)) + scores[best],
                     len({id(job) for job, _, _ in pairs})))
        return pairs[k]
    t.fleet.pair_rule = rule
    ticks = watch(t, cost)
    t.run()
    rows, first = [], 0
    for k, (at, ctx, mean, feat, by_lot, options, _, jobs) in enumerate(made):
        while first < len(ticks) and ticks[first][0] <= at:
            first += 1
        later = next((m for m in range(k + 1, len(made)) if made[m][0] >= at + WINDOW_S), None)
        until = made[later][0] if later is not None else at + 4 * WINDOW_S
        target, i = 0.0, first
        while i < len(ticks) and ticks[i][0] <= until:
            target += ticks[i][1] * (TICK_S / 60.0) * 0.5 ** ((ticks[i][0] - at) / HALF_S)
            i += 1
        if later is not None:                        # the rule's own estimate of what comes after, for the best choice then open
            target += 0.5 ** ((made[later][0] - at) / HALF_S) * made[later][6]
        rows.append((ctx, mean, feat, target, by_lot, options, seed, jobs))
    return {"seed": seed, "rows": rows, **measures(t, ticks)}


def trial(task: tuple) -> dict:
    """A shift under a named rule (or under the learned rule with the weights given), with the cost read off it.
    setting may carry guard False: the run is made without the ten-minute guard."""
    seed, rule, weights, cost, setting = task
    setting = dict(setting or {})
    kept = fleets.OVERDUE_S
    if not setting.pop("guard", True):
        fleets.OVERDUE_S = float("inf")
    try:
        if rule == "learned" and weights is not None:
            t = Terminal(layout(), shift(seed, setting).using(dispatch="nearest_job"))
            t.fleet.policy, t.fleet.pair_rule, t.fleet.weights = "learned", rules.learned, tuple(weights)
        else:
            t = Terminal(layout(), shift(seed, setting).using(dispatch=rule))
        ticks = watch(t, cost)
        t.run()
    finally:
        fleets.OVERDUE_S = kept
    return {"seed": seed, "rule": rule, **measures(t, ticks)}


# ---- the fit: least squares, by hand so that it needs nothing installed ---------------------------------------


def solve(a: list, b: list) -> list:
    """x with a x = b, by Gauss-Jordan elimination with pivoting. a is square, small and well conditioned here."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def sums(rows: list, by_lot: bool = True) -> dict:
    """What a least-squares fit needs from a set of rows. by_lot: the fit this tool uses, on the choices made by lot, explaining
    the target by the situation, the average pair on offer and the pair chosen. Not by_lot: the plain fit, on every choice,
    by the situation and the pair chosen."""
    k = len(CONTEXT) + len(rules.FEATURES) * (2 if by_lot else 1)
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    n, y1, y2 = 0, 0.0, 0.0
    for ctx, mean, feat, target, lot, *_ in rows:
        if by_lot and not lot:
            continue
        x = (*ctx, *mean, *feat) if by_lot else (*ctx, *feat)
        n, y1, y2 = n + 1, y1 + target, y2 + target * target
        for i in range(k):
            if x[i]:
                xty[i] += x[i] * target
                row = xtx[i]
                for j in range(i, k):
                    row[j] += x[i] * x[j]
    return {"xtx": xtx, "xty": xty, "n": n, "y1": y1, "y2": y2}


def add(a: dict | None, b: dict, sign: float = 1.0) -> dict:
    if a is None:
        return {"xtx": [row[:] for row in b["xtx"]], "xty": b["xty"][:], "n": b["n"], "y1": b["y1"], "y2": b["y2"]}
    return {"xtx": [[x + sign * y for x, y in zip(ra, rb)] for ra, rb in zip(a["xtx"], b["xtx"])], "xty": [x + sign * y for x, y in zip(a["xty"], b["xty"])],
            "n": a["n"] + int(sign) * b["n"], "y1": a["y1"] + sign * b["y1"], "y2": a["y2"] + sign * b["y2"]}


def fit(s: dict) -> dict:
    """The least-squares weights from sums(): {"level": what the situation explains, "weights": the pair chosen, "r2", "rows"}."""
    k, f = len(s["xty"]), len(rules.FEATURES)
    a = [row[:] for row in s["xtx"]]
    for i in range(k):
        for j in range(i):
            a[i][j] = a[j][i]
    full = [row[:] for row in a]
    for i in range(len(CONTEXT), k):
        a[i][i] += RIDGE * s["n"]
    beta = solve(a, s["xty"])
    rss = s["y2"] - 2 * sum(b * y for b, y in zip(beta, s["xty"])) + sum(beta[i] * full[i][j] * beta[j] for i in range(k) for j in range(k))
    tss = s["y2"] - s["y1"] ** 2 / s["n"]
    return {"level": beta[:k - f], "weights": beta[k - f:], "r2": 1.0 - rss / tss if tss > 0 else 0.0, "rows": s["n"]}


def jackknife(parts: dict) -> tuple:
    """How sure each weight is: the fit is made again without each training shift in turn, and the spread of the answers is
    scaled to a standard error. Shifts are left out whole because the choices within one are not independent.
    Returns (the standard error of each weight, how each pair of weights moves together: their covariance)."""
    total = None
    for part in parts.values():
        total = add(total, part)
    left_out = [fit(add(total, part, -1.0))["weights"] for part in parts.values() if part["n"]]
    n = len(left_out)
    k = len(rules.FEATURES)
    means = [sum(w[i] for w in left_out) / n for i in range(k)]
    cov = [[(n - 1) / n * sum((w[i] - means[i]) * (w[j] - means[j]) for w in left_out) for j in range(k)] for i in range(k)]
    return [math.sqrt(cov[i][i]) for i in range(k)], cov


# ---- train -------------------------------------------------------------------------------------------------


def mean_of(runs: list, m: str) -> float:
    return round(sum(r[m] for r in runs) / len(runs), 3)


def named(values, names=None) -> dict:
    return {name: round(v, 4) for name, v in zip(names or rules.FEATURES, values)}


def train(cost: str, jobs: int, rounds: int, out: Path | None, log_to: Path, say=print) -> dict:
    started = time.perf_counter()
    weights = level = None
    lots, plain = {}, {}                             # for each training shift, the sums of the two fits
    total = None
    seed = TRAIN_FROM
    curve, used = [], []
    count = {"choices": 0, "between_pairs": 0, "between_jobs": 0, "by_lot": 0}      # decisions; those with two or more pairs, or jobs, on offer
    with multiprocessing.Pool(jobs) as pool:
        def develop(tasks, label):
            runs = sorted(pool.imap_unordered(trial, tasks), key=lambda r: r["seed"])
            point = {**{m: mean_of(runs, m) for m in CURVE}, "finished": sum(r["finished"] for r in runs), "problems": sum(r["problems"] for r in runs), "runs": runs}
            say(f"    {label}: cost {point['cost_min']:7.1f} min, quay {point['quay_moves_per_crane_hour']:.2f} moves per crane-hour, "
                f"a job waits {point['agv_wait_for_agv_min']:.2f} min (longest {point['job_wait_max_min']:.0f}), AGVs drive {point['agv_km']:.0f} km, "
                f"ships {point['ship_turnaround_min']:.1f} min in port (finished {point['finished']} of {len(runs)}, failed checks {point['problems']})")
            return point

        def learned(w):
            return [(s, "learned", list(w), cost, None) for s in DEV_SEEDS]
        say(f"cost: {COSTS[cost]['what']}.")
        say(f"On the development shifts (seeds {DEV_SEEDS[0]} to {DEV_SEEDS[-1]}), rules written by hand:")
        yardsticks = {name: develop([(s, rule, None, cost, setting) for s in DEV_SEEDS], name) for name, rule, setting in YARDSTICKS}
        say("Before any training (every score zero: the job that has waited longest, the lowest-numbered AGV):")
        curve.append({"round": 0, "epsilon": None, "episodes": 0, "weights": None, **develop(learned([0.0] * len(rules.FEATURES)), "the rule")})
        for r in range(rounds):
            eps, n = EPSILON[min(r, len(EPSILON) - 1)], EPISODES[min(r, len(EPISODES) - 1)]
            seeds = list(range(seed, seed + n))
            seed += n
            used += seeds
            done = sorted(pool.imap_unordered(episode, [(s, weights, level, eps, cost, f"{cost}|{r}") for s in seeds]), key=lambda e: e["seed"])
            for e in done:
                rows = e.pop("rows")
                lots[e["seed"]], plain[e["seed"]] = sums(rows), sums(rows, by_lot=False)
                total = add(total, lots[e["seed"]])
                count["choices"] += len(rows)
                count["between_pairs"] += sum(1 for row in rows if row[5] > 1)
                count["between_jobs"] += sum(1 for row in rows if row[7] > 1)
                count["by_lot"] += lots[e["seed"]]["n"]
            f = fit(total)
            weights, level = f["weights"], f["level"]
            say(f"round {r + 1}: {n} shifts with {eps:.0%} of choices by lot (seeds {seeds[0]} to {seeds[-1]}), training cost {mean_of(done, 'cost_min'):.1f} min a shift; "
                f"{count['choices']:,} choices so far, {count['between_jobs']:,} of them between jobs, {count['by_lot']:,} by lot; "
                f"the fit explains {f['r2']:.0%}  [{time.perf_counter() - started:.0f} s]")
            say("    " + "  ".join(f"{name} {w:+.2f}" for name, w in zip(rules.FEATURES, weights)))
            curve.append({"round": r + 1, "epsilon": eps, "episodes": len(used), "weights": [round(w, 4) for w in weights],
                          "training_cost_min": mean_of(done, "cost_min"), "r2": round(f["r2"], 4), "so_far": dict(count),
                          **develop(learned(weights), "the rule")})
        errors, moves_together = jackknife(lots)
        say("    " + "  ".join(f"{name} ±{e:.2f}" for name, e in zip(rules.FEATURES, errors)) + "   (standard errors, leaving out one shift at a time)")
        whole = None
        for part in plain.values():
            whole = add(whole, part)
        naive = fit(whole)
        say("The plain fit on the same shifts (every choice, the situation without the average pair):")
        say("    " + "  ".join(f"{name} {w:+.2f}" for name, w in zip(rules.FEATURES, naive["weights"])))
        plain_fit = {"weights": named(naive["weights"]), "explained": round(naive["r2"], 4), "choices": naive["rows"], **develop(learned(naive["weights"]), "that rule")}
    result = {
        "made_by": f"python tools/learned_dispatch.py train --cost {cost}",
        "made": time.strftime("%Y-%m-%d"),
        "method": "Q-learning in its batch form with a linear Q-function and n-step targets, fitted on the choices made by lot: see the top of tools/learned_dispatch.py",
        "cost": cost, "cost_in_words": COSTS[cost]["what"], "expects": COSTS[cost]["expects"],
        "weights": named(weights), "standard_errors": named(errors),
        "level_weights": named(level, (*CONTEXT, *(f"average_{name}" for name in rules.FEATURES))),
        "training": {"rounds": rounds, "shifts": len(used), "seeds": [used[0], used[-1]], "share_by_lot": list(EPSILON[:rounds]), **count,
                     "explained": curve[-1]["r2"], "tick_s": TICK_S, "half_life_s": HALF_S, "window_s": WINDOW_S,
                     "ridge": RIDGE, "development_seeds": [DEV_SEEDS[0], DEV_SEEDS[-1]]},
    }
    if out is not None:
        out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    log_to.write_text(json.dumps({"rule": result, "yardsticks": yardsticks, "curve": curve, "plain_fit": plain_fit,
                                  "covariance": [[round(x, 6) for x in row] for row in moves_together],
                                  "seconds": round(time.perf_counter() - started), "jobs": jobs}, indent=1) + "\n", encoding="utf-8")
    say(f"wrote {'' if out is None else shown(out) + ' and '}{shown(log_to)} in {time.perf_counter() - started:.0f} s")
    return result


# ---- test: the experiment's own shifts, which training never saw ------------------------------------------------


def tool(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare(runs: list, rule: str, against: str, interval) -> dict:
    """Run by run on the same seeds: `rule` minus `against`, with the 95 percent interval of the mean difference."""
    a = {r["seed"]: r for r in runs if r["rule"] == rule}
    b = {r["seed"]: r for r in runs if r["rule"] == against}
    return {m: interval([a[s][m] - b[s][m] for s in sorted(a) if a[s][m] is not None and b[s][m] is not None]) for m in TABLE}


def published(runs: list) -> dict:
    """The rules written before were run on these seeds by the experiment (docs/experiments.csv): the same numbers must come out."""
    import csv

    mine = {}
    for r in runs:
        setting = r["setting"] or {}
        if setting.get("scale") == "small":
            key = ("dispatch", "small", f"dispatch = {r['rule']}")
        elif "agvs" in setting:
            key = ("fleet", "shift", f"agvs = {setting['agvs']}") if r["rule"] == "nearest_feasible" else None
        else:
            key = ("dispatch", "shift", f"dispatch = {r['rule']}")
        if key:
            mine[(*key, r["seed"])] = r
    rows = same = 0
    if not (DOCS / "experiments.csv").exists():      # the experiment has not been run here: nothing to compare with
        return {"compared": 0, "the_same": 0}
    with open(DOCS / "experiments.csv", newline="") as f:
        for row in csv.DictReader(f):
            run = mine.get((row["experiment"], row["scale"], row["setting"], int(row["seed"])))
            if run is not None and row["measure"] in MEASURES and row["value"] != "":
                rows += 1
                same += abs(run[row["measure"]] - float(row["value"])) < 1e-9
    return {"compared": rows, "the_same": same}


def test(jobs: int, say=print) -> dict:
    started = time.perf_counter()
    learned = rules.load_weights()
    cost = learned["cost"]
    interval = tool("experiments").interval
    tasks = [(seed, rule, None, cost, None) for rule in RULES for seed in TEST_SEEDS]
    tasks += [(seed, rule, None, cost, setting) for setting in CARRY for rule in CARRY_RULES for seed in TEST_SEEDS]
    tasks += [(0, rule, None, cost, PLAN) for rule in CARRY_RULES]
    say(f"{len(tasks)} runs on {jobs} at a time ...")
    done = []
    with multiprocessing.Pool(jobs) as pool:
        for n, (task, run) in enumerate(pool.imap_unordered(_trial_with_task, tasks, chunksize=1), start=1):
            done.append({**run, "setting": task[4]})
            if n % 30 == 0:
                say(f"  {n} of {len(tasks)}  [{time.perf_counter() - started:.0f} s]")
    done.sort(key=lambda r: (json.dumps(r["setting"]), RULES.index(r["rule"]), r["seed"]))
    planned = [r for r in done if r["setting"] is None]

    def table(runs, names, pairs):
        return {"rules": {rule: {m: interval([r[m] for r in runs if r["rule"] == rule]) for m in TABLE} for rule in names},
                "differences": [{"rule": a, "against": b, "measures": compare(runs, a, b, interval)} for a, b in pairs],
                "finished": sum(r["finished"] for r in runs), "problems": sum(r["problems"] for r in runs), "runs": len(runs)}
    result = {
        "made_by": "python tools/learned_dispatch.py test", "made": time.strftime("%Y-%m-%d"),
        "seeds": [TEST_SEEDS[0], TEST_SEEDS[-1]], "cost": cost, "weights": learned["weights"], "weights_made": learned["made"],
        **table(planned, RULES, PAIRS),
        "published": published(done),
        "carry": [{"setting": setting, **table([r for r in done if r["setting"] == setting], CARRY_RULES,
                                               [(a, b) for a, b in PAIRS if a in CARRY_RULES and b in CARRY_RULES])} for setting in CARRY],
        "plan": {r["rule"]: {k: v for k, v in r.items() if k not in ("setting", "seed", "rule")} for r in done if r["setting"] == PLAN},
        "each_run": [{k: v for k, v in r.items() if k != "setting"} for r in planned],
        "seconds": round(time.perf_counter() - started), "jobs": jobs,
    }
    RESULTS.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    for rule in RULES:
        say(f"  {rule:18s}" + "".join(f"{result['rules'][rule][m]['mean']:10.2f} ±{result['rules'][rule][m]['half']:6.2f}" for m in TABLE))
    for d in result["differences"]:
        say(f"  {d['rule'] + ' - ' + d['against']:34s}" + "".join(
            f"{d['measures'][m]['mean']:+10.2f} ±{d['measures'][m]['half']:5.2f}{'*' if abs(d['measures'][m]['mean']) > d['measures'][m]['half'] else ' '}" for m in TABLE))
    say(f"the rules written before: {result['published']['the_same']} of {result['published']['compared']} numbers the same as docs/experiments.csv")
    say(f"wrote {shown(RESULTS)} in {time.perf_counter() - started:.0f} s")
    return result


def _trial_with_task(task: tuple) -> tuple:
    return task, trial(task)


# ---- report: the write-up and its two figures, made from the numbers so that they never differ from them ----------

INK, SOFT, FAINT, GRID, BLUE = "#1c2422", "#55615d", "#8b9692", "#e3e7e3", "#2a78d6"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
NAMES = {"agv_wait_for_agv_min": "A job waits for its AGV (min)", "quay_moves_per_crane_hour": "Quay moves per crane-hour", "ship_turnaround_min": "Ship: time in port (min)",
         "truck_turnaround_min": "Truck: gate to gate (min)", "quay_crane_wait_min": "Quay cranes wait (min)", "agv_km": "AGVs drive (km)",
         "job_wait_max_min": "Longest wait of a job (min)", "cost_min": "Training cost (min)", "end_min": "Finished after (min)"}
DIGITS = {"agv_wait_for_agv_min": 2, "quay_moves_per_crane_hour": 2, "ship_turnaround_min": 1, "truck_turnaround_min": 2, "quay_crane_wait_min": 0, "agv_km": 0,
          "job_wait_max_min": 1, "cost_min": 0, "end_min": 1}
BETTER = {"quay_moves_per_crane_hour": "higher"}             # for every other measure lower is better
WORDS = {       # a sure difference in words: (when the number is higher, when it is lower, the measure as a noun, its unit)
    "agv_wait_for_agv_min": ("a job waits {v} min longer for its AGV", "a job waits {v} min less for its AGV", "the wait of a job for its AGV", " min"),
    "quay_moves_per_crane_hour": ("each quay crane makes {v} more moves an hour", "each quay crane makes {v} fewer moves an hour", "quay moves per crane-hour", ""),
    "ship_turnaround_min": ("a ship is in port {v} min longer", "a ship is in port {v} min less", "a ship's time in port", " min"),
    "truck_turnaround_min": ("a truck takes {v} min longer gate to gate", "a truck takes {v} min less gate to gate", "a truck's time gate to gate", " min"),
    "quay_crane_wait_min": ("the quay cranes wait {v} min longer for AGVs over a shift", "the quay cranes wait {v} min less for AGVs over a shift",
                            "the quay cranes' wait for AGVs", " min"),
    "agv_km": ("the AGVs drive {v} km more over a shift", "the AGVs drive {v} km less over a shift", "the distance the AGVs drive", " km"),
    "job_wait_max_min": ("the longest wait of any job is {v} min longer", "the longest wait of any job is {v} min shorter", "the longest wait of any job", " min"),
}
SAID = ("agv_wait_for_agv_min", "agv_km", "quay_crane_wait_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "truck_turnaround_min", "job_wait_max_min")
LOOKS_AT = {
    "drive": "The drive from the AGV to the pickup, for a job at a quay crane that no AGV is coming to yet",
    "drive_served_1": "The same drive, when one AGV is already coming to that crane: added to the line above",
    "drive_served_2": "The same drive, when two or more are: added to the first line",
    "drive_load": "The same drive, when the job fetches an export for a ship: added to the first line",
    "drive_truck": "The same drive, when the job is for a truck: added to the first line",
    "waited": "How long the job has waited for an AGV",
    "waited_long": "The part of that wait beyond four minutes: added to the line above",
    "discharge_1": "The job is at a quay crane that one AGV is already coming to",
    "discharge_2": "The job is at a quay crane that two or more AGVs are already coming to",
    "load_0": "The job fetches an export for a quay crane that no AGV is fetching for yet",
    "load_1": "The job fetches an export for a quay crane that one AGV is already fetching for",
    "load_2": "The job fetches an export for a quay crane that two or more are already fetching for",
    "deliver": "The job takes an import to a truck, against fetching a truck's export",
}


def rule_name(rule: str) -> str:
    from sim.control import POLICY_NAMES

    return POLICY_NAMES[rule]


def sure(i: dict | None) -> bool:
    return i is not None and abs(i["mean"]) > i["half"]


def pm(i: dict | None, m: str, sign: bool = False) -> str:
    if i is None:
        return "-"
    d = DIGITS[m]
    mean = round(i["mean"], d) + 0.0                 # a difference that rounds to nothing is 0, not -0
    return (f"{mean:+.{d}f}" if sign and mean else f"{mean:.{d}f}") + f" ± {i['half']:.{d}f}"


def in_words(n: int) -> str:
    return ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")[n] if 0 <= n <= 12 else str(n)


def upper_first(words: str) -> str:
    return words[:1].upper() + words[1:]


def line(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def table(block: dict, names, pairs, measures) -> list:
    """Rules as rows, measures as columns, then each pair of rules compared run by run."""
    out = [line(["Rule", *(NAMES[m] for m in measures)]), line(["---", *["---:"] * len(measures)])]
    out += [line([rule_name(rule), *(pm(block["rules"][rule][m], m) for m in measures)]) for rule in names]
    for a, b in pairs:
        d = next(x for x in block["differences"] if (x["rule"], x["against"]) == (a, b))["measures"]
        out.append(line([f"*{rule_name(a)} minus {rule_name(b)}*", *(pm(d[m], m, True) + (" **\\***" if sure(d[m]) else "") for m in measures)]))
    return out


def told(block: dict, rule: str, against: str) -> dict:
    """One rule against another on the test: what is better and more than chance, what is worse, what cannot be told."""
    d = next(x for x in block["differences"] if (x["rule"], x["against"]) == (rule, against))["measures"]
    return judged(d, {m: block["rules"][against][m]["mean"] for m in SAID})


def paired(a: dict, b: dict, interval) -> dict:
    """Two sets of development runs compared shift by shift: a minus b, each measure with its 95 percent interval."""
    x, y = {r["seed"]: r for r in a["runs"]}, {r["seed"]: r for r in b["runs"]}
    return {m: interval([x[seed][m] - y[seed][m] for seed in sorted(x)]) for m in CURVE}


def judged(d: dict, bases: dict, measures=SAID) -> dict:
    """Differences in words: what is better and more than chance, what is worse, what cannot be told."""
    better, worse, same = [], [], []
    for m in measures:
        i, base = d[m], bases[m]
        if not sure(i):
            same.append(f"{WORDS[m][2]} ({pm(i, m, True)}{WORDS[m][3]})")
            continue
        v = f"{abs(i['mean']):.{DIGITS[m]}f} ± {i['half']:.{DIGITS[m]}f}"
        text = WORDS[m][0 if i["mean"] > 0 else 1].format(v=v) + (f" ({abs(i['mean']) / base:.0%})" if base else "")
        (better if (i["mean"] > 0) == (BETTER.get(m) == "higher") else worse).append(text)
    return {"better": better, "worse": worse, "same": same}


def listed(items: list) -> str:
    return "; ".join(items) if items else "nothing"


def sentences(v: dict) -> str:
    out = []
    if v["better"]:
        out.append(f"Better, and more than chance: {listed(v['better'])}.")
    if v["worse"]:
        out.append(f"Worse, and more than chance: {listed(v['worse'])}.")
    if v["same"]:
        out.append(f"No sure difference: {listed(v['same'])}.")
    return " ".join(out)


def spread(cov: list, combo: dict) -> float:
    """The standard error of a sum of weights, each times a number, from how the weights move together."""
    idx = {name: i for i, name in enumerate(rules.FEATURES)}
    return math.sqrt(max(0.0, sum(a * b * cov[idx[x]][idx[y]] for x, a in combo.items() for y, b in combo.items())))


def write_up(rule: dict, log: dict, results: dict, others: dict) -> str:
    """docs/learned-dispatch.md. Every number is read from the three files; sentences that judge are chosen by the numbers."""
    w, se, tr, cov = rule["weights"], rule["standard_errors"], rule["training"], log["covariance"]
    curve, marks, cell = log["curve"], log["yardsticks"], layout().cell_size_m
    last, first = curve[-1], curve[0]
    n_test = results["seeds"][1] - results["seeds"][0] + 1
    vs_plan, vs_hand, hand_vs_plan = told(results, "learned", "nearest_feasible"), told(results, "learned", "nearest_job"), told(results, "nearest_job", "nearest_feasible")
    runs = results["runs"] + sum(c["runs"] for c in results["carry"])
    unfinished = runs - results["finished"] - sum(c["finished"] for c in results["carry"])
    failed = results["problems"] + sum(c["problems"] for c in results["carry"])
    seen = 1 - 0.5 ** (tr["window_s"] / tr["half_life_s"])
    lots = ", ".join(f"{e:.0%}" for e in tr["share_by_lot"][1:])
    interval = tool("experiments").interval
    dev = ("agv_wait_for_agv_min", "agv_km", "quay_moves_per_crane_hour", "ship_turnaround_min", "job_wait_max_min")
    hand, plan_rule = marks["nearest_job"], marks["nearest_feasible"]
    room = {m: abs(hand[m] - first[m]) / first[m] for m in ("agv_wait_for_agv_min", "agv_km", "quay_moves_per_crane_hour", "ship_turnaround_min")}
    settled = [name for name in rules.FEATURES if se[name] > 0 and abs(w[name]) >= 2 * se[name]]
    unseen = [name for name in rules.FEATURES if se[name] == 0 and w[name] == 0]            # never met in training: the weight is nothing, not a finding
    cost_name, _, cost_how = rule["cost_in_words"].partition(": ")
    n_dev = len(hand["runs"])
    has = {m: (first[m] - plan_rule[m]) / (first[m] - hand[m]) if first[m] != hand[m] else 0.0 for m in ("agv_wait_for_agv_min", "agv_km")}
    if vs_hand["better"] and not vs_hand["worse"]:
        verdict = (f"Learning added something to the freedom to choose the job. Against {rule_name('nearest_job')}, a rule written by hand that has the same freedom, "
                   f"the learned rule is better on some measures and worse on none that {n_test} shifts can show.")
    elif vs_hand["worse"] and not vs_hand["better"]:
        verdict = (f"Learning did not beat a rule one line long. Starting from nothing, the rule learned that the drive is what matters (the learning curve below). "
                   f"{rule_name('nearest_job')}, which was told that and nothing else, still does as well or better on every measure.")
    elif vs_hand["worse"] and vs_hand["better"]:
        verdict = (f"Against {rule_name('nearest_job')}, a rule written by hand that has the same freedom to choose the job, the learned rule is a trade: "
                   f"better on some measures and worse on others. Which is the better rule depends on what the terminal values.")
    else:
        verdict = (f"The learned rule and {rule_name('nearest_job')}, a rule written by hand with the same freedom to choose the job, cannot be told apart on {n_test} shifts. "
                   f"What helps is choosing the job as well as the AGV; on this map the learning adds nothing to that which {n_test} shifts can show.")
    o = [
        "# A dispatch rule learned in the simulator",
        "",
        f"Written by `python tools/learned_dispatch.py report` from `sim/dispatch_weights.json` and `docs/learned-dispatch-training.json` "
        f"(`python tools/learned_dispatch.py train` writes both) and `docs/learned-dispatch.json` (`python tools/learned_dispatch.py test` writes it). "
        f"The rule is `sim/dispatch_learned.py`; how it is trained is at the top of `tools/learned_dispatch.py`.",
        "",
        "A learned dispatch rule is the AGV owner's idea (the check-in of Oct 22: Q-learning). This is one way to build it, made in the integration so that the idea "
        "can be tried in the whole terminal. It is the owner's to adopt, change or replace. Nothing else depends on it: the terminal still runs "
        f"{rule_name('nearest_feasible')} as planned, and the experiment in `docs/experiments.md` is as it was.",
        "",
        "## In short",
        "",
        f"* **What it is.** Whenever an AGV is free and jobs are waiting, the rule scores every pair of a waiting job and a free AGV and starts the pair with the lowest score. "
        f"A score is a sum of {len(w)} weights. The weights were learned by Q-learning inside the simulator, from {tr['shifts']} shifts and {tr['choices']:,} choices, "
        f"{tr['by_lot']:,} of them made by lot.",
        f"* **The test.** {n_test} shifts that training never saw, the same {n_test} the experiment uses, each rule on the same shifts. "
        f"Against {rule_name('nearest_feasible')}, the rule the terminal runs as planned: {sentences(vs_plan)}",
        f"* **Against a rule written by hand with the same freedom** ({rule_name('nearest_job')}: the pair with the shortest drive): {sentences(vs_hand)}",
        f"* **What that says.** {verdict}",
        f"* **How much there is to win.** Between a rule that ignores distance (the job that has waited longest, the lowest-numbered AGV) and "
        f"{rule_name('nearest_job')} lie {room['agv_wait_for_agv_min']:.0%} of the wait of a job, {room['agv_km']:.0%} of the kilometres, "
        f"{room['ship_turnaround_min']:.0%} of a ship's time in port and {room['quay_moves_per_crane_hour']:.0%} of the quay moves per crane-hour "
        f"(the {in_words(n_dev)} development shifts). That is the whole gap between ignoring distance and the best rule here, and "
        f"{rule_name('nearest_feasible')}, the planned rule, already has {has['agv_wait_for_agv_min']:.0%} of the first and {has['agv_km']:.0%} of the second.",
        f"* **Checks.** {runs - unfinished} of {runs} test runs finished" + (f", {failed} checks failed in them" if failed else " and none failed a check")
        + (f". The rules written before gave the experiment's published numbers again ({results['published']['the_same']:,} of {results['published']['compared']:,} the same)."
           if results["published"]["compared"] else ". The experiment's numbers (`docs/experiments.csv`) were not there to compare the older rules with."),
        "",
        "## The question the rule answers",
        "",
        f"The three rules the terminal had ({rule_name('nearest_feasible')}, {rule_name('longest_idle')}, {rule_name('balanced')}) are asked: this job has waited longest, "
        "which AGV gets it? The two rules added here are asked a wider question: of every waiting job and every free AGV that could take it, which pair starts now?",
        "",
        "| Rule | In the code | What it starts |",
        "| --- | --- | --- |",
        f"| {rule_name('nearest_job')} | `nearest_job` | The pair with the shortest drive to the pickup. Written by hand, one line. It is the yardstick: it has the learned rule's freedom "
        "and no learning, so what separates the two is what learning added. |",
        f"| {rule_name('learned')} | `learned` | The pair with the lowest score, the score being a sum of weights that were learned. |",
        "",
        "Two things hold under both, as they do under every rule. A job for a truck goes before a job for a ship: that is the scheduler's priority. And a job that has waited "
        "ten minutes is taken before any other. Ten minutes is the wait the simulator already reports as a stall.",
        "",
        f"The second is there because a rule that looks only at the drive leaves far jobs waiting. On the {in_words(n_dev)} development shifts the longest wait of any job averaged "
        f"{marks['nearest_job_without_guard']['job_wait_max_min']:.0f} min under {rule_name('nearest_job')} without it and {marks['nearest_job']['job_wait_max_min']:.0f} min with it "
        f"({marks['nearest_feasible']['job_wait_max_min']:.0f} min under {rule_name('nearest_feasible')}).",
        "",
        f"How much room is there to choose? In the training shifts the fleet started {tr['choices']:,} jobs. In {tr['between_pairs'] / tr['choices']:.0%} of those moments "
        f"there was more than one pair on offer, and in {tr['between_jobs'] / tr['choices']:.0%} more than one job: only there can the two new rules differ from the old question.",
        "",
        "## The test",
        "",
        f"Seeds {results['seeds'][0]} to {results['seeds'][1]} of `sim.scenario.shift`: the experiment's own shifts (four ships with 140 to 240 moves each, 80 trucks, 30 AGVs, "
        "the calibrated numbers of `docs/data-sheet.md`). Training used other seeds and so did every decision made while building this. "
        f"A cell is the mean of {n_test} runs plus or minus half the width of its 95 percent confidence interval. Under the rules, two rules are compared run by run on the same "
        "shifts; a difference marked **\\*** has an interval that does not include zero: it is more than chance. There are many comparisons on this page: "
        "if no two rules differed at all, about one in twenty would still be marked.",
        "",
        *table(results, RULES, PAIRS[:3], TABLE[:6]),
        "",
        *table(results, RULES, PAIRS[:3], TABLE[6:]),
        "",
        f"The training cost is what the rule learned to keep low: {cost_name}, summed over the shift.",
        "",
        f"![Each rule against {rule_name('nearest_feasible')} on four measures, as a mean difference with its 95 percent interval](learned-dispatch-test.svg)",
        "",
        f"{rule_name('nearest_job')} against {rule_name('nearest_feasible')}, which shows what the freedom to choose the job is worth without any learning: {sentences(hand_vs_plan)}",
        "",
        "## How it was learned",
        "",
        f"1. The simulator runs a shift. Every {tr['tick_s']:.0f} seconds of terminal time the cost is read. The cost is {cost_name}: {cost_how}. "
        "The reading changes nothing: a run with it is the same run as without (a test holds this).",
        "2. Each time the fleet starts a job the choice is noted: the situation, the pairs on offer, the pair chosen.",
        f"3. After the shift each choice gets a target, the cost that followed it: the cost of the next {tr['window_s'] / 60:.0f} minutes as it happened, each minute counting "
        f"less than the one before (half as much after {tr['half_life_s'] / 60:.0f} minutes), plus the rule's own estimate of what comes after, for the best choice open at the next "
        f"decision past those {tr['window_s'] / 60:.0f} minutes. With these numbers about {seen:.0%} of a target is cost that was seen.",
        f"4. In the first round every choice is made by lot, so the rule starts knowing nothing. In the later rounds it follows the weights fitted so far, except for a share "
        f"of choices still made by lot ({lots}). After each round the weights are fitted again, by least squares, to every choice made by lot so far.",
        "",
        "Why the choices made by lot, and only those. Most of the cost that follows a choice has nothing to do with the choice: it is how busy the terminal is at that moment. "
        "Where the rule chose, what it chose depends on the situation, and a plain fit credits the choice with the situation. Where the lot chose, the chosen pair differs from "
        "the average pair on offer by chance alone. So the fit explains each target by the situation (six numbers: jobs waiting, AGVs on their way, AGVs free, the cost being "
        "paid, cranes at work, and a constant), by the average pair on offer, and by the pair chosen; the weights of the pair chosen are the rule. "
        "It is an experiment run inside the simulator. What the plain fit learns from the same shifts is under \"What was tried and not kept\".",
        "",
        f"Training took {tr['rounds']} rounds and {tr['shifts']} shifts (seeds {tr['seeds'][0]} to {tr['seeds'][1]}). The final fit explains {tr['explained']:.0%} of the "
        "spread of the targets, nearly all of it by the situation. The pair chosen moves the cost that follows by a minute or two at most, and what nothing explains "
        "is several times larger: that is why it takes so many choices to settle a weight.",
        "",
        "In the terms of reinforcement learning:",
        "",
        "| Term | Here |",
        "| --- | --- |",
        "| State | The terminal at a moment when a job can start, seen through the situation numbers and the pairs on offer |",
        "| Action | One pair of a waiting job and a free AGV. The set of actions is different at every decision, which is why the rule scores pairs and keeps no table |",
        f"| Reward | Minus the cost: {cost_name} ({cost_how}) |",
        f"| Discount | Continuous in time: a half every {tr['half_life_s'] / 60:.0f} minutes. Decisions are not evenly spaced, so the discount goes by the clock, not by the step |",
        "| Q-function | Linear: the level of the situation (the six numbers and the average pair) plus the weights times the features of the pair. The level is the same for every "
        "pair on offer, so the choice needs only the weights |",
        f"| Target | n-step: the discounted cost seen until the first decision at least {tr['window_s'] / 60:.0f} minutes later, plus the discounted Q of the best action there, "
        "computed with the weights that ran the episode (in the first round there are none yet, and that part is zero). Not corrected for the choices by lot inside those minutes |",
        f"| Exploring | Epsilon-greedy over the pairs on offer, epsilon {', '.join(f'{e:g}' for e in tr['share_by_lot'])} by round |",
        f"| Update | Batch (fitted Q iteration): after each round, least squares on every exploratory choice so far, with a small ridge term ({tr['ridge']:g}) |",
        "",
        "## The learning curve",
        "",
        f"After each round the rule as it then stood, with no choices by lot, ran {in_words(n_dev)} development shifts (seeds {tr['development_seeds'][0]} to {tr['development_seeds'][1]}). "
        "They are not training shifts and not test shifts; every decision about the method was made on them.",
        "",
        "![The learned rule on the development shifts after each round of training, against two rules written by hand](learned-dispatch-curve.svg)",
        "",
        line(["After", "Training shifts", "Choices by lot so far", *(NAMES[m] for m in ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "agv_km", "cost_min"))]),
        line(["---", *["---:"] * 7]),
    ]
    shown = ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "agv_km", "cost_min")
    for c in curve:
        o.append(line(["no training" if not c["round"] else f"round {c['round']}", c["episodes"], f"{c['so_far']['by_lot']:,}" if c["round"] else 0,
                       *(f"{c[m]:.{DIGITS[m]}f}" for m in shown)]))
    for name, label in (("nearest_feasible", rule_name("nearest_feasible")), ("nearest_job", rule_name("nearest_job"))):
        o.append(line([f"*{label}*", "", "", *(f"{marks[name][m]:.{DIGITS[m]}f}" for m in shown)]))
    dev_runs = sum(len(c["runs"]) for c in curve) + sum(len(y["runs"]) for y in marks.values()) + len(log["plain_fit"]["runs"])
    dev_bad = (dev_runs - sum(c["finished"] for c in curve) - sum(y["finished"] for y in marks.values()) - log["plain_fit"]["finished"],
               sum(c["problems"] for c in curve) + sum(y["problems"] for y in marks.values()) + log["plain_fit"]["problems"])
    o += [
        "",
        "Before any training every score is zero and the rule takes the job that has waited longest and the lowest-numbered AGV. "
        f"{upper_first(in_words(n_dev))} shifts are few, so the rule after the last round is compared shift by shift with:",
        "",
        f"* **No training.** {sentences(judged(paired(last, first, interval), first, dev))}",
        f"* **{rule_name('nearest_feasible')}.** {sentences(judged(paired(last, plan_rule, interval), plan_rule, dev))}",
        f"* **{rule_name('nearest_job')}.** {sentences(judged(paired(last, hand, interval), hand, dev))}",
        "",
        "The test above, on other shifts and more of them, is the measurement.",
        "",
        f"Of the {dev_runs} development runs behind this section, {dev_runs - dev_bad[0]} finished" + (f" and {dev_bad[1]} checks failed" if dev_bad[1] else " and none failed a check") + ".",
        "",
        "## What it learned",
        "",
        "A weight says how much the cost that follows a choice changes, in minutes, with one thing about the pair, the rest equal. Lower is better, so a positive weight "
        "counts against a pair. The standard error says how far the weight moves when one training shift is left out (a jackknife over shifts); a weight less than two "
        "standard errors from zero is not settled, and is marked.",
        "",
        "| What the rule looks at | Unit | Weight (min) | Standard error | |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    units = {"drive": "per 100 m", "drive_served_1": "per 100 m", "drive_served_2": "per 100 m", "drive_load": "per 100 m", "drive_truck": "per 100 m",
             "waited": "per minute waited", "waited_long": "per minute beyond four"}
    per = {**{k: 1.0 / cell for k in units if k.startswith("drive")}, "waited": 0.1, "waited_long": 0.1}       # from the feature's own unit to the one shown
    for name in rules.FEATURES:
        k = per.get(name, 1.0)
        o.append(line([LOOKS_AT[name], units.get(name, "yes or no"), f"{w[name] * k:+.2f}", f"{se[name] * k:.2f}",
                       "" if name in settled else "never met in training" if name in unseen else "not settled"]))
    drives = (("at a quay crane that no AGV is coming to yet", {"drive": 1}), ("at a quay crane that one AGV is coming to", {"drive": 1, "drive_served_1": 1}),
              ("at a quay crane that two or more are coming to", {"drive": 1, "drive_served_2": 1}), ("fetching an export for a ship", {"drive": 1, "drive_load": 1}),
              ("for a truck", {"drive": 1, "drive_truck": 1}))
    o += [
        "",
        "Put together, what each 100 m of drive to the pickup adds to a pair's score:",
        "",
        "| The job is | Each 100 m adds (min) | Standard error |",
        "| --- | ---: | ---: |",
        *(line([what, f"{sum(w[x] * a for x, a in combo.items()) / cell:+.2f}", f"{spread(cov, combo) / cell:.2f}"]) for what, combo in drives),
        "",
        "And what the wait so far adds:",
        "",
        "| The job has waited | 2 min | 4 min | 6 min | 8 min |",
        "| --- | ---: | ---: | ---: | ---: |",
        line(["Added to the score (min)", *(f"{w['waited'] * t / 10 + w['waited_long'] * max(0.0, t - rules.LONG_S / 60) / 10:+.2f}" for t in (2, 4, 6, 8))]),
        line(["Standard error", *(f"{spread(cov, {'waited': t / 10, 'waited_long': max(0.0, t - rules.LONG_S / 60) / 10}):.2f}" for t in (2, 4, 6, 8))]),
        "",
        "The lines for the kind of job (the last six in the first table) are each measured against a job at a quay crane that no AGV is coming to yet, except the last, "
        "which compares the two kinds of job for a truck with each other: a truck's job is never on offer together with a ship's.",
        "",
        f"Settled, of the {len(w)}: " + (", ".join(f"`{name}`" for name in settled) if settled else "none") + ". "
        + ("The others the rule cannot tell from zero with this much training, yet they still steer its choices, and a weight that is only noise costs something each time "
           "it decides. The standard errors fall with the square root of the number of training shifts: four times the shifts would halve them." if len(settled) < len(w) else ""),
        "",
        "## What it was not trained on",
        "",
        f"The rule learned with 30 AGVs on shifts at real scale. The same {n_test} seeds with a smaller and a larger fleet, and the experiment's small workloads "
        "(three ships with about ten containers each, 14 trucks, 10 AGVs, on the reference numbers, where every time is fixed), show how far it carries.",
    ]
    for c in results["carry"]:
        what = "The small workloads" if c["setting"].get("scale") == "small" else f"{c['setting']['agvs']} AGVs"
        cols = ("agv_wait_for_agv_min", "agv_km", "quay_crane_wait_min", "ship_turnaround_min", "truck_turnaround_min", "job_wait_max_min")
        o += ["", f"### {what}", "", f"{c['runs']} runs, {c['finished']} finished, {c['problems']} failed checks.", "",
              *table(c, CARRY_RULES, [(a, b) for a, b in PAIRS[:3]], cols)]
    plan = results["plan"]
    o += [
        "",
        "### The plan's own morning",
        "",
        "One morning, with no seed and no randomness (`sim.scenario.demo`): three ships, 58 containers, 12 AGVs on the reference numbers. It is the run the 3D view opens on, "
        "so this is what a visitor sees when the rule is switched in the control room. One run is an example, not a measurement.",
        "",
        line(["Rule", *(NAMES[m] for m in ("end_min", "agv_wait_for_agv_min", "agv_km", "ship_turnaround_min", "truck_turnaround_min"))]),
        line(["---", *["---:"] * 5]),
        *(line([rule_name(r), *(f"{plan[r][m]:.{max(DIGITS[m], 1)}f}" for m in ("end_min", "agv_wait_for_agv_min", "agv_km", "ship_turnaround_min", "truck_turnaround_min"))])
          for r in CARRY_RULES),
        "",
        "## What was tried and not kept",
        "",
    ]
    pf = log["plain_fit"]
    cmp_cols = ("agv_wait_for_agv_min", "quay_moves_per_crane_hour", "ship_turnaround_min", "agv_km", "job_wait_max_min")
    o += [
        "**The plain fit.** The usual way to fit a Q-function uses every choice, the rule's own as well as those by lot, and no average pair. On the same training shifts "
        f"({pf['choices']:,} choices) it gives a rule that does this on the development shifts, from the weights under it:",
        "",
        line(["On the development shifts", *(NAMES[m] for m in cmp_cols)]),
        line(["---", *["---:"] * len(cmp_cols)]),
        line(["The rule as trained (choices by lot, against the average pair)", *(f"{last[m]:.{DIGITS[m]}f}" for m in cmp_cols)]),
        line(["The plain fit", *(f"{pf[m]:.{DIGITS[m]}f}" for m in cmp_cols)]),
        "",
        "| What the rule looks at | Weight as trained | Weight from the plain fit |",
        "| --- | ---: | ---: |",
        *(line([f"`{name}`", f"{w[name]:+.2f}", f"{pf['weights'][name]:+.2f}"]) for name in rules.FEATURES),
        "",
        f"(These are in the features' own units: the drive in hundreds of moves of {cell:g} m, the wait in tens of minutes.) "
        f"Shift by shift on the development shifts, the rule as trained against the plain fit: {sentences(judged(paired(last, pf, interval), pf, dev))}",
        "",
        "The first version of the training used the plain fit. Its curve stopped improving after the first round, and its weights for the kind of job changed sign with the cost "
        "being trained on, a sign that they were measuring the situation and not the choice. That is why the fit was changed. The runs of that first version were stopped "
        "after four rounds and are not kept; the plain fit above, on the final shifts, stands in for them.",
        "",
    ]
    for cost, other in others.items():
        a, b = last, other["curve"][-1]
        o += [
            f"**Another cost.** The rule can be trained to keep something else low. Tried: {COSTS[cost]['what'].replace(': ', ' (', 1)}). "
            f"`python tools/learned_dispatch.py train --cost {cost}` trains it and writes `docs/{also_tried(cost).name}`. Same method, same shifts. "
            "On the development shifts after the last round:",
            "",
            line(["Trained to keep low", *(NAMES[m] for m in cmp_cols)]),
            line(["---", *["---:"] * len(cmp_cols)]),
            line([f"{upper_first(cost_name)} (kept)", *(f"{a[m]:.{DIGITS[m]}f}" for m in cmp_cols)]),
            line([upper_first(COSTS[cost]["what"].split(": ")[0]), *(f"{b[m]:.{DIGITS[m]}f}" for m in cmp_cols)]),
            "",
            f"Shift by shift, the second against the first: {sentences(judged(paired(b, a, interval), a, dev))} {KEPT_BECAUSE}",
            "",
        ]
    o += [
        "**A table.** Q-learning is usually first met with a table of states and actions. Here the actions are whichever jobs and AGVs happen to be free, "
        "a different list at every decision, so there is no fixed table to fill; the rule scores each pair with the same weights instead.",
        "",
        "## Limits",
        "",
        f"* The rule is a weighted sum of {len(w)} things chosen by hand. Learning sets how much each counts; it cannot find a new thing to look at.",
        "* It learned on one kind of shift: `sim.scenario.shift` with 30 AGVs and 10 trucks an hour. Other fleets and the small workloads are above; another map, "
        "other crane rates or a heavier truck load were not tried.",
        f"* It was trained to keep one thing low: {cost_name}. What it does to ship time, quay rate, kilometres and energy is measured, not aimed at.",
        f"* Each target follows the cost for {tr['window_s'] / 60:.0f} minutes under the choices actually made, some of them by lot, before the rule's own estimate takes over. "
        "So the weights are for a terminal that still makes some choices by lot. All rounds are fitted together, so the first round, where every choice was by lot, still counts.",
        "* The standard errors treat the training shifts as independent. Shifts of a later round follow weights fitted on earlier rounds, so they are not quite.",
        "* The rule knows about charge only what every rule knows: whether the AGV has enough for the job. It does not plan charging.",
        "* Before the seeds were set aside, three rules written by hand were each run once on seeds 1 to 3 to see how much dispatch can change. "
        "Nothing was tuned on those runs, and the learned rule saw none of the test seeds before the test.",
        "* The numbers are a model's: they rank the rules on this map, they are not a forecast for a real terminal.",
        "",
        "## If the owner takes it further",
        "",
        f"* **Train longer.** `python tools/learned_dispatch.py train --rounds {4 * tr['rounds']}` trains from the start with that many rounds: the first {tr['rounds']} as here, "
        "the rest like the last. More shifts settle more weights: the standard errors fall with the square root of the number of shifts.",
        "* **Pull unsure weights toward zero.** A weight could be shrunk by as much as it is unsure before the rule uses it (a ridge fit, or dropping what is not settled). "
        "Not tried here, so that the method stayed one thing decided before the test.",
        "* **Give it more to look at.** The charge of the AGV, whether the crane's buffer is full, where the job ends and what is likely to be waiting near there. "
        "Each is one more number in `features()` in `sim/dispatch_learned.py` and one more weight.",
        "* **Aim it at something else.** The cost is one function, `cost_rate()` in `tools/learned_dispatch.py`. Ship time or kilometres could be put in it directly.",
        "* **A network in place of the weights.** It could find what a weighted sum cannot. It would need many times the shifts, and the small gap between "
        "a rule that knows nothing and the best rule here says there is not much to find on this map.",
        "",
        "## Run it yourself",
        "",
        "```",
        f"python tools/learned_dispatch.py train     # {tr['shifts']} training shifts and the development runs: {log['seconds'] / 60:.0f} min with {log['jobs']} at a time where this was made",
        f"python tools/learned_dispatch.py test      # {runs + len(plan)} runs: {results['seconds'] / 60:.0f} min with {results['jobs']} at a time",
        "python tools/learned_dispatch.py report    # this page and its two figures",
        "python sim/run.py --dispatch learned       # the plan's morning under the learned rule",
        "```",
        "",
        "Training and the test are the same from run to run: training twice gives the same weights to the last digit. In the control room (`python tools/control_room.py`) the rule is "
        f"\"{rule_name('learned')}\" under AGV jobs; click an AGV to read why it was sent where it was.",
        "",
    ]
    return "\n".join(o)


# Why one cost was kept, decided on the development shifts before the test was run.
KEPT_BECAUSE = ("The first was kept. The rule set beforehand was to keep the plainer cost unless the other was clearly better on both quay moves and ship time: "
                "the plainer cost needs no assumption (the other has to say how a minute of empty driving compares with a minute of a blocked crane), "
                "and the wait of a job is what the experiment already reports for dispatch.")


def scale(lo: float, hi: float, about: int = 4) -> list:
    """Round tick values that cover lo to hi."""
    span = (hi - lo) or abs(hi) or 1.0
    raw = span / about
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 5, 10)), key=lambda x: abs(x - raw))
    first = math.floor(lo / step + 1e-9) * step
    out, v = [], first
    while v < hi - step * 1e-9:
        v += step
        out.append(round(v - step, 10))
    return [*out, round(v, 10)]


def num(v: float, step: float = 1.0, sign: bool = False) -> str:
    digits = 0 if step >= 1 else 1 if step >= 0.1 else 2
    text = f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}"
    return text.replace("-", "−")


def text(x: float, y: float, what: str, size: int = 12, fill: str = SOFT, anchor: str = "start", weight: int = 400) -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{what}</text>'


def picture(width: int, height: int, title: str, desc: str, body: list) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" font-family="{FONT}">\n'
            f'<title>{title}</title>\n<desc>{desc}</desc>\n<rect width="{width}" height="{height}" fill="#ffffff"/>\n' + "\n".join(body) + "\n</svg>\n")


SHADE, ROW = "#55615d", "#2a78d6"            # washes, drawn faintly: the better side of zero; the row of the rule this is about
HALO = "#edf3fb"                             # the row's wash as it looks on white: put round figures written over it, so no line runs through them


def wide(what: str, size: float = 12) -> float:
    """About how wide a line of text is drawn, to place what follows it."""
    return len(what) * size * 0.53


def minus(what: str) -> str:
    return what.replace("-", "−")


def apart(wanted: list, gap: float, lo: float, hi: float) -> list:
    """Heights for labels that should sit at the wanted heights but not on top of each other."""
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    ys = [wanted[i] for i in order]
    for _ in range(30):
        for i in range(1, len(ys)):
            short = gap - (ys[i] - ys[i - 1])
            if short > 0:
                ys[i - 1] -= short / 2
                ys[i] += short / 2
        shift = max(0.0, lo - ys[0]) - max(0.0, ys[-1] - hi)
        ys = [y + shift for y in ys]
    out = [0.0] * len(wanted)
    for i, y in zip(order, ys):
        out[i] = y
    return out


def of_these(k: int, n: int) -> str:
    return f"all {in_words(n)} of these measures" if k == n else f"none of these {in_words(n)} measures" if not k else f"{in_words(k)} of these {in_words(n)} measures"


def curve_figure(log: dict) -> str:
    """The learning curve: three measures on the development shifts after each round of training, against two rules written by hand."""
    curve, marks = log["curve"], log["yardsticks"]
    panels = ("agv_wait_for_agv_min", "agv_km", "quay_moves_per_crane_hour")
    hand = (("nearest_feasible", "6 4", "as planned"), ("nearest_job", "1.5 4", "written by hand"))
    w, h, left, right, top, bottom = 320, 300, 52, 56, 116, 40
    steps = [b["episodes"] - a["episodes"] for a, b in zip(curve, curve[1:])]
    rounds = (f" Training: {steps[0]} shifts in the first round, {steps[1]} in each round after." if len(steps) > 1 and len(set(steps[1:])) == 1 and steps[0] != steps[1]
              else f" Training: {steps[0]} shifts a round." if steps and len(set(steps)) == 1 else "")
    body = [text(16, 24, "What the rule had learned after each round of training", 14, INK, weight=600),
            text(16, 43, f"Measured after every round on {in_words(len(curve[0]['runs']))} development shifts it never trained on.{rounds}", 12, SOFT)]
    # the key, once, above the three charts
    x, y = 16, 66
    body.append(f'<line x1="{x}" x2="{x + 28}" y1="{y}" y2="{y}" stroke="{BLUE}" stroke-width="2.25" stroke-linecap="round"/>'
                f'<circle cx="{x + 14}" cy="{y}" r="4" fill="{BLUE}" stroke="#ffffff" stroke-width="2"/>')
    body.append(text(x + 36, y + 4, "The learned rule", 12, INK))
    x += 36 + wide("The learned rule") + 26
    for rule, dash, note in hand:
        label = f"{rule_name(rule)}, {note}"
        body.append(f'<line x1="{x:.1f}" x2="{x + 28:.1f}" y1="{y}" y2="{y}" stroke="{SOFT}" stroke-width="1.5" stroke-dasharray="{dash}" stroke-linecap="round"/>')
        body.append(text(x + 36, y + 4, label, 12, INK))
        x += 36 + wide(label) + 26
    xs = [c["episodes"] for c in curve]
    for k, m in enumerate(panels):
        x0 = 16 + k * w
        ys = [c[m] for c in curve] + [marks[r][m] for r, _, _ in hand]
        ticks = scale(min(ys), max(ys), 3)
        lo, hi, step = ticks[0], ticks[-1], ticks[1] - ticks[0]
        px = lambda v: x0 + left + (w - left - right) * v / max(xs)
        py = lambda v: top + (h - top - bottom) * (hi - v) / (hi - lo)
        said = lambda v: minus(f"{v:,.{DIGITS[m]}f}")
        body.append(text(x0 + left, 92, NAMES[m], 12, INK, weight=600))
        body.append(text(x0 + left, 107, "higher is better" if BETTER.get(m) else "lower is better", 11, SOFT))
        for t in ticks:
            body.append(f'<line x1="{px(0):.1f}" x2="{px(max(xs)):.1f}" y1="{py(t):.1f}" y2="{py(t):.1f}" stroke="{GRID}"/>')
            body.append(text(x0 + left - 8, py(t) + 4, num(t, step), 11, SOFT, "end"))
        for t in range(0, max(xs) + 1, 40):
            body.append(text(px(t), h - bottom + 16, str(t), 11, SOFT, "middle"))
        body.append(text(px(max(xs) / 2), h - 6, "training shifts so far", 11, SOFT, "middle"))
        for rule, dash, _ in hand:
            y = py(marks[rule][m])
            body.append(f'<line x1="{px(0):.1f}" x2="{px(max(xs)):.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{SOFT}" stroke-width="1.5" stroke-dasharray="{dash}" stroke-linecap="round">'
                        f'<title>{rule_name(rule)}: {said(marks[rule][m])}</title></line>')
        body.append(f'<polyline fill="none" stroke="{BLUE}" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round" points="'
                    + " ".join(f"{px(c['episodes']):.1f},{py(c[m]):.1f}" for c in curve) + '"/>')
        for c in curve:
            last = c is curve[-1]
            body.append(f'<circle cx="{px(c["episodes"]):.1f}" cy="{py(c[m]):.1f}" r="{5 if last else 3.5}" fill="{BLUE}" stroke="#ffffff" stroke-width="2">'
                        f'<title>After {c["episodes"]} training shifts: {said(c[m])}</title></circle>')
        # where each line ends, in figures beside it; moved apart when two lines end close together
        ends = [(curve[-1][m], INK, 600)] + [(marks[rule][m], SOFT, 400) for rule, _, _ in hand]
        for (v, fill, weight), y in zip(ends, apart([py(v) for v, _, _ in ends], 13, top + 2, h - bottom - 2)):
            body.append(text(px(max(xs)) + 10, y + 4, said(v), 12 if weight == 600 else 11, fill, weight=weight))
    return picture(16 + 3 * w, h + 8, "The learning curve of the dispatch rule",
                   "Three line charts over the number of training shifts: how long a job waits for its AGV, how far the AGVs drive, and quay moves per crane-hour, "
                   "each measured on the development shifts, with the level of two hand-written rules drawn across.", body)


def test_figure(results: dict) -> str:
    """The test: each rule against the rule the terminal runs as planned, shift by shift, on four measures."""
    panels = ("agv_wait_for_agv_min", "agv_km", "ship_turnaround_min", "quay_moves_per_crane_hour")
    rows = [d for d in results["differences"] if d["against"] == "nearest_feasible"]
    rows.sort(key=lambda d: ("longest_idle", "balanced", "nearest_job", "learned").index(d["rule"]))
    n = results["seeds"][1] - results["seeds"][0] + 1
    good = lambda m, i: sure(i) and (i["mean"] > 0) == (BETTER.get(m) == "higher")
    bad = lambda m, i: sure(i) and not good(m, i)
    mine = next(d for d in rows if d["rule"] == "learned")["measures"]
    worse = sum(bad(m, mine[m]) for m in panels)
    label_w, w, pad, top, row_h = 172, 190, 22, 138, 36
    dot = lambda x, y, paint, full, tip="": (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4.9 if full else 3.2}" fill="{paint if full else "#ffffff"}" '
                                             f'stroke="{"#ffffff" if full else paint}" stroke-width="{2 if full else 1.8}">{tip}</circle>')
    height = top + row_h * len(rows) + 30
    width = label_w + len(panels) * w + 8
    body = [text(16, 24, f"{rule_name('learned')} is better than the planned rule on {of_these(sum(good(m, mine[m]) for m in panels), len(panels))}"
                         + (f", and worse on {in_words(worse)}" if worse else ""), 14, INK, weight=600)]
    y = 43
    hand = next((d["measures"] for d in results["differences"] if (d["rule"], d["against"]) == ("learned", "nearest_job")), None)
    if hand:                                                               # the rule written by hand, which it does not beat: said, not left to be read off
        b, c = sum(good(m, hand[m]) for m in panels), sum(bad(m, hand[m]) for m in panels)
        body.append(text(16, y, f"Against {rule_name('nearest_job')}, a rule written by hand, it is better on {in_words(b) if b else 'none'} and worse on {in_words(c) if c else 'none'}.", 12, INK))
        y += 17
    body.append(text(16, y, f"Each row is a rule minus the planned rule, {rule_name('nearest_feasible')}, on the same {n} shifts, which training never saw.", 12, SOFT))
    # the key: what a full dot, an open dot and the shaded side mean
    x, y = 16, y + 21
    for full, label in ((True, "mean and 95 percent interval, clear of zero: more than chance"), (False, "the interval reaches zero: no sure difference")):
        body.append(f'<line x1="{x}" x2="{x + 26}" y1="{y}" y2="{y}" stroke="{FAINT}" stroke-width="2" stroke-linecap="round"/>' + dot(x + 13, y, SOFT, full))
        body.append(text(x + 33, y + 4, label, 12, SOFT))
        x += 33 + wide(label) + 22
    body.append(f'<rect x="{x:.1f}" y="{y - 6}" width="18" height="12" fill="{SHADE}" fill-opacity="0.09"/>')
    body.append(text(x + 25, y + 4, "the better side", 12, SOFT))
    y1, y2 = top + 2, top + row_h * len(rows)
    for i, d in enumerate(rows):
        yc = top + row_h * i + row_h / 2
        if d["rule"] == "learned":                                         # the row this is about, washed from edge to edge
            body.append(f'<rect x="8" y="{yc - row_h / 2 + 2:.1f}" width="{width - 16}" height="{row_h - 2}" rx="4" fill="{ROW}" fill-opacity="0.08"/>')
        body.append(text(16, yc + 4, rule_name(d["rule"]), 12, INK, weight=600 if d["rule"] == "learned" else 400))
    for k, m in enumerate(panels):
        x0 = label_w + k * w
        los = [d["measures"][m]["mean"] - d["measures"][m]["half"] for d in rows] + [0.0]
        his = [d["measures"][m]["mean"] + d["measures"][m]["half"] for d in rows] + [0.0]
        ticks = scale(min(los), max(his), 3)
        lo, hi, step = ticks[0], ticks[-1], ticks[1] - ticks[0]
        px = lambda v: x0 + pad + (w - 2 * pad) * (v - lo) / (hi - lo)
        name, _, unit = NAMES[m].partition(" (")
        body.append(text(x0 + pad, top - 28, name, 12, INK, weight=600))
        body.append(text(x0 + pad, top - 13, (unit.rstrip(")") + ", " if unit else "") + ("higher is better" if BETTER.get(m) else "lower is better"), 11, SOFT))
        xa, xb = (px(0), px(hi)) if BETTER.get(m) else (px(lo), px(0))
        body.append(f'<rect x="{xa:.1f}" y="{y1}" width="{xb - xa:.1f}" height="{y2 - y1}" fill="{SHADE}" fill-opacity="0.09"/>')
        zero = min(range(len(ticks)), key=lambda i: abs(ticks[i]))
        for i, t in enumerate(ticks):
            t = 0.0 if abs(t) < step * 1e-6 else t
            body.append(f'<line x1="{px(t):.1f}" x2="{px(t):.1f}" y1="{y1}" y2="{y2}" stroke="{SOFT if t == 0 else GRID}" stroke-width="1"/>')
            if len(ticks) <= 4 or (i - zero) % 2 == 0:                     # every line is drawn; when they are close, every other one is numbered
                body.append(text(px(t), y2 + 16, num(t, step, sign=t != 0), 11, SOFT, "middle"))
        for i, d in enumerate(rows):
            v, yc = d["measures"][m], top + row_h * i + row_h / 2
            own = d["rule"] == "learned"
            paint = BLUE if own else SOFT
            yd = yc + (6 if own else 0)                                    # the row with its figures written over it sits a little lower
            body.append(f'<line x1="{px(v["mean"] - v["half"]):.1f}" x2="{px(v["mean"] + v["half"]):.1f}" y1="{yd:.1f}" y2="{yd:.1f}" stroke="{BLUE if own else FAINT}" '
                        f'stroke-width="2" stroke-linecap="round"/>')
            body.append(dot(px(v["mean"]), yd, paint, sure(v),
                            f'<title>{rule_name(d["rule"])}, {name}: {minus(pm(v, m, True))}{" (clear of zero)" if sure(v) else " (reaches zero)"}</title>'))
            if own:                                                        # its figures, written out: the one row a reader will want to quote
                said = minus(pm(v, m, True))
                at = min(max(px(v["mean"]), x0 + 6 + wide(said, 11) / 2), x0 + w - 6 - wide(said, 11) / 2)
                body.append(text(at, yd - 10, said, 11, INK, "middle", 600).replace("<text ", f'<text paint-order="stroke" stroke="{HALO}" stroke-width="4" stroke-linejoin="round" '))
    return picture(width, height, "The dispatch rules compared on the held-out shifts",
                   "A dot and a 95 percent interval for each of four rules on four measures, each as a difference from the rule the terminal runs as planned. "
                   "Full dots mark intervals clear of zero; the better side of zero is shaded.", body)


def documents() -> dict:
    """The three written files, as they should be: {path: text}."""
    rule, log, results = (json.loads(path.read_text(encoding="utf-8")) for path in (rules.WEIGHTS, TRAINING, RESULTS))
    if log["rule"]["weights"] != rule["weights"]:
        raise SystemExit(f"{TRAINING.name} is the curve of other weights than {rules.WEIGHTS.name}: python tools/learned_dispatch.py train")
    if results["weights"] != rule["weights"]:
        raise SystemExit(f"{RESULTS.name} is a test of other weights than {rules.WEIGHTS.name}: python tools/learned_dispatch.py test")
    others = {cost: json.loads(also_tried(cost).read_text(encoding="utf-8")) for cost in COSTS if cost != rule["cost"] and also_tried(cost).exists()}
    return {REPORT: write_up(rule, log, results, others), CURVE_SVG: curve_figure(log), TEST_SVG: test_figure(results)}


def report(only_check: bool, say=print) -> int:
    stale = 0
    for path, wanted in documents().items():
        if not only_check:
            path.write_text(wanted, encoding="utf-8")
            say(f"wrote {shown(path)}")
        elif not path.exists() or path.read_text(encoding="utf-8") != wanted:
            stale += 1
            say(f"out of date: {shown(path)} (python tools/learned_dispatch.py report)")
    return 1 if stale else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Learn the dispatch rule in the simulator, test it, write it up.")
    sub = p.add_subparsers(dest="what", required=True)
    tr = sub.add_parser("train", help="learn the weights (writes sim/dispatch_weights.json and docs/learned-dispatch-training.json)")
    tr.add_argument("--cost", choices=sorted(COSTS), default=DEFAULT_COST,
                    help=f"what the rule learns to keep low ({DEFAULT_COST}); another cost writes only docs/learned-dispatch-training-COST.json, to compare")
    tr.add_argument("--rounds", type=int, default=len(EPSILON))
    tr.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="shifts at a time")
    tr.add_argument("--out", type=Path, help="where the weights go, if not where the simulator reads them")
    tr.add_argument("--log", type=Path, help="where the learning curve goes")
    te = sub.add_parser("test", help="the learned rule against the others on the experiment's 30 shifts (writes docs/learned-dispatch.json)")
    te.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="shifts at a time")
    re = sub.add_parser("report", help="write docs/learned-dispatch.md and its two figures from what train and test wrote")
    re.add_argument("--check", action="store_true", help="write nothing; exit 1 if a written file is out of date")
    a = p.parse_args(argv)
    if a.what == "train":
        kept = a.cost == DEFAULT_COST
        train(a.cost, a.jobs, a.rounds, a.out or (rules.WEIGHTS if kept else None), a.log or (TRAINING if kept else also_tried(a.cost)))
    elif a.what == "test":
        test(a.jobs)
    else:
        return report(a.check)
    return 0


def also_tried(cost: str) -> Path:
    return DOCS / f"learned-dispatch-training-{cost}.json"


if __name__ == "__main__":
    sys.exit(main())
