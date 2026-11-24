"""The AGV module (sim/agv.py): how vehicles move on the map, who takes a job, and charging."""

import pytest

from sim.agv import POLICIES, STALL_S, AgvFleet, Energy, Job, Traffic, Vehicle
from sim.kernel import Clock, EventLog
from sim.metrics import cell_conflicts
from terminal_layout.timing import travel_time


def world(layout, energy=False):
    clock = Clock()
    return clock, Traffic(clock, layout, EventLog(clock), Energy() if energy else None)


def put(tr, name, cell):
    v = Vehicle(name, "agv", cell)
    tr.add(v, cell)
    return v


def test_an_unblocked_trip_follows_the_layouts_moves_and_takes_the_shared_travel_time(dames):
    clock, tr = world(dames)
    v, done = put(tr, "V", (31, 34)), []
    tr.send(v, (19, 18), lambda v: done.append(clock.now))
    clock.run()
    path = tr.route((31, 34), (19, 18))
    assert v.cell == (19, 18) and v.moves == len(path) - 1
    assert all(b in dames.neighbors(*a) for a, b in zip(path, path[1:]))
    assert done[0] == pytest.approx(travel_time(dames, path, loaded=False, stops=tr.stops), abs=0.05)
    assert tr.route((80, 0), (89, 4)) == dames.shortest_path((80, 0), (89, 4))      # away from the quay: the layout's own path


def test_a_vehicle_does_not_stop_at_a_transfer_point_it_only_passes(dames):
    clock, tr = world(dames)
    v = put(tr, "V", (35, 24))                       # bay 17 of A2; bay 1 is 16 cells down the same lane
    tr.send(v, (19, 24), lambda v: None)
    clock.run()
    assert [len(cells) - 1 for _, _, _, cells in tr.segments] == [16]


def test_a_loaded_vehicle_is_slower(dames):
    times = []
    for load in (None, "BOX"):
        clock, tr = world(dames)
        v = put(tr, "V", (35, 24))
        v.load = load
        tr.send(v, (19, 24), lambda v: times.append(clock.now))
        clock.run()
    assert times[1] > times[0] * 1.3


def test_two_vehicles_never_share_a_cell_and_the_second_queues_right_behind_the_first(dames):
    clock, tr = world(dames)
    a, b = put(tr, "A", (32, 34)), put(tr, "B", (31, 34))     # A is ahead of B on the way out of the quay
    arrived = {}

    def a_served(v):                                 # A stands at bay 7 for two minutes, then drives on
        arrived["A"] = clock.now
        clock.after(120, tr.send, a, (19, 24), lambda v: None)
    tr.send(a, (25, 24), a_served)
    tr.send(b, (22, 24), lambda v: arrived.setdefault("B", clock.now))     # B's bay lies beyond A's on the same lane
    clock.run()
    assert cell_conflicts(tr, clock.now) == []
    assert b.cell == (22, 24) and arrived["B"] > arrived["A"] + 120
    assert b.waited_s > 100 and tr.still_waiting() == []
    assert any(leg["who"] == "B" and leg["path"][-1] == (26, 24) for leg in tr.legs)     # it waited in the lane, one cell back


def test_traffic_passes_a_crane_lane_on_the_bypass_and_goes_round_a_vehicle_standing_in_its_way(dames):
    clock, tr = world(dames)
    lanes = set(dames.quay_cranes["QC1"].transfer_points)
    put(tr, "SERVED", (14, 34))                      # stands in QC1's lane
    v = put(tr, "V", (8, 33))
    tr.send(v, (31, 34), lambda v: None)
    clock.run()
    driven = {c for leg in tr.legs for c in leg["path"]}
    assert v.cell == (31, 34) and v.waited_s == 0 and not driven & lanes

    clock, tr = world(dames)
    put(tr, "PARKED", (20, 33))                      # stands on the bypass itself
    v = put(tr, "V", (15, 33))
    tr.send(v, (25, 33), lambda v: None)
    clock.run()
    assert v.cell == (25, 33) and v.moves == 12      # two moves more than the ten straight along the bypass
    assert cell_conflicts(tr, clock.now) == []


def test_a_destination_no_road_leads_to_is_reported_and_not_driven_to(dames):
    """Dr. Wang's list: detection and reporting of unreachable destinations."""
    clock, tr = world(dames.with_closures({(23, 24)}))        # the service lane is shut before the bay
    v = put(tr, "V", (15, 33))
    with pytest.raises(RuntimeError, match="no road"):
        tr.send(v, (22, 24), lambda v: None)
    assert [(s["vehicle"], s["cell"], s["why"]) for s in tr.stalls] == [("V", (22, 24), "unreachable")]
    assert v.cell == (15, 33) and "held up" in tr.log.entries[-1].text


