"""Every number the simulation runs on, in one place, with where each one comes from.

Two sets:

    REFERENCE    PortSim's numbers (the course's reference simulator) and the assumptions made in the
                 check-ins. Every time is fixed, so a run gives the same result every time. The demo
                 morning and its what-ifs run on this set, and the recorded runs stay valid.
    CALIBRATED   a number from a published source wherever one was found, random variation around the
                 means, and driving that keeps its speed through a junction.

docs/data-sheet.md is written from this file (tools/data_sheet.py), so the document and the code cannot
drift apart: ROWS says what each number is and where it is from, SOURCES lists the publications.

Random variation. A service time is drawn from a lognormal distribution with the given mean and
coefficient of variation (cv). Each draw has its own stream, named by what is being timed (the seed, the
kind of move, the container, how many times it has been moved), not by when it happens. So two runs of
the same workload under different policies give the same container the same crane times: the common
random numbers Dr. Wang asked for ("identical saved workloads and random seeds for both policies").
With seed None nothing varies.

    from sim.params import CALIBRATED
    sc = scenario.using(params=CALIBRATED.seeded(7))
"""

from __future__ import annotations

import dataclasses
import math
import random
from dataclasses import dataclass, field

from terminal_layout.timing import (ACCEL_M_S2, GATE_SERVICE_S, HANDOFF_S, QUAY_SERVICE_S, SPEED_EMPTY_M_S,
                                    SPEED_LOADED_M_S, YARD_SERVICE_S)

FT = 0.3048
KNOT = 1852.0 / 3600.0


@dataclass(frozen=True)
class Energy:
    """An AGV's battery. The defaults are PortSim's (appendix C.1)."""
    capacity_kwh: float = 40.0
    start: float = 0.75
    kwh_per_m: float = 0.003
    loaded_factor: float = 1.35
    standing_kw: float = 0.6
    charge_kw: float = 100.0
    charge_to: float = 0.90
    reserve_kwh: float = 2.5         # kept back when judging whether a job fits (assumption)
    charge_below: float = 0.35       # a pooled AGV with no job goes to charge below this share (assumption)


@dataclass(frozen=True)
class Params:
    name: str = "reference"
    seed: int | None = None                  # None: nothing varies
    # cranes: the mean time of one move, seconds
    quay_move_s: float = QUAY_SERVICE_S
    yard_move_s: float = YARD_SERVICE_S
    exchange_move_s: float = YARD_SERVICE_S
    handoff_s: float = HANDOFF_S             # a container onto or off a vehicle
    move_cv: float = 0.0                     # spread of a crane move around its mean
    gantry_m_s: float = 0.0                  # a yard crane's travel along its blocks; 0: it is wherever it is needed
    block_change_s: float = 0.0              # extra time for a yard crane to move to another block
    yard_patience_s: float = 600.0           # a travelling yard crane takes the nearest job first, unless one has waited this long
    # gate
    gate_check_s: float = GATE_SERVICE_S
    gate_cv: float = 0.0
    no_show_s: float | None = None           # an import waiting at the exchange goes back to the yard this long after its
                                             # truck's appointment, or when the truck is turned away; None: it stays there
    exchange_ahead: int | None = None        # imports fetched to the exchange before their trucks take it, at most; None: no limit
    # vehicles
    speed_empty_m_s: float = SPEED_EMPTY_M_S
    speed_loaded_m_s: float = SPEED_LOADED_M_S
    accel_empty_m_s2: float = ACCEL_M_S2
    accel_loaded_m_s2: float = ACCEL_M_S2
    rolling: bool = False                    # keep speed through a junction crossed straight (False: PortSim, rest to rest)
    agvs_per_crane: int = 2                  # AGVs called to a working quay crane at a time
    call_lead_s: float | None = None         # AGVs are called this long before a ship berths; None: when it is cleared
    energy: Energy = field(default_factory=Energy)
    # ships
    keel_clearance_m: float = 1.0

    def seeded(self, seed: int | None) -> "Params":
        return dataclasses.replace(self, seed=seed)

    def draw(self, mean: float, cv: float, *key) -> float:
        """A time with this mean: the mean itself without a seed, otherwise drawn from the stream `key` names."""
        if self.seed is None or cv <= 0.0 or mean <= 0.0:
            return mean
        s2 = math.log(1.0 + cv * cv)
        stream = random.Random(f"{self.seed}|" + "|".join(str(k) for k in key))
        return round(stream.lognormvariate(math.log(mean) - s2 / 2.0, math.sqrt(s2)), 3)

    def move(self, kind: str, *key) -> float:
        """Seconds for one crane move of this kind ("quay", "yard", "exchange") or one "gate" check."""
        if kind == "gate":
            return self.draw(self.gate_check_s, self.gate_cv, kind, *key)
        return self.draw(getattr(self, f"{kind}_move_s"), self.move_cv, kind, *key)

    def speed(self, loaded: bool) -> float:
        return self.speed_loaded_m_s if loaded else self.speed_empty_m_s

    def accel(self, loaded: bool) -> float:
        return self.accel_loaded_m_s2 if loaded else self.accel_empty_m_s2


