"""Walk through the terminal_layout API on the demo layout.

Run from the repo root:  python examples/demo.py
Writes layout.png and layout-closed.png next to where you run it.
"""

from pathlib import Path

from terminal_layout import LayoutError, load_layout

LAYOUT = Path(__file__).resolve().parent.parent / "layouts" / "demo-12x8.json"


def main() -> None:
    layout = load_layout(LAYOUT)
    print(layout)

    print("\nBlock A has", len(layout.blocks["A"].slots), "ground slots")
    slot = "A-03-02"
    point = layout.transfer_point_for(slot)
    print(f"Slot {slot} is served from the transfer point at {point.pos}")

    gate = layout.gate_cells("in")[0]
    exit_gate = layout.gate_cells("out")[0]
    path = layout.shortest_path(gate.pos, exit_gate.pos)
    print(f"\nA truck from {gate.attrs['id']} to {exit_gate.attrs['id']} takes {len(path) - 1} moves")

    print("\nQuay cranes and their transfer points:")
    for berth_id, cranes in layout.berths.items():
        for crane_id, pos in cranes:
            print(f"  {berth_id} {crane_id} at {pos}")

    layout.render_2d("layout.png")

    # A scenario: close one service-lane cell. The original layout is unchanged.
    closed = layout.with_closures({(5, 1)})
    print("\nWith (5, 1) closed:")
    print("  path from gate to gate:", closed.shortest_path(gate.pos, exit_gate.pos))
    for warning in closed.warnings:
        print("  warning:", warning)
    closed.render_2d("layout-closed.png")

    # Bad input is reported in full, with rule numbers.
    try:
        layout.with_closures({(4, 3)})
    except LayoutError as exc:
        print("\nClosing a yard slot is refused:")
        print(exc)


if __name__ == "__main__":
    main()
