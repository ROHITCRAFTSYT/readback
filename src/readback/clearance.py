"""Structured form of one ATC radio transmission: who it is for and what it instructs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

KINDS = (
    "flight_level",  # value "100" for FL100
    "altitude",  # feet, value "5000"
    "heading",  # value "240"; qualifier "left" / "right" when a turn direction is given
    "speed",  # knots, value "180"
    "squawk",  # value "4721"
    "frequency",  # MHz, value "119.605"
    "qnh",  # hPa, value "1014"
    "runway",  # value "27", "06l"
    "cleared_to_land",  # value is the runway, or ""
    "cleared_for_takeoff",  # value is the runway, or ""
    "line_up",  # value is the runway, or ""
    "hold_short",  # value is the runway or holding point, or ""
    "direct_to",  # value is the waypoint name
)
# Items ICAO Doc 4444 requires the pilot to read back; a wrong value here is a readback error.
READBACK_REQUIRED = {
    "flight_level", "altitude", "heading", "speed", "squawk", "frequency", "qnh", "runway",
    "cleared_to_land", "cleared_for_takeoff", "line_up", "hold_short",
}  # fmt: skip


@dataclass(frozen=True)
class Item:
    kind: str
    value: str
    qualifier: str = ""  # climb / descend / maintain, left / right, or ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown item kind {self.kind!r}")


@dataclass(frozen=True)
class Clearance:
    callsign: str | None  # canonical tokens joined by spaces, e.g. "csa 1 d z"
    items: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Clearance:
        return cls(
            callsign=data.get("callsign") or None,
            items=[Item(**item) for item in data.get("items", [])],
        )


# Strict-mode JSON schema for Muse structured output (every property required, no extras).
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "callsign": {
            "type": ["string", "null"],
            "description": "Callsign in spoken canonical form, or null if none was said.",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "value": {"type": "string"},
                    "qualifier": {"type": "string"},
                },
                "required": ["kind", "value", "qualifier"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["callsign", "items"],
    "additionalProperties": False,
}
