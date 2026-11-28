"""Step 5: dispatch rules that choose the job as well as the AGV, one of them learned by Q-learning in the simulator
(sim/dispatch_learned.py, tools/learned_dispatch.py, docs/learned-dispatch.md)."""

import importlib.util
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

from sim import agv, control, dispatch_learned, scenario
from sim.agv import OVERDUE_S, PAIR_POLICIES, POLICIES, STALL_S, AgvFleet, Energy, Job, Traffic
from sim.kernel import Clock, EventLog
from sim.metrics import check, summary
from sim.terminal import Terminal

ROOT = Path(__file__).resolve().parent.parent
DRIVE_ONLY = tuple(1.0 if f == "drive" else 0.0 for f in dispatch_learned.FEATURES)


def tool(name):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def learner():
    return tool("learned_dispatch")


def fleet_of(layout, n, policy="nearest_job", weights=None):
    clock = Clock()
    traffic = Traffic(clock, layout, EventLog(clock), Energy())
    fleet = AgvFleet(clock, layout, traffic, traffic.log, n=n, policy="nearest_job" if weights is not None else policy)
    if weights is not None:
        fleet.policy, fleet.pair_rule, fleet.weights = "learned", dispatch_learned.learned, tuple(weights)
    return clock, traffic, fleet


def waiting(fleet, jobs):
    """Jobs put in the queue with no AGV free to take them yet (the fleet is held), as (job, released) pairs."""
    for job, released in jobs:
        job.released = released
        fleet.pending.append(job)
        fleet.jobs.append(job)


def quay_job(name, lane, priority=0, kind="discharge"):
    return Job(name, kind, lane, lambda agv, job: None, priority=priority)


# ---- the choices a rule is given ------------------------------------------------------------------------


def test_the_rules_written_before_are_untouched_and_the_new_ones_are_listed(dames):
    assert tuple(POLICIES) == ("nearest_feasible", "longest_idle", "balanced") and tuple(PAIR_POLICIES) == ("nearest_job", "learned")
    assert scenario.POLICIES["dispatch"] == (*POLICIES, *PAIR_POLICIES) and scenario.DEFAULT_POLICIES["dispatch"] == "nearest_feasible"
    assert control.POLICY_NAMES["nearest_job"] == "Nearest job first" and control.POLICY_NAMES["learned"] == "Learned (Q-learning)"
    names = [x["id"] for x in control.options(dames)["policies"]["dispatch"]]
    assert names == list(scenario.POLICIES["dispatch"])                    # the control room offers them
    with pytest.raises(ValueError, match="unknown dispatch policy 'cleverest': choose from"):
        fleet_of(dames, 2, policy="cleverest")
    assert tool("experiments").POLICY["dispatch"] == tuple(POLICIES)       # the published experiment is still the three rules


def test_a_rule_is_offered_every_pair_of_the_top_priority_and_an_overdue_job_first(dames):
    clock, traffic, fleet = fleet_of(dames, 3)
    clock.at(100.0, lambda: None)
    clock.run()
    lanes = [dames.quay_cranes[q].transfer_points[1] for q in ("QC1", "QC3", "QC6")]
    ship_a, ship_b, truck = quay_job("J1", lanes[0]), quay_job("J2", lanes[1]), quay_job("J3", lanes[2], priority=1, kind="deliver")
    waiting(fleet, [(ship_a, 40.0), (ship_b, 60.0)])
    pairs = fleet.pairs()
    assert {(j.id, v.id) for j, v, _ in pairs} == {(j, v) for j in ("J1", "J2") for v in ("AGV1", "AGV2", "AGV3")}
    assert all(moves == traffic.moves(traffic.position(v), j.pickup) for j, v, moves in pairs)
    waiting(fleet, [(truck, 90.0)])
    assert {j.id for j, _, _ in fleet.pairs()} == {"J3"}                   # the scheduler's priority: a truck's job before a ship's
    fleet.pending.remove(truck)
    clock.at(60.0 + OVERDUE_S - 1.0, lambda: None)
    clock.run()
    assert {j.id for j, _, _ in fleet.pairs()} == {"J1"}                   # J1 has waited ten minutes: it goes before any other
    assert OVERDUE_S == STALL_S == 600.0
    for v in fleet.agvs.values():
        v.broken_until = 1e9
    assert fleet.pairs() == []                                             # no AGV free: nothing to choose from


