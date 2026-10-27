"""Compare a controller's instruction with the pilot's readback."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from readback.clearance import READBACK_REQUIRED, Clearance

RUNWAY_CLEARANCES = {"cleared_to_land", "cleared_for_takeoff", "line_up", "hold_short"}
RUNWAY_FAMILY = RUNWAY_CLEARANCES | {"runway"}


@dataclass(frozen=True)
class Finding:
    type: str  # "wrong_value", "wrong_callsign", "missing" or "partial"
    kind: str  # item kind, or "callsign"
    expected: str
    heard: str

    @property
    def severity(self) -> str:
        # A wrong value or callsign is a readback error. An omitted or partly matching item
        # is only a warning: pilots legitimately shorten readbacks and recognizers drop words.
        return "warning" if self.type in ("missing", "partial") else "error"


def _tail(callsign: str) -> list[str]:
    toks = callsign.split()
    return toks[1:] if toks and len(toks[0]) > 1 else toks


def same_callsign(a: str, b: str) -> bool:
    """Equal, or equal once the airline designator is dropped, or one is an abbreviation
    (suffix) of the other, as in "csa 1 d z" vs "1 d z" or "o k f a o" vs "f a o"."""
    if a == b:
        return True
    ta, tb = _tail(a), _tail(b)
    if ta == tb:
        return True
    short, long_ = sorted((ta, tb), key=len)
    return len(short) >= 2 and long_[-len(short) :] == short


def compare(instruction: Clearance, readback: Clearance) -> list[Finding]:
    findings: list[Finding] = []
    if (
        instruction.callsign
        and readback.callsign
        and not same_callsign(instruction.callsign, readback.callsign)
    ):
        findings.append(
            Finding("wrong_callsign", "callsign", instruction.callsign, readback.callsign)
        )

    heard = defaultdict(list)
    for item in readback.items:
        heard[item.kind].append(item.value)
    # A runway can be read back inside any runway clearance or on its own ("runway 3 1"), so
    # runway values are compared across the whole family.
    runways_heard = [it.value for it in readback.items if it.kind in RUNWAY_FAMILY and it.value]
    for item in instruction.items:
        if item.kind not in READBACK_REQUIRED:
            continue
        values = heard.get(item.kind, [])
        if item.kind in RUNWAY_FAMILY:
            if item.value and item.value in runways_heard:
                continue
            if item.value and runways_heard:
                findings.append(Finding("wrong_value", item.kind, item.value, runways_heard[0]))
            elif not values:
                findings.append(Finding("missing", item.kind, item.value, ""))
            continue
        if not values:
            findings.append(Finding("missing", item.kind, item.value, ""))
        elif item.value in values:
            continue
        elif item.kind == "frequency" and any(_prefix_match(item.value, v) for v in values):
            # "121.9" vs "121.926": usually the callsign's digits run on after the frequency
            findings.append(Finding("partial", item.kind, item.value, values[0]))
        else:
            findings.append(Finding("wrong_value", item.kind, item.value, values[0]))
    return findings


def _prefix_match(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)
