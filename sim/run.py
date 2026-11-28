"""Run the integrated terminal: the four modules together on the Dames Point layout.

    python sim/run.py                       the demo morning, with the default policies
    python sim/run.py --stacking shortest_stack --gate fifo
    python sim/run.py --seed 7 --agvs 6     a generated workload instead of the demo
    python sim/run.py --numbers calibrated --vary 1     the demo on the published numbers (docs/data-sheet.md), times varying from seed 1
    python sim/run.py --shift 1 --quiet     a shift at real scale: four ships, 80 trucks, 30 AGVs (about ten seconds)
    python sim/run.py --quiet               only the summary and the checks
    python sim/run.py --follow IMP-03       everything the log says about one container, truck, ship or AGV
    python sim/run.py --why BRAVO           why: every decision about one ship, truck, AGV, crane or container, with its reason

The modules are sim/ship_arrival.py, sim/stacking.py, sim/agv.py and sim/gate.py; sim/terminal.py is the
scheduler that joins them. Each was written from what its owner said in the check-ins and is the owner's to replace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terminal_layout import load_layout  # noqa: E402

from sim import params as numbers  # noqa: E402
from sim import scenario as scenarios  # noqa: E402
from sim.metrics import check, summary  # noqa: E402
from sim.terminal import Terminal  # noqa: E402


def stamp(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def run(scenario, layout=None) -> Terminal:
    layout = layout or load_layout(ROOT / "layouts" / "dames-point.json")
    return Terminal(layout, scenario).run()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for part, names in scenarios.POLICIES.items():
        p.add_argument(f"--{part}", choices=names, help=f"{part} policy (default {scenarios.DEFAULT_POLICIES[part]})")
    p.add_argument("--agvs", type=int, help="fleet size; more than the 12 charging stations share them")
    p.add_argument("--seed", type=int, help="run a generated workload with this seed instead of the demo")
    p.add_argument("--shift", type=int, metavar="SEED", help="run a shift at real scale with this seed (calibrated numbers)")
    p.add_argument("--numbers", choices=sorted(numbers.SETS), help="the set of numbers (default reference; a shift is always calibrated)")
    p.add_argument("--vary", type=int, metavar="SEED", help="with calibrated numbers: crane and gate times vary, from this seed")
    p.add_argument("--quiet", action="store_true", help="do not print the event log")
    p.add_argument("--all", action="store_true", help="print every log line, also the routine ones")
    p.add_argument("--follow", help="print only what concerns this container, truck, ship or vehicle")
    p.add_argument("--why", metavar="NAME", help="print the reasons the modules gave for their decisions about this ship, truck, vehicle, crane or container (all: every reason)")
    a = p.parse_args(argv)

    changes = {part: getattr(a, part) for part in scenarios.POLICIES if getattr(a, part)}
    if a.shift is not None:
        sc = scenarios.shift(a.shift, **({"agvs": a.agvs} if a.agvs else {}))
    else:
        sc = scenarios.demo() if a.seed is None else scenarios.generate(a.seed)
        if a.agvs:
            changes["agvs"] = a.agvs
        if a.numbers == "calibrated" or a.vary is not None:
            changes["params"] = numbers.CALIBRATED.seeded(a.vary)
    sc = sc.using(**changes)
    t = run(sc)

    print(f"{sc.name}: {', '.join(f'{k} = {v}' for k, v in sc.policies.items())}, {sc.agvs} AGVs, {sc.params.name} numbers"
          + (f" varying from seed {sc.params.seed}" if sc.params.seed is not None else "") + "\n")
    if a.why:
        reasons = t.log.reasons if a.why == "all" else t.log.why(a.why)
        for r in reasons:
            print(f"{stamp(r.t)}  [{r.module}] {r.text}")
        print(f"{len(reasons)} decisions" if a.why == "all" else f"nothing was decided about {a.why} in this run" if not reasons else "", end="\n\n" if a.why == "all" or not reasons else "\n")
    if a.follow:
        for e in t.log.about(a.follow):
            print(f"{stamp(e.t)}  [{e.module}] {e.text}")
        print()
    elif not a.quiet and not a.why:
        for e in t.log.entries:
            if a.all or not e.data.get("quiet"):
                print(f"{stamp(e.t)}  {e.text}")
        print()
    for key, value in summary(t).items():
        print(f"{key:24s} {value}")
    problems = check(t)
    print(f"\nchecks: {'all hold' if not problems else str(len(problems)) + ' problems'}")
    for line in problems[:20]:
        print("  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:                 # the output was piped to head or less and cut short
        sys.stderr.close()
        sys.exit(0)
