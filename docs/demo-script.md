# Demo script: the symposium table

For the School of Computing Symposium, Tuesday December 1, 2 to 5 pm. Everything here runs with the Wi-Fi off.

There are two ways to show the terminal. Use the first; keep the second ready.

| | Start it | What it is |
| --- | --- | --- |
| **Live** | `python tools/control_room.py --tour` | The 3D model with the simulator behind it. Every change you make is simulated again by the Python modules. Needs the terminal window to stay open. |
| **One file** | open `viewer3d/dames-point-3d.html` and add `#tour` to the address | The same model playing recorded runs: the plan, 13 what-ifs and a shift at real scale. Needs nothing but a browser. |

## The evening before (15 minutes)

1. Plug the laptop in. In the project folder, with its environment active, run

   ```
   python tools/preflight.py --full --tests
   ```

   It must end with `Ready`. A line that says `FAIL` says what to run to fix it.
2. Start the live version, `python tools/control_room.py --tour`, and watch one loop of the tour. It takes a little over four minutes. The picture should move smoothly, by day and after dark (stop 18). If it stutters, see the last table of this page.
3. Turn the Wi-Fi off and reload the page. Nothing may change: the model, its typefaces and the tour all come from this computer.
4. Press **F** for the full screen. On stops 13, 14 and 15 the dashboard must show under the picture, with the caption beside it. It needs a window of at least 1180 by 660; in a smaller one the tour leaves stop 13 out and plays the other two without the dashboard.
5. In System Settings, set the display to stay on while the laptop is plugged in, and turn on Do Not Disturb for the afternoon. The tour asks the browser to keep the screen awake, but a browser may refuse.
6. Copy `viewer3d/dames-point-3d.html` to a second place: a USB stick, or a teammate's laptop. It is the whole demo in one file and opens by double-click.

## At the table (5 minutes)

1. Power cable in, sound off, every other window closed.
2. `python tools/preflight.py`. Ten seconds, and it must end with `Ready`.
3. `python tools/control_room.py --tour`. The browser opens with the tour running.
4. Press **F**. Leave the terminal window open behind the browser: closing it stops the simulator.

For the first 20 seconds or so the dots of stops 14 and 15 may be grey: the control room is simulating those two runs in the background. They are ready long before the tour reaches them.

## What plays by itself

The tour has 18 stops and one loop takes about 4 minutes 15 seconds. Then it starts again. Each stop names the module it is about in the corner of its caption.

| # | Module | Stop | About | Point at |
| ---: | --- | --- | ---: | --- |
| 1 | Layout | Dames Point terminal | 11 s | The ship coming alongside; six quay cranes on two berths; the yard blocks behind them. |
| 2 | Scheduler | Ship to shore | 11 s | The AGV already waiting in the crane's lane, so the container goes straight onto it. |
| 3 | Scheduler | A full buffer stops the crane | 14 s | Two containers on the ground beside the lane, and the crane standing still above them. |
| 4 | AGVs | Why this AGV? | 19 s | The orange frame marks the AGV. The caption quotes the dispatcher's own reason, with the runner-up's distance. |
| 5 | AGVs | AGV to the yard | 13 s | It keeps to the one-way roads the whole way. |
| 6 | Stacking | Into the stack | 10 s | The yard crane lifting the container straight off the AGV. |
| 7 | Stacking | A buried container | 13 s | The container that is moved aside first. That move is one rehandle. |
| 8 | Gate | Truck exchange | 11 s | The exchange crane loading the truck, which then leaves through the gate. |
| 9 | Gate | The gate | 17 s | The three trucks turned away, each for its own reason. |
| 10 | Scheduler | Exports go aboard | 14 s | The same crane now loading: a ship sails only when its exports are on. |
| 11 | Ship arrival | Waiting for the tide | 14 s | BRAVO coming in after CHARLIE, which arrived later but did not need the tide. |
| 12 | AGVs | Charging | 14 s | The AGV pulling in at its charger, and the charge it arrives with. |
| 13 | Dashboard | The run in numbers | 16 s | The clock line moving across the charts, and the tiles above them. |
| 14 | Control room | What if? | 18 s | The blue line falling behind the grey one: this run against the plan. |
| 15 | Real scale | A whole shift | 19 s | Thirty AGVs at once, and the crane meters on the right. |
| 16 | Layout | Where vehicles wait | 12 s | The columns along the quay: two thirds of all waiting is there. |
| 17 | Layout | Plan any route | 17 s | The drawn route: 118 moves on one-way roads against 10 on two-way roads. |
| 18 | Layout | Day and night | 12 s | The lights coming on. |

A caption stays up long enough to be read aloud, so the times change if a caption's words do.

## Keys

