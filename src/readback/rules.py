"""Rule-based clearance parser over canonical tokens.

Fast, offline and predictable, so it doubles as the baseline the Muse Spark parser has to beat.
It only knows standard ICAO phraseology; free-form speech is left to the language model.
"""

from __future__ import annotations

from readback.clearance import Clearance, Item
from readback.metrics import extract_callsign
from readback.normalize import canonical

VERTICAL = {
    "climb": "climb", "climbing": "climb", "descend": "descend", "descending": "descend",
    "maintain": "maintain", "maintaining": "maintain",
}  # fmt: skip
SIDES = {"left": "l", "right": "r", "center": "c", "centre": "c"}
SPEED_CONTEXT = {"speed", "reduce", "reducing", "increase", "increasing"}
CHANNEL_ENDINGS = {
    "00", "05", "10", "15", "25", "30", "35", "40", "50", "55", "60", "65", "75", "80", "85", "90",
}  # fmt: skip


def _digits(toks: list[str], start: int, max_n: int) -> tuple[str, int]:
    j = start
    while j < len(toks) and j - start < max_n and toks[j].isdigit():
        j += 1
    return "".join(toks[start:j]), j


def _phrase(toks: list[str], i: int, *words: str) -> bool:
    return toks[i : i + len(words)] == list(words)


def _lookback(toks: list[str], i: int, table: dict[str, str], span: int = 4) -> str:
    for t in reversed(toks[max(0, i - span) : i]):
        if t in table:
            return table[t]
    return ""


def _frequency_fraction(digits: str) -> str:
    """Trim digits that can't belong to the frequency (often the callsign that follows).
    25 kHz channels end in these pairs; 8.33 kHz channels are spoken with the same endings."""
    if len(digits) == 3 and digits[1:] in CHANNEL_ENDINGS:
        return digits
    if len(digits) >= 2 and digits[1] in "0257":
        return digits[:2]
    return digits[:1]


def _runway_at(toks: list[str], i: int) -> tuple[str, int] | None:
    """Parse "runway 2 7 left" starting at index i (pointing at "runway"). Runway numbers
    always have two digits; one digit means the clip was cut off mid-number."""
    num, j = _digits(toks, i + 1, 2)
    if len(num) != 2:
        return None
    if j < len(toks) and toks[j] in SIDES:
        return num + SIDES[toks[j]], j + 1
    return num, j


def _nearby_runway(toks: list[str], i: int, span: int = 5) -> str:
    for k in range(i, min(len(toks), i + span)):
        if toks[k] == "runway" and (rwy := _runway_at(toks, k)):
            return rwy[0]
    return ""


def parse(text: str, designators: set[str]) -> Clearance:
    toks = canonical(text)
    items: list[Item] = []
    attached_runways: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = i + 1

        if _phrase(toks, i, "flight", "level"):
            num, j = _digits(toks, i + 2, 3)
            if len(num) >= 2:
                items.append(Item("flight_level", str(int(num)), _lookback(toks, i, VERTICAL)))
                nxt = j
            else:
                nxt = i + 2
        elif t == "heading":
            num, j = _digits(toks, i + 1, 3)
            if len(num) == 3:
                side = _lookback(toks, i, {"left": "left", "right": "right"}, span=3)
                items.append(Item("heading", num, side))
                nxt = j
        elif t == "squawk":
            num, j = _digits(toks, i + 1, 4)
            if len(num) == 4:
                items.append(Item("squawk", num))
                nxt = j
        elif t == "qnh":
            num, j = _digits(toks, i + 1, 4)
            if len(num) >= 3:
                items.append(Item("qnh", num))
                nxt = j
        elif t == "altitude":
            num, j = _digits(toks, i + 1, 5)
            if len(num) >= 3:
                items.append(Item("altitude", str(int(num)), _lookback(toks, i, VERTICAL)))
                nxt = j
        elif t.isdigit():
            num, j = _digits(toks, i, 5)
            after = toks[j] if j < len(toks) else ""
            before = toks[max(0, i - 6) : i]
            if after == "feet" and len(num) >= 3:
                items.append(Item("altitude", str(int(num)), _lookback(toks, i, VERTICAL)))
                nxt = j + 1
            elif (
                after == "knots"
                and len(num) == 3
                and SPEED_CONTEXT & set(before)
                and "wind" not in before
            ):
                items.append(Item("speed", str(int(num))))
                nxt = j + 1
            elif after == "decimal" and len(num) == 3 and num.startswith("1"):
                frac, _ = _digits(toks, j + 1, 3)
                if frac:
                    frac = _frequency_fraction(frac)
                    items.append(Item("frequency", f"{num}.{frac}"))
                    nxt = j + 1 + len(frac)
                else:
                    nxt = j
            else:
                nxt = j
        elif t == "speed":
            num, j = _digits(toks, i + 1, 3)
            if len(num) == 3:  # ATC speeds are three digits; fewer means a cut-off clip
                items.append(Item("speed", str(int(num))))
                nxt = j
        elif _phrase(toks, i, "cleared", "to", "land") or _phrase(toks, i, "clear", "to", "land"):
            rwy = _nearby_runway(toks, i + 3) or _nearby_runway(toks, max(0, i - 4), 4)
            items.append(Item("cleared_to_land", rwy))
            attached_runways.add(rwy)
            nxt = i + 3
        elif t in ("cleared", "clear") and "takeoff" in toks[i + 1 : i + 3]:
            k = toks.index("takeoff", i + 1)
            rwy = _nearby_runway(toks, k + 1) or _nearby_runway(toks, max(0, i - 4), 4)
            items.append(Item("cleared_for_takeoff", rwy))
            attached_runways.add(rwy)
            nxt = k + 1
        elif t in ("line", "lining") and nxt < len(toks) and toks[nxt] == "up":
            rwy = _nearby_runway(toks, i + 2) or _nearby_runway(toks, max(0, i - 4), 4)
            items.append(Item("line_up", rwy))
            attached_runways.add(rwy)
            nxt = i + 2
        elif _phrase(toks, i, "hold", "short") or _phrase(toks, i, "holding", "point"):
            rwy = _nearby_runway(toks, i + 2)
            items.append(Item("hold_short", rwy))
            attached_runways.add(rwy)
            nxt = i + 2
        elif t == "direct" and nxt < len(toks):
            k = nxt + (toks[nxt] == "to")
            if k < len(toks) and toks[k].isalpha() and len(toks[k]) > 1:
                items.append(Item("direct_to", toks[k]))
                nxt = k + 1

        i = max(nxt, i + 1)

    for k, t in enumerate(toks):
        if t == "runway" and (rwy := _runway_at(toks, k)) and rwy[0] not in attached_runways:
            if not any(it.kind == "runway" and it.value == rwy[0] for it in items):
                items.append(Item("runway", rwy[0]))

    callsign = extract_callsign(toks, designators)
    return Clearance(" ".join(callsign) if callsign else None, items)