def from_dict(d: dict) -> Params:
    """A Params from dataclasses.asdict (a saved scenario)."""
    return Params(**{**d, "energy": Energy(**d["energy"])})


REFERENCE = Params()

# ---- the calibrated set ---------------------------------------------------------------------------
# Tide at NOAA station 8720219 (Dames Point), metres above MLLW; the berths are 40 ft deep at low water.
MHW_M, MLW_M, MTL_M = 1.065, 0.033, 0.549
BERTH_DEPTH_FT = 40.0
TIDE_MEAN_DEPTH_M = round(BERTH_DEPTH_FT * FT + MTL_M, 2)             # 12.74
TIDE_AMPLITUDE_M = round((MHW_M - MLW_M) / 2.0, 3)                   # 0.516
TIDE_PERIOD_S = 44_712.0                                             # 12 h 25 min, the main lunar tide
SEA_BUOY_NM = 14.2
PASSAGE_KNOTS = 10.0                                                 # assumption
TRANSIT_S = round(SEA_BUOY_NM * 1852.0 / (PASSAGE_KNOTS * KNOT), 0)  # 5112 s, about 85 minutes
MAX_DRAFT_M = round(BERTH_DEPTH_FT * FT, 2)                          # 12.19: the deepest ship berths 16 and 17 take
NO_SHOW_SHARE = 0.14

# One yard crane move from Liebherr's speeds: lower the empty spreader, lock, hoist, trolley, lower, unlock,
# hoist the empty spreader, trolley back. Heights and the allowance are assumptions (stacks five high).
_LIFT_M, _TROLLEY_M, _LOCK_S, _ALLOWANCE_S = 9.0, 8.0, 5.0, 18.0
YARD_MOVE_S = round(2 * _LIFT_M / 56.0 * 60 + 2 * _LIFT_M / 28.0 * 60 + 2 * _TROLLEY_M / 70.0 * 60 + 2 * _LOCK_S + _ALLOWANCE_S, 0)   # 100

CALIBRATED = Params(
    name="calibrated",
    quay_move_s=round(3600.0 / 35.0, 0),             # 103 s: 35 moves an hour, the crane's own best rate
    yard_move_s=YARD_MOVE_S,
    exchange_move_s=YARD_MOVE_S,
    handoff_s=22.5,                                  # the middle of 15 to 30 s
    move_cv=0.3,
    gantry_m_s=round(130.0 / 60.0, 3),               # 130 m/min without load
    block_change_s=120.0,
    gate_check_s=GATE_SERVICE_S,
    gate_cv=0.3,
    no_show_s=1800.0,
    exchange_ahead=4,
    speed_empty_m_s=6.0,
    speed_loaded_m_s=6.0,
    accel_empty_m_s2=1.0,
    accel_loaded_m_s2=0.5,
    rolling=True,
    agvs_per_crane=3,
    call_lead_s=600.0,
    energy=Energy(capacity_kwh=250.0, start=0.75, kwh_per_m=0.0014, loaded_factor=1.35, standing_kw=0.6,
                  charge_kw=167.0, charge_to=0.90, reserve_kwh=15.0, charge_below=0.35),
    keel_clearance_m=round(2.5 * FT, 2),             # 0.76 m
)

