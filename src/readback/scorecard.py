"""Score a replay against the problems planted in its scenario.

Each label is a planted problem: a wrong readback value, a callsign confusion, an omitted
item, an unanswered instruction, or a track deviation. An alert catches a label of the same
class for the same aircraft within a time window; error-level alerts that catch nothing are
false errors. scripts/eval_scenarios.py scores the benchmark this way, and a replay in the web
app sends the same scorecard when it ends.
"""

from __future__ import annotations

from collections import Counter

from readback.adsb import spoken_callsign
from readback.compare import same_callsign

VERTICAL = {"level_bust", "level_off", "wrong_vertical_direction"}
READBACK_VALUES = {"flight_level", "altitude", "heading", "speed", "squawk", "qnh"}
CLASSES = [
    ("Wrong readback value", "readback-value"),
    ("Callsign confusion", "callsign"),
    ("Omitted readback item", "missing"),
    ("Unanswered instruction", "no_readback"),
    ("Vertical deviation", "track-vertical"),
    ("Heading deviation", "track-heading"),
    ("Squawk deviation", "track-squawk"),
]


def label_class(label: dict) -> str:
    if label["source"] == "track":
        return "track-vertical" if label["kind"] in VERTICAL else f"track-{label['kind']}"
    if label["kind"] in READBACK_VALUES:
        return "readback-value"
    return label["kind"]


def alert_class(alert) -> str:
    if alert.source == "track":
        return "track-vertical" if alert.kind in VERTICAL else f"track-{alert.kind}"
    if alert.kind in ("callsign", "no_readback"):
        return alert.kind
    return "missing" if alert.severity == "warning" else "readback-value"


def window(cls: str) -> tuple[float, float]:
    """Seconds before and after the planted time in which an alert counts."""
    if cls.startswith("track"):
        return -60.0, 420.0
    if cls == "no_readback":
        return -5.0, 40.0
    return -5.0, 5.0


class Scorer:
    """Matches one scenario's alerts, as they are raised, to its planted problems."""

    def __init__(self, scen: dict) -> None:
        self.start = scen["start"]
        self.labels = [
            dict(lb, cls=label_class(lb), caught=False, alert_index=None, delay_s=None)
            for lb in scen["labels"]
        ]
        self.false_alarms: Counter = Counter()  # error-level alerts that caught nothing, by class
        self.unmatched_warnings = 0

    def add(self, alert, index: int | None = None) -> dict | None:
        """Score one alert (index: its place in the replay's alerts). Returns the label it
        caught, or None."""
        cls = alert_class(alert)
        lo, hi = window(cls)
        match = next(
            (
                lb
                for lb in self.labels
                if not lb["caught"]
                and lb["cls"] == cls
                and same_callsign(alert.callsign, spoken_callsign(lb["callsign"]))
                and lo <= alert.time - (self.start + lb["t"]) <= hi
            ),
            None,
        )
        if match is not None:
            delay = alert.time - (self.start + match["t"])
            match.update(caught=True, alert_index=index, delay_s=delay)
        elif alert.severity == "error":
            self.false_alarms[cls] += 1
        else:
            self.unmatched_warnings += 1
        return match

    def score(self) -> dict:
        keys = ("t", "callsign", "source", "kind", "detail", "cls", "caught", "alert_index")
        return {
            "planted": len(self.labels),
            "caught": sum(lb["caught"] for lb in self.labels),
            "false_errors": sum(self.false_alarms.values()),
            "unmatched_warnings": self.unmatched_warnings,
            "labels": [
                {
                    **{k: lb.get(k) for k in keys},
                    "delay_s": None if lb["delay_s"] is None else round(lb["delay_s"], 1),
                }
                for lb in self.labels
            ],
        }
