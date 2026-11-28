"""The container stacking module (sim/stacking.py): where a container goes and what it costs to get it out."""

import importlib.util
from pathlib import Path
from statistics import mean

import pytest

from sim.kernel import Container
from sim.stacking import Yard, YardFull, Zone

ONE_BAY = (Zone("z", "general", "A1", (1, 1)),)      # 2 slots x 3 container rows = 6 stacks, 5 high


def box(i, due=None, cargo="general"):
    return Container(f"C{i}", "import", cargo=cargo, due_s=due)


def put(yard, c):
    yard.assign(c)
    pos = yard.settle(c.id)
    yard.set_down(c.id, pos)
    return pos


def test_shortest_stack_fills_the_lowest_stack_first(dames):
    yard = Yard(dames, ONE_BAY, "shortest_stack")
    assert [put(yard, box(i))[2] for i in range(12)] == [1] * 6 + [2] * 6
    assert yard.count() == 12 and sorted(yard.heights()) == [2] * 6


def test_departure_time_puts_an_early_leaver_on_top_and_keeps_a_late_one_out_of_the_way(dames):
    yard = Yard(dames, ONE_BAY, "departure_time")
    late, early = put(yard, box(1, due=5000)), put(yard, box(2, due=1000))
    assert early[:2] == late[:2] and early[2] == 2            # on top of the one that leaves later: nothing to rehandle
    later = put(yard, box(3, due=9000))
    assert later[:2] != late[:2] and later[2] == 1            # it would be in the way: an empty stack instead
    assert yard.blockers("C1") == ["C2"] and yard.blockers("C2") == []
    unknown = put(yard, box(4))                               # no departure known: treated as leaving last
    assert unknown[2] == 1


def test_getting_a_buried_container_out_costs_one_rehandle_for_each_container_on_it(dames):
    yard = Yard(dames, ONE_BAY, "shortest_stack")
    for i in range(18):
        put(yard, box(i))
    assert yard.blockers("C0") == ["C12", "C6"]               # top first
    with pytest.raises(RuntimeError, match="not on top"):
        yard.lift_off("C0")
    for b in yard.blockers("C0"):
        to = yard.rehandle_target(b, "C0")
        assert to[:2] != yard.where["C0"][:2]                 # never back onto the stack being dug
        yard.lift_off(b)
        yard.relocate(b, to)
    yard.lift_off("C0")
    yard.gone("C0")
    assert yard.rehandles == 2 and yard.count() == 17 and yard.taken == 1
    assert max(yard.heights()) <= 5


def test_a_bay_keeps_room_to_dig_out_its_lowest_container(dames):
    yard = Yard(dames, ONE_BAY, "shortest_stack")
    for i in range(26):                                       # 30 positions less 4 kept free
        put(yard, box(i))
    with pytest.raises(YardFull):
        yard.assign(box(99))
    assert yard.capacity() == 30 and yard.occupancy() == pytest.approx(26 / 30)


def test_room_is_kept_for_a_container_from_the_moment_it_is_assigned(dames):
    yard = Yard(dames, ONE_BAY, "shortest_stack")
    for i in range(26):
        yard.assign(box(i))                                   # all still on their way
    with pytest.raises(YardFull):
        yard.assign(box(99))
    with pytest.raises(RuntimeError, match="already has a place"):
        yard.assign(box(0))


def test_food_and_hazmat_each_go_to_their_own_zone_and_the_zones_must_be_apart(dames):
    zones = (Zone("g", "general", "A1", (1, 1)), Zone("f", "food", "A1", (8, 8)), Zone("h", "hazmat", "N4", (1, 1)))
    yard = Yard(dames, zones, "departure_time")
    assert put(yard, box(1, cargo="food"))[0].startswith("A1-08")
    assert put(yard, box(2, cargo="hazmat"))[0].startswith("N4-01")
    assert put(yard, box(3))[0].startswith("A1-01")
    assert yard.mixed() == []
    with pytest.raises(ValueError, match="must be at least"):
        Yard(dames, (Zone("f", "food", "A1", (1, 1)), Zone("h", "hazmat", "A1", (3, 3))))
    with pytest.raises(ValueError, match="in two zones"):
        Yard(dames, (Zone("a", "general", "A1", (1, 2)), Zone("b", "general", "A1", (2, 3))))
    with pytest.raises(YardFull):
        Yard(dames, ONE_BAY).assign(box(9, cargo="hazmat"))   # no zone for it


def test_a_zone_takes_the_containers_of_its_own_berth_first(dames):
    zones = (Zone("west", "general", "A1", (1, 1), serves="B1"), Zone("east", "general", "H1", (1, 1), serves="B2"))
    yard = Yard(dames, zones, "shortest_stack")
    assert yard.assign(box(1), "B2") == ("H1", 1) and yard.assign(box(2), "B1") == ("A1", 1)
    assert yard.stand(("H1", 1)) == dames.transfer_point_for("H1-01-01").pos and yard.crane(("H1", 1)) == "RTG5"


def test_on_the_owners_own_test_departure_time_needs_far_fewer_rehandles(dames):
    """Oct 29: 120 containers through a small yard, coming and going; departure-time stacking won."""
    spec = importlib.util.spec_from_file_location("experiments", Path(__file__).resolve().parent.parent / "tools" / "experiments.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    for fill in (0.5, 0.75, 0.9):
        short = [ex.stacking_trial(dames, "shortest_stack", s, fill=fill) for s in range(1, 9)]
        timed = [ex.stacking_trial(dames, "departure_time", s, fill=fill) for s in range(1, 9)]
        assert all(t["lifts"] == 120 and t["left_in_yard"] == 0 for t in short + timed)       # every container got out again
        assert mean(t["rehandles"] for t in timed) < 0.7 * mean(t["rehandles"] for t in short)
    assert ex.stacking_trial(dames, "departure_time", 3) == ex.stacking_trial(dames, "departure_time", 3)