| Key | Does |
| --- | --- |
| **→**, Page Down, N | The next stop, and the tour holds there. A presenter's clicker sends Page Down and Page Up. |
| **←**, Page Up, P | The stop before, held. |
| **Space**, K | Hold this stop, or let the tour go on. |
| Home, End | The first stop, the last stop. |
| **F** | The full screen, and back. |
| **Esc** | Leave the tour. The model is then yours: drag, zoom, click. |

A held stop goes on by itself after three minutes without a key or a click, so a forgotten Hold does not freeze an unattended screen. The dots under the caption jump to any stop.

Started with `--tour`, or by any address that ends in `#tour`, the page is a screen left running. When a visitor has left the tour to try the model and nothing is touched for three minutes, the tour starts again from the first stop. Opened any other way it waits for **Start the guided tour**, at the top of the panel.

## Three ways to tell it

### 30 seconds, for someone walking past

> This is JAXPORT's Dames Point container terminal, simulated. Five modules, one from each of us, run it together: ships come in on the tide, cranes unload them, driverless vehicles carry the containers to the yard, and trucks collect them at the gate. Nothing here is animation. Every move was decided by the simulator, and if you click anything it tells you why.

Then hand over the mouse: **Esc**, and click an AGV.

### 3 minutes, with the arrow keys

Hold on these stops and say one thing at each.

| Stop | Say |
| --- | --- |
| 1, Dames Point terminal | What it is: the real layout, six quay cranes, 27 yard blocks, a truck gate, on a grid of 15 m cells. |
| 4, Why this AGV? | Every decision carries its reason. This AGV was the nearest of the free ones with enough charge, and the caption names the runner-up. |
| 7, A buried container | A policy question. Stacking by departure time needs 4 rehandles this morning. At real scale it needs 21 a shift, against 400 for stacking on the shortest stack. |
| 11, Waiting for the tide | The ship module: BRAVO is too deep to come in at low water, so CHARLIE, which arrived later, goes first. |
| 14, What if? | The control room: half the AGVs, and the morning takes 224 minutes instead of 115. |
| 15, A whole shift | The same modules at real scale on published numbers: 4 ships, 716 quay moves, 30 AGVs. Every setting in the experiment was run 30 times. |

End on the finding: past about 30 AGVs, more of them barely help. From 18 to 30 AGVs the quay goes from 13.4 to 15.7 moves per crane-hour, and six more AGVs take it only to 16.0. The limit is the layout and the yard cranes, not the fleet.

### 8 minutes, live, for someone who stays

1. **Esc** to leave the tour.
2. Under **Control room** in the panel, drag **AGVs** from 12 to 6. The status line says how long the simulation took, the model carries on from the same moment, and the dashboard's first tile says how far the run is behind the plan.
3. **Back to the plan.**
4. Click an AGV in the model. Its card, under **Details**, says why it was sent. Press **Break it down now (10 min)** and watch the AGVs behind it queue or go round.
5. Click a cell of the main road and press **Close this road now (15 min)**.
6. In the dashboard choose **Decisions**: every decision of the run with its reason, and a click on a name marks it in the model. **Table** shows the same numbers as the charts.
7. Under **Control room** set **AGV jobs** to **Learned (Q-learning)**. The morning is simulated again with a rule that was learned in the simulator giving out the jobs. Click an AGV: its card says what the rule expected and which pair it passed over. This small morning ends 6 minutes later under it than as planned, because it learned on shifts with 30 AGVs: say so, and give the numbers of the 30 shifts from the questions below. Then **Back to the plan**.
8. Under **What if** choose **A shift at real scale**. The first time it takes about 20 seconds to simulate. It plays 600 times faster.
9. If they ask how a module decides, the terminal answers too: `python sim/run.py --why BRAVO`.

In the one-file version steps 2 to 5 and 7 are not there: **What if** offers the recorded runs instead, the morning under the learned rule among them.

## Each module's stops

So that each of us can take the stops of our own module.

| Module | Stops | The decision it makes |
| --- | --- | --- |
| Layout | 1, 16, 17, 18 | Where everything is and which way every road runs. It answers "what is next to this cell" and "the shortest legal path". |
| Ship arrival | 11 | Which ship comes in next: the tide, the berth, radio contact. |
| AGVs | 4, 5, 12 | Which AGV takes a job, the road it claims, when it charges. |
| Stacking | 6, 7 | Which stack a container goes on, and what must move to reach one below. |
| Gate | 8, 9 | Which trucks are let in, in what order, and who is turned away. |
| Scheduler | 2, 3, 10 | The join between the modules: cranes, buffers, the order of jobs. |
| All of them | 13, 14, 15 | The dashboard, the control room and the shift at real scale. |

## Questions, with the numbers

**Is this real data?** The layout is JAXPORT's Dames Point, with the equipment its own page lists: six quay cranes and six yard cranes. The shift runs on published numbers wherever one was found, each with its source in `docs/data-sheet.md`: a quay crane makes 35 moves an hour at best, an AGV drives 6 m/s, 14 percent of booked trucks do not come. None of it is a measurement taken at Dames Point, and the data sheet marks every number that is an assumption. What stands round the terminal in the picture is scenery, drawn by eye: the bridge downriver, the woods, the far bank, the shapes of the ships and the weather.

