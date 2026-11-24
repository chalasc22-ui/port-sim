# The experiment: every table

Written by `python tools/data_sheet.py` from `docs/experiments-summary.json`, which `python tools/experiments.py` writes. Every run's numbers are in `docs/experiments.csv`.

Each setting was run 30 times. A cell is the mean of the runs, plus or minus half the width of its 95 percent confidence interval (Student's t): a range built this way holds the long-run mean 19 times in 20. Under every table the settings are compared run by run, each run with the run that had the same seed, so the same ships, trucks and containers and, in the shift, the same crane time for each container. A difference marked **\*** has an interval that does not include zero: it is more than chance.

The numbers rank the settings on this map. They are not a forecast for a real terminal.

## A shift at real scale (calibrated numbers)

`sim.scenario.shift(seed)`: four ships with 140 to 240 moves each, 80 trucks, 30 AGVs, the yard 65 percent full, on the calibrated numbers of `docs/data-sheet.md`. What varies from run to run is the workload and every random time.

### Stacking

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Rehandles | Quay moves per crane-hour | Ship: time in port (min) | Truck: gate to gate (min) |
| --- | ---: | ---: | ---: | ---: |
| `stacking = shortest_stack` | 400.2 ± 12.1 | 13.0 ± 0.2 | 519.7 ± 25.2 | 14.7 ± 0.8 |
| `stacking = departure_time` | 21.4 ± 3.6 | 15.7 ± 0.3 | 442.3 ± 26.8 | 13.4 ± 0.5 |
| *`stacking = departure_time` minus `stacking = shortest_stack`* | -378.8 ± 10.7 **\*** | +2.7 ± 0.2 **\*** | -77.4 ± 10.6 **\*** | -1.3 ± 0.5 **\*** |

### Ship arrival

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Ship: at anchor (min) | Ship: longest at anchor (min) | Ship: time in port (min) |
| --- | ---: | ---: | ---: |
| `ship = fcfs` | 148.8 ± 62.0 | 271.9 ± 70.9 | 484.1 ± 60.1 |
| `ship = earliest_eligible` | 107.0 ± 29.8 | 299.8 ± 91.6 | 442.3 ± 26.8 |
| *`ship = earliest_eligible` minus `ship = fcfs`* | -41.8 ± 39.5 **\*** | +27.9 ± 48.3 | -41.8 ± 40.9 **\*** |

### Gate

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Truck: gate to gate (min) | Truck: before driving in (min) | Truck: inside (min) | Imports sent back to the yard |
| --- | ---: | ---: | ---: | ---: |
| `gate = fifo` | 16.2 ± 1.2 | 10.4 ± 1.1 | 5.8 ± 0.2 | 0.0 ± 0.0 |
| `gate = appointment` | 13.4 ± 0.5 | 7.8 ± 0.5 | 5.6 ± 0.1 | 5.1 ± 0.8 |
| *`gate = appointment` minus `gate = fifo`* | -2.8 ± 1.2 **\*** | -2.6 ± 1.1 **\*** | -0.2 ± 0.2 **\*** | +5.1 ± 0.8 **\*** |

### AGV dispatch

3 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | A job waits for its AGV (min) | Quay moves per crane-hour | Ship: time in port (min) | AGVs drive (km) |
| --- | ---: | ---: | ---: | ---: |
| `dispatch = nearest_feasible` | 5.7 ± 0.2 | 15.7 ± 0.3 | 442.3 ± 26.8 | 2033.1 ± 34.4 |
| `dispatch = longest_idle` | 6.3 ± 0.2 | 15.3 ± 0.3 | 449.9 ± 26.9 | 2224.2 ± 34.4 |
| `dispatch = balanced` | 5.8 ± 0.2 | 15.6 ± 0.3 | 446.3 ± 27.0 | 2058.4 ± 34.8 |
| *`dispatch = longest_idle` minus `dispatch = nearest_feasible`* | +0.6 ± 0.1 **\*** | -0.4 ± 0.1 **\*** | +7.6 ± 2.8 **\*** | +191.1 ± 13.0 **\*** |
| *`dispatch = balanced` minus `dispatch = nearest_feasible`* | +0.1 ± 0.1 | -0.2 ± 0.2 **\*** | +4.0 ± 4.1 | +25.3 ± 8.5 **\*** |

### Fleet size

4 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Quay moves per crane-hour | Ship: time in port (min) | Truck: gate to gate (min) | Quay cranes wait (min) | AGVs held up (min) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `agvs = 18` | 13.4 ± 0.3 | 511.9 ± 22.8 | 13.0 ± 0.6 | 656.9 ± 66.9 | 1062.7 ± 53.0 |
| `agvs = 24` | 15.0 ± 0.3 | 461.7 ± 25.7 | 13.0 ± 0.6 | 390.9 ± 42.0 | 1706.3 ± 128.1 |
| `agvs = 30` | 15.7 ± 0.3 | 442.3 ± 26.8 | 13.4 ± 0.5 | 282.7 ± 33.1 | 2488.7 ± 200.4 |
| `agvs = 36` | 16.0 ± 0.3 | 434.5 ± 27.9 | 13.6 ± 0.7 | 214.6 ± 29.2 | 3325.2 ± 256.3 |
| *`agvs = 24` minus `agvs = 18`* | +1.6 ± 0.1 **\*** | -50.2 ± 6.3 **\*** | +0.0 ± 0.2 | -266.0 ± 29.9 **\*** | +643.6 ± 93.8 **\*** |
| *`agvs = 30` minus `agvs = 18`* | +2.3 ± 0.1 **\*** | -69.6 ± 8.4 **\*** | +0.4 ± 0.3 **\*** | -374.2 ± 40.3 **\*** | +1426.0 ± 166.9 **\*** |
| *`agvs = 36` minus `agvs = 18`* | +2.6 ± 0.2 **\*** | -77.5 ± 9.6 **\*** | +0.6 ± 0.3 **\*** | -442.3 ± 42.4 **\*** | +2262.5 ± 222.4 **\*** |

### Trucks an hour

4 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Truck: gate to gate (min) | Truck: before driving in (min) | Truck: inside (min) | Trucks served | Ship: time in port (min) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `trucks_per_hour = 6` | 11.1 ± 0.6 | 5.9 ± 0.6 | 5.2 ± 0.1 | 42.2 ± 0.7 | 437.2 ± 28.4 |
| `trucks_per_hour = 10` | 13.4 ± 0.5 | 7.8 ± 0.5 | 5.6 ± 0.1 | 69.1 ± 1.2 | 442.3 ± 26.8 |
| `trucks_per_hour = 14` | 17.1 ± 1.1 | 11.0 ± 1.1 | 6.2 ± 0.1 | 96.9 ± 1.1 | 451.5 ± 26.5 |
| `trucks_per_hour = 18` | 24.5 ± 2.3 | 17.8 ± 2.4 | 6.7 ± 0.1 | 124.8 ± 1.7 | 451.1 ± 25.1 |
| *`trucks_per_hour = 10` minus `trucks_per_hour = 6`* | +2.3 ± 0.7 **\*** | +1.9 ± 0.7 **\*** | +0.4 ± 0.1 **\*** | +26.9 ± 1.4 **\*** | +5.1 ± 4.7 **\*** |
| *`trucks_per_hour = 14` minus `trucks_per_hour = 6`* | +6.1 ± 1.2 **\*** | +5.1 ± 1.2 **\*** | +1.0 ± 0.2 **\*** | +54.7 ± 1.4 **\*** | +14.3 ± 5.4 **\*** |
| *`trucks_per_hour = 18` minus `trucks_per_hour = 6`* | +13.5 ± 2.1 **\*** | +11.9 ± 2.2 **\*** | +1.6 ± 0.2 **\*** | +82.6 ± 2.0 **\*** | +13.8 ± 11.4 **\*** |

## Small workloads (reference numbers)

`sim.scenario.generate(seed)`: three ships with about ten containers each, 14 trucks, 10 AGVs, on PortSim's numbers, where every time is fixed. What varies from run to run is the workload.

### Stacking alone

2 settings, 90 runs each.

| Setting | Rehandles, 50% full | Rehandles, 75% full | Crane time at 75% (min) | Rehandles, 90% full |
| --- | ---: | ---: | ---: | ---: |
| `shortest_stack` | 75.3 ± 1.3 | 125.7 ± 1.8 | 307.1 ± 2.3 | 151.2 ± 2.4 |
| `departure_time` | 2.4 ± 0.5 | 29.7 ± 2.0 | 187.2 ± 2.5 | 64.0 ± 2.5 |

### Stacking

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Rehandles | Truck: gate to gate (min) | Finished after (min) |
| --- | ---: | ---: | ---: |
| `stacking = shortest_stack` | 3.4 ± 1.0 | 21.9 ± 2.1 | 146.7 ± 4.5 |
| `stacking = departure_time` | 1.8 ± 0.5 | 27.7 ± 3.0 | 149.0 ± 4.6 |
| *`stacking = departure_time` minus `stacking = shortest_stack`* | -1.6 ± 1.2 **\*** | +5.8 ± 2.0 **\*** | +2.3 ± 2.3 |

### Ship arrival

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Ship: at anchor (min) | Ship: longest at anchor (min) | Ship: time in port (min) |
| --- | ---: | ---: | ---: |
| `ship = fcfs` | 61.3 ± 3.5 | 95.6 ± 7.9 | 126.4 ± 6.1 |
| `ship = earliest_eligible` | 38.0 ± 3.1 | 86.4 ± 6.4 | 105.4 ± 5.5 |
| *`ship = earliest_eligible` minus `ship = fcfs`* | -23.3 ± 3.9 **\*** | -9.2 ± 6.3 **\*** | -21.0 ± 4.8 **\*** |

### Gate

2 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Truck: gate to gate (min) | Truck: before driving in (min) | Truck: inside (min) |
| --- | ---: | ---: | ---: |
| `gate = fifo` | 44.8 ± 3.4 | 40.7 ± 3.4 | 4.1 ± 0.1 |
| `gate = appointment` | 27.7 ± 3.0 | 23.5 ± 3.0 | 4.2 ± 0.1 |
| *`gate = appointment` minus `gate = fifo`* | -17.2 ± 2.9 **\*** | -17.2 ± 2.9 **\*** | +0.1 ± 0.1 |

### AGV dispatch

3 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | A job waits for its AGV (min) | Finished after (min) | AGVs drive (km) | AGVs use (kWh) |
| --- | ---: | ---: | ---: | ---: |
| `dispatch = nearest_feasible` | 17.4 ± 1.2 | 149.0 ± 4.6 | 170.6 ± 6.3 | 593.4 ± 21.4 |
| `dispatch = longest_idle` | 18.1 ± 1.2 | 150.4 ± 4.7 | 174.6 ± 6.1 | 605.1 ± 20.9 |
| `dispatch = balanced` | 17.6 ± 1.2 | 149.0 ± 4.6 | 171.6 ± 6.1 | 596.5 ± 20.7 |
| *`dispatch = longest_idle` minus `dispatch = nearest_feasible`* | +0.7 ± 0.4 **\*** | +1.5 ± 2.3 | +4.0 ± 2.5 **\*** | +11.7 ± 7.5 **\*** |
| *`dispatch = balanced` minus `dispatch = nearest_feasible`* | +0.2 ± 0.1 **\*** | +0.0 ± 1.0 | +1.0 ± 0.7 **\*** | +3.1 ± 2.2 **\*** |

### Fleet size

4 settings, 30 runs each. Runs that did not finish: 0. Failed checks: 0.

| Setting | Finished after (min) | Moves an hour | Ship: time in port (min) | Truck: gate to gate (min) | Quay cranes wait (min) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `agvs = 6` | 233.2 ± 8.0 | 13.6 ± 0.2 | 127.9 ± 9.1 | 84.4 ± 6.3 | 148.7 ± 20.8 |
| `agvs = 8` | 179.9 ± 5.8 | 17.6 ± 0.3 | 90.1 ± 6.7 | 48.3 ± 4.2 | 90.8 ± 16.6 |
| `agvs = 10` | 149.0 ± 4.6 | 21.2 ± 0.3 | 72.0 ± 5.3 | 27.7 ± 3.0 | 56.7 ± 14.1 |
| `agvs = 12` | 134.8 ± 4.4 | 23.5 ± 0.6 | 60.1 ± 4.3 | 18.3 ± 1.8 | 30.3 ± 10.4 |
| *`agvs = 8` minus `agvs = 6`* | -53.3 ± 4.0 **\*** | +4.0 ± 0.3 **\*** | -37.7 ± 5.0 **\*** | -36.2 ± 3.2 **\*** | -57.9 ± 10.0 **\*** |
| *`agvs = 10` minus `agvs = 6`* | -84.3 ± 4.2 **\*** | +7.6 ± 0.2 **\*** | -55.9 ± 6.0 **\*** | -56.8 ± 5.4 **\*** | -92.1 ± 14.9 **\*** |
| *`agvs = 12` minus `agvs = 6`* | -98.4 ± 6.1 **\*** | +9.9 ± 0.6 **\*** | -67.8 ± 6.8 **\*** | -66.1 ± 5.3 **\*** | -118.5 ± 15.7 **\*** |

