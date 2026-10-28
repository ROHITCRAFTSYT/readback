"""Score the full monitor (radio pairing + readback check + ADS-B conformance) on simulated
scenarios with planted errors.

Each label is a planted problem: a wrong readback value, a callsign confusion, an omitted
item, an unanswered instruction, or a track deviation (level bust, levelling off short,
climbing/descending the wrong way, wrong heading, wrong squawk). An alert matches a label of
the same class for the same aircraft within a time window. Error-level alerts that match no
label are false alarms.

Transmissions are scored from their text, so this measures everything after speech
recognition. Speaker roles are inferred, as on live radio, unless --use-roles is given.

With --audio every transmission is also spoken (Windows speech synthesis + VHF radio
effect), transcribed by Muse Voice Transcribe with the scenario's ADS-B callsigns as
keywords, and the monitor runs again on the transcripts; the report puts both side by side
(results/scenario-audio-eval.md). Transcripts are cached in results/raw/asr-scenarios/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from readback import rules
from readback.adsb import KNOWN_DESIGNATORS, spoken_callsign
from readback.compare import same_callsign
from readback.keywords import PHRASEOLOGY, fit
from readback.metrics import wer
from readback.monitor import Monitor
from readback.normalize import canonical, spoken
from readback.replay import replay
from readback.sim import generate
from readback.tagger.decode import tidy

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


def parse(text: str):
    return tidy(rules.parse(text, KNOWN_DESIGNATORS))  # as the live server does


def score(scenarios: list[dict], use_roles: bool) -> dict:
    """Run the monitor over the scenarios and match its alerts to the planted problems."""
    totals, hits, delays = Counter(), Counter(), []
    false_alarms, error_alerts, warning_alerts, unmatched_warnings = Counter(), 0, 0, 0
    n_tx = 0
    for scen in scenarios:
        start = scen["start"]
        labels = [dict(lb, cls=label_class(lb), matched=False) for lb in scen["labels"]]
        for lb in labels:
            totals[lb["cls"]] += 1
        monitor = Monitor(parse)
        for ev, alerts, _ in replay(scen, monitor, use_roles=use_roles):
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
    return {
        "totals": totals,
        "hits": hits,
        "delays": delays,
        "false_alarms": false_alarms,
        "error_alerts": error_alerts,
        "warning_alerts": warning_alerts,
        "unmatched_warnings": unmatched_warnings,
        "n_tx": n_tx,
    }


def summary(r: dict) -> list[str]:
    fa = sum(r["false_alarms"].values())
    lines = [
        f"- Error-level alerts: {r['error_alerts']}, of which {fa} matched no planted problem "
        f"(precision {(r['error_alerts'] - fa) / max(r['error_alerts'], 1):.1%})."
        + (f" False alarms by class: {dict(r['false_alarms'])}." if fa else ""),
        f"- Warning-level alerts: {r['warning_alerts']}, of which {r['unmatched_warnings']} "
        "matched no planted problem.",
    ]
    if r["delays"]:
        lines.append(
            f"- Track deviations were flagged a median {statistics.median(r['delays']):.0f} s "
            "after the planted onset (onset is approximate: about 30 s after the readback for "
            "a bust)."
        )
    return lines


def transcribe(scenarios: list[dict]) -> tuple[list[dict], dict]:
    """Speak, radio-filter and transcribe every transmission. Returns the scenarios with the
    transcripts in place of the scripted text, and the speech-recognition stats."""
    import numpy as np
    from dotenv import load_dotenv

    from readback.asr import TranscribeError, VoiceTranscribe, drop_repeats
    from readback.radio import SapiTTS, on_frequency

    load_dotenv(ROOT / ".env")
    asr, tts = VoiceTranscribe(), SapiTTS()
    errors = words = failed = 0
    latencies, out = [], []
    for scen in scenarios:
        cache = ROOT / "results" / "raw" / "asr-scenarios" / f"{scen['name']}.json"
        done = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        # ADS-B shows every aircraft in range; their callsigns are the keywords.
        states = (ev["state"]["callsign"] for ev in scen["events"] if ev["type"] == "state")
        keywords = list(PHRASEOLOGY) + [
            fit(spoken(spoken_callsign(c).split())) for c in sorted(set(states))
        ]
        events = []
        for i, ev in enumerate(scen["events"]):
            if ev["type"] != "tx":
                events.append(ev)
                continue
            if ev["id"] not in done:
                rng = np.random.default_rng([scen["seed"], i])  # same noise on every rerun
                wav = on_frequency(tts, ev["text"], ev["role"], rng)
                t0 = time.perf_counter()
                try:
                    hyp = asr.transcribe(wav, keywords=keywords).transcript
                    done[ev["id"]] = {"text": hyp, "latency_s": round(time.perf_counter() - t0, 3)}
                except TranscribeError as exc:
                    print(f"  failed {scen['name']} {ev['id']}: {exc}")
                    failed += 1
            hyp = drop_repeats(done.get(ev["id"], {}).get("text", ""))
            if ev["id"] in done:
                latencies.append(done[ev["id"]]["latency_s"])
            e, n = wer(canonical(ev["text"]), canonical(hyp))
            errors, words = errors + e, words + n
            events.append(dict(ev, text=hyp, scripted=ev["text"]))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(done, indent=1), encoding="utf-8")
        out.append(dict(scen, events=events))
        print(f"{scen['name']}: {len(done)} transmissions transcribed")
    stats = {
        "wer": errors / max(words, 1),
        "failed": failed,
        "median_latency_s": statistics.median(latencies) if latencies else 0.0,
    }
    return out, stats


def recall_rows(text: dict, audio: dict | None) -> list[str]:
    def cell(hits: int, n: int) -> str:
        return f"{hits} ({hits / max(n, 1):.1%})"

    rows = []
    for name, cls in [*CLASSES, ("**All**", None)]:
        if cls is None:
            n, ht = sum(text["totals"].values()), sum(text["hits"].values())
            ha = sum(audio["hits"].values()) if audio else 0
        else:
            n, ht = text["totals"][cls], text["hits"][cls]
            ha = audio["hits"][cls] if audio else 0
        rows.append(f"| {name} | {n} | {cell(ht, n)} |" + (f" {cell(ha, n)} |" if audio else ""))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scenarios", type=int, default=None, help="default 200, or 6 with --audio")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--use-roles", action="store_true", help="give the monitor speaker roles")
    ap.add_argument("--audio", action="store_true", help="also run on transcribed radio audio")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    n = args.scenarios or (6 if args.audio else 200)
    name = "scenario-audio-eval.md" if args.audio else "scenario-eval.md"
    out = args.out or ROOT / "results" / name

    scenarios = [dict(generate(args.seed + k).to_dict(), seed=args.seed + k) for k in range(n)]
    text = score(scenarios, args.use_roles)
    run = (
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC: {n} scenarios (seeds {args.seed}-"
        f"{args.seed + n - 1}), {text['n_tx']} transmissions, rule-based parser, speaker roles "
        f"{'given' if args.use_roles else 'inferred'}."
    )

    if not args.audio:
        lines = [
            "# Full monitor on simulated scenarios",
            "",
            run,
            "",
            "| Planted problem | Planted | Caught |",
            "|---|---|---|",
            *recall_rows(text, None),
            "",
            *summary(text),
            "",
            "Scenarios come from `readback.sim`: an illustrative Bengaluru approach frequency, "
            "not real aeronautical data. They use clean standard phraseology, so this checks the "
            "monitor's logic end to end; real-world accuracy is measured on recorded ATC speech "
            "(`results/readback-eval.md`, `results/spike-asr.md`).",
        ]
    else:
        heard, asr = transcribe(scenarios)
        audio = score(heard, args.use_roles)
        lines = [
            "# Full monitor on simulated radio audio",
            "",
            run,
            "",
            "Every transmission was spoken by Windows speech synthesis (one voice for the "
            "controller, another for pilots), band-limited to 300-3000 Hz with VHF noise at "
            "10-20 dB SNR, and transcribed by `muse-voice-transcribe-1.0` with the scenario's "
            "ADS-B callsigns as keywords, its stutters dropped (`asr.drop_repeats`): "
            f"WER {asr['wer']:.1%}, median latency "
            f"{asr['median_latency_s']:.2f} s, {asr['failed']} failed calls.",
            "",
            "| Planted problem | Planted | Caught from the script | Caught from the audio |",
            "|---|---|---|---|",
            *recall_rows(text, audio),
            "",
            "From the script:",
            "",
            *summary(text),
            "",
            "From the audio:",
            "",
            *summary(audio),
            "",
            "Synthetic speech is cleaner than real radio (no accents, no clipped words, no "
            "overlapping transmissions), so this shows the pipeline working end to end, not "
            "real-world accuracy; `results/spike-asr.md` measures Voice Transcribe on recorded "
            "ATC radio.",
        ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
