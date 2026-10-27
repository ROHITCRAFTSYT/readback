"""Synthetic radio + ADS-B scenarios with ground-truth labels.

Each scenario is a few minutes of one approach frequency: controllers issue clearances, pilots
read them back (sometimes wrongly), and every aircraft flies a simple kinematic model that
follows the value the pilot believes, so a wrong readback can turn into a level bust. Labels
record every planted error so the monitor's alerts can be scored exactly.

The airport layout and frequencies are illustrative, not real aeronautical data.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from readback.adsb import StateVector

DIGIT_WORDS = "zero one two three four five six seven eight niner".split()
LETTER_WORDS = {
    "A": "alfa", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot",
    "G": "golf", "H": "hotel", "K": "kilo", "L": "lima", "M": "mike", "P": "papa",
    "R": "romeo", "S": "sierra", "T": "tango", "X": "xray", "Z": "zulu",
}  # fmt: skip
AIRLINES = {
    "IGO": "ifly", "SEJ": "spicejet", "BAW": "speedbird", "UAE": "emirates",
    "DLH": "lufthansa", "QTR": "qatari", "SIA": "singapore", "AIC": "airindia",
}  # fmt: skip
CENTER = (13.1986, 77.7066)  # Bengaluru (VOBL) reference point
TRANSITION_ALTITUDE_FT = 11000
APPROACH = ("bengaluru approach", "127.850")
RADAR = ("bengaluru radar", "125.450")


def say_digits(s: str) -> str:
    return " ".join(DIGIT_WORDS[int(c)] for c in s)


def say_callsign(callsign: str) -> str:
    words = [AIRLINES[callsign[:3]]]
    for c in callsign[3:]:
        words.append(DIGIT_WORDS[int(c)] if c.isdigit() else LETTER_WORDS[c])
    return " ".join(words)


def say_altitude(ft: int) -> str:
    thousands, hundreds = divmod(ft, 1000)
    words = [say_digits(str(thousands)), "thousand"]
    if hundreds:
        words += [DIGIT_WORDS[hundreds // 100], "hundred"]
    return " ".join(words) + " feet"


def say_item(kind: str, value: str, qualifier: str, pilot: bool) -> str:
    if kind == "flight_level":
        verb = {"climb": "climbing", "descend": "descending"}[qualifier] if pilot else qualifier
        return f"{verb} flight level {say_digits(value.zfill(3))}"
    if kind == "altitude":
        verb = {"climb": "climbing", "descend": "descending"}[qualifier] if pilot else qualifier
        return f"{verb} {say_altitude(int(value))}"
    if kind == "heading":
        return (
            f"{qualifier} heading {say_digits(value)}"
            if pilot
            else (f"turn {qualifier} heading {say_digits(value)}")
        )
    if kind == "speed":
        return f"speed {say_digits(value)}" if pilot else f"reduce speed {say_digits(value)} knots"
    if kind == "squawk":
        return f"squawk {say_digits(value)}"
    if kind == "qnh":
        return f"qnh {say_digits(value)}"
    if kind == "frequency":
        unit, (mhz, khz) = qualifier, value.split(".")
        spoken = f"{say_digits(mhz)} decimal {say_digits(khz.rstrip('0') or '0')}"
        return spoken if pilot else f"contact {unit} {spoken}"
    raise ValueError(kind)


@dataclass
class Aircraft:
    callsign: str
    icao24: str
    lat: float
    lon: float
    alt: float
    gs: float
    track: float
    squawk: str
    arrival: bool
    target_alt: float = 0.0
    target_track: float = 0.0
    turn: str = ""  # "left", "right" or "" (shortest way)
    vs: float = 0.0

    def __post_init__(self) -> None:
        self.target_alt = self.target_alt or self.alt
        self.target_track = self.target_track or self.track

    def step(self, dt: float) -> None:
        diff = self.target_alt - self.alt
        rate = 2200.0 if diff > 0 else 1800.0
        self.vs = math.copysign(min(rate, abs(diff) * 60 / dt), diff) if abs(diff) > 1 else 0.0
        self.alt += self.vs * dt / 60
        delta = (self.target_track - self.track + 540) % 360 - 180
        if self.turn == "left" and delta > 0:
            delta -= 360
        elif self.turn == "right" and delta < 0:
            delta += 360
        turn = max(-3.0 * dt, min(3.0 * dt, delta))
        self.track = (self.track + turn) % 360
        if abs(delta) <= 3.0 * dt:
            self.turn = ""
        nm = self.gs * dt / 3600
        self.lat += nm * math.cos(math.radians(self.track)) / 60
        self.lon += (
            nm * math.sin(math.radians(self.track)) / (60 * math.cos(math.radians(self.lat)))
        )

    def state(self, t: float) -> StateVector:
        return StateVector(
            icao24=self.icao24, callsign=self.callsign, time=t, lat=round(self.lat, 5),
            lon=round(self.lon, 5), altitude_ft=round(self.alt), ground_speed_kt=round(self.gs),
            track_deg=round(self.track, 1), vertical_rate_fpm=round(self.vs), squawk=self.squawk,
        )  # fmt: skip


@dataclass
class Scenario:
    name: str
    start: float
    center: tuple[float, float]
    aircraft: list[dict]
    events: list[dict] = field(default_factory=list)
    labels: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _mutate_digit(value: str, rng: random.Random, positions: list[int] | None = None) -> str:
    pos = rng.choice(positions if positions else range(len(value)))
    choices = [d for d in "0123456789" if d != value[pos]]
    return value[:pos] + rng.choice(choices) + value[pos + 1 :]


def _wrong_value(kind: str, value: str, rng: random.Random) -> str:
    if kind == "flight_level":  # change the tens digit: FL100 -> FL110 / FL090
        lvl = int(value) + rng.choice([-20, -10, 10, 20])
        return str(max(lvl, 30))
    if kind == "altitude":
        return str(int(value) + rng.choice([-1000, 1000]))
    if kind == "heading":
        hdg = (int(value) + rng.choice([-40, -30, 30, 40])) % 360 or 360
        return f"{hdg:03d}"
    return _mutate_digit(value, rng)


def _plan_items(ac: Aircraft, rng: random.Random, used_squawks: set[str]) -> list[tuple]:
    """One or two clearance items suited to the aircraft's phase of flight."""
    items: list[tuple] = []
    if ac.arrival and ac.target_alt > 4000:
        new = max(3000, ac.target_alt - rng.choice([2000, 3000, 4000, 5000]))
        if new >= TRANSITION_ALTITUDE_FT:
            items.append(("flight_level", str(int(new // 1000 * 10)), "descend"))
        else:
            items.append(("altitude", str(int(new // 500 * 500)), "descend"))
    elif not ac.arrival and ac.target_alt < 30000:
        new = ac.target_alt + rng.choice([4000, 6000, 8000])
        items.append(("flight_level", str(int(new // 1000 * 10)), "climb"))
    roll = rng.random()
    if roll < 0.45 or not items:
        side = rng.choice(["left", "right"])
        hdg = (ac.target_track + (-1 if side == "left" else 1) * rng.choice([20, 30, 40])) % 360
        items.append(("heading", f"{round(hdg / 5) * 5 % 360 or 360:03d}", side))
    elif roll < 0.6 and ac.arrival and ac.gs > 200:
        items.append(("speed", str(rng.choice([180, 190, 200, 210])), ""))
    elif roll < 0.75 and not ac.arrival and ac.squawk == "2000":
        while (sq := "".join(rng.choice("01234567") for _ in range(4))) in used_squawks:
            pass
        used_squawks.add(sq)
        items.append(("squawk", sq, ""))
    elif roll < 0.85 and ac.arrival and any(k == "altitude" for k, _, _ in items):
        items.append(("qnh", str(rng.choice([1009, 1010, 1011, 1012, 1013])), ""))
    return items


def _apply(ac: Aircraft, kind: str, value: str, qualifier: str) -> None:
    if kind == "flight_level":
        ac.target_alt = int(value) * 100
    elif kind == "altitude":
        ac.target_alt = int(value)
    elif kind == "heading":
        ac.target_track, ac.turn = float(value), qualifier
    elif kind == "speed":
        ac.gs = float(value) + 20  # rough ground speed for the indicated speed
    elif kind == "squawk":
        ac.squawk = value


def _fleet(rng: random.Random, n: int) -> list[Aircraft]:
    numbers = rng.sample(range(100, 999), n)
    designators = rng.sample(list(AIRLINES), n)
    callsigns = [f"{d}{num}" for d, num in zip(designators, numbers, strict=True)]
    # Two similar callsigns on one frequency, the setup for callsign confusion.
    callsigns[1] = callsigns[0][:3] + callsigns[0][3] + callsigns[0][5] + callsigns[0][4]
    if callsigns[1] == callsigns[0]:
        callsigns[1] = callsigns[0][:-1] + str((int(callsigns[0][-1]) + 1) % 10)
    fleet = []
    for i, cs in enumerate(callsigns):
        arrival = i < n - 1
        bearing = rng.uniform(0, 360)
        dist = rng.uniform(20, 40) if arrival else rng.uniform(3, 8)
        lat = CENTER[0] + dist * math.cos(math.radians(bearing)) / 60
        lon = CENTER[1] + dist * math.sin(math.radians(bearing)) / (
            60 * math.cos(math.radians(lat))
        )
        inbound = (bearing + 180 + rng.uniform(-30, 30)) % 360
        fleet.append(
            Aircraft(
                callsign=cs, icao24=f"8{rng.randrange(16**5):05x}", lat=lat, lon=lon,
                alt=rng.choice([14000, 16000, 18000, 20000]) if arrival else 3000.0,
                gs=260.0 if arrival else 220.0,
                track=inbound if arrival else rng.choice([90.0, 270.0]),
                squawk=f"{rng.randrange(1000, 7000):04o}"[-4:] if arrival else "2000",
                arrival=arrival,
            )
        )  # fmt: skip
    return fleet


def generate(
    seed: int,
    duration: float = 540.0,
    n_aircraft: int = 5,
    p_readback_error: float = 0.2,
    p_callsign_confusion: float = 0.1,
    p_omission: float = 0.1,
    p_bust: float = 0.08,
    settle: float = 300.0,
    start: float = 1_793_000_000.0,
    dt: float = 5.0,
) -> Scenario:
    rng = random.Random(seed)
    fleet = _fleet(rng, n_aircraft)
    by_cs = {ac.callsign: ac for ac in fleet}
    scen = Scenario(
        name=f"vobl-{seed}", start=start, center=CENTER,
        aircraft=[{"callsign": a.callsign, "icao24": a.icao24, "spoken": say_callsign(a.callsign)}
                  for a in fleet],
    )  # fmt: skip
    used_squawks: set[str] = set()
    actions: list[tuple[float, str, tuple]] = []  # (time, callsign, item) the pilot will fly
    channel_free = 5.0
    next_call = {ac.callsign: rng.uniform(5, 60) for ac in fleet}
    tx_id = 0

    def say(t: float, role: str, speaker: str, text: str) -> float:
        nonlocal tx_id
        tx_id += 1
        scen.events.append({"t": round(t, 1), "type": "tx", "id": f"tx{tx_id}", "role": role,
                            "speaker": speaker, "text": text, "audio": None})  # fmt: skip
        return t + 0.4 * len(text.split()) + 0.6

    def label(t: float, cs: str, source: str, kind: str, detail: str) -> None:
        scen.labels.append({"t": round(t, 1), "callsign": cs, "source": source, "kind": kind,
                            "detail": detail})  # fmt: skip

    t = 0.0
    while t < duration + settle:  # radio stops at `duration`; tracks run on so errors can show
        for cs, when in sorted(next_call.items(), key=lambda kv: kv[1]):
            if when > t or channel_free > t or when > duration - 60:
                continue
            ac = by_cs[cs]
            if abs(ac.alt - ac.target_alt) > 300 and t < when + 120:
                continue  # let the aircraft settle before the next clearance, as a controller would
            items = _plan_items(ac, rng, used_squawks)
            if not items:
                next_call[cs] = t + 600
                continue
            spoken_cs = say_callsign(cs)
            instruction = ", ".join(say_item(k, v, q, pilot=False) for k, v, q in items)
            end = say(t, "controller", "ATC", f"{spoken_cs}, {instruction}")
            reply_at = end + rng.uniform(1.0, 3.0)

            flown = list(items)
            heard = list(items)
            responder = cs
            roll = rng.random()
            if roll < p_callsign_confusion and cs == fleet[0].callsign:
                responder = fleet[1].callsign
                label(reply_at, cs, "readback", "callsign", f"{responder} took {cs}'s clearance")
                label(reply_at, cs, "readback", "no_readback", f"{cs} never read back")
                if any(k in ("flight_level", "altitude") for k, _, _ in items):
                    label(reply_at + 120, cs, "track", "level_off", f"{cs} never starts the change")
                flown = []
            elif roll < p_callsign_confusion + p_readback_error:
                i = rng.randrange(len(items))
                k, v, q = items[i]
                wrong = _wrong_value(k, v, rng)
                heard[i] = flown[i] = (k, wrong, q)
                label(reply_at, cs, "readback", k, f"read back {wrong}, cleared {v}")
                if k in ("flight_level", "altitude"):
                    target = int(v) * (100 if k == "flight_level" else 1)
                    believed = int(wrong) * (100 if k == "flight_level" else 1)
                    going_down = q == "descend"
                    if (believed > ac.alt) if going_down else (believed < ac.alt):
                        label(reply_at + 30, cs, "track", "wrong_vertical_direction",
                              f"heads for {wrong} the wrong way")  # fmt: skip
                    elif (believed < target) if going_down else (believed > target):
                        label(reply_at + 30, cs, "track", "level_bust", f"flies {wrong}")
                    else:
                        label(reply_at + 90, cs, "track", "level_off", f"levels at {wrong}")
                elif k == "heading":
                    label(reply_at + 45, cs, "track", "heading", f"flies heading {wrong}")
                elif k == "squawk":
                    label(reply_at + 30, cs, "track", "squawk", f"squawks {wrong}")
            elif roll < p_callsign_confusion + p_readback_error + p_omission and len(items) > 1:
                i = rng.randrange(len(items))
                label(reply_at, cs, "readback", "missing", f"omitted {items[i][0]}")
                heard = items[:i] + items[i + 1 :]
            elif roll < p_callsign_confusion + p_readback_error + p_omission + p_bust:
                vertical = [
                    i for i, it in enumerate(items) if it[0] in ("flight_level", "altitude")
                ]
                if vertical:
                    i = vertical[0]
                    k, v, q = items[i]
                    overshoot = rng.choice([600, 800, 1000]) * (-1 if q == "descend" else 1)
                    base = int(v) * (100 if k == "flight_level" else 1)
                    flown[i] = ("altitude", str(base + overshoot), q)
                    label(reply_at + 30, cs, "track", "level_bust", "correct readback, overshoots")

            readback = ", ".join(say_item(k, v, q, pilot=True) for k, v, q in heard)
            channel_free = say(
                reply_at, "pilot", responder, f"{readback}, {say_callsign(responder)}"
            )
            channel_free += rng.uniform(2.0, 5.0)
            for item in flown:
                actions.append((channel_free + rng.uniform(2, 6), cs, item))
            # After a planted track problem, give it time to show before the next clearance.
            planted_track = any(lb["source"] == "track" and lb["callsign"] == cs
                                and lb["t"] >= reply_at for lb in scen.labels)  # fmt: skip
            next_call[cs] = channel_free + (300 if planted_track else rng.uniform(70, 130))
        for when, cs, item in [a for a in actions if a[0] <= t]:
            _apply(by_cs[cs], *item)
            actions.remove((when, cs, item))
        for ac in fleet:
            ac.step(dt)
            sv = ac.state(start + t)
            scen.events.append({"t": round(t, 1), "type": "state", "state": asdict(sv)})
        t += dt

    scen.events.sort(key=lambda e: (e["t"], e["type"] != "tx"))
    return scen
