# Data sheet: every number the simulation runs on

Written by `python tools/data_sheet.py` from `sim/params.py`. Change a number there, not here.

The simulation has two sets of numbers.

* **Reference**: PortSim's numbers (the course's reference simulator) and the assumptions made in the check-ins. Every time is fixed. The demo morning, its what-ifs and the recorded 3D runs use this set, so they give the same result every time.
* **Calibrated**: a number from a published source wherever one was found, crane and gate times that vary from one move to the next, and AGVs that keep their speed through a junction. The shift at real scale (`sim.scenario.shift`) uses this set.

The column *Basis* says how far to trust a calibrated number: **published** (the source's own figure), **worked out** (from published figures, the note says how), **assumed** (our choice, no source found: these are the ones to replace first), **PortSim** (the reference value kept).

## Quay cranes

| What | Reference | Calibrated | Unit | Basis | Source | Note |
| --- | --- | --- | --- | --- | --- | --- |
| One quay crane move | 60 | 103 | s | published | [S7](https://www.bts.gov/archive/port_performance/smith/slide14_text) | 35 moves an hour is the 'modern crane maximum'; waiting for AGVs brings the achieved rate down, which the validation compares with the same source's sustainable 28. |
| A container onto or off a vehicle | 15 | 22.5 | s | published | [S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf) | The middle of the 15 to 30 s the source gives for the crane and the AGV to hand over. |
| Spread of a crane move (coefficient of variation) | 0 | 0.3 |  | assumed |  | No measured distribution found. Lognormal, the same spread for quay, yard and exchange cranes. |
| AGVs called to a working crane at a time | 2 | 3 |  | assumed |  | The scheduler's rule, not the fleet size. A crane has four lanes. |
| AGVs are called before the ship berths | none | 600 | s | assumed |  | Reference: from the moment the ship is cleared, ten minutes out. With the real passage of 85 minutes that would hold AGVs idle, so they are called ten minutes ahead. |

## Stacking

| What | Reference | Calibrated | Unit | Basis | Source | Note |
| --- | --- | --- | --- | --- | --- | --- |
| One yard crane move | 75 | 100 | s | worked out | [S11](https://www-assets.liebherr.com/media/bu-media/lhbu-mcc/downloads-and-brochures/brochures/lcc/liebherr-rtg-cranes-technical-description.pdf) | From the crane's speeds (hoist 56 m/min empty and 28 loaded, trolley 70 m/min): 9 m of hoist travel each way, 8 m of trolley travel, 5 s to lock and to unlock, 18 s for acceleration and positioning. The heights and allowances are assumptions. |
| Yard crane travel along its blocks | 0 | 2.167 | m/s | published | [S11](https://www-assets.liebherr.com/media/bu-media/lhbu-mcc/downloads-and-brochures/brochures/lcc/liebherr-rtg-cranes-technical-description.pdf) | 130 m/min without load. In the reference the crane is at every bay at once. |
| Yard crane moves to another block | 0 | 120 | s | assumed |  | Turning the wheels and lining up, on top of the travel. |
| A yard crane takes the nearest job first, unless one has waited | no such rule | 600 s |  | assumed |  | Only when the crane's travel is modelled. In the reference it takes a retrieval before a container to stack (PortSim), in the order asked. |

## Gate

| What | Reference | Calibrated | Unit | Basis | Source | Note |
| --- | --- | --- | --- | --- | --- | --- |
| One exchange crane move | 75 | 100 | s | worked out | [S11](https://www-assets.liebherr.com/media/bu-media/lhbu-mcc/downloads-and-brochures/brochures/lcc/liebherr-rtg-cranes-technical-description.pdf) | Taken to be the same machine as a yard crane, until the gate owner sets it. |
| One gate check | 30 | 30 | s | PortSim | PortSim | No published gate transaction time was found, so the reference value is kept. |
| Spread of a gate check (coefficient of variation) | 0 | 0.3 |  | assumed |  |  |
| An import whose truck did not come goes back to the yard after | none | 1800 | s | assumed |  | Counted from the appointment; also when the truck is turned away. In the reference the container stays in the exchange stack, which has eight places. |
| Imports at the exchange ahead of their trucks, at most | none | 4 |  | assumed |  | Half of the eight places of the exchange stack, so a truck that brings an export always finds room. Without a limit the stack fills and the exchange locks up at real scale. |
| Trucks that do not come for their appointment | 0 (set by hand) | 14% |  | published | [S12](https://www.pierpass.org/news/pierpass-posts-2-0-update-results/) | No-shows in the first shift at Los Angeles and Long Beach, second quarter of 2019. Used by the shift workload. |

## AGVs

| What | Reference | Calibrated | Unit | Basis | Source | Note |
| --- | --- | --- | --- | --- | --- | --- |
| Top speed, empty | 6 | 6 | m/s | published | [S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf) | Battery AGV. |
| Top speed, loaded | 4 | 6 | m/s | published | [S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf) | The source gives 6 m/s with a 50 t load as well. PortSim slows a loaded vehicle to 4. |
| Acceleration and braking, empty | 1.2 | 1 | m/s2 | published | [S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf) |  |
| Acceleration and braking, loaded | 1.2 | 0.5 | m/s2 | published | [S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf) | With a 50 t load. |
| Keeps its speed through a junction | no | yes |  | assumed |  | Reference (PortSim, section 3.2): every stretch between two junctions is driven from rest to rest. Calibrated: a vehicle that crosses a junction straight, with the next stretch free, does not stop; it stops to turn. The source's 3 m/s in a curve is not modelled, a stop is, which is a little slow. |
| Battery | 40 | 250 | kWh | worked out | [S9](https://konecranes.com/discover/planning-for-sustainable-future) | Not published. 13.2 kWh an hour for the 19 hours the source says one charge of about 1.5 hours covers. |
| Energy per metre, empty | 0.003 | 0.0014 | kWh/m | worked out | [S9](https://konecranes.com/discover/planning-for-sustainable-future) | Set so that a working AGV in the model uses about what the sources report (13.2 kWh an hour in S9, 17 in S8), keeping PortSim's ratio of loaded to empty. The validation shows what came out; it is not an independent check. |
| Loaded against empty | 1.35 | 1.35 | x | PortSim | PortSim |  |
| Draw while standing | 0.6 | 0.6 | kW | PortSim | PortSim |  |
| Charging power | 100 | 167 | kW | worked out | [S9](https://konecranes.com/discover/planning-for-sustainable-future) | The battery above filled in 1.5 hours. |
| Charged up to | 0.9 | 0.9 | share | PortSim | PortSim |  |
| Charge at the start | 0.75 | 0.75 | share | PortSim | PortSim | The shift workload starts each AGV between 35 and 90 percent instead, so some must charge during the shift. |
| Goes to charge below | 0.35 | 0.35 | share | assumed |  | Only for a fleet larger than the 12 chargers, where AGVs share them (25 AGVs share 6 chargers at HHLA, S10). |

## Ship arrival

| What | Reference | Calibrated | Unit | Basis | Source | Note |
| --- | --- | --- | --- | --- | --- | --- |
| Water under the keel in the channel | 1 | 0.76 | m | published | [S6](https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02) | 2.5 ft. |
| Depth at mean tide | 12.5 | 12.74 | m | worked out | [S2](https://jaxport.com/?p=277) | 40 ft alongside at low water (S2) plus the mean tide level of 0.549 m (S5). |
| Tide, half the range | 0.9 | 0.516 | m | published | [S5](https://tidesandcurrents.noaa.gov/benchmarks/8720219.html) | Mean high water 1.065 m and mean low water 0.033 m above the chart datum: a range of 1.03 m, where the reference assumed 1.8. |
| Time between high waters | 12 h 25 min | 12 h 25 min |  | assumed |  | The main lunar tide; a plain sine curve, not NOAA's predictions for a date. |
| Passage from the sea buoy to the berth | 10 min | 85 min |  | worked out | [S1](https://jaxport.com/cargo/facilities/dames-point-marine-terminal) | 14.2 nautical miles (S1) at an assumed 10 knots. The demo morning keeps its 10 minutes. |
| Deepest ship | 13.4 m | 12.19 m (40 ft) |  | published | [S6](https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02) | The maximum draft at berths 16 and 17. |
| Ships in the channel at a time | 2 | 2 |  | assumed |  | From the Oct 29 check-in. |

## How a time varies

A crane move or a gate check is drawn from a lognormal distribution with the mean in the tables and a coefficient of variation of 0.3 (cranes) or 0.3 (gate): about two moves in three fall within 30 percent of the mean, and none is negative. Each draw has its own random stream, named by the run's seed, the kind of move, the container and how many times it has been moved. So the same container gets the same crane time under two policies, and the difference between two runs of a workload is the policy (Dr. Wang: "identical saved workloads and random seeds for both policies"). Without a seed nothing varies.

## The shift at real scale

`sim.scenario.shift(seed)` makes the workload the experiment and the validation run. Its own numbers:

| What | Value | Basis | Source | Note |
| --- | --- | --- | --- | --- |
| Ships in the shift | 4, two for each berth | assumed |  | The first two arrive at the start, 20 minutes apart; the others about four hours later, each up to 20 minutes early or late. |
| Containers a ship discharges, loads | 90 to 150, 50 to 90 | assumed |  | 140 to 240 moves a call. JAXPORT's 1,388,841 TEU on 1,611 vessel calls of every kind in fiscal 2025 ([S3](https://www.jaxport.com/media/statistics/)) is 862 TEU a call, so these are small calls; they are what the modelled terminal turns round in a shift. |
| Ship draft | 9.0 to 12.19 m | published (the maximum) | [S6](https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02) | Uniform between the two. |
| Cargo | 88% general, 8% food, 4% hazardous | assumed |  | Food and hazardous cargo each have a zone of their own. |
| Yard at the start | 65% of the 2,578 places the run uses | assumed | [S13](https://www.maritime.dot.gov/sites/marad.dot.gov/files/docs/resources/3661/070810tiogachcpproductivityreport.pdf) | The source puts the practical limit of a yard at 80 percent of its capacity and the average US container yard at about half. Seven of the 27 storage blocks are used for general cargo, each on a lane of its own. A bay keeps four positions free for rehandles, so this is 55 percent of every position in those blocks. |
| Trucks | 10 an hour for 8 hours | assumed |  | More in the second and third hour. 55% collect an import, the others bring an export for a later ship. |
| Trucks with an appointment | 80%, for a half-hour slot | assumed |  | They come about 8 minutes after the slot starts, give or take 9; later than 15 minutes is late. |
| Booked trucks that do not come | 14% | published | [S12](https://www.pierpass.org/news/pierpass-posts-2-0-update-results/) | |
| Trucks with a problem at the gate | 1.5% plate, 1% toll, 3% weight, 1% damaged export | assumed |  | |
| AGVs | 30, sharing the 12 charging stations | assumed |  | Five to a quay crane; the source's simulations use 6.5 ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)). 25 AGVs share 6 chargers at HHLA ([S10](https://konecranes.com/discover/hamburger-hafen-und-logistik-ag-germany)). |
| AGV breakdowns | one in 150 AGV-hours, 15 minutes on average | assumed |  | No published failure rate was found. |
| Crane outages | one in 300 crane-hours, 20 minutes on average | assumed |  | The same. |

## Sources

Read on 20 November 2026.

* **PortSim** PortSim, the course's reference simulator, appendix C.1 and section 3.2
* **S1** JAXPORT, Dames Point Marine Terminal: <https://jaxport.com/cargo/facilities/dames-point-marine-terminal>
* **S2** JAXPORT, technical specifications: <https://jaxport.com/?p=277>
* **S3** JAXPORT, cargo statistics: <https://www.jaxport.com/media/statistics/>
* **S4** JAXPORT, harbor deepening: <https://www.jaxport.com/cargo/port-improvements//harbor-deepening>
* **S5** NOAA Tides and Currents, station 8720219 Dames Point, datums: <https://tidesandcurrents.noaa.gov/benchmarks/8720219.html>
* **S6** Moran Shipping, port update Jacksonville, 2 April 2026: <https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02>
* **S7** Bureau of Transportation Statistics, port performance working group, slide 14 (2 August 2016): <https://www.bts.gov/archive/port_performance/smith/slide14_text>
* **S8** Y. Saanen (TBA), Port Technology International 70: AGV, Lift-AGV and ALV compared: <https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf>
* **S9** Konecranes, Planning for a sustainable future: <https://konecranes.com/discover/planning-for-sustainable-future>
* **S10** Konecranes, HHLA customer story (25 Li-ion AGVs, 6 quick chargers): <https://konecranes.com/discover/hamburger-hafen-und-logistik-ag-germany>
* **S11** Liebherr, rubber tyre gantry cranes, technical description: <https://www-assets.liebherr.com/media/bu-media/lhbu-mcc/downloads-and-brochures/brochures/lcc/liebherr-rtg-cranes-technical-description.pdf>
* **S12** PierPass, results of the revised OffPeak program, 25 July 2019: <https://www.pierpass.org/news/pierpass-posts-2-0-update-results/>
* **S13** The Tioga Group for MARAD, Improving marine container terminal productivity (2010): <https://www.maritime.dot.gov/sites/marad.dot.gov/files/docs/resources/3661/070810tiogachcpproductivityreport.pdf>
* **S14** Georgia Ports Authority, Savannah fact sheet: <https://gaports.com/wp-content/uploads/2026/09/00336_GPA_Fact-Sheets-26_Savannah_R2.pdf>

## What has no source yet

These are choices, not measurements. Each module's owner knows better than a search does:

* Quay cranes: spread of a crane move (coefficient of variation)
* Quay cranes: aGVs called to a working crane at a time
* Quay cranes: aGVs are called before the ship berths
* Stacking: yard crane moves to another block
* Stacking: a yard crane takes the nearest job first, unless one has waited
* Gate: spread of a gate check (coefficient of variation)
* Gate: an import whose truck did not come goes back to the yard after
* Gate: imports at the exchange ahead of their trucks, at most
* AGVs: keeps its speed through a junction
* AGVs: goes to charge below
* Ship arrival: time between high waters
* Ship arrival: ships in the channel at a time
* The shift: the number and size of the ships, the truck volume and how punctual trucks are, the failure rates, how full the yard is.

