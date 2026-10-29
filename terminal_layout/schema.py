"""Pydantic models for the authored layout file (rule 10: enums and shapes)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .model import LayoutError

Direction = Literal["N", "E", "S", "W"]
Cargo = Literal["general", "reefer", "hazmat"]
Flow = Literal["in", "out"]
Orientation = Literal["EW", "NS"]
BlockedReason = Literal["building", "exclusion", "unused"]
LaneKind = Literal["lane", "quay"]
MAX_GRID = 500  # cells per side; a typo like 120000 should fail fast, not allocate gigabytes

Int = Annotated[int, Field(strict=True)]
PosInt = Annotated[int, Field(strict=True, ge=1)]
NonNegInt = Annotated[int, Field(strict=True, ge=0)]
GridSize = Annotated[int, Field(strict=True, ge=1, le=MAX_GRID)]
Metres = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Ident = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$")]
PointT = tuple[Int, Int]
RectT = tuple[PointT, PointT]


class _Entry(BaseModel):
    # Unknown keys are errors, so a typo like "exist" never passes silently.
    model_config = ConfigDict(extra="forbid", frozen=True)
    notes: str | None = None  # free text for whoever edits the file; never used


def _unique_exits(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("exits must not repeat a direction")
    return value


class WaterEntry(_Entry):
    rect: RectT


class BlockedEntry(_Entry):
    rect: RectT
    reason: BlockedReason = "building"


class CraneEntry(_Entry):
    id: Ident
    x: Int
    # Quay cells under the crane where a vehicle stands for the handover, side by side along
    # the quay and centred on x (4 lanes: x - 1 to x + 2). One lane per vehicle.
    transfer_lanes: PosInt = 1
    # Containers that may be set on the ground at each transfer point. 0 is a coupled
    # handover: the crane lifts straight between the ship and a waiting vehicle.
    buffer_capacity: NonNegInt = 0


class BerthEntry(_Entry):
    id: Ident
    rect: RectT
    quay_y: Int
    cranes: list[CraneEntry] = []


class LaneEntry(_Entry):
    rect: RectT
    exits: list[Direction]
    kind: LaneKind = "lane"
    berth: Ident | None = None
    speed_kph: Metres | None = None

    _check_exits = field_validator("exits")(_unique_exits)

    @model_validator(mode="after")
    def _kind_matches_berth(self) -> LaneEntry:
        if self.kind == "quay" and self.berth is None:
            raise ValueError("a lane of kind 'quay' needs a berth id")
        if self.kind == "lane" and self.berth is not None:
            raise ValueError("berth is only allowed on lanes of kind 'quay'")
        return self


class BlockEntry(_Entry):
    id: Ident
    origin: PointT
    bays: PosInt
    rows: PosInt
    orientation: Orientation
    max_tier: PosInt
    cargo: Cargo = "general"
    service_side: Direction
    crane: Ident | None = None
    # Container rows side by side in one cell row of the block (a 15 m cell is about six container
    # widths of floor, so 3 leaves room for the RTG's legs and clearances). Capacity only; slot ids
    # still name the cell. Schema 1.2, default 1.
    container_rows: PosInt = 1
    # Containers that may be set on the ground at each of the block's transfer points. 0 is a
    # coupled handover: the yard crane lifts straight between the stack and a waiting vehicle.
    buffer_capacity: NonNegInt = 0

    @model_validator(mode="after")
    def _side_matches_orientation(self) -> BlockEntry:
        # Bays run along the long axis; the service lane runs parallel to it.
        allowed = ("N", "S") if self.orientation == "EW" else ("E", "W")
        if self.service_side not in allowed:
            raise ValueError(
                f"orientation {self.orientation} needs service_side "
                f"{' or '.join(allowed)}, got {self.service_side}"
            )
        return self


class GateEntry(_Entry):
    id: Ident
    cell: PointT
    flow: Flow
    exits: list[Direction] = []
    lane_no: PosInt | None = None

    _check_exits = field_validator("exits")(_unique_exits)


class ChargerEntry(_Entry):
    """A charging station: the loader turns the lane cell at ``cell`` into one."""

    id: Ident
    cell: PointT


class LayoutFile(_Entry):
    # 1.1 added chargers and buffer_capacity; 1.2 adds transfer_lanes and lets buffer_capacity
    # be 0 (its new default). Older files still load.
    schema_version: Literal["1.0", "1.1", "1.2"]
    name: Annotated[str, Field(min_length=1)]
    cell_size_m: Metres
    width: GridSize
    height: GridSize
    water: list[WaterEntry] = []
    blocked: list[BlockedEntry] = []
    berths: list[BerthEntry] = []
    lanes: list[LaneEntry] = []
    blocks: list[BlockEntry] = []
    gates: list[GateEntry] = []
    chargers: list[ChargerEntry] = []
    closures: list[PointT] = []


def _where(location: tuple[Any, ...]) -> str:
    text = ""
    for part in location:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else str(part)
    return text or "(file)"


def parse_authored(data: Any) -> LayoutFile:
    """Validate the shape of an authored file; raise LayoutError (rule 10) if wrong."""
    if not isinstance(data, dict):
        raise LayoutError(["rule 10: (file): the top level must be a JSON object { ... }"])
    try:
        return LayoutFile.model_validate(data)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            message = error["msg"].removeprefix("Value error, ")
            lines.append(f"rule 10: {_where(error['loc'])}: {message}")
        raise LayoutError(lines) from None
