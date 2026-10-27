"""The monitor: pairs radio transmissions into instruction/readback exchanges, raises readback
alerts, and watches each aircraft's ADS-B track against the clearance it was given."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from readback.adsb import ClearanceTrack, StateVector, spoken_callsign
from readback.clearance import READBACK_REQUIRED, Clearance, Item
from readback.compare import compare, same_callsign
from readback.normalize import canonical

TRACKED = {"flight_level", "altitude", "heading", "squawk"}
VERTICAL = {"flight_level", "altitude"}


def describe(kind: str, value: str) -> str:
    """Human-readable item, e.g. ("flight_level", "100") -> "FL100"."""
    if kind == "flight_level":
        return f"FL{int(value):03d}" if value.isdigit() else f"FL{value}"
    if kind == "callsign":  # "lufthansa 5 0 4" -> "LUFTHANSA 504", "o k f a o" -> "OKFAO"
        toks = value.split()
        if toks and len(toks[0]) > 1:
            return f"{toks[0]} {''.join(toks[1:])}".upper()
        return "".join(toks).upper()
    return {
        "altitude": f"{value} ft",
        "heading": f"heading {value}",
        "speed": f"{value} kt",
        "squawk": f"squawk {value}",
        "frequency": f"{value} MHz",
        "qnh": f"QNH {value}",
        "runway": f"runway {value.upper()}",
        "cleared_to_land": f"cleared to land {value.upper()}".strip(),
        "cleared_for_takeoff": f"cleared for takeoff {value.upper()}".strip(),
        "line_up": f"line up {value.upper()}".strip(),
        "hold_short": f"hold short {value.upper()}".strip(),
        "direct_to": f"direct {value.upper()}",
    }.get(kind, f"{kind} {value}")


@dataclass(frozen=True)
class Transmission:
    time: float
    text: str
    role: str | None = None  # "controller", "pilot", or None to infer
    id: str = ""


@dataclass(frozen=True)
class Alert:
    time: float
    callsign: str
    source: str  # "readback" or "track"
    kind: str  # item kind, "callsign", "no_readback", "level_bust", ...
    severity: str  # "error" or "warning"
    message: str
    expected: str = ""
    heard: str = ""
    ref: str = ""  # id of the transmission that raised it

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def infer_role(text: str, clearance: Clearance) -> str:
    """Controllers open with the callsign; pilots usually close with it."""
    if not clearance.callsign:
        return "pilot"
    toks = canonical(text)
    first = clearance.callsign.split()[0]
    return "controller" if toks and toks[0] == first else "pilot"


@dataclass
class _Pending:
    tx: Transmission
    clearance: Clearance
    taken: bool = False  # another aircraft already read this instruction back


@dataclass
class Monitor:
    parse: Callable[[str], Clearance]
    pairing_window: float = 20.0
    pending: list[_Pending] = field(default_factory=list)
    tracks: dict[str, dict[str, ClearanceTrack]] = field(default_factory=dict)
    _raised: set = field(default_factory=set)

    def on_transmission(self, tx: Transmission) -> tuple[Clearance, str, list[Alert]]:
        clearance = self.parse(tx.text)
        role = tx.role or infer_role(tx.text, clearance)
        alerts = self.expire(tx.time)
        if role == "controller":
            if any(it.kind in READBACK_REQUIRED for it in clearance.items):
                self.pending.append(_Pending(tx, clearance))
            if clearance.callsign:
                self._assign(clearance.callsign, clearance.items, tx.time)
        else:
            alerts += self._readback(tx, clearance)
        return clearance, role, alerts

    def expire(self, now: float) -> list[Alert]:
        """Instructions nobody read back within the pairing window."""
        alerts, keep = [], []
        for p in self.pending:
            if now - p.tx.time <= self.pairing_window:
                keep.append(p)
                continue
            cs = p.clearance.callsign or "unknown"
            alerts.append(
                Alert(
                    p.tx.time + self.pairing_window, cs, "readback", "no_readback", "warning",
                    f"{describe('callsign', cs)}: no readback of "
                    + ", ".join(describe(i.kind, i.value) for i in p.clearance.items),
                    ref=p.tx.id,
                )
            )  # fmt: skip
        self.pending = keep
        return alerts

    def _readback(self, tx: Transmission, clearance: Clearance) -> list[Alert]:
        match = None
        for p in reversed(self.pending):
            if tx.time - p.tx.time > self.pairing_window:
                continue
            if clearance.callsign and p.clearance.callsign:
                if same_callsign(clearance.callsign, p.clearance.callsign):
                    match = p
                    break
            elif match is None:
                match = p  # no callsign to go on: the most recent open instruction
        if match is None:
            # A readback whose callsign matches nothing open may be a reply to an
            # instruction meant for a similar callsign: the classic callsign confusion.
            if clearance.callsign and self.pending:
                latest = self.pending[-1]
                recent = tx.time - latest.tx.time <= self.pairing_window
                if latest.clearance.callsign and recent and not latest.taken:
                    # Leave the instruction open: its real addressee still hasn't answered,
                    # and will be reported as "no readback" if they never do.
                    latest.taken = True
                    return [self._callsign_alert(tx, latest.clearance.callsign, clearance.callsign)]
            return []
        self.pending.remove(match)

        alerts = []
        cs = match.clearance.callsign or clearance.callsign or "unknown"
        for f in compare(match.clearance, clearance):
            if f.type == "wrong_callsign":
                alerts.append(self._callsign_alert(tx, f.expected, f.heard))
                continue
            if f.type == "missing":
                msg = f"{describe('callsign', cs)} did not read back {describe(f.kind, f.expected)}"
            else:
                msg = (
                    f"{describe('callsign', cs)} read back {describe(f.kind, f.heard)}, "
                    f"cleared {describe(f.kind, f.expected)}"
                )
            alerts.append(
                Alert(tx.time, cs, "readback", f.kind, f.severity, msg, f.expected, f.heard, tx.id)
            )
        return alerts

    @staticmethod
    def _callsign_alert(tx: Transmission, expected: str, heard: str) -> Alert:
        return Alert(
            tx.time, expected, "readback", "callsign", "error",
            f"Readback by {describe('callsign', heard)} for an instruction to "
            f"{describe('callsign', expected)}",
            expected, heard, tx.id,
        )  # fmt: skip

    def _assign(self, callsign: str, items: list[Item], now: float) -> None:
        tracks = self.tracks.setdefault(callsign, {})
        for item in items:
            if item.kind not in TRACKED:
                continue
            group = "vertical" if item.kind in VERTICAL else item.kind
            tracks[group] = ClearanceTrack(item, now)
            self._raised = {k for k in self._raised if k[:2] != (callsign, group)}

    def on_state(self, sv: StateVector) -> list[Alert]:
        spoken = spoken_callsign(sv.callsign)
        alerts = []
        for callsign, tracks in self.tracks.items():
            if not same_callsign(callsign, spoken):
                continue
            for group, track in tracks.items():
                dev = track.check(sv)
                key = (callsign, group, dev.kind if dev else None)
                if dev is None or key in self._raised:
                    continue
                self._raised.add(key)
                alerts.append(
                    Alert(
                        sv.time, callsign, "track", dev.kind, dev.severity,
                        f"{describe('callsign', callsign)} ({sv.callsign}): {dev.detail}",
                        dev.expected, dev.actual,
                    )
                )  # fmt: skip
        return alerts
