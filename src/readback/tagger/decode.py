"""Tags to clearance: turn labeled spans of canonical tokens into a structured clearance.

The tagger marks spans (B-/I- tags) and this module reads the values out of them, so the
network never has to spell out digits itself. The same decoder turns gold tags into labels,
so the model is always scored on exactly what can be read from its tags.
"""

from __future__ import annotations

from readback.clearance import KINDS, Clearance, Item
from readback.rules import VERTICAL

LABELS = ("callsign", *KINDS)
TAGS = ("O", *(f"{p}-{label}" for label in LABELS for p in "BI"))
FILLERS = {"uh", "eh", "er", "ehm", "hm"}
SIDES = {"left": "l", "right": "r", "center": "c", "centre": "c", "l": "l", "r": "r", "c": "c"}
RUNWAY_CLEARANCES = {"cleared_to_land", "cleared_for_takeoff", "line_up", "hold_short"}
RUNWAY_KINDS = RUNWAY_CLEARANCES | {"runway"}
NOT_WAYPOINTS = {"direct", "to", "proceed", "proceeding", "fly", "cleared", "and"}


def spans(tags: list[str]) -> list[tuple[str, int, int]]:
    """(label, start, end) for each span. A stray I- tag opens a span of its own."""
    out: list[tuple[str, int, int]] = []
    for i, tag in enumerate(tags):
        if tag == "O":
            continue
        prefix, label = tag.split("-", 1)
        if prefix == "I" and out and out[-1][0] == label and out[-1][2] == i:
            out[-1] = (label, out[-1][1], i + 1)
        else:
            out.append((label, i, i + 1))
    return out


def _groups(toks: list[str]) -> list[str]:
    groups, cur = [], ""
    for t in toks:
        if t.isdigit():
            cur += t
        elif cur:
            groups.append(cur)
            cur = ""
    return groups + [cur] if cur else groups


def item_from_span(kind: str, toks: list[str]) -> Item | None:
    digits = "".join(t for t in toks if t.isdigit())
    vertical = next((VERTICAL[t] for t in toks if t in VERTICAL), "")
    if kind == "flight_level":
        return Item(kind, str(int(digits)), vertical) if 2 <= len(digits) <= 3 else None
    if kind == "altitude":
        return Item(kind, str(int(digits)), vertical) if 3 <= len(digits) <= 5 else None
    if kind == "heading":
        side = next((t for t in toks if t in ("left", "right")), "")
        return Item(kind, digits.zfill(3), side) if 2 <= len(digits) <= 3 else None
    if kind == "speed":
        return Item(kind, str(int(digits))) if 2 <= len(digits) <= 3 else None
    if kind == "squawk":
        return Item(kind, digits) if len(digits) == 4 else None
    if kind == "qnh":
        return Item(kind, digits) if 3 <= len(digits) <= 4 else None
    if kind == "frequency":
        groups = _groups(toks)
        if len(groups) >= 2 and len(groups[0]) == 3:
            mhz, khz = groups[0], groups[1]
        elif len(groups) == 1 and len(groups[0]) >= 4:
            mhz, khz = groups[0][:3], groups[0][3:]
        else:
            return None
        return Item(kind, f"{mhz}.{khz}") if mhz.startswith("1") and len(khz) <= 3 else None
    if kind in RUNWAY_KINDS:
        value = ""
        for i, t in enumerate(toks):
            if t.isdigit() and i + 1 < len(toks) and toks[i + 1].isdigit():
                value = t + toks[i + 1]
                if i + 2 < len(toks) and toks[i + 2] in SIDES:
                    value += SIDES[toks[i + 2]]
                break
        if kind == "runway" and not value:
            return None
        return Item(kind, value)
    if kind == "direct_to":
        names = [t for t in toks if t.isalpha() and len(t) > 1 and t not in NOT_WAYPOINTS]
        return Item(kind, names[-1]) if names else None
    raise ValueError(kind)


def tidy(clearance: Clearance) -> Clearance:
    """Drop repeated items, and give a runway clearance said apart from its runway ("runway
    one three, wind calm, cleared to land", "line up runway one three and cleared for
    takeoff") the one runway the transmission mentions. Applied to every parser's output, so
    parsers are compared on what they understood rather than on bookkeeping."""
    items: list[Item] = []
    for it in clearance.items:
        if it not in items:
            items.append(it)
    empty = [it for it in items if it.kind in RUNWAY_CLEARANCES and not it.value]
    runways = {it.value for it in items if it.kind in RUNWAY_KINDS and it.value}
    if empty and len(runways) == 1:
        (rwy,) = runways
        filled = [Item(it.kind, rwy, it.qualifier) if it in empty else it for it in items]
        if not any(it.kind in RUNWAY_CLEARANCES and it.value for it in items):
            filled = [it for it in filled if it.kind != "runway"]  # the runway was theirs
        items = []
        for it in filled:
            if it not in items:
                items.append(it)
    return Clearance(clearance.callsign, items)


def decode(tokens: list[str], tags: list[str]) -> Clearance:
    callsign = None
    items: list[Item] = []
    for label, start, end in spans(tags):
        toks = [t for t in tokens[start:end] if t not in FILLERS]
        if label == "callsign":
            if callsign is None and toks:
                callsign = " ".join(toks)
        elif (item := item_from_span(label, toks)) is not None:
            items.append(item)
    return tidy(Clearance(callsign, items))