def test_nearest_job_takes_the_shortest_drive_and_says_what_it_passed_over(dames):
    clock, traffic, fleet = fleet_of(dames, 2)
    clock.at(100.0, lambda: None)
    clock.run()
    west, east = fleet.agvs["AGV1"], fleet.agvs["AGV2"]
    far_old, near_new = quay_job("J1", dames.quay_cranes["QC6"].transfer_points[1]), quay_job("J2", dames.quay_cranes["QC1"].transfer_points[1])
    east.broken_until = 1e9                                                # only the west AGV is free
    waiting(fleet, [(far_old, 20.0), (near_new, 80.0)])
    pairs = fleet.pairs()
    job, v, moves = dispatch_learned.nearest_job(fleet, pairs)
    assert (job.id, v.id) == ("J2", "AGV1") and moves == min(m for _, _, m in pairs)
    assert POLICIES["nearest_feasible"](fleet, far_old, [west]) is west    # the old rules would have served J1, the oldest
    fleet.dispatch()
    assert near_new.agv == "AGV1" and far_old in fleet.pending
    said = fleet.log.why("AGV1")[-1].text
    assert said.startswith("AGV1 is sent to (") and "of 2 jobs waiting and 1 free AGV with enough charge, the shortest drive to a pickup, " in said
    assert "(the job that has waited longest, 80 s, is " in said and said.endswith(" m from the nearest AGV that could take it).")


def test_with_one_job_waiting_nearest_job_is_the_nearest_agv_rule(dames):
    clock, traffic, fleet = fleet_of(dames, 4)
    clock.at(50.0, lambda: None)
    clock.run()
    job = quay_job("J1", dames.quay_cranes["QC2"].transfer_points[1])
    waiting(fleet, [(job, 50.0)])
    free = list(fleet.agvs.values())
    assert dispatch_learned.nearest_job(fleet, fleet.pairs())[1] is POLICIES["nearest_feasible"](fleet, job, free)
    fleet.dispatch()
    assert "the nearest of the 4 free AGVs with enough charge, a drive of " in fleet.log.why(job.agv)[-1].text


def test_a_morning_under_nearest_job_finishes_and_passes_every_check(dames):
    plan = Terminal(dames, scenario.demo()).run()
    t = Terminal(dames, scenario.demo().using(dispatch="nearest_job")).run()
    assert check(t) == [] and summary(t)["ships_served"] == 3 and summary(t)["unfinished"] == summary(plan)["unfinished"]
    assert summary(plan)["end_min"] == 115.1                               # the plan itself is as it was
    reasons = [r.text for r in t.log.reasons if r.module == "agv" and " is sent " in r.text]
    assert reasons and all("free AGV" in text for text in reasons)


# ---- what the learned rule looks at -----------------------------------------------------------------------


def test_the_features_of_a_pair(dames):
    clock, traffic, fleet = fleet_of(dames, 3)
    clock.at(1000.0, lambda: None)
    clock.run()
    qc2, qc5 = dames.quay_cranes["QC2"].transfer_points, dames.quay_cranes["QC5"].transfer_points
    stand = next(c.pos for c in dames.cells() if c.type == "transfer_point" and c.attrs.get("serves", {}).get("block") == "A1")
    at_quay, export, truck = quay_job("J1", qc2[1]), Job("J2", "load", stand, None, drops=tuple(qc5)), quay_job("J3", stand, priority=1, kind="deliver")
    waiting(fleet, [(at_quay, 1000.0 - 90.0), (export, 1000.0 - 300.0), (truck, 1000.0)])
    assert dispatch_learned.crane_of(fleet, at_quay) == "QC2" and dispatch_learned.crane_of(fleet, export) == "QC5" and dispatch_learned.crane_of(fleet, truck) is None
    names = dispatch_learned.FEATURES
    none = Counter()
    f = dict(zip(names, dispatch_learned.features(fleet, at_quay, 250, none)))
    assert f == {**dict.fromkeys(names, 0.0), "drive": 2.5, "waited": 0.15}                   # a quay crane nobody is coming to: the case the rest is measured against
    f = dict(zip(names, dispatch_learned.features(fleet, at_quay, 250, Counter({"QC2": 1}))))
    assert f == {**dict.fromkeys(names, 0.0), "drive": 2.5, "drive_served_1": 2.5, "waited": 0.15, "discharge_1": 1.0}
    f = dict(zip(names, dispatch_learned.features(fleet, at_quay, 40, Counter({"QC2": 5}))))
    assert (f["drive"], f["drive_served_2"], f["discharge_2"], f["drive_served_1"], f["discharge_1"]) == (0.4, 0.4, 1.0, 0.0, 0.0)
    f = dict(zip(names, dispatch_learned.features(fleet, export, 100, Counter({"QC5": 1, "QC2": 2}))))
    assert f == {**dict.fromkeys(names, 0.0), "drive": 1.0, "drive_load": 1.0, "waited": 0.5, "waited_long": 0.1, "load_1": 1.0}
    f = dict(zip(names, dispatch_learned.features(fleet, truck, 100, Counter({"QC5": 1}))))
    assert f == {**dict.fromkeys(names, 0.0), "drive": 1.0, "drive_truck": 1.0, "deliver": 1.0}
    truck.kind = "store"                                                   # a truck's export: the other kind of truck job, the one deliver is measured against
    f = dict(zip(names, dispatch_learned.features(fleet, truck, 100, Counter({"QC5": 1}))))
    assert f == {**dict.fromkeys(names, 0.0), "drive": 1.0, "drive_truck": 1.0}
    assert len(names) == 13 and dispatch_learned.score(DRIVE_ONLY, tuple(f.values())) == 1.0


