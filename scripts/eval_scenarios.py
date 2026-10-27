"""Score the full monitor (radio pairing + readback check + ADS-B conformance) on simulated
scenarios with planted errors.

Each label is a planted problem: a wrong readback value, a callsign confusion, an omitted
item, an unanswered instruction, or a track deviation (level bust, levelling off short,
climbing/descending the wrong way, wrong heading, wrong squawk). An alert matches a label of
the same class for the same aircraft within a time window. Error-level alerts that match no
label are false alarms.

Transmissions are scored from their text, so this measures everything after speech
recognition. Speaker roles are inferred, as on live radio, unless --use-roles is given.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from readback import rules
from readback.adsb import KNOWN_DESIGNATORS, spoken_callsign
from readback.compare import same_callsign
from readback.monitor import Monitor
from readback.replay import replay
from readback.sim import generate

ROOT = Path(__file__).resolve().parent.parent
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
    if cls.startswith("track"):
        return -60.0, 420.0
    if cls == "no_readback":
        return -5.0, 40.0
    return -5.0, 5.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scenarios", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--use-roles", action="store_true", help="give the monitor speaker roles")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "scenario-eval.md")
    args = ap.parse_args()

    def parse(text: str):
        return rules.parse(text, KNOWN_DESIGNATORS)

    totals, hits, delays = Counter(), Counter(), []
    false_alarms, error_alerts, warning_alerts, unmatched_warnings = Counter(), 0, 0, 0
    n_tx = 0
    for k in range(args.scenarios):
        scen = generate(args.seed + k).to_dict()
        start = scen["start"]
        labels = [dict(lb, cls=label_class(lb), matched=False) for lb in scen["labels"]]
        for lb in labels:
            totals[lb["cls"]] += 1
        monitor = Monitor(parse)
        for ev, alerts, _ in replay(scen, monitor, use_roles=args.use_roles):
            n_tx += ev["type"] == "tx"
            for alert in alerts:
                cls = alert_class(alert)
                lo, hi = window(cls)
                match = next(
                    (
                        lb
                        for lb in labels
                        if not lb["matched"]
                        and lb["cls"] == cls
                        and same_callsign(alert.callsign, spoken_callsign(lb["callsign"]))
                        and lo <= alert.time - (start + lb["t"]) <= hi
                    ),
                    None,
                )
                if alert.severity == "error":
                    error_alerts += 1
                else:
                    warning_alerts += 1
                if match is not None:
                    match["matched"] = True
                    hits[cls] += 1
                    if cls.startswith("track"):
                        delays.append(alert.time - (start + match["t"]))
                elif alert.severity == "error":
                    false_alarms[cls] += 1
                else:
                    unmatched_warnings += 1

    lines = [
        "# Full monitor on simulated scenarios",
        "",
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC: {args.scenarios} scenarios (seeds "
        f"{args.seed}-{args.seed + args.scenarios - 1}), {n_tx} transmissions, rule-based "
        f"parser, speaker roles {'given' if args.use_roles else 'inferred'}.",
        "",
        "| Planted problem | Planted | Caught | Recall |",
        "|---|---|---|---|",
    ]
    for name, cls in CLASSES:
        n = totals[cls]
        lines.append(f"| {name} | {n} | {hits[cls]} | {hits[cls] / max(n, 1):.1%} |")
    caught_all = sum(hits.values())
    planted_all = sum(totals.values())
    fa = sum(false_alarms.values())
    lines += [
        f"| **All** | {planted_all} | {caught_all} | {caught_all / max(planted_all, 1):.1%} |",
        "",
        f"- Error-level alerts: {error_alerts}, of which {fa} matched no planted problem "
        f"(precision {(error_alerts - fa) / max(error_alerts, 1):.1%})."
        + (f" False alarms by class: {dict(false_alarms)}." if fa else ""),
        f"- Warning-level alerts: {warning_alerts}, of which {unmatched_warnings} matched no "
        "planted problem.",
    ]
    if delays:
        lines.append(
            f"- Track deviations were flagged a median {statistics.median(delays):.0f} s after "
            "the planted onset (onset is approximate: about 30 s after the readback for a bust)."
        )
    lines += [
        "",
        "Scenarios come from `readback.sim`: an illustrative Bengaluru approach frequency, "
        "not real aeronautical data. They use clean standard phraseology, so this checks the "
        "monitor's logic end to end; real-world accuracy is measured on recorded ATC speech "
        "(`results/readback-eval.md`, `results/spike-asr.md`).",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
