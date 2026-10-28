"""The monitor: pairs radio transmissions into instruction/readback exchanges, raises readback
alerts, and watches each aircraft's ADS-B track against the clearance it was given."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from readback.adsb import ClearanceTrack, StateVector, spoken_callsign
from readback.clearance import READBACK_REQUIRED, Clearance, Item, plausible
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


PILOT_CUES = (("ready", "for"), ("with", "you"), ("request",), ("requesting",), ("wilco",))


def _pilot_cue(toks: list[str]) -> bool:
    """Phrases only an aircraft uses: "ready for departure", "with you", "request descent".
    "say request" and "report ready" are the controller asking for them."""
    for i in range(len(toks)):
        for cue in PILOT_CUES:
            if tuple(toks[i : i + len(cue)]) == cue and (i == 0 or toks[i - 1] != "say"):
                return True
    return False


def infer_role(text: str, clearance: Clearance) -> str:
    """Controllers open with the callsign; pilots usually close with it, and calls asking for
    something are the pilot's whichever end the callsign is."""
    if not clearance.callsign:
        return "pilot"
    toks = canonical(text)
    if _pilot_cue(toks):
        return "pilot"
    first = clearance.callsign.split()[0]
    return "controller" if toks and toks[0] == first else "pilot"


@dataclass
class _Pending:
    tx: Transmission
    clearance: Clearance
    taken: bool = False  # another aircraft already read this instruction back


def _edits(a: list[str], b: list[str]) -> int:
    """Token edit distance (insertions, deletions, substitutions)."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def snap(callsign: str, traffic: set[str]) -> str:
    """The ADS-B callsign a heard callsign most likely is: the only aircraft in range that
    matches it (same_callsign) or is one token away ("ifly 7 8 4" for ifly 7 8 5). A callsign
    that fits none, or fits two aircraft, is left as heard. A pilot who answers with another
    aircraft's real callsign still matches that aircraft, so callsign confusion stays visible."""
    if callsign in traffic:
        return callsign
    near = [k for k in traffic if same_callsign(callsign, k)]
    if not near:
        near = [k for k in traffic if _edits(callsign.split(), k.split()) <= 1]
    return near[0] if len(near) == 1 else callsign


def by_number(text: str, traffic: set[str]) -> str | None:
    """The one aircraft in range whose flight number (three or more tokens after the
    designator) the transmission says, as in the abbreviated "one one zero two, cleared ..."."""
    toks = canonical(text)
    hits = []
    for k in traffic:
        parts = k.split()
        number = parts[1:] if len(parts[0]) > 1 else []
        n = len(number)
        if n >= 3 and any(toks[i : i + n] == number for i in range(len(toks) - n + 1)):
            hits.append(k)
    return hits[0] if len(hits) == 1 else None


@dataclass
class Monitor:
    parse: Callable[[str], Clearance]
    pairing_window: float = 20.0
    pending: list[_Pending] = field(default_factory=list)
    tracks: dict[str, dict[str, ClearanceTrack]] = field(default_factory=dict)
    traffic: set[str] = field(default_factory=set)  # spoken callsigns seen on ADS-B
    snapped: dict[str, str] = field(default_factory=dict)  # tx id -> callsign as heard
    _raised: set = field(default_factory=set)
    _last: tuple | None = None  # (time, role, callsign) of the previous transmission

    def _awaits_reply(self, tx: Transmission, clearance: Clearance) -> bool:
        """The previous transmission was an instruction to this same aircraft, still open."""
        if self._last is None or not clearance.callsign:
            return False
        t, role, callsign = self._last
        return (
            role == "controller"
            and bool(callsign)
            and same_callsign(callsign, clearance.callsign)
            and tx.time - t <= self.pairing_window
            and any(p.clearance.callsign == callsign for p in self.pending)
        )

    def on_transmission(self, tx: Transmission) -> tuple[Clearance, str, list[Alert]]:
        clearance = self.parse(tx.text)
        role = tx.role or infer_role(tx.text, clearance)  # from the callsign as spoken
        if self.traffic:
            known = snap(clearance.callsign, self.traffic) if clearance.callsign else None
            if known not in self.traffic:  # misheard or unparsed: look for a flight number
                known = by_number(tx.text, self.traffic) or known
            if known and known != clearance.callsign:
                self.snapped[tx.id] = clearance.callsign or ""
                clearance = replace(clearance, callsign=known)
        alerts = self.expire(tx.time)
        if tx.role is None and role == "controller" and self._awaits_reply(tx, clearance):
            role = "pilot"  # the aircraft just addressed, answering with its callsign first
        self._last = (tx.time, role, clearance.callsign)
        if role == "controller":
            # An impossible instruction (FL2770) is a mishearing, not something to check.
            clearance = replace(clearance, items=[i for i in clearance.items if plausible(i)])
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
        self.traffic.add(spoken)
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
