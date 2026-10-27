"""Synthetic ATC transmissions with a known tag for every token, for training the tagger.

A transmission is assembled from pieces: the callsign, one span per clearance item, and
unlabeled filler. Each piece is put into canonical form on its own, so the label of every
canonical token is known exactly. Three levels add realism step by step:

1. standard ICAO phraseology, full callsigns, one or two items
2. everyday variation: readbacks that drop keywords ("descending one zero zero"), station
   names, greetings, up to three items, wind and taxi distractors, abbreviated registrations
3. what a real frequency and a recognizer add: clips cut off mid-word, hesitations,
   corrections, misheard words, and airlines the model has never seen

Version 3 widens the phraseology to what ICAO Doc 9432 and CAP 413 describe for any
airport, not one corpus: VFR circuit calls (touch and go, downwind, final), runways said
without the word "runway", type-prefixed and abbreviated registrations ("heli charlie
zulu"), the "heavy" suffix, gusting and variable wind, traffic information with altitudes,
passing-level reports, initial-climb clearances, speed limits ("maximum one six zero"),
and greetings in the languages heard on European frequencies.

`focus` multiplies how often an item kind or a kind of noise is drawn, so the trainer can
push practice toward whatever the model currently gets wrong.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from readback.clearance import Clearance, Item
from readback.normalize import canonical
from readback.tagger.decode import FILLERS, RUNWAY_KINDS, decode, item_from_span

# Bump when the generator changes what it produces; the trainer then re-scores its champion.
VERSION = 3

DIGIT_WORDS = "zero one two three four five six seven eight niner".split()
PLAIN_DIGITS = "zero one two three four five six seven eight nine".split()
LETTERS = {
    "a": "alfa", "b": "bravo", "c": "charlie", "d": "delta", "e": "echo", "f": "foxtrot",
    "g": "golf", "h": "hotel", "i": "india", "j": "juliett", "k": "kilo", "l": "lima",
    "m": "mike", "n": "november", "o": "oscar", "p": "papa", "q": "quebec", "r": "romeo",
    "s": "sierra", "t": "tango", "u": "uniform", "v": "victor", "w": "whiskey", "x": "xray",
    "y": "yankee", "z": "zulu",
}  # fmt: skip
# Airline telephony designators (ICAO Doc 8585), spoken the way controllers say them.
TELEPHONY = [
    "csa", "lufthansa", "speedbird", "klm", "airfrans", "swiss", "austrian", "lot", "wizz",
    "ryanair", "easy", "turkish", "emirates", "qatari", "aeroflot", "finnair", "scandinavian",
    "nor shuttle", "german wings", "beeline", "skytravel", "smartlynx", "sunexpress",
    "transavia", "air malta", "fedex", "ups", "ifly", "spicejet", "airindia", "vistara",
    "shamrock", "iberia", "alitalia", "brussels", "condor", "eurowings", "tarom", "aegean",
    "belavia", "ukraine international", "air berlin", "travel service", "blue danube",
    "jetset", "silkair", "singapore", "cathay", "united", "american", "springbok", "ethiopian",
    "royal jordanian", "el al", "pegasus", "air serbia", "croatia", "adria", "bulgarian",
    "oman air", "etihad", "saudia", "kuwaiti", "gulf air", "southern air", "dhl", "air france",
    "wizz air", "finn air", "speed bird", "malev", "air baltic", "vueling", "norwegian",
    "air europa", "jet two", "virgin", "tap", "luxair", "cyprus", "olympic", "tunair",
    "qantas", "jetstar", "velocity", "cathay", "dragon", "china southern", "china eastern",
    "air china", "korean air", "asiana", "japan air", "all nippon", "air canada", "westjet",
    "delta", "jetblue", "alaska", "southwest", "air new zealand", "edelweiss", "helvetic",
    "european", "air nostrum", "royal air maroc", "air portugal", "tatra", "smartwings",
    "aer lingus", "icelandair", "air dolomiti", "sky express", "bulgaria air", "kenya",
    "egyptair", "saudia", "air arabia", "flydubai", "indigo", "akasa air", "garuda",
    "thai", "malaysian", "philippine", "vietnam airlines", "atlas", "kalitta", "polar",
    "cargolux", "nippon cargo", "air hong kong", "rega", "swiss air force", "netjets",
    "vista jet", "jet aviation", "global", "executive", "europa", "air force",
]  # fmt: skip
# Light aircraft and helicopters say their type before the registration ("heli charlie zulu").
TYPE_PREFIXES = ["heli", "helicopter", "cessna", "piper", "diamond", "robin", "pilatus", "glider"]
# Callsigns are also spoken as the three-letter ICAO code: "sierra delta mike two two one".
CODE_LETTERS = "abcdefghijklmnoprstuvwxyz"
REGISTRATION_PREFIXES = [
    "o k", "d", "h b", "g", "v t", "s p", "o e", "o m", "n", "f", "v h", "c", "e i", "s e",
    "l n", "o y", "9 h", "i", "e c", "p h", "o o", "y r", "h a", "s 5", "9 a", "z k",
]  # fmt: skip
STATIONS = [
    "ruzyne tower", "ruzyne ground", "praha radar", "prague director", "prague approach",
    "praha information", "bengaluru approach", "bengaluru radar", "bengaluru tower", "tower",
    "radar", "approach", "director", "ground", "delivery", "praha", "ruzyne", "apron",
    "information", "departure", "arrival", "control",
]  # fmt: skip
STATION_KINDS = ["tower", "ground", "approach", "radar", "apron", "information", "departure"]
GREETINGS = [
    "hello", "good morning", "good day", "good afternoon", "good evening", "servus",
    "bonjour", "bonsoir", "gruezi", "guten morgen", "guten tag", "dobry den", "buongiorno",
    "hola", "dzien dobry", "hello again", "hi",
]  # fmt: skip
SIGNOFFS = [
    "bye", "bye bye", "good day", "thank you", "thanks", "ciao", "see you", "merci",
    "au revoir", "danke", "tschuss", "ahoj", "diky", "grazie", "cheers", "good night",
]  # fmt: skip
ACKS = ["roger", "wilco", "ok", "okay", "copied", "affirm"]
CONNECTORS = ["", "", "and", "then"]
CHANNEL_ENDINGS = [
    "0",
    "05",
    "1",
    "15",
    "2",
    "25",
    "3",
    "35",
    "4",
    "5",
    "55",
    "6",
    "65",
    "7",
    "75",
    "8",
    "85",
    "9",
    "125",
    "625",
    "880",
    "355",
    "605",
    "830",
]
SYLLABLES = [
    "ba",
    "lo",
    "mi",
    "ke",
    "tu",
    "ra",
    "vo",
    "ne",
    "si",
    "do",
    "ga",
    "pu",
    "le",
    "ko",
    "zi",
    "ma",
    "ri",
    "na",
    "te",
    "bo",
]

# Circuit and approach calls that name a runway (ICAO Doc 9432 ch. 4, CAP 413 ch. 4).
VFR_RUNWAY_LEADS = [
    "cleared touch and go", "cleared stop and go", "cleared low approach", "cleared for the option",
    "join downwind", "join right downwind", "join left downwind", "join left base",
    "join right base", "report downwind", "report final", "report left base", "downwind",
    "on final", "final", "base", "number one", "cleared ils approach", "cleared rnp approach",
    "cleared visual approach", "localizer established", "circling", "taxi to", "behind traffic",
    "landing", "departure",
]  # fmt: skip
# Leads after which the runway is often said without the word "runway": "downwind two five".
NO_KEYWORD_LEADS = (
    "established", "fully", "downwind", "on final", "final", "base", "join", "report downwind",
    "report final", "cleared ils", "cleared rnp", "cleared visual", "localizer", "circling",
    "cross", "backtrack",
)  # fmt: skip
CIRCUIT_TAILS = [
    "cleared touch and go", "touch and go", "cleared stop and go", "full stop", "low approach",
    "report downwind", "report final", "number one", "number two", "continue approach",
    "in use", "vacate left", "vacate right", "backtrack approved",
]  # fmt: skip

KIND_WEIGHTS = {
    "flight_level": 3.0, "altitude": 1.5, "heading": 2.0, "speed": 1.5, "squawk": 1.0,
    "frequency": 2.0, "qnh": 1.0, "runway": 1.0, "cleared_to_land": 1.5,
    "cleared_for_takeoff": 1.0, "line_up": 1.0, "hold_short": 0.7, "direct_to": 1.0,
}  # fmt: skip
# Probability of each kind of noise, per level.
NOISE = {
    1: {},
    2: {"distractor": 0.35, "station": 0.25, "greeting": 0.2, "abbreviated": 0.15,
        "novel_airline": 0.08, "dropped_keyword": 0.3},
    3: {"distractor": 0.4, "station": 0.3, "greeting": 0.25, "abbreviated": 0.2,
        "novel_airline": 0.25, "dropped_keyword": 0.35, "truncate": 0.35, "hesitation": 0.2,
        "correction": 0.06, "misheard": 0.25, "repeat": 0.08},
    # Level 4 is a stress level past the optimal bar: everything at once, more often.
    4: {"distractor": 0.6, "station": 0.45, "greeting": 0.4, "abbreviated": 0.3,
        "novel_airline": 0.45, "dropped_keyword": 0.55, "truncate": 0.55, "hesitation": 0.4,
        "correction": 0.12, "misheard": 0.45, "repeat": 0.15},
}  # fmt: skip
MAX_LEVEL = max(NOISE)


@dataclass
class Example:
    tokens: list[str]
    tags: list[str]
    clearance: Clearance
    role: str
    features: set[str] = field(default_factory=set)
    truth: Clearance | None = None  # what was said before noise; for testing the decoder


def say(digits: str, plain: bool = False) -> str:
    words = PLAIN_DIGITS if plain else DIGIT_WORDS
    return " ".join(words[int(c)] for c in digits)


def spell(letters: str) -> str:
    return " ".join(PLAIN_DIGITS[int(c)] if c.isdigit() else LETTERS[c] for c in letters)


class Generator:
    def __init__(self, level: int, rng: random.Random, focus: dict[str, float] | None = None):
        if level not in NOISE:
            raise ValueError(f"level must be 1-{MAX_LEVEL}, not {level}")
        self.level = level
        self.rng = rng
        self.focus = focus or {}

    # -- helpers ------------------------------------------------------------------------
    def _p(self, noise: str) -> bool:
        p = NOISE[self.level].get(noise, 0.0) * self.focus.get(f"noise:{noise}", 1.0)
        return self.rng.random() < min(p, 0.9)

    def _digits(self, s: str) -> str:
        # Recognizers and some speakers say "nine", most controllers say "niner".
        return say(s, plain=self.level > 1 and self.rng.random() < 0.4)

    def _pick(self, *options):
        return self.rng.choice(options)

    def _novel_word(self) -> str:
        while True:  # not one that is already a word with a meaning ("mike", "tango")
            word = "".join(self.rng.choice(SYLLABLES) for _ in range(self.rng.choice((2, 3))))
            if canonical(word) == [word]:
                return word

    # -- callsigns ----------------------------------------------------------------------
    def callsign(self, feats: set[str]) -> str:
        r = self.rng.random()
        if r < 0.22:
            prefix = self.rng.choice(REGISTRATION_PREFIXES).replace(" ", "")
            if prefix == "n" and self.rng.random() < 0.6:  # US: N plus digits and letters
                feats.add("callsign:n_number")
                tail = str(self.rng.randint(1, 9)) + "".join(
                    str(self.rng.randint(0, 9)) for _ in range(self.rng.randint(1, 3))
                )
                letters = spell("n") + " " + self._digits(tail)
                if self.rng.random() < 0.5:
                    n_letters = self.rng.randint(1, 2)
                    letters += " " + spell(
                        "".join(self.rng.choice("abcdefghkmprstxyz") for _ in range(n_letters))
                    )
                return letters
            letters = prefix + "".join(
                self.rng.choice("abcdefghijklmnoprstuvwxyz") for _ in range(5 - len(prefix))
            )
            typed = self.level > 1 and self.rng.random() < 0.25
            if self._p("abbreviated"):
                feats.add("callsign:abbreviated")
                # ICAO: the first character and at least the last two; a type may replace it
                letters = letters[-2:] if typed else letters[0] + letters[-2:]
            else:
                feats.add("callsign:registration")
            text = spell(letters)
            if typed:
                feats.add("callsign:type_prefix")
                text = f"{self.rng.choice(TYPE_PREFIXES)} {text}"
            return text
        if self.level > 1 and r < 0.3:
            feats.add("callsign:spelled_code")
            airline = spell("".join(self.rng.choice(CODE_LETTERS) for _ in range(3)))
        elif self._p("novel_airline"):
            feats.add("callsign:novel_airline")
            airline = self._novel_word()
            if self.rng.random() < 0.3:
                airline = self._pick(
                    f"{airline} air", f"air {airline}", f"{airline} {self._novel_word()}"
                )
        else:
            feats.add("callsign:airline")
            airline = self.rng.choice(TELEPHONY)
        n = self.rng.choice((1, 2, 3, 3, 3, 4, 4))
        number = str(self.rng.randint(1, 9)) + "".join(
            str(self.rng.randint(0, 9)) for _ in range(n - 1)
        )
        parts = [airline, self._digits(number)]
        if n < 4 and self.rng.random() < 0.3:  # e.g. CSA1DZ, BAW12K: at most four after the name
            n_letters = self.rng.randint(1, 4 - n)
            parts.append(
                spell("".join(self.rng.choice("abcdefghkmprstxyz") for _ in range(n_letters)))
            )
        return " ".join(parts)

    # -- clearance items ----------------------------------------------------------------
    def _runway(self) -> tuple[str, str]:
        num = f"{self.rng.randint(1, 36):02d}"
        side = self.rng.choice(("", "", "", "l", "r", "c"))
        words = self._digits(num)
        if side:
            words += " " + {"l": "left", "r": "right", "c": "center"}[side]
        return words, num + side

    def item(self, kind: str, pilot: bool, feats: set[str]) -> tuple[list, Item]:
        """Pieces [(text, kind or None)] for one item, and the item they encode."""
        rng, d = self.rng, self._digits
        drop = pilot and self._p("dropped_keyword")
        if kind == "flight_level":
            fl = rng.randrange(50, 420, 10)
            q = rng.choice(("climb", "descend", "descend", "maintain"))
            if fl % 100 == 0 and rng.random() < 0.4:
                num = f"{PLAIN_DIGITS[fl // 100]} hundred"
            elif fl < 100 and self.level > 1 and rng.random() < 0.5:
                num = d(str(fl))
            else:
                num = d(f"{fl:03d}")
            if pilot:
                verb = {"climb": "climbing", "descend": "descending", "maintain": "maintaining"}[q]
                if drop:
                    feats.add("form:fl_no_keyword")
                    text = self._pick(f"{verb} {num}", f"{verb} to {num}", f"level {num} {verb}")
                else:
                    text = self._pick(
                        f"{verb} flight level {num}",
                        f"{verb} to flight level {num}",
                        f"flight level {num} {verb}",
                        f"{verb} level {num}",
                    )
            elif q == "maintain":
                text = f"maintain flight level {num}"
            else:
                text = self._pick(
                    f"{q} flight level {num}",
                    f"{q} to flight level {num}",
                    f"{q} and maintain flight level {num}",
                )
            return [(text, kind)], Item(kind, str(fl), q)
        if kind == "altitude":
            ft = (
                rng.randrange(1000, 13500, 500)
                if self.level > 1
                else rng.randrange(2000, 10000, 500)
            )
            q = rng.choice(("climb", "descend", "descend", "maintain"))
            thousands, rest = divmod(ft, 1000)
            # ICAO says the thousands digit by digit: "one one thousand" for 11000
            num = f"{say(str(thousands), plain=True)} thousand" + (
                f" {PLAIN_DIGITS[rest // 100]} hundred" if rest else ""
            )
            feet = "" if (drop or (self.level > 1 and rng.random() < 0.2)) else " feet"
            if pilot:
                verb = {"climb": "climbing", "descend": "descending", "maintain": "maintaining"}[q]
                text = self._pick(
                    f"{verb} {num}{feet}", f"{verb} altitude {num}{feet}", f"{verb} to {num}{feet}"
                )
            elif q == "maintain":
                text = self._pick(f"maintain {num} feet", f"maintain altitude {num}{feet}")
            elif q == "climb" and self.level > 1 and rng.random() < 0.3:
                feats.add("form:initial_climb")
                text = self._pick(f"initial climb {num} feet", f"climb initially {num} feet")
            else:
                text = self._pick(
                    f"{q} altitude {num}{feet}",
                    f"{q} to altitude {num}{feet}",
                    f"{q} to {num}{feet}",
                    f"{q} {num} feet",
                )
            return [(text, kind)], Item(kind, str(ft), q)
        if kind == "heading":
            hdg = f"{rng.randrange(5, 365, 5):03d}"
            side = rng.choice(("left", "right", ""))
            if pilot:
                if drop and side:
                    feats.add("form:heading_no_keyword")
                    text = self._pick(f"{side} {d(hdg)}", f"turning {side} {d(hdg)}")
                elif side:
                    text = self._pick(
                        f"{side} heading {d(hdg)}",
                        f"turning {side} heading {d(hdg)}",
                        f"heading {d(hdg)} {side}",
                    )
                elif drop:
                    feats.add("form:heading_no_keyword")
                    text = f"{d(hdg)} degrees"
                else:
                    text = self._pick(f"heading {d(hdg)}", f"flying heading {d(hdg)}",
                                      f"heading {d(hdg)} degrees")  # fmt: skip
            elif side:
                text = f"turn {side} heading {d(hdg)}"
            else:
                text = self._pick(
                    f"fly heading {d(hdg)}", f"heading {d(hdg)}", f"continue heading {d(hdg)}"
                )
            return [(text, kind)], Item(kind, hdg, side)
        if kind == "speed":
            spd = str(rng.randrange(120 if self.level > 1 else 160, 300, 10))
            if self.level > 1 and rng.random() < 0.2:
                feats.add("form:speed_limit")
                text = self._pick(f"maximum {d(spd)} knots", f"maximum speed {d(spd)}",
                                  f"{d(spd)} knots or less", f"maximum {d(spd)}")  # fmt: skip
                return [(text, kind)], Item(kind, spd)
            if pilot:
                text = (
                    self._pick(f"{d(spd)} knots", f"reducing {d(spd)}")
                    if drop
                    else self._pick(
                        f"speed {d(spd)}",
                        f"reducing {d(spd)} knots",
                        f"speed {d(spd)} knots",
                        f"reducing speed {d(spd)}",
                    )
                )
            else:
                text = self._pick(
                    f"reduce speed {d(spd)} knots",
                    f"speed {d(spd)} knots",
                    f"reduce to {d(spd)} knots",
                    f"maintain speed {d(spd)}",
                    f"increase speed {d(spd)} knots",
                    f"speed {d(spd)}",
                )
            return [(text, kind)], Item(kind, spd)
        if kind == "squawk":
            code = "".join(str(rng.randint(0, 7)) for _ in range(4))
            text = (
                self._pick(f"squawk {d(code)}", f"squawking {d(code)}")
                if pilot
                else self._pick(f"squawk {d(code)}", f"set squawk {d(code)}")
            )
            return [(text, kind)], Item(kind, code)
        if kind == "qnh":
            qnh = str(rng.randint(990, 1035))
            text = (
                self._pick(
                    f"qnh {d(qnh)}",
                    f"q n h {d(qnh)}",
                    f"qnh is {d(qnh)}",
                    f"qnh {d(qnh)} hectopascal",
                )  # fmt: skip
                if self.level >= 2
                else f"qnh {d(qnh)}"
            )
            return [(text, kind)], Item(kind, qnh)
        if kind == "frequency":
            mhz = str(rng.randint(118, 136))
            khz = rng.choice(CHANNEL_ENDINGS)
            dec = rng.choice(("decimal", "decimal", "point"))
            num = f"{d(mhz)} {dec} {d(khz)}"
            station = self.station()
            if pilot:
                if drop:
                    feats.add("form:frequency_no_decimal")
                    num = f"{d(mhz)} {d(khz)}"
                pieces = [(num, kind)] + ([(station, None)] if rng.random() < 0.3 else [])
            else:
                verb = self._pick("contact", "contact", "monitor", "call", "go to")
                if self.level > 1 and rng.random() < 0.25:
                    feats.add("form:frequency_no_decimal")
                    num = f"{d(mhz)} {d(khz)}"
                pieces = [(f"{verb} {station}", None), (num, kind)]
                if rng.random() < 0.15:
                    pieces = [(f"{verb} {station} on", None), (num, kind)]
            return pieces, Item(kind, f"{mhz}.{khz}")
        if kind == "runway":
            words, value = self._runway()
            leads = ["expect ils approach", "expect", "vacate", "cross", "backtrack",
                     "established localizer", "established ils", "fully established", "for",
                     "ready for departure", "via", "continue approach", ""]  # fmt: skip
            if self.level > 1:
                leads += VFR_RUNWAY_LEADS
            lead = rng.choice(leads)
            if (self.level > 1 and lead.startswith(NO_KEYWORD_LEADS)
                    and rng.random() < 0.5):  # fmt: skip
                feats.add("form:runway_no_keyword")
                return [(f"{lead} {words}", kind)], Item(kind, value)
            if self.level > 1 and not lead and rng.random() < 0.4:
                # "runway two five, cleared touch and go": the instruction follows the runway
                feats.add("form:runway_then_circuit")
                return [(f"runway {words}", kind), (rng.choice(CIRCUIT_TAILS), None)], Item(
                    kind, value
                )
            pieces = ([(lead, None)] if lead else []) + [(f"runway {words}", kind)]
            return pieces, Item(kind, value)
        if kind in ("cleared_to_land", "cleared_for_takeoff", "line_up", "hold_short"):
            words, value = self._runway()
            no_runway = rng.random() < (0.25 if pilot else 0.1)
            rwy = (
                ""
                if no_runway
                else self._pick(
                    f"runway {words}", f"runway {words}", words if pilot else f"runway {words}"
                )
            )
            if kind == "cleared_for_takeoff" and self.level > 1 and rng.random() < 0.25:
                return self._line_up_and_takeoff(words, value, no_runway, feats)
            if kind == "hold_short" and self.level > 1 and rng.random() < 0.3:
                # a named holding point: "taxi to holding point bravo (runway two five)"
                feats.add("form:named_holding_point")
                point = spell(rng.choice("abcdefghkmnpswz"))
                if rng.random() < 0.4:
                    point += " " + PLAIN_DIGITS[rng.randint(1, 9)]
                verb = self._pick(
                    "taxi to holding point", "holding point", "taxi to holding position",
                    "hold at holding point",
                )  # fmt: skip
                text = f"{verb} {point}" + ("" if no_runway else f" runway {words}")
                return [(text, kind)], Item(kind, "" if no_runway else value)
            phrase = {
                "cleared_to_land": self._pick("cleared to land", "clear to land", "clear land"),
                "cleared_for_takeoff": self._pick(
                    "cleared for takeoff",
                    "clear for takeoff",
                    "clear to takeoff",
                    "cleared takeoff",
                    "clear takeoff",
                ),
                "line_up": self._pick(
                    "line up and wait", "line up", "lining up", "line up and wait"
                )
                if pilot
                else self._pick("line up and wait", "line up"),
                "hold_short": self._pick(
                    "hold short",
                    "hold short of",
                    "holding short of",
                    "holding point",
                    "holding position",
                    "approaching holding point",
                    "continue to holding point",
                    "taxi to holding point",
                    "stop at holding point",
                ),
            }[kind]
            if not rwy:
                text = phrase
            elif kind == "hold_short" or rng.random() < 0.5:
                text = f"{phrase} {rwy}"
            elif self.level > 1 and kind != "line_up" and rng.random() < 0.3:
                # "runway one three, wind ..., cleared to land": the runway stands apart
                feats.add("form:runway_apart")
                return [
                    (f"runway {words}", "runway"),
                    (self.distractor(wind=True), None),
                    (phrase, kind),
                ], Item(kind, value)
            else:
                text = f"{rwy} {phrase}"
            return [(text, kind)], Item(kind, "" if no_runway else value)
        if kind == "direct_to":
            wpt = (
                self._novel_word()
                if rng.random() < 0.7
                else rng.choice(
                    ("lomki", "baltu", "vozice", "rapet", "okiba", "dobak", "gamli", "nermi")
                )
            )
            text = (
                self._pick(f"direct {wpt}", f"proceeding direct {wpt}")
                if pilot
                else self._pick(
                    f"proceed direct {wpt}",
                    f"direct to {wpt}",
                    f"fly direct {wpt}",
                    f"cleared direct {wpt}",
                )
            )
            return [(text, kind)], Item(kind, wpt)
        raise ValueError(kind)

    def _line_up_and_takeoff(self, words, value, no_runway, feats):
        """ "line up and cleared for takeoff runway one three": two clearances, one runway."""
        feats.add("form:line_up_and_takeoff")
        rwy = "" if no_runway else f" runway {words}"
        takeoff = self._pick("cleared for takeoff", "clear for takeoff", "takeoff", "clear takeoff")
        if self.rng.random() < 0.5:
            pieces = [
                ("line up", "line_up"),
                ("and", None),
                (f"{takeoff}{rwy}", "cleared_for_takeoff"),
            ]
        else:
            pieces = [(f"line up{rwy}", "line_up"), ("and", None), (takeoff, "cleared_for_takeoff")]
        v = "" if no_runway else value
        return pieces, [Item("line_up", v), Item("cleared_for_takeoff", v)]

    def station(self) -> str:
        """A station name: a known one, or at level 2+ a city never heard before."""
        if self.level > 1 and self.rng.random() < 0.35:
            return f"{self._novel_word()} {self.rng.choice(STATION_KINDS)}"
        return self.rng.choice(STATIONS)

    def _wind(self) -> str:
        rng, d = self.rng, self._digits
        direction = f"{rng.randrange(10, 370, 10):03d}"
        speed = rng.randint(2, 25)
        text = f"wind {d(direction)} degrees {d(str(speed))} knots"
        if self.level == 1:
            return text
        extra = rng.random()
        if extra < 0.2:
            text += f" gusting {d(str(speed + rng.randint(5, 15)))}"
        elif extra < 0.35:
            lo = (int(direction) - rng.randrange(30, 70, 10)) % 360 or 360
            hi = (int(direction) + rng.randrange(30, 70, 10)) % 360 or 360
            text += f" variable between {d(f'{lo:03d}')} and {d(f'{hi:03d}')} degrees"
        elif extra < 0.45:
            text += (
                f" maximum {d(str(speed + rng.randint(3, 10)))} minimum {d(str(max(1, speed - 4)))}"
            )
        elif extra < 0.55:
            text = f"wind {d(direction)} at {d(str(speed))}"
        return text

    def distractor(self, wind: bool = False) -> str:
        rng, d = self.rng, self._digits
        if wind:
            return self._pick(self._wind(), self._wind(), "wind calm", "wind is calm")
        if self.level > 1 and rng.random() < 0.45:
            return self._wider_distractor()
        return rng.choice([
            lambda: f"wind {d(f'{rng.randrange(10, 370, 10):03d}')} degrees "
                    f"{d(str(rng.randint(2, 25)))} knots",
            lambda: f"information {LETTERS[rng.choice('abcdefghkl')]}",
            lambda: f"taxi via {spell(rng.choice('abcdfkl'))} and {spell(rng.choice('mnprst'))}",
            lambda: f"stand {d(str(rng.randint(10, 60)))}",
            lambda: f"{PLAIN_DIGITS[rng.randint(3, 9)]} miles final",
            lambda: f"number {PLAIN_DIGITS[rng.randint(1, 3)]}",
            lambda: "continue approach",
            lambda: "report established",
            lambda: "report ready for departure",
            lambda: "no speed restrictions",
            lambda: "vacate left when able",
            lambda: f"traffic {PLAIN_DIGITS[rng.randint(1, 9)]} miles ahead",
            lambda: "with you",
            lambda: "ready for departure",
            lambda: f"visibility {d(str(rng.randint(2, 10)))} kilometers",
            lambda: "wind calm",
            lambda: "time check",
            lambda: self._pick("startup approved", "pushback approved", "push back approved"),
            lambda: "break break",
            lambda: f"behind landing {self._pick('traffic', 'boeing', 'airbus')} on short final",
            lambda: f"departing traffic {d(self._pick('737', '320', '319', '380'))}",
            lambda: f"expect {PLAIN_DIGITS[rng.randint(2, 9)]} minutes delay",
            lambda: f"vacate via {spell(rng.choice('abcdfghklp'))}",
            lambda: "runway in sight",
            lambda: "squawk ident",
        ])()  # fmt: skip

    def _wider_distractor(self) -> str:
        """Things said on any frequency that carry numbers or keywords but no clearance."""
        rng, d = self.rng, self._digits
        clock = (
            PLAIN_DIGITS[rng.randint(1, 9)]
            if rng.random() < 0.8
            else self._pick("ten", "eleven", "twelve")
        )
        thousands, rest = divmod(rng.randrange(1000, 12000, 500), 1000)
        alt = f"{say(str(thousands), plain=True)} thousand" + (
            f" {PLAIN_DIGITS[rest // 100]} hundred" if rest else ""
        )
        return rng.choice([
            lambda: self._wind(),
            lambda: f"traffic at your {clock} o clock {PLAIN_DIGITS[rng.randint(2, 9)]} miles "
                    f"{self._pick('opposite direction', 'same direction', 'crossing')} "
                    f"{alt} feet",
            lambda: f"traffic {self._pick('a cessna', 'a helicopter', 'an airbus', 'a glider')} "
                    f"{alt} feet not verified",
            lambda: f"passing {alt} feet",
            lambda: f"passing flight level {d(f'{rng.randrange(50, 400, 10):03d}')}",
            lambda: f"{PLAIN_DIGITS[rng.randint(2, 9)]} miles from {self._novel_word()}",
            lambda: "frequency change approved",
            lambda: f"report {spell(rng.choice('ehnswz'))} {PLAIN_DIGITS[rng.randint(1, 3)]}",
            lambda: f"enter control zone via {self._pick('route', 'sector')} "
                    f"{spell(rng.choice('ehnswz'))}",
            lambda: f"cleared to {self._novel_word()} via {self._novel_word()} "
                    f"{PLAIN_DIGITS[rng.randint(1, 9)]} {LETTERS[rng.choice('abcdekmnpqrstz')]} "
                    "departure",
            lambda: self._pick("orbit approved", "hold position", "go around", "report final",
                               "report downwind", "number one", "number two", "continue approach",
                               "touch and go approved", "full stop", "request startup",
                               "ready for taxi", "startup approved", "squawk vfr", "identified",
                               "radar contact", "say again", "stand by", "negative"),
            lambda: f"time {d(str(rng.randint(0, 59)).zfill(2))}",
            lambda: f"information {LETTERS[rng.choice('abcdefghklmnopqrs')]} received",
            lambda: f"we are {PLAIN_DIGITS[rng.randint(2, 9)]} persons on board",
            lambda: f"{self.station()} {self._pick('hello', 'good day', 'bonjour')}",
        ])()  # fmt: skip

    # -- whole transmissions ------------------------------------------------------------
    def example(self) -> Example:
        rng, feats = self.rng, set()
        pilot = rng.random() < 0.45
        role = "pilot" if pilot else "controller"
        feats.add(f"role:{role}")
        weights = {k: w * self.focus.get(f"kind:{k}", 1.0) for k, w in KIND_WEIGHTS.items()}
        max_items = 2 if self.level == 1 else 3
        n_items = rng.choices(range(max_items + 1), weights=[1, 6, 3, 1][: max_items + 1])[0]
        kinds: list[str] = []
        while len(kinds) < n_items:
            k = rng.choices(list(weights), weights=list(weights.values()))[0]
            # one runway per transmission, as on a real frequency
            if k not in kinds and not (k in RUNWAY_KINDS and RUNWAY_KINDS & set(kinds)):
                kinds.append(k)

        cs_text = self.callsign(feats) if rng.random() < 0.92 else ""
        # "heavy" / "super" follow airline callsigns of big aircraft; they aren't the callsign
        heavy = self.level > 1 and cs_text and "callsign:airline" in feats and rng.random() < 0.12
        cs_piece = [(cs_text, "callsign")] + (
            [(self._pick("heavy", "heavy", "super"), None)] if heavy else []
        )
        items: list[Item] = []
        body: list = []
        for idx, k in enumerate(kinds):
            feats.add(f"kind:{k}")
            pieces, made = self.item(k, pilot, feats)
            if idx and (conn := rng.choice(CONNECTORS)):
                body.append((conn, None))
            if not pilot and self._p("correction"):
                feats.add("noise:correction")
                wrong, _ = self.item(k, pilot, set())
                body += [(text, None) for text, _ in wrong] + [("correction", None)]
            body += pieces
            items += made if isinstance(made, list) else [made]
        if self._p("distractor") or not kinds:
            feats.add("noise:distractor")
            body.insert(rng.randint(0, len(body)), (self.distractor(), None))

        pieces: list = []
        if self._p("station"):
            feats.add("noise:station")
            pieces.append((self.station(), None))
        if pilot:
            if rng.random() < 0.25:
                pieces.append((rng.choice(ACKS), None))
            if cs_text and rng.random() < 0.3:
                pieces += cs_piece + body
            else:
                pieces += body + (cs_piece if cs_text else [])
        else:
            pieces += cs_piece if cs_text else []
            if self._p("greeting"):
                feats.add("noise:greeting")
                pieces.append((rng.choice(GREETINGS), None))
            pieces += body
        if self._p("greeting"):
            feats.add("noise:greeting")
            pieces.append((rng.choice(SIGNOFFS), None))

        # owner: which labeled piece a token belongs to (0 is the callsign, -1 none)
        tokens, tags, owner = [], [], []
        n_spans = 0
        for text, label in pieces:
            toks = canonical(text)
            if not toks:
                continue
            if label is None:
                ref = -1
            elif label == "callsign":
                ref = 0
            else:
                n_spans += 1
                ref = n_spans
            for j, t in enumerate(toks):
                tokens.append(t)
                tags.append("O" if ref < 0 else f"{'B' if j == 0 else 'I'}-{label}")
                owner.append(ref)
        tokens, tags = self._noise(tokens, tags, owner, feats)
        truth = Clearance(" ".join(canonical(cs_text)) or None, items)
        return Example(tokens, tags, decode(tokens, tags), role, feats, truth)

    # -- recognizer and radio noise -----------------------------------------------------
    def _noise(self, tokens, tags, owner, feats):
        rng = self.rng
        if self._p("hesitation"):
            feats.add("noise:hesitation")
            for _ in range(rng.choice((1, 1, 2))):
                i = rng.randint(1, len(tokens)) if tokens else 0
                inside = 0 < i < len(tokens) and tags[i].startswith("I-")
                tokens.insert(i, rng.choice(sorted(FILLERS)))
                tags.insert(i, tags[i] if inside else "O")
                owner.insert(i, owner[i] if inside else -1)
        if self._p("repeat") and tokens:
            feats.add("noise:repeat")
            i = rng.randrange(len(tokens))
            if tags[i] == "O":
                tokens.insert(i, tokens[i])
                tags.insert(i, "O")
                owner.insert(i, -1)
        if self._p("misheard"):
            feats.add("noise:misheard")
            for i, t in enumerate(tokens):
                if len(t) > 3 and rng.random() < 0.12:
                    tokens[i] = _mishear(t, rng)
        if self._p("truncate") and len(tokens) > 4:
            feats.add("noise:truncate")
            tokens, tags, owner = self._truncate(tokens, tags, owner)
        return tokens, tags

    def _truncate(self, tokens, tags, owner):
        """Cut the clip off at the start or the end, often mid-word. A span that was cut keeps
        its label only if it still says the same thing ("...ared to land runway 1 3" still
        clears to land on 13, "flight level 1 0" is not FL100); a callsign survives with two
        or more tokens left (pilots and controllers abbreviate them anyway)."""
        rng = self.rng
        before = {ref: _span_item(tokens, tags, owner, ref) for ref in set(owner) - {-1}}
        cut = rng.randint(1, 3)
        at_start = rng.random() < 0.5
        gone = set(range(cut)) if at_start else set(range(len(tokens) - cut, len(tokens)))
        hit = {owner[i] for i in gone}
        edge = cut if at_start else len(tokens) - cut - 1
        if rng.random() < 0.6:
            hit.add(owner[edge])
            tokens[edge] = _fragment(tokens[edge], rng, keep_end=at_start)
            tags[edge], owner[edge] = "O", -1
        idx = [i for i in range(len(tokens)) if i not in gone]
        tokens, tags, owner = (
            [tokens[i] for i in idx],
            [tags[i] for i in idx],
            [owner[i] for i in idx],
        )
        for i, o in enumerate(owner):  # the first surviving token of a cut span opens it again
            if o >= 0 and (i == 0 or owner[i - 1] != o):
                tags[i] = "B" + tags[i][1:]
        for ref in hit - {-1}:
            if ref == 0 and sum(o == 0 for o in owner) >= 2:
                continue
            if (
                ref > 0
                and before[ref] is not None
                and (_span_item(tokens, tags, owner, ref) == before[ref])
            ):
                continue
            for i, o in enumerate(owner):
                if o == ref:
                    tags[i], owner[i] = "O", -1
        return tokens, tags, owner


def _span_item(tokens, tags, owner, ref):
    toks = [t for t, o in zip(tokens, owner, strict=True) if o == ref and t not in FILLERS]
    label = next((g[2:] for g, o in zip(tags, owner, strict=True) if o == ref), None)
    return item_from_span(label, toks) if label and label != "callsign" and toks else None


def _spoken(token: str) -> str:
    if token.isdigit():
        return PLAIN_DIGITS[int(token)]
    return LETTERS.get(token, token)


def _fragment(token: str, rng: random.Random, keep_end: bool) -> str:
    word = _spoken(token)
    n = rng.randint(1, max(1, len(word) - 1))
    return word[-n:] if keep_end else word[:n]


def _mishear(word: str, rng: random.Random) -> str:
    op = rng.random()
    i = rng.randrange(len(word))
    if op < 0.4:
        return word[:i] + word[i + 1 :]
    if op < 0.7:
        return word[:i] + rng.choice("aeioulnrst") + word[i + 1 :]
    return word[:i] + rng.choice("aeioulnrst") + word[i:]


def dataset(level: int, n: int, seed: int | str, focus: dict[str, float] | None = None):
    gen = Generator(level, random.Random(seed), focus)
    return [gen.example() for _ in range(n)]