def test_the_agvs_at_work_for_a_crane_are_counted_as_the_rule_means_it(dames):
    clock, traffic, fleet = fleet_of(dames, 4)
    lane = dames.quay_cranes["QC3"].transfer_points[1]
    stand = next(c.pos for c in dames.cells() if c.type == "transfer_point" and c.attrs.get("serves", {}).get("block") == "A1")
    a, b, c, d = fleet.agvs.values()
    a.job, a.state = quay_job("J1", lane), "to_pickup"                    # called to QC3
    b.job, b.state = quay_job("J2", lane), "to_drop"                      # loaded at QC3 and gone to the yard: no longer coming to it
    c.job, c.state = Job("J3", "load", stand, None, drops=(lane,)), "to_drop"      # bringing an export to QC3
    d.job, d.state = quay_job("J4", stand, kind="deliver"), "to_pickup"   # for a truck
    assert dispatch_learned.at_work(fleet) == Counter({"QC3": 2})


def test_with_only_the_drive_weighing_the_learned_rule_is_the_nearest_job_rule(dames):
    """The score is a sum of weights: put every weight but the drive's at zero and the two rules must make the same morning."""
    by_hand = Terminal(dames, scenario.demo().using(dispatch="nearest_job")).run()
    t = Terminal(dames, scenario.demo().using(dispatch="nearest_job"))
    t.fleet.policy, t.fleet.pair_rule, t.fleet.weights = "learned", dispatch_learned.learned, DRIVE_ONLY
    t.run()
    assert summary(t) == summary(by_hand) and check(t) == []
    said = [r.text for r in t.log.reasons if r.module == "agv" and "the learned rule expects the least" in r.text]
    assert said and all("it is also the shortest drive" in text for text in said)


def test_the_learned_rule_says_why_and_what_it_passed_over(dames):
    clock, traffic, fleet = fleet_of(dames, 2, weights=tuple(-1.0 if f == "waited" else 0.01 if f == "drive" else 0.0 for f in dispatch_learned.FEATURES))
    clock.at(400.0, lambda: None)
    clock.run()
    fleet.agvs["AGV2"].broken_until = 1e9
    far_old, near_new = quay_job("J1", dames.quay_cranes["QC6"].transfer_points[1]), quay_job("J2", dames.quay_cranes["QC1"].transfer_points[1])
    far_old.purpose, near_new.purpose = "to QC6, to take a container of ALPHA to the yard", "to QC1, to take a container of ALPHA to the yard"
    waiting(fleet, [(far_old, 100.0), (near_new, 390.0)])
    fleet.dispatch()                                                       # this rule weighs the wait far more than the drive: the old job goes
    assert far_old.agv == "AGV1"
    said = fleet.log.why("AGV1")[-1].text
    assert said.startswith("AGV1 is sent to QC6, to take a container of ALPHA to the yard: the learned rule expects the least ")
    assert "of 2 jobs waiting and 1 free AGV with enough charge: a drive of " in said and "for a job that has waited 5 min, for QC6, which has no AGV at work for it yet" in said
    assert ". The shortest drive, AGV1 to QC1, to take a container of ALPHA to the yard, " in said and said.endswith(" min worse.")


# ---- the trainer -------------------------------------------------------------------------------------------


def choices(n, true_w, level, rng, lot_share=1.0, shifts=40):
    """Made-up choices with a known answer. At each, the load on the terminal raises the first feature of every pair on offer,
    and the cost that follows whatever is chosen (by `level` times the first feature of the average pair). Where the rule
    chooses it takes the pair with the lowest first feature; a row is what tools/learned_dispatch.py notes for a choice."""
    k = len(dispatch_learned.FEATURES)
    rows = []
    for i in range(n):
        busy = rng.random()
        offer = [tuple((busy if j == 0 else 0.0) + rng.random() * (j == 0 or rng.random() < 0.6) for j in range(k)) for _ in range(4)]
        lot = rng.random() < lot_share
        chosen = rng.choice(offer) if lot else min(offer)
        ctx = (1.0, *(rng.random() for _ in range(5)))
        mean = tuple(sum(f[j] for f in offer) / 4 for j in range(k))
        target = level * mean[0] + sum(a * b for a, b in zip(true_w, chosen)) + rng.gauss(0, 0.05)
        rows.append((ctx, mean, chosen, target, lot, 4, 1001 + i % shifts, 2))
    return rows