**How do you know it is right?** Three ways. Every run checks itself: no two vehicles in one cell, no crane with two lifts at once, every container accounted for. Those checks are the items of Dr. Wang's evaluation list (`docs/integration.md`, "What is checked"). The test suite holds the code to them. And `docs/validation.md` puts the shift beside published figures and says where it is off: the quay reaches 15.7 moves per crane-hour against the 28 its source calls sustainable, and the document gives the three reasons. They are in the layout and the equipment, not in a module.

**What did the experiment find?** Each setting was run 30 times at real scale, and the tables in `docs/experiments.md` give 95 percent confidence intervals.

| Question | Answer |
| --- | --- |
| Stack by departure time, or on the shortest stack? | 21 rehandles a shift against 400. |
| Appointments at the gate, or first in, first out? | A truck is through in 13.4 minutes against 16.2. |
| How many AGVs? | 18, 24, 30 and 36 give 13.4, 15.0, 15.7 and 16.0 quay moves per crane-hour. |
| How many trucks can the gate take? | At 6, 10, 14 and 18 an hour a truck takes 11.1, 13.4, 17.1 and 24.5 minutes. |
| A learned dispatch rule, or the planned one? | Under the learned rule a ship is in port 5.1 minutes less and the AGVs drive 50 km less a shift. A rule one line long, written by hand, does as well. |

**Why are the roads one-way?** It is a choice made in the layout: the roads are reduced to one-way loops, so the arrows on the map are the whole traffic plan. The model shows what that costs. Stop 17 is the worst case: 118 moves from a charger to a quay crane that would be 10 on two-way roads. Stop 16 shows where vehicles wait: 66 percent of all waiting is on the quay.

**What happens when something breaks?** Show it: step 4 or 5 of the eight minutes. The run is simulated again from that moment and the cards say who is held up by whom.

**How fast is it?** The morning simulates in under a second. A 14-hour shift with 30 AGVs and 2,163 containers takes about ten seconds.

**Where is the machine learning?** *The team has to agree this answer before the day. The learned rule was the AGV owner's idea, so it is the owner's to give.* One policy is learned. The AGV jobs can be given out by a rule trained by Q-learning inside the simulator: it scores every pair of a waiting job and a free AGV, and its 13 weights were fitted from 120 simulated shifts, in which 30,745 choices were made by lot so that the effect of a choice could be told from the situation it was made in. On 30 shifts it never saw, against the rule the terminal runs as planned, a ship is in port 5.1 minutes less, a job waits 3 percent less for its AGV and the AGVs drive 50 km less a shift, each more than chance. Then say the other half: a rule one line long, written by hand with the same freedom to choose the job, does as well and drives another 47 km less. Dispatch has little room on this map, and a learned rule has to be measured against a simple one, not only against the rule it replaces. Every other policy is a rule written by hand (stacking by departure time, the earliest ship that may come in, appointments at the gate), and the experiment ranks those. `docs/learned-dispatch.md` has the method, the learning curve and the limits. Do not claim more than that.

## If something goes wrong

| What you see | Do |
| --- | --- |
| The tour is stuck on one stop | It is on hold: the button says **Go on**. Press **Space**. It also goes on by itself after three minutes. |
| No dashboard on stops 13 to 15 | The window is too small. Press **F**. |
| The dots of stops 14 and 15 are grey | Live only: those runs are still being simulated. Wait 20 seconds. If they stay grey, the terminal window was closed: see two rows down. |
| A change in the control room shows an error | Read the status line: it says what was wrong with the setting. **Back to the plan** clears it. |
| Choosing **Learned (Q-learning)** shows an error about weights | The file `sim/dispatch_weights.json` is gone. Choose another rule; `python tools/preflight.py` says what to run to make it again. |
| The status line says the simulator did not answer | The terminal window was closed. Run `python tools/control_room.py --tour` again and use the page it opens. |
| The address it prints is not port 8642 | Another control room is still running. Either is fine; use the address printed last. |
| The 3D view is blank | Reload. If it stays blank, open the one-file page in another browser. |
| The picture stutters, or the fan is loud | In the panel, under **View**, set **Picture quality** to Balanced or Light. Left on Automatic the page does this by itself after a few seconds of slow frames. |
| The laptop slept | Wake it. The tour carries on where it was. |
| The tour started again while you were talking | Three minutes passed without a key or a click. **Esc** gives the model back: the same run at the same moment, with the same thing selected. The view is the whole terminal again. |
| The laptop is gone | Open the copy of `viewer3d/dames-point-3d.html` on the second machine and add `#tour` to the address. |