def test_a_long_wait_is_reported_with_its_cell_and_a_vehicle_stuck_at_the_end_is_listed(dames):
    """Dr. Wang's list: detection and reporting of prolonged lack of progress."""
    clock, tr = world(dames)
    a, b = put(tr, "A", (32, 34)), put(tr, "B", (31, 34))
    tr.send(a, (25, 24), lambda v: clock.after(700, tr.send, a, (19, 24), lambda v: None))     # A is served for 700 s
    tr.send(b, (22, 24), lambda v: None)                      # B's bay lies beyond A's on the same lane
    clock.run()
    (stall,) = tr.stalls
    assert stall["vehicle"] == "B" and stall["cell"] == (25, 24) and stall["until"] - stall["since"] >= STALL_S
    assert b.cell == (22, 24) and tr.still_waiting() == []

    clock, tr = world(dames)
    a, b = put(tr, "A", (32, 34)), put(tr, "B", (31, 34))
    tr.send(a, (25, 24), lambda v: None)                      # this time A never leaves its bay
    tr.send(b, (22, 24), lambda v: None)
    clock.run()
    assert [(w["vehicle"], w["cell"], w["blocked_by"]) for w in tr.still_waiting()] == [("B", (25, 24), "A")]


def test_driving_and_standing_use_portsims_energy_and_charging_stops_at_ninety_percent(dames):
    e = Energy()
    for load, factor in ((None, 1.0), ("BOX", e.loaded_factor)):
        clock, tr = world(dames, energy=True)
        v = put(tr, "V", (35, 24))
        v.battery, v.load = 30.0, load
        tr.send(v, (19, 24), lambda v: None)
        clock.run()
        tr.settle(v)
        driving = 16 * 15 * e.kwh_per_m * factor
        assert v.used_kwh == pytest.approx(driving + e.standing_kw * clock.now / 3600, rel=1e-6)
        assert v.battery == pytest.approx(30.0 - v.used_kwh)
    clock, tr = world(dames, energy=True)
    v = put(tr, "V", (3, 29))
    v.battery, v.charging = 10.0, True
    clock.at(360, lambda: None)
    clock.run()
    tr.settle(v)
    assert v.battery == pytest.approx(20.0)          # 100 kW for six minutes
    clock.at(3600, lambda: None)
    clock.run()
    tr.settle(v)
    assert v.battery == pytest.approx(e.capacity_kwh * e.charge_to)


def fleet_of(layout, n, policy="nearest_feasible"):
    clock, tr = world(layout, energy=True)
    return clock, tr, AgvFleet(clock, layout, tr, EventLog(clock), n=n, policy=policy)


def test_the_fleet_starts_parked_and_charging_half_in_each_depot(dames):
    clock, tr, fleet = fleet_of(dames, 4)
    homes = [fleet.station_id[v.home] for v in fleet.agvs.values()]
    assert homes == ["CH1", "CH7", "CH2", "CH8"]
    assert all(v.state == "parked" and v.charging and v.cell == v.home for v in fleet.agvs.values())
    with pytest.raises(ValueError, match="12 charging stations"):          # without a pool every AGV needs its own station
        clock, tr = world(dames, energy=True)
        AgvFleet(clock, dames, tr, EventLog(clock), n=13, pool=False)
    with pytest.raises(ValueError, match="unknown dispatch policy"):
        fleet_of(dames, 2, policy="fastest")


def test_an_agv_takes_a_job_only_when_its_charge_covers_the_job_and_the_way_home(dames):
    clock, tr, fleet = fleet_of(dames, 2)
    for v in fleet.agvs.values():
        v.battery = 3.0
    at_pickup = []
    fleet.submit(Job("J1", "test", (31, 34), lambda agv, job: at_pickup.append(agv), drops=((19, 18),)))
    clock.run(until=120)
    assert not at_pickup and fleet.pending          # nobody has the charge yet; the job waits
    clock.run(until=3600)
    assert at_pickup == ["AGV1"] and not fleet.pending
    assert fleet.agvs["AGV1"].battery > 0 and tr.stalls == []


def test_a_finished_agv_takes_the_next_job_or_drives_home_to_charge(dames):
    clock, tr, fleet = fleet_of(dames, 1)
    seen = []

    def work(agv, job):
        fleet.carry(agv, "BOX", job.drops[0], lambda a: (fleet.unloaded(a), fleet.release(a), seen.append(job.id)))
    for i in (1, 2):
        fleet.submit(Job(f"J{i}", "test", (31, 34), work, drops=((19, 18),)))
    clock.run()
    v = fleet.agvs["AGV1"]
    assert seen == ["J1", "J2"] and v.jobs_done == 2
    assert v.state == "parked" and v.cell == v.home and v.charging and v.load is None


def test_each_dispatch_policy_picks_the_agv_it_says(dames):
    clock, tr, fleet = fleet_of(dames, 2)
    clock.at(1000, lambda: None)
    clock.run()
    west, east = fleet.agvs["AGV1"], fleet.agvs["AGV2"]
    near_west = Job("J", "test", (14, 34), None)
    assert POLICIES["nearest_feasible"](fleet, near_west, [west, east]) is west
    assert POLICIES["nearest_feasible"](fleet, Job("J", "test", (99, 34), None), [west, east]) is east
    west.idle_since, east.idle_since = 900.0, 100.0
    assert POLICIES["longest_idle"](fleet, near_west, [west, east]) is east
    for v, kwh in ((west, 12.0), (east, 36.0)):      # the far one has waited long and is full: the balanced rule sends it
        v.battery, v.charging, v.energy_t = kwh, False, clock.now
    assert POLICIES["balanced"](fleet, near_west, [west, east]) is east
    west.idle_since, west.battery = 100.0, 36.0
    assert POLICIES["balanced"](fleet, near_west, [west, east]) is west