SETS = {"reference": REFERENCE, "calibrated": CALIBRATED}

# ---- where the numbers come from ---------------------------------------------------------------------
SOURCES = {
    "PortSim": ("PortSim, the course's reference simulator, appendix C.1 and section 3.2", None),
    "S1": ("JAXPORT, Dames Point Marine Terminal", "https://jaxport.com/cargo/facilities/dames-point-marine-terminal"),
    "S2": ("JAXPORT, technical specifications", "https://jaxport.com/?p=277"),
    "S3": ("JAXPORT, cargo statistics", "https://www.jaxport.com/media/statistics/"),
    "S4": ("JAXPORT, harbor deepening", "https://www.jaxport.com/cargo/port-improvements//harbor-deepening"),
    "S5": ("NOAA Tides and Currents, station 8720219 Dames Point, datums", "https://tidesandcurrents.noaa.gov/benchmarks/8720219.html"),
    "S6": ("Moran Shipping, port update Jacksonville, 2 April 2026", "https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02"),
    "S7": ("Bureau of Transportation Statistics, port performance working group, slide 14 (2 August 2016)", "https://www.bts.gov/archive/port_performance/smith/slide14_text"),
    "S8": ("Y. Saanen (TBA), Port Technology International 70: AGV, Lift-AGV and ALV compared", "https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf"),
    "S9": ("Konecranes, Planning for a sustainable future", "https://konecranes.com/discover/planning-for-sustainable-future"),
    "S10": ("Konecranes, HHLA customer story (25 Li-ion AGVs, 6 quick chargers)", "https://konecranes.com/discover/hamburger-hafen-und-logistik-ag-germany"),
    "S11": ("Liebherr, rubber tyre gantry cranes, technical description", "https://www-assets.liebherr.com/media/bu-media/lhbu-mcc/downloads-and-brochures/brochures/lcc/liebherr-rtg-cranes-technical-description.pdf"),
    "S12": ("PierPass, results of the revised OffPeak program, 25 July 2019", "https://www.pierpass.org/news/pierpass-posts-2-0-update-results/"),
    "S13": ("The Tioga Group for MARAD, Improving marine container terminal productivity (2010)", "https://www.maritime.dot.gov/sites/marad.dot.gov/files/docs/resources/3661/070810tiogachcpproductivityreport.pdf"),
    "S14": ("Georgia Ports Authority, Savannah fact sheet", "https://gaports.com/wp-content/uploads/2026/09/00336_GPA_Fact-Sheets-26_Savannah_R2.pdf"),
}

