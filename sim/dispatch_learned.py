"""Dispatch rules that choose the job as well as the AGV, one of them learned (the AGV owner's idea of Oct 22:
"a learned (Q-learning) dispatch rule").

The three rules in sim/agv.py give the job that has waited longest an AGV. The rules here are asked a wider
question: of every (waiting job, free AGV) pair the fleet could start now, which goes first?

    nearest_job   the pair with the shortest drive to the pickup. Written by hand: the yardstick for the learned
                  rule, since it uses the same freedom and no learning.
    learned       the pair with the lowest score. The score is a sum of weights, one for each thing the rule
                  looks at; the weights were learned by Q-learning in the simulator (tools/learned_dispatch.py)
                  and are kept in sim/dispatch_weights.json with how they were made.

Both keep two things the fleet does under every rule. A job for a truck goes before a job for a ship: that is
the scheduler's priority. And no job is passed over for ever: one that has waited ten minutes (the wait the
simulator already reports as a stall) is taken before any other.

What the learned rule looks at, for each pair (features):

    drive            the drive from the AGV to the job's pickup, in hundreds of moves along the roads
    drive_served_1   the same drive again when the job is at a quay crane that one AGV is already coming to,
    drive_served_2   or two or more: a longer drive may matter less when the crane is not waiting for this one
    drive_load       the same drive again when the job fetches an export for a ship,
    drive_truck      or is for a truck
    waited           how long the job has waited for an AGV, in tens of minutes
    waited_long      the part of that beyond four minutes: waiting may weigh more as it gets long
    discharge_1, discharge_2     the job is at a quay crane that one AGV, or two or more, are already coming to
    load_0, load_1, load_2       the job fetches an export for a quay crane that none, one, or two or more AGVs
                                 are already fetching for
    deliver                      the job takes an import to a truck, rather than fetching a truck's export

A job at a quay crane nobody is coming to yet is the case the other jobs for ships are measured against. The
two kinds of job for a truck are only ever compared with each other (a truck's job goes before a ship's), so
they need one number between them. The score of a pair is the cost the rule expects to follow that choice, in
minutes, less the part that is the same for every pair at that moment and so cannot change the choice. What
counts as cost is set where the rule is trained (tools/learned_dispatch.py) and written into the weights file.

Why weights and not a table: a table needs a fixed list of actions. Here the choices are whichever jobs
and AGVs happen to be free, a different list every time, so the rule scores each pair with the same small
set of learned numbers (Q-learning with a linear Q-function).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

WEIGHTS = Path(__file__).with_name("dispatch_weights.json")
FEATURES = ("drive", "drive_served_1", "drive_served_2", "drive_load", "drive_truck", "waited", "waited_long",
            "discharge_1", "discharge_2", "load_0", "load_1", "load_2", "deliver")
LONG_S = 240.0               # the part of a wait beyond four minutes also counts on its own
AT_WORK = ("to_pickup", "at_pickup", "to_drop", "at_drop")
EXPECTS = "waiting"          # what the weights were trained to keep low, in the words of the reason noted with a choice,
#                              when the weights file does not say ("expects")

_weights: dict | None = None


def load_weights(path: Path | None = None) -> dict:
    """The learned weights, as tools/learned_dispatch.py wrote them: {"weights": {feature: number}, ...}."""
    global _weights
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _weights is None:
        if not WEIGHTS.exists():
            raise ValueError(f"the learned dispatch rule has no weights yet ({WEIGHTS.name} is missing): python tools/learned_dispatch.py train")
        found = json.loads(WEIGHTS.read_text(encoding="utf-8"))
        missing = [f for f in FEATURES if f not in found.get("weights", {})]
        if missing:
            raise ValueError(f"{WEIGHTS.name} has no weight for {', '.join(missing)}: python tools/learned_dispatch.py train")
        _weights = found                             # kept only once it is known to be whole
    return _weights


def crane_of(fleet, job) -> str | None:
    """The quay crane a ship's job is for: where a discharge is picked up, where an export is taken."""
    cell = job.pickup if job.kind == "discharge" else job.drops[0] if job.kind == "load" and job.drops else None
    if cell is None:
        return None
    known = fleet.__dict__.setdefault("_crane_at", {})
    if cell not in known:
        known[cell] = fleet.layout.cell(*cell).attrs.get("serves", {}).get("crane")
    return known[cell]


def at_work(fleet) -> Counter:
    """For each quay crane, the AGVs already at work for it: called to it and not yet loaded, or fetching an export for it."""
    count: Counter = Counter()
    for v in fleet.agvs.values():
        job = v.job
        if job is None or v.state not in AT_WORK:
            continue
        if job.kind == "load" or (job.kind == "discharge" and v.state in ("to_pickup", "at_pickup")):
            count[crane_of(fleet, job)] += 1
    return count