def test_the_fit_on_choices_by_lot_finds_the_weights_and_the_plain_fit_is_fooled(learner):
    rng = random.Random(7)
    true_w = [rng.uniform(-3, 3) for _ in dispatch_learned.FEATURES]
    rows = choices(6000, true_w, 30.0, rng, lot_share=0.5)
    by_lot = learner.fit(learner.sums(rows))
    assert by_lot["rows"] == sum(1 for r in rows if r[4]) and 2700 < by_lot["rows"] < 3300      # the rule's own choices are left out
    assert all(abs(a - b) < 0.05 for a, b in zip(by_lot["weights"], true_w))
    assert len(by_lot["level"]) == len(learner.CONTEXT) + len(dispatch_learned.FEATURES) and by_lot["r2"] > 0.99
    assert by_lot["level"][len(learner.CONTEXT)] == pytest.approx(30.0, abs=0.1)                 # the load is found where it is: in the average pair
    plain = learner.fit(learner.sums(rows, by_lot=False))                   # every choice, and no average pair: the situation leaks into the weights
    assert plain["rows"] == 6000 and len(plain["level"]) == len(learner.CONTEXT)
    assert abs(plain["weights"][0] - true_w[0]) > 5.0                       # the load raises the cost and the first feature together
    assert learner.solve([[2.0, 1.0], [1.0, 3.0]], [3.0, 5.0]) == pytest.approx([0.8, 1.4])
    a, b = learner.sums(rows[:3000]), learner.sums(rows[3000:])
    both = learner.fit(learner.add(a, b))
    assert both["weights"] == pytest.approx(by_lot["weights"]) and learner.add(learner.add(a, b), b, -1.0)["n"] == a["n"]


def test_the_jackknife_says_how_sure_each_weight_is(learner):
    rng = random.Random(11)
    true_w = [1.0] * len(dispatch_learned.FEATURES)
    spread = []
    for noise_shifts in (40, 40):
        rows = choices(4000, true_w, 30.0, rng, shifts=noise_shifts)
        parts = {seed: learner.sums([r for r in rows if r[6] == seed]) for seed in {r[6] for r in rows}}
        errors, cov = learner.jackknife(parts)
        spread.append(learner.fit(learner.sums(rows))["weights"])
        assert len(errors) == len(true_w) and all(0 < e < 0.05 for e in errors)
        assert all(cov[i][i] == pytest.approx(errors[i] ** 2) for i in range(len(errors))) and cov[0][1] == pytest.approx(cov[1][0])
    assert all(abs(a - b) < 6 * e for a, b, e in zip(*spread, errors))      # two samples differ by about what the standard error says


def test_reading_the_cost_changes_nothing_and_a_training_shift_notes_every_choice(learner, dames, monkeypatch):
    monkeypatch.setattr(learner, "shift", lambda seed, setting=None: scenario.generate(seed, agvs=10))      # a small morning: quick
    monkeypatch.setattr(learner, "_layout", dames)
    plain = Terminal(dames, scenario.generate(3, agvs=10).using(dispatch="nearest_job")).run()
    run = learner.trial((3, "nearest_job", None, "jobs", None))
    assert {m: run[m] for m in learner.MEASURES} == {m: summary(plain)[m] for m in learner.MEASURES}         # watched or not, the same morning
    assert run["cost_min"] > 0 and run["problems"] == 0 and run["finished"] == 1
    waits = sum(j.times["at_pickup"] - j.released for j in plain.fleet.jobs if "at_pickup" in j.times) / 60.0
    assert run["cost_min"] == pytest.approx(waits, rel=0.08)                # cost "jobs" is the waiting of jobs, read every ten seconds
    assert learner.trial((3, "nearest_job", None, "blocked", None))["cost_min"] < run["cost_min"]            # the other cost counts fewer things
    k = len(dispatch_learned.FEATURES)
    e = learner.episode((3, None, None, 1.0, "jobs", "test"))
    rows = e.pop("rows")
    assert len(rows) == len(plain.fleet.jobs) and e["problems"] == 0 and e["finished"] == 1
    for ctx, mean, feat, target, lot, options, seed, jobs in rows:
        assert len(ctx) == len(learner.CONTEXT) and len(mean) == len(feat) == k and target >= 0 and seed == 3 and 1 <= jobs <= options
        assert lot == (options > 1)                                        # in a round where every choice is by lot, every real choice is
        assert options > 1 or mean == feat                                 # one pair on offer: it is the average pair
    assert any(lot for *_, lot, _, _, _ in rows) and any(jobs > 1 for *_, jobs in rows)
    again = learner.episode((3, None, None, 1.0, "jobs", "test"))
    assert again.pop("rows") == rows and again == e                        # the same seed and tag: the same choices by lot
    assert learner.episode((3, None, None, 1.0, "jobs", "another round")).pop("rows") != rows
    greedy = learner.episode((3, list(DRIVE_ONLY), [0.0] * (len(learner.CONTEXT) + k), 0.0, "jobs", "test"))
    assert not any(row[4] for row in greedy.pop("rows")) and {m: greedy[m] for m in learner.MEASURES} == {m: run[m] for m in learner.MEASURES}


def test_a_run_without_the_guard_puts_the_guard_back(learner, dames, monkeypatch):
    monkeypatch.setattr(learner, "shift", lambda seed, setting=None: scenario.generate(seed, agvs=10))
    monkeypatch.setattr(learner, "_layout", dames)
    seen = []
    kept = agv.AgvFleet.pairs
    monkeypatch.setattr(agv.AgvFleet, "pairs", lambda self: (seen.append(agv.OVERDUE_S), kept(self))[1])
    learner.trial((3, "nearest_job", None, "jobs", {"guard": False}))
    assert seen and set(seen) == {float("inf")} and agv.OVERDUE_S == STALL_S       # off for the run, and back after it
    seen.clear()
    learner.trial((3, "nearest_job", None, "jobs", None))
    assert set(seen) == {STALL_S}