# What each number is. "field" names a Params field (or energy.<field>); a row without one gives its two
# values itself. basis: "source" (the published value), "derived" (worked out from published values, the
# note says how), "assumed" (our choice: no source found), "PortSim" (kept from the reference).
ROWS = [
    {"module": "Quay cranes", "what": "One quay crane move", "field": "quay_move_s", "unit": "s", "basis": "source", "source": "S7",
     "note": "35 moves an hour is the 'modern crane maximum'; waiting for AGVs brings the achieved rate down, which the validation compares with the same source's sustainable 28."},
    {"module": "Quay cranes", "what": "A container onto or off a vehicle", "field": "handoff_s", "unit": "s", "basis": "source", "source": "S8",
     "note": "The middle of the 15 to 30 s the source gives for the crane and the AGV to hand over."},
    {"module": "Quay cranes", "what": "Spread of a crane move (coefficient of variation)", "field": "move_cv", "unit": "", "basis": "assumed", "source": None,
     "note": "No measured distribution found. Lognormal, the same spread for quay, yard and exchange cranes."},
    {"module": "Quay cranes", "what": "AGVs called to a working crane at a time", "field": "agvs_per_crane", "unit": "", "basis": "assumed", "source": None,
     "note": "The scheduler's rule, not the fleet size. A crane has four lanes."},
    {"module": "Quay cranes", "what": "AGVs are called before the ship berths", "field": "call_lead_s", "unit": "s", "basis": "assumed", "source": None,
     "note": "Reference: from the moment the ship is cleared, ten minutes out. With the real passage of 85 minutes that would hold AGVs idle, so they are called ten minutes ahead."},
    {"module": "Stacking", "what": "One yard crane move", "field": "yard_move_s", "unit": "s", "basis": "derived", "source": "S11",
     "note": f"From the crane's speeds (hoist 56 m/min empty and 28 loaded, trolley 70 m/min): {_LIFT_M:g} m of hoist travel each way, {_TROLLEY_M:g} m of trolley travel, {_LOCK_S:g} s to lock and to unlock, {_ALLOWANCE_S:g} s for acceleration and positioning. The heights and allowances are assumptions."},
    {"module": "Stacking", "what": "Yard crane travel along its blocks", "field": "gantry_m_s", "unit": "m/s", "basis": "source", "source": "S11",
     "note": "130 m/min without load. In the reference the crane is at every bay at once."},
    {"module": "Stacking", "what": "Yard crane moves to another block", "field": "block_change_s", "unit": "s", "basis": "assumed", "source": None,
     "note": "Turning the wheels and lining up, on top of the travel."},
    {"module": "Stacking", "what": "A yard crane takes the nearest job first, unless one has waited", "values": ("no such rule", f"{Params().yard_patience_s:g} s"), "unit": "", "basis": "assumed", "source": None,
     "note": "Only when the crane's travel is modelled. In the reference it takes a retrieval before a container to stack (PortSim), in the order asked."},
    {"module": "Gate", "what": "One exchange crane move", "field": "exchange_move_s", "unit": "s", "basis": "derived", "source": "S11",
     "note": "Taken to be the same machine as a yard crane, until the gate owner sets it."},
    {"module": "Gate", "what": "One gate check", "field": "gate_check_s", "unit": "s", "basis": "PortSim", "source": "PortSim",
     "note": "No published gate transaction time was found, so the reference value is kept."},
    {"module": "Gate", "what": "Spread of a gate check (coefficient of variation)", "field": "gate_cv", "unit": "", "basis": "assumed", "source": None, "note": ""},
    {"module": "Gate", "what": "An import whose truck did not come goes back to the yard after", "field": "no_show_s", "unit": "s", "basis": "assumed", "source": None,
     "note": "Counted from the appointment; also when the truck is turned away. In the reference the container stays in the exchange stack, which has eight places."},
    {"module": "Gate", "what": "Imports at the exchange ahead of their trucks, at most", "field": "exchange_ahead", "unit": "", "basis": "assumed", "source": None,
     "note": "Half of the eight places of the exchange stack, so a truck that brings an export always finds room. Without a limit the stack fills and the exchange locks up at real scale."},
    {"module": "Gate", "what": "Trucks that do not come for their appointment", "values": ("0 (set by hand)", f"{NO_SHOW_SHARE:.0%}"), "unit": "", "basis": "source", "source": "S12",
     "note": "No-shows in the first shift at Los Angeles and Long Beach, second quarter of 2019. Used by the shift workload."},
    {"module": "AGVs", "what": "Top speed, empty", "field": "speed_empty_m_s", "unit": "m/s", "basis": "source", "source": "S8", "note": "Battery AGV."},
    {"module": "AGVs", "what": "Top speed, loaded", "field": "speed_loaded_m_s", "unit": "m/s", "basis": "source", "source": "S8",
     "note": "The source gives 6 m/s with a 50 t load as well. PortSim slows a loaded vehicle to 4."},
    {"module": "AGVs", "what": "Acceleration and braking, empty", "field": "accel_empty_m_s2", "unit": "m/s2", "basis": "source", "source": "S8", "note": ""},
    {"module": "AGVs", "what": "Acceleration and braking, loaded", "field": "accel_loaded_m_s2", "unit": "m/s2", "basis": "source", "source": "S8", "note": "With a 50 t load."},
    {"module": "AGVs", "what": "Keeps its speed through a junction", "field": "rolling", "unit": "", "basis": "assumed", "source": None,
     "note": "Reference (PortSim, section 3.2): every stretch between two junctions is driven from rest to rest. Calibrated: a vehicle that crosses a junction straight, with the next stretch free, does not stop; it stops to turn. The source's 3 m/s in a curve is not modelled, a stop is, which is a little slow."},
    {"module": "AGVs", "what": "Battery", "field": "energy.capacity_kwh", "unit": "kWh", "basis": "derived", "source": "S9",
     "note": "Not published. 13.2 kWh an hour for the 19 hours the source says one charge of about 1.5 hours covers."},
    {"module": "AGVs", "what": "Energy per metre, empty", "field": "energy.kwh_per_m", "unit": "kWh/m", "basis": "derived", "source": "S9",
     "note": "Set so that a working AGV in the model uses about what the sources report (13.2 kWh an hour in S9, 17 in S8), keeping PortSim's ratio of loaded to empty. The validation shows what came out; it is not an independent check."},
    {"module": "AGVs", "what": "Loaded against empty", "field": "energy.loaded_factor", "unit": "x", "basis": "PortSim", "source": "PortSim", "note": ""},
    {"module": "AGVs", "what": "Draw while standing", "field": "energy.standing_kw", "unit": "kW", "basis": "PortSim", "source": "PortSim", "note": ""},
    {"module": "AGVs", "what": "Charging power", "field": "energy.charge_kw", "unit": "kW", "basis": "derived", "source": "S9",
     "note": "The battery above filled in 1.5 hours."},
    {"module": "AGVs", "what": "Charged up to", "field": "energy.charge_to", "unit": "share", "basis": "PortSim", "source": "PortSim", "note": ""},
    {"module": "AGVs", "what": "Charge at the start", "field": "energy.start", "unit": "share", "basis": "PortSim", "source": "PortSim",
     "note": "The shift workload starts each AGV between 35 and 90 percent instead, so some must charge during the shift."},
    {"module": "AGVs", "what": "Goes to charge below", "field": "energy.charge_below", "unit": "share", "basis": "assumed", "source": None,
     "note": "Only for a fleet larger than the 12 chargers, where AGVs share them (25 AGVs share 6 chargers at HHLA, S10)."},
    {"module": "Ship arrival", "what": "Water under the keel in the channel", "field": "keel_clearance_m", "unit": "m", "basis": "source", "source": "S6", "note": "2.5 ft."},
    {"module": "Ship arrival", "what": "Depth at mean tide", "values": ("12.5", f"{TIDE_MEAN_DEPTH_M:g}"), "unit": "m", "basis": "derived", "source": "S2",
     "note": "40 ft alongside at low water (S2) plus the mean tide level of 0.549 m (S5)."},
    {"module": "Ship arrival", "what": "Tide, half the range", "values": ("0.9", f"{TIDE_AMPLITUDE_M:g}"), "unit": "m", "basis": "source", "source": "S5",
     "note": "Mean high water 1.065 m and mean low water 0.033 m above the chart datum: a range of 1.03 m, where the reference assumed 1.8."},
    {"module": "Ship arrival", "what": "Time between high waters", "values": ("12 h 25 min", "12 h 25 min"), "unit": "", "basis": "assumed", "source": None,
     "note": "The main lunar tide; a plain sine curve, not NOAA's predictions for a date."},
    {"module": "Ship arrival", "what": "Passage from the sea buoy to the berth", "values": ("10 min", f"{TRANSIT_S / 60:.0f} min"), "unit": "", "basis": "derived", "source": "S1",
     "note": f"{SEA_BUOY_NM:g} nautical miles (S1) at an assumed {PASSAGE_KNOTS:g} knots. The demo morning keeps its 10 minutes."},
    {"module": "Ship arrival", "what": "Deepest ship", "values": ("13.4 m", f"{MAX_DRAFT_M:g} m (40 ft)"), "unit": "", "basis": "source", "source": "S6",
     "note": "The maximum draft at berths 16 and 17."},
    {"module": "Ship arrival", "what": "Ships in the channel at a time", "values": ("2", "2"), "unit": "", "basis": "assumed", "source": None, "note": "From the Oct 29 check-in."},
]
