# Validation: the model against published figures

Written by `python tools/data_sheet.py` from the experiment (`docs/experiments-summary.json`) and the data sheet (`sim/params.py`).

The model's column is the shift at real scale as planned (`sim.scenario.shift`, 30 AGVs, 10 trucks an hour, every module on its default policy), 30 runs, the mean plus or minus half its 95 percent confidence interval. 30 of 30 runs finished every job, with 0 failed checks. A shift is 762 quay moves on four ships, 69 trucks served and 2,199 containers on the terminal; the run ends 15.4 hours after the first ship arrives at the sea buoy.

This is a comparison of size. It does not prove the model right: the published figures are from other terminals, and several inputs are assumptions (`docs/data-sheet.md` lists them). What it shows is where the model is in the range of a real terminal and where it is not, and why.

| Measure | Model | Published | Reading |
| --- | --- | --- | --- |
| Quay crane moves per crane-hour alongside | 15.7 ± 0.3 | 28 sustainable, 35 the crane's best ([S7](https://www.bts.gov/archive/port_performance/smith/slide14_text)); 42 in simulated automated terminals ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)) | **56 percent of the sustainable rate.** The crane's own cycle is the source's 35 an hour; the rest is waiting for AGVs. With 36 AGVs it is 16.0 ± 0.3, so more AGVs do not close the gap. The causes are below. |
| One AGV job, from the call to the container set down | 16.9 ± 0.3 min | 9.2 min: 6.5 boxes per AGV-hour ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)) | **1.8 times as long.** The drive is long (a round trip between a quay crane and a yard block of its berth is 1.1 to 3.7 km on this map's one-way roads) and the AGV waits its turn in a lane. |
| AGV speed while driving | 4.44 ± 0.01 m/s | 6 m/s top speed, 3 m/s in a curve ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)) | Plausible: the mean of a top speed of 6 with a stop to turn and to queue. Not a check, since the source gives no mean. |
| Share of an AGV's cycle spent waiting at the yard crane | 20% | 35 to 45% at peak, for AGVs at end-loaded stacking cranes ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)) | **Lower.** The figures are not quite the same thing: our AGV drives along the block to its bay, and the time it queues in the lane behind another AGV counts as driving held up, not as waiting at the crane. |
| AGV energy per hour of work | 14.5 ± 0.3 kWh | 13.2 ([S9](https://konecranes.com/discover/planning-for-sustainable-future)), 17 ([S8](https://www.porttechnology.org/wp-content/uploads/2019/05/TBA_-_Yvo-PTI70-12.pdf)) | In range, **by construction**: the energy per metre was set to land here (data sheet). It shows the setting worked, nothing more. |
| Charging | 23 stays on 12 stations in a shift, 30 AGVs | 25 AGVs share 6 chargers at HHLA ([S10](https://konecranes.com/discover/hamburger-hafen-und-logistik-ag-germany)) | Consistent: with a battery sized from the source the stations are never the bottleneck. |
| Truck turn time, gate to gate | 13.4 ± 0.5 min at 10 trucks an hour; 24.5 ± 2.3 at 18 | 30 min for a single move at Savannah's Garden City Terminal ([S14](https://gaports.com/wp-content/uploads/2026/09/00336_GPA_Fact-Sheets-26_Savannah_R2.pdf)) | **Faster, with light traffic.** A truck here never drives into the yard and there are no chassis to fetch. The one exchange crane is the limit: at 18 trucks an hour the wait before driving in is 17.8 ± 2.4 min and still rising. No figure for the truck volume at Dames Point was found. |
| Booked trucks that did not come | 14% (9.0 of about 64 booked) | 14% ([S12](https://www.pierpass.org/news/pierpass-posts-2-0-update-results/)) | The input, recovered: it shows the workload generator does what the data sheet says. |
| Yard occupancy, of every position in the blocks used | 55% at the start, 62% at the end | about 50% on average in the US; 80% of capacity is the practical limit ([S13](https://www.maritime.dot.gov/sites/marad.dot.gov/files/docs/resources/3661/070810tiogachcpproductivityreport.pdf)) | Inside the range the source calls workable. (The stacking module keeps four positions free in each bay for rehandles, so the data sheet's 65 percent is of the places it will fill.) |
| Deepest ship: hours of each tide it may start up the river | 3.1 h of 12.4 (water enough for 4.6 h, less the 85 min passage) | about 4.0 h: the pilots' window for 36 to 40 ft tankers and bulkers ([S6](https://www.moranshipping.com/news/bulletins/port-update-jacksonville-fl-2026-04-02)) | **Close, and a little stricter.** A 12.19 m ship needs the tide 0.76 m above the chart datum for the whole passage. The pilots' rule is for other ship types and is tied to Mayport's tide, so this is a check of size, not of detail. |
| Moves a vessel call | 190 (293 TEU at the 1.54 TEU a container of [S7](https://www.bts.gov/archive/port_performance/smith/slide14_text)) | 862 TEU a call: 1,388,841 TEU on 1,611 calls of every kind, fiscal 2025 ([S3](https://www.jaxport.com/media/statistics/)) | **Small calls.** They are sized to what this terminal turns round in a shift, and the port's figure divides by car carriers and bulk ships too. |

## Why the quay runs at half a real crane's rate

The map is JAXPORT's Dames Point with the equipment its page lists: six quay cranes and six yard cranes ([S1](https://jaxport.com/cargo/facilities/dames-point-marine-terminal)). Three things on it hold the quay back, and the fleet experiment separates them from the fleet size (13.4 ± 0.3 moves per crane-hour with 18 AGVs, 15.7 ± 0.3 with 30, 16.0 ± 0.3 with 36):

1. **One-way loops.** The road beside the quay runs east only, and one road leads from the quay into the west yard. A round trip between a quay crane and a block of its berth is 74 to 244 cells (1.1 to 3.7 km). From the east berth's first two cranes the way back from the blocks east of the main road is 1.8 to 5.1 times the way out, because the only road north on that side comes out beyond them.
2. **One yard crane to a quay crane.** Savannah's Garden City Terminal has 238 yard cranes for 34 quay cranes, seven to one ([S14](https://gaports.com/wp-content/uploads/2026/09/00336_GPA_Fact-Sheets-26_Savannah_R2.pdf)). Here each yard crane serves a lane in which AGVs cannot pass each other, and spends 27 percent of its working time travelling between bays.
3. **Queues that reach the main road.** A block beside the main road has room for one or two waiting AGVs before the queue blocks every lane behind it. The shift uses the far end of each lane for that reason, which lengthens the drive.

None of this is a fault in a module. It is what this layout and this equipment give at this load, and it is the kind of answer the model is for.

## What broke on the way to real scale, and what was done

The demo morning has 12 AGVs and 58 containers. A shift has 30 AGVs and about 2,200. Four things failed at that size and were changed, each only in the calibrated set, so the demo morning runs exactly as before:

* **PortSim's road rule.** Reserving a stretch up to the next junction lets one moving vehicle at a time onto a 285 m stretch. With 30 AGVs the fleet stood still. A vehicle now claims the road cell by cell, as far ahead as it needs to stop, and keeps its speed through a junction (`sim/agv.py`).
* **Parking.** Every AGV had a charging station of its own, and there are 12. A larger fleet shares them and stands by on the quay apron before the cranes.
* **The truck exchange.** Imports fetched ahead of their trucks filled its eight places, and trucks that never came left them there. The scheduler now fetches at most four ahead and sends a container back to the yard when its truck is 30 minutes overdue or is turned away.
* **The yard.** One rule for the best bay sends every container to the same bay, and a crane that serves five blocks loses its time changing blocks. The scheduler now names the yard crane with the least work waiting, and a crane takes the nearest job first.

## Limits

* The published figures are from other terminals and from suppliers' and consultants' papers. None is a measurement at Dames Point.
* The spread of a crane move, the failure rates, the truck volume and its punctuality, and the size and number of the ships are assumptions.
* The tide is a sine curve with NOAA's mean range, not the predictions for a date; wind, fog and the pilots' daylight rules are not modelled.
* A curve is driven as a stop, not at 3 m/s; containers are all one size; reefers, chassis, rail and empties are not modelled.