def features(fleet, job, moves: int, served: Counter) -> tuple:
    """What the learned rule looks at for one pair, in the order of FEATURES."""
    waited = fleet.clock.now - job.released
    kind = job.kind
    n = min(served[crane_of(fleet, job)], 2) if kind in ("discharge", "load") else 0
    drive = moves / 100.0
    quay, load, truck = kind == "discharge", kind == "load", kind in ("deliver", "store")
    return (drive, drive * (quay and n == 1), drive * (quay and n == 2), drive * load, drive * truck,
            waited / 600.0, max(0.0, waited - LONG_S) / 600.0,
            float(quay and n == 1), float(quay and n == 2), float(load and n == 0), float(load and n == 1), float(load and n == 2),
            float(kind == "deliver"))


def score(weights, f: tuple) -> float:
    return sum(w * x for w, x in zip(weights, f))


def order(job, v) -> tuple:
    """How a tie is broken, by every rule here: the older job, then the lower-numbered AGV."""
    return (job.released, job.id, v.n)


# ---- the rules: each is given the fleet and the pairs [(job, AGV, moves to the pickup)], and returns one of them ----


def nearest_job(fleet, pairs):
    return min(pairs, key=lambda p: (p[2], *order(p[0], p[1])))


def learned(fleet, pairs):
    weights = fleet.weights
    served = at_work(fleet)
    return min(pairs, key=lambda p: (round(score(weights, features(fleet, p[0], p[2], served)), 9), *order(p[0], p[1])))


PAIR_POLICIES = {"nearest_job": nearest_job, "learned": learned}


# ---- the reason noted with each choice (it reads, and changes nothing) ----------------------------------


def why(fleet, chosen, pairs) -> str:
    """Why this pair of the ones the fleet could start, with the numbers the rule went by."""
    job, v, moves = chosen
    far = lambda m: f"{m * fleet.traffic.cell_m:,.0f} m"
    span = lambda s: f"{s / 60:.0f} min" if s >= 90 else f"{s:.0f} s"
    waited = lambda j: fleet.clock.now - j.released
    jobs = {p[0].id: p[0] for p in pairs}
    agvs = {p[1].id for p in pairs}
    charge = " with enough charge" if fleet.energy else ""
    near = nearest_job(fleet, pairs)
    if len(pairs) == 1:
        return f"the only free AGV{charge}, a drive of {far(moves)}"
    if len(jobs) == 1 and fleet.policy == "nearest_job":
        nxt = min((p for p in pairs if p[1] is not v), key=lambda p: (p[2], p[1].n))
        return f"the nearest of the {len(agvs)} free AGVs{charge}, a drive of {far(moves)} (the next, {nxt[1].id}, {far(nxt[2])})"
    had = f"{len(jobs)} jobs waiting and {len(agvs)} free AGV{'s' if len(agvs) != 1 else ''}{charge}" if len(jobs) > 1 else f"the {len(agvs)} free AGVs{charge}"
    oldest = min(jobs.values(), key=lambda j: (j.released, j.id))
    if fleet.policy == "nearest_job":
        text = f"of {had}, the shortest drive to a pickup, {far(moves)}"
        if oldest is not job:
            best = min(p[2] for p in pairs if p[0] is oldest)
            text += f" (the job that has waited longest, {span(waited(oldest))}, is {far(best)} from the nearest AGV that could take it)"
        return text
    served = at_work(fleet)
    mine = score(fleet.weights, features(fleet, job, moves, served))
    crane = crane_of(fleet, job)
    coming = served[crane] if crane else 0
    about = f"a drive of {far(moves)} for a job that has waited {span(waited(job))}"
    if crane:
        about += f", for {crane}, which has " + ("no AGV at work for it yet" if not coming else f"{coming} AGV{'s' if coming != 1 else ''} at work for it already")
    text = f"the learned rule expects the least {getattr(fleet, 'expects', None) or EXPECTS} to follow this choice, of {had}: {about}"
    if near[0] is not job or near[1] is not v:
        theirs = score(fleet.weights, features(fleet, near[0], near[2], served))
        gap = theirs - mine
        text += (f". The shortest drive, {near[1].id} {near[0].purpose or 'to ' + str(near[0].pickup)}, {far(near[2])}, "
                 + (f"scores {gap:.1f} min worse" if gap >= 0.05 else "scores less than 0.1 min worse"))
    else:
        text += "; it is also the shortest drive"
    return text