def test_training_development_and_test_shifts_never_overlap(learner):
    train = set(range(learner.TRAIN_FROM, learner.TRAIN_FROM + sum(learner.EPISODES)))
    assert not train & set(learner.DEV_SEEDS) and not train & set(learner.TEST_SEEDS) and not set(learner.DEV_SEEDS) & set(learner.TEST_SEEDS)
    assert learner.TEST_SEEDS == tuple(range(1, tool("experiments").REPS + 1))             # the experiment's own shifts
    assert len(learner.EPSILON) == len(learner.EPISODES) and learner.EPSILON[0] == 1.0             # the first round is all by lot
    assert all(0 < e <= 1 for e in learner.EPSILON) and list(learner.EPSILON) == sorted(learner.EPSILON, reverse=True)
    assert learner.shift(3, {"scale": "small"}).agvs == 10 and learner.shift(3, {"agvs": 18}).agvs == 18 and learner.shift(3).agvs == 30
    assert len(learner.shift(0, {"scale": "plan"}).vessels) == 3 and learner.shift(0, {"scale": "plan"}).agvs == 12


# ---- the rule that was trained, its test, and what was written about them ----------------------------------------


STALE = ("the simulator no longer gives the runs the learned rule was tested on: python tools/learned_dispatch.py test, then report "
         "(and train first if the rule should learn from the change)")


@pytest.fixture(scope="module")
def trained():
    return json.loads((ROOT / "sim" / "dispatch_weights.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results():
    return json.loads((ROOT / "docs" / "learned-dispatch.json").read_text(encoding="utf-8"))


def test_the_weights_file_is_whole_and_says_how_it_was_made(learner, trained):
    names = dispatch_learned.FEATURES
    assert set(trained["weights"]) == set(trained["standard_errors"]) == set(names) and all(e > 0 for e in trained["standard_errors"].values())
    assert trained["cost"] == learner.DEFAULT_COST and trained["expects"] == learner.COSTS[trained["cost"]]["expects"] == "waiting"
    assert trained["made_by"] == f"python tools/learned_dispatch.py train --cost {trained['cost']}"
    tr = trained["training"]
    assert tr["shifts"] == sum(learner.EPISODES) and tr["seeds"] == [learner.TRAIN_FROM, learner.TRAIN_FROM + tr["shifts"] - 1]
    assert tr["share_by_lot"] == list(learner.EPSILON) and tr["by_lot"] <= tr["between_pairs"] <= tr["choices"] and tr["between_jobs"] <= tr["between_pairs"]
    assert not set(range(tr["seeds"][0], tr["seeds"][1] + 1)) & (set(learner.TEST_SEEDS) | set(learner.DEV_SEEDS))       # trained on shifts of its own
    log = json.loads(learner.TRAINING.read_text(encoding="utf-8"))
    assert log["rule"] == trained and len(log["curve"]) == tr["rounds"] + 1 and log["curve"][-1]["weights"] == [trained["weights"][f] for f in names]
    assert log["curve"][0]["weights"] is None and [c["episodes"] for c in log["curve"]][-1] == tr["shifts"]
    every = [*log["curve"], *log["yardsticks"].values(), log["plain_fit"]]
    assert all(point["finished"] == len(learner.DEV_SEEDS) and point["problems"] == 0 for point in every)                 # every development run finished clean
    assert set(log["yardsticks"]) == {name for name, _, _ in learner.YARDSTICKS}
    assert all(log["covariance"][i][i] == pytest.approx(trained["standard_errors"][f] ** 2, abs=5e-4) for i, f in enumerate(names))
    other = json.loads(learner.also_tried("blocked").read_text(encoding="utf-8"))                                          # the cost that was not kept
    assert other["rule"]["cost"] == "blocked" and other["rule"]["training"]["seeds"] == tr["seeds"] and len(other["curve"]) == len(log["curve"])


def test_without_weights_the_learned_rule_says_what_to_run(dames, monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_learned, "WEIGHTS", tmp_path / "dispatch_weights.json")
    monkeypatch.setattr(dispatch_learned, "_weights", None)
    with pytest.raises(ValueError, match=r"has no weights yet \(dispatch_weights.json is missing\): python tools/learned_dispatch.py train"):
        Terminal(dames, scenario.demo().using(dispatch="learned"))
    for _ in range(2):                                                     # asked twice: a file that is not whole is never kept
        (tmp_path / "dispatch_weights.json").write_text(json.dumps({"weights": {"drive": 1.0}}))
        with pytest.raises(ValueError, match="dispatch_weights.json has no weight for drive_served_1, "):
            Terminal(dames, scenario.demo().using(dispatch="learned"))
    Terminal(dames, scenario.demo().using(dispatch="nearest_job"))        # the other rules need no file


def test_the_plan_under_the_learned_rule_is_the_run_in_the_write_up(learner, dames, results):
    t = Terminal(dames, scenario.demo().using(dispatch="learned")).run()
    s = summary(t)
    assert check(t) == [] and s["ships_served"] == 3 and t.fleet.expects == "waiting"
    assert {m: s[m] for m in learner.MEASURES} == {m: results["plan"]["learned"][m] for m in learner.MEASURES}, STALE
    assert results["plan"]["nearest_feasible"]["end_min"] == 115.1 and set(results["plan"]) == set(learner.CARRY_RULES)
    said = [r.text for r in t.log.reasons if r.module == "agv" and "the learned rule expects the least waiting to follow this choice" in r.text]
    assert len(said) > 10 and any(" min worse." in text for text in said)                          # it does not always take the shortest drive, and says so
    assert summary(control.run({"policies": {"dispatch": "learned"}}, dames)) == s               # the control room runs the same morning


def test_a_held_out_shift_under_the_learned_rule_is_the_run_that_was_published(learner, results):
    row = next(r for r in results["each_run"] if r["rule"] == "learned" and r["seed"] == 1)
    assert learner.trial((1, "learned", None, results["cost"], None)) == row, STALE


def test_the_test_was_run_on_the_rule_as_it_is_and_on_the_experiments_own_shifts(learner, results, trained):
    assert results["weights"] == trained["weights"] and results["cost"] == trained["cost"]
    assert results["seeds"] == [learner.TEST_SEEDS[0], learner.TEST_SEEDS[-1]] == [1, 30] and results["runs"] == 150 == len(results["each_run"])
    assert results["finished"] == 150 and results["problems"] == 0
    assert set(results["rules"]) == set(learner.RULES) and [(d["rule"], d["against"]) for d in results["differences"]] == list(learner.PAIRS)
    assert results["published"]["compared"] >= 360 and results["published"]["the_same"] == results["published"]["compared"]   # the older rules: the experiment's numbers again
    assert [c["setting"] for c in results["carry"]] == list(learner.CARRY) and all(c["runs"] == 90 and c["problems"] == 0 for c in results["carry"])
    mine = [r["agv_wait_for_agv_min"] for r in results["each_run"] if r["rule"] == "learned"]
    assert results["rules"]["learned"]["agv_wait_for_agv_min"]["mean"] == pytest.approx(sum(mine) / 30, abs=1e-3)


def test_the_write_up_is_what_the_numbers_give(learner, results):
    assert learner.main(["report", "--check"]) == 0, "run python tools/learned_dispatch.py report"
    text = learner.REPORT.read_text(encoding="utf-8")
    assert "A learned dispatch rule is the AGV owner's idea" in text and "It is the owner's to adopt, change or replace." in text
    for name in ("learned-dispatch-test.svg", "learned-dispatch-curve.svg"):
        assert f"]({name})" in text and (ROOT / "docs" / name).read_text(encoding="utf-8").startswith("<svg ")
    d = next(x for x in results["differences"] if (x["rule"], x["against"]) == ("learned", "nearest_job"))["measures"]["agv_km"]
    assert f"| *Learned (Q-learning) minus Nearest job first* |" in text and f"{d['mean']:+.0f} ± {d['half']:.0f}" in text


def test_the_test_figure_says_in_words_and_marks_what_the_numbers_say(learner, results):
    panels = ("agv_wait_for_agv_min", "agv_km", "ship_turnaround_min", "quay_moves_per_crane_hour")
    svg = learner.test_figure(results)
    of = lambda rule, against: next(d for d in results["differences"] if (d["rule"], d["against"]) == (rule, against))["measures"]
    good = lambda m, i: abs(i["mean"]) > i["half"] and (i["mean"] > 0) == (m == "quay_moves_per_crane_hour")
    bad = lambda m, i: abs(i["mean"]) > i["half"] and not good(m, i)
    words = ("no", "one", "two", "three", "four")
    plan, hand = of("learned", "nearest_feasible"), of("learned", "nearest_job")
    better = sum(good(m, plan[m]) for m in panels)
    assert 0 < better < 4 and f">Learned (Q-learning) is better than the planned rule on {words[better]} of these four measures<" in svg
    b, w = sum(good(m, hand[m]) for m in panels), sum(bad(m, hand[m]) for m in panels)
    assert b == 0 and w >= 1                                     # it does not beat the rule written by hand: the picture says so in words
    assert f">Against Nearest job first, a rule written by hand, it is better on none and worse on {words[w]}.<" in svg
    reach = sum(abs(d["measures"][m]["mean"]) <= d["measures"][m]["half"] for d in results["differences"] if d["against"] == "nearest_feasible" for m in panels)
    assert svg.count('r="3.2"') == reach + 1 and svg.count('r="4.9"') == 16 - reach + 1          # an open dot for every interval that reaches zero, and one in the key
    for m in panels:                                             # the learned rule's own figures are written over its row, as the write-up gives them
        assert ">" + learner.pm(plan[m], m, True).replace("-", "\u2212") + "<" in svg


def test_figures_written_beside_lines_that_end_together_are_moved_apart(learner):
    ys = learner.apart([100.0, 101.0, 140.0], 13, 20, 200)
    assert ys[0] < ys[1] < ys[2] and ys[1] - ys[0] >= 13 - 1e-6 and ys[2] == 140.0 and abs((ys[0] + ys[1]) / 2 - 100.5) < 1e-6
    low = learner.apart([198.0, 199.0], 13, 20, 200)             # at the foot of a chart: pushed up, not out of it
    assert low[1] <= 200 + 1e-6 and low[1] - low[0] >= 13 - 1e-6
    assert learner.apart([50.0], 13, 20, 200) == [50.0]
    assert [learner.of_these(k, 4) for k in (0, 3, 4)] == ["none of these four measures", "three of these four measures", "all four of these measures"]


def test_the_curve_figure_has_one_key_and_a_figure_at_the_end_of_each_line(learner):
    log = json.loads(learner.TRAINING.read_text(encoding="utf-8"))
    svg = learner.curve_figure(log)
    assert svg.count(">The learned rule<") == 1 and svg.count(">Nearest with charge, as planned<") == 1 and svg.count(">Nearest job first, written by hand<") == 1
    last = log["curve"][-1]
    for m, digits in (("agv_wait_for_agv_min", 2), ("agv_km", 0), ("quay_moves_per_crane_hour", 2)):
        for v in (last[m], log["yardsticks"]["nearest_feasible"][m], log["yardsticks"]["nearest_job"][m]):
            assert f">{v:,.{digits}f}<" in svg
    steps = [b["episodes"] - a["episodes"] for a, b in zip(log["curve"], log["curve"][1:])]
    assert f"Training: {steps[0]} shifts in the first round, {steps[1]} in each round after." in svg


class Serial:
    """multiprocessing.Pool for a test: the same calls, one after another in this process."""

    def __init__(self, jobs):
        self.jobs = jobs

    def __enter__(self):
        return self

    def __exit__(self, *problem):
        return False

    def imap_unordered(self, f, tasks, chunksize=1):
        return map(f, tasks)


def test_train_test_and_report_run_end_to_end_on_small_workloads(learner, dames, monkeypatch, tmp_path):
    small = lambda seed, setting=None: scenario.demo() if (setting or {}).get("scale") == "plan" else scenario.generate(seed, agvs=10)
    for name, value in (("shift", small), ("_layout", dames), ("DEV_SEEDS", (5001, 5002)), ("TEST_SEEDS", (1, 2)), ("EPISODES", (3, 3)), ("EPSILON", (1.0, 0.5)),
                        ("CARRY", ({"agvs": 18},)), ("DOCS", tmp_path), ("TRAINING", tmp_path / "training.json"), ("RESULTS", tmp_path / "results.json"),
                        ("REPORT", tmp_path / "write-up.md"), ("CURVE_SVG", tmp_path / "curve.svg"), ("TEST_SVG", tmp_path / "test.svg")):
        monkeypatch.setattr(learner, name, value)
    monkeypatch.setattr(learner.multiprocessing, "Pool", Serial)
    monkeypatch.setattr(dispatch_learned, "WEIGHTS", tmp_path / "weights.json")
    monkeypatch.setattr(dispatch_learned, "_weights", None)
    said = []
    rule = learner.train("jobs", 1, 2, dispatch_learned.WEIGHTS, learner.TRAINING, say=said.append)
    assert rule == json.loads(dispatch_learned.WEIGHTS.read_text()) and rule["training"]["shifts"] == 6 and rule["training"]["seeds"] == [1001, 1006]
    assert said[0].startswith("cost: the minutes jobs wait for an AGV") and said[-1].startswith("wrote ")
    again = learner.train("jobs", 1, 2, tmp_path / "again.json", tmp_path / "again-log.json", say=lambda text: None)
    assert again == rule                                                   # trained twice: the same weights to the last digit
    result = learner.test(1, say=said.append)
    assert result["runs"] == 10 == result["finished"] and result["problems"] == 0 and result["weights"] == rule["weights"]
    assert result["published"] == {"compared": 0, "the_same": 0} and result["plan"]["nearest_feasible"]["end_min"] == 115.1
    assert learner.report(False, say=said.append) == 0 and learner.report(True, say=said.append) == 0
    text = learner.REPORT.read_text(encoding="utf-8")
    assert text.startswith("# A dispatch rule learned in the simulator") and "were not there to compare the older rules with" in text and "**Another cost.**" not in text
    learner.REPORT.write_text(text + "changed by hand\n", encoding="utf-8")
    assert learner.report(True, say=said.append) == 1 and said[-1].startswith("out of date: ") and said[-1].endswith("(python tools/learned_dispatch.py report)")
    learner.train("jobs", 1, 1, dispatch_learned.WEIGHTS, learner.TRAINING, say=lambda text: None)            # other weights than the ones tested
    with pytest.raises(SystemExit, match="results.json is a test of other weights than weights.json"):
        learner.report(True)


def test_the_preflight_checks_the_learned_rule(monkeypatch, tmp_path):
    preflight = tool("preflight")
    report = preflight.Report(out=lambda line: None)
    preflight.check_learned(report, {})
    assert report.lines == [("ok", "learned dispatch rule: 13 weights, trained 2026-11-21, and its write-up (docs/learned-dispatch.md) is current", None)]
    assert ("the learned dispatch rule", preflight.check_learned) in preflight.CHECKS
    # no weights: the control room offers the rule, so that is a failure, with what to run
    monkeypatch.setattr(dispatch_learned, "WEIGHTS", tmp_path / "dispatch_weights.json")
    monkeypatch.setattr(dispatch_learned, "_weights", None)
    report = preflight.Report(out=lambda line: None)
    preflight.check_learned(report, {})
    status, text, fix = report.lines[0]
    assert status == "FAIL" and text == "learned dispatch rule: the learned dispatch rule has no weights yet (dispatch_weights.json is missing)"
    assert fix.startswith("python tools/learned_dispatch.py train, then test and report") and "Learned (Q-learning)" in fix
    # weights that the write-up was not made from: a warning, since the rule itself still runs
    whole = json.loads((ROOT / "sim" / "dispatch_weights.json").read_text(encoding="utf-8"))
    whole["weights"]["drive"] += 1.0
    (tmp_path / "dispatch_weights.json").write_text(json.dumps(whole), encoding="utf-8")
    report = preflight.Report(out=lambda line: None)
    preflight.check_learned(report, {})
    status, text, fix = report.lines[0]
    assert status == "WARN" and "its write-up cannot be checked" in text and "is the curve of other weights" in text and fix.startswith("python tools/learned_dispatch.py test")


def test_the_documents_quote_the_tests_numbers(learner, results, trained):
    d = {(x["rule"], x["against"]): x["measures"] for x in results["differences"]}
    plan, hand, tr = d[("learned", "nearest_feasible")], d[("learned", "nearest_job")], trained["training"]
    ships, km, wait = plan["ship_turnaround_min"], plan["agv_km"], plan["agv_wait_for_agv_min"]
    assert all(learner.sure(i) and i["mean"] < 0 for i in (ships, km, wait))                                   # "each more than chance"
    assert learner.sure(hand["agv_km"]) and hand["agv_km"]["mean"] > 0                                         # the rule written by hand drives less
    assert not any(learner.sure(hand[m]) for m in ("agv_wait_for_agv_min", "ship_turnaround_min", "quay_moves_per_crane_hour", "truck_turnaround_min"))    # "does as well"
    share = round(100 * -wait["mean"] / results["rules"]["nearest_feasible"]["agv_wait_for_agv_min"]["mean"])
    script = (ROOT / "docs" / "demo-script.md").read_text(encoding="utf-8")
    for part in (f"its {len(trained['weights'])} weights were fitted from {tr['shifts']} simulated shifts, in which {tr['by_lot']:,} choices were made by lot",
                 f"On {results['seeds'][1]} shifts it never saw", f"a ship is in port {-ships['mean']:.1f} minutes less, a job waits {share} percent less for its AGV "
                 f"and the AGVs drive {-km['mean']:.0f} km less a shift, each more than chance", f"does as well and drives another {hand['agv_km']['mean']:.0f} km less",
                 f"Under the learned rule a ship is in port {-ships['mean']:.1f} minutes less and the AGVs drive {-km['mean']:.0f} km less a shift.",
                 "The learned rule was the AGV owner's idea, so it is the owner's to give.", "`docs/learned-dispatch.md`"):
        assert part in script, part
    later = round(results["plan"]["learned"]["end_min"] - results["plan"]["nearest_feasible"]["end_min"])
    recorded = next(v for v in control.VARIANTS if v["id"] == "dispatch-learned")
    assert recorded["settings"] == {"policies": {"dispatch": "learned"}} and f"this small morning ends {later} minutes later under it than as planned" in recorded["about"]
    assert f"This small morning ends {later} minutes later under it than as planned" in script and later == 6
    page = (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")
    assert "**AGV jobs**" in script and '<label for="ctl-dispatch">AGV jobs</label>' in page and f"**{control.POLICY_NAMES['learned']}**" in script
    quoted = (f"{-ships['mean']:.1f} ± {ships['half']:.1f} minutes less", f"{-km['mean']:.0f} ± {km['half']:.0f} km less",
              f"another {hand['agv_km']['mean']:.0f} ± {hand['agv_km']['half']:.0f} km less")
    for name in ("docs/integration.md", "CHANGELOG.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        for part in quoted:
            assert part in text, (name, part)
    assert f"waits {share} percent less for its AGV" in " ".join((ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split())
    assert f"of {len(trained['weights'])} weights" in " ".join((ROOT / "docs" / "integration.md").read_text(encoding="utf-8").split())
