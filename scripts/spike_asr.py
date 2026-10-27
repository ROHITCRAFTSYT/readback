"""Day-1 go/no-go: how well does Muse Voice Transcribe hear real ATC radio?

Runs a fixed random sample of clips from the open ATC test sets through Voice Transcribe under
three keyword conditions and reports word, digit and callsign accuracy:

  none         no biasing
  phraseology  ICAO alphabet and standard phraseology
  context      phraseology + the callsigns ADS-B would show on frequency (the speaking
               aircraft hidden among distractors from the same recording)

Per-clip results are cached in results/raw/ (gitignored), so reruns only call the API for
clips that are missing. The aggregate report goes to results/spike-asr.md.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from readback.asr import TranscribeError, VoiceTranscribe
from readback.audio import to_pcm16_wav
from readback.datasets import DATASETS, Clip, load_clips
from readback.keywords import PHRASEOLOGY, callsign_keywords
from readback.metrics import contains, digit_errors, discover_designators, extract_callsign, wer
from readback.normalize import canonical

ROOT = Path(__file__).resolve().parent.parent
CONDITIONS = ("none", "phraseology", "context")
# Readback checking lives or dies on callsigns and numbers, so those set the bar.
GO = {"callsign_acc": 0.70, "digit_err": 0.25}
CAUTION = {"callsign_acc": 0.50, "digit_err": 0.40}


def sample(clips: list[Clip], n: int, seed: int) -> list[Clip]:
    usable = [c for c in clips if 1.0 <= c.duration <= 30.0 and c.text.strip()]
    return random.Random(seed).sample(usable, min(n, len(usable)))


def keywords_for(
    clip: Clip,
    condition: str,
    callsign: list[str] | None,
    pools: dict,
    n_distractors: int,
    seed: int,
) -> list[str] | None:
    if condition == "none":
        return None
    if condition == "phraseology":
        return list(PHRASEOLOGY)
    rng = random.Random(f"{seed}:{clip.id}")
    pool = pools[(clip.dataset, clip.group)]
    if len(pool) <= n_distractors:
        pool = pool + pools[(clip.dataset, "*")]
    if callsign is None:
        # ADS-B doesn't know who is talking; without a callsign in the clip, send distractors only.
        callsign = rng.choice(pool) if pool else []
    return list(PHRASEOLOGY) + callsign_keywords(callsign, pool, n_distractors, rng)


def run_one(asr: VoiceTranscribe | None, clip: Clip, condition: str, keywords, cache: Path):
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if asr is None:  # dry run: score the reference against itself to validate the pipeline
        record = {"hypothesis": clip.text, "latency_s": 0.0, "error": None}
    else:
        start = time.perf_counter()
        try:
            result = asr.transcribe(to_pcm16_wav(clip.wav), keywords=keywords)
            record = {"hypothesis": result.transcript, "error": None}
        except TranscribeError as exc:
            record = {"hypothesis": "", "error": str(exc)}
        record["latency_s"] = round(time.perf_counter() - start, 3)
    record.update(id=clip.id, condition=condition, reference=clip.text, keywords=keywords)
    if asr is not None and record["error"] is None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def verdict(m: dict) -> str:
    if m["callsign_acc"] >= GO["callsign_acc"] and m["digit_err"] <= GO["digit_err"]:
        return "GO"
    if m["callsign_acc"] >= CAUTION["callsign_acc"] and m["digit_err"] <= CAUTION["digit_err"]:
        return "CAUTION"
    return "NO-GO"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--atco2", type=int, default=60, help="clips from the ATCO2 1h test set")
    ap.add_argument("--uwb", type=int, default=40, help="clips from the UWB-ATCC test split")
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--distractors", type=int, default=14, help="other callsigns per keyword list")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="no API calls; reference vs itself")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "spike-asr.md")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    conditions = [c for c in args.conditions.split(",") if c]
    if bad := set(conditions) - set(CONDITIONS):
        ap.error(f"unknown conditions: {sorted(bad)}")

    all_clips: dict[str, list[Clip]] = {}
    for name, (_, filename) in DATASETS.items():
        path = ROOT / "data" / "raw" / filename
        if not path.exists():
            print(f"missing {path}; run scripts/fetch_data.py first", file=sys.stderr)
            return 1
        all_clips[name] = load_clips(path, name)

    designators = discover_designators(c.text for cs in all_clips.values() for c in cs)
    callsigns = {
        c.id: extract_callsign(canonical(c.text), designators)
        for cs in all_clips.values()
        for c in cs
    }
    pools: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    for cs in all_clips.values():
        for c in cs:
            if (cs_tokens := callsigns[c.id]) is not None:
                pools[(c.dataset, c.group)].append(cs_tokens)
                pools[(c.dataset, "*")].append(cs_tokens)

    chosen = sample(all_clips["atco2-1h"], args.atco2, args.seed) + sample(
        all_clips["uwb-atcc"], args.uwb, args.seed
    )
    asr = None if args.dry_run else VoiceTranscribe()
    jobs = []
    for clip in chosen:
        for cond in conditions:
            kw = keywords_for(clip, cond, callsigns[clip.id], pools, args.distractors, args.seed)
            safe_id = clip.id.replace("/", "_")
            cache = ROOT / "results" / "raw" / clip.dataset / f"{safe_id}__{cond}.json"
            jobs.append((clip, cond, kw, cache))

    print(f"{len(chosen)} clips x {len(conditions)} conditions = {len(jobs)} transcriptions")
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        records = list(pool.map(lambda j: run_one(asr, *j), jobs))

    agg: dict[tuple[str, str], dict] = {}
    for (clip, cond, _, _), rec in zip(jobs, records, strict=True):
        a = agg.setdefault(
            (clip.dataset, cond),
            {"clips": 0, "w": [0, 0], "d": [0, 0], "cs": [0, 0], "lat": [], "failed": 0},
        )
        a["clips"] += 1
        if rec["error"]:
            a["failed"] += 1
            print(f"  failed {clip.id} [{cond}]: {rec['error']}", file=sys.stderr)
            continue
        ref, hyp = canonical(clip.text), canonical(rec["hypothesis"])
        for key, (e, n) in (("w", wer(ref, hyp)), ("d", digit_errors(ref, hyp))):
            a[key][0] += e
            a[key][1] += n
        if (cs_tokens := callsigns[clip.id]) is not None:
            a["cs"][1] += 1
            a["cs"][0] += contains(cs_tokens, hyp)
        a["lat"].append(rec["latency_s"])

    rows = []
    for (ds, cond), a in sorted(agg.items(), key=lambda kv: (kv[0][0], CONDITIONS.index(kv[0][1]))):
        m = {
            "dataset": ds,
            "condition": cond,
            "clips": a["clips"],
            "failed": a["failed"],
            "wer": a["w"][0] / max(a["w"][1], 1),
            "digit_err": a["d"][0] / max(a["d"][1], 1),
            "callsign_acc": a["cs"][0] / max(a["cs"][1], 1),
            "callsign_n": a["cs"][1],
            "latency": statistics.median(a["lat"]) if a["lat"] else 0.0,
        }
        m["verdict"] = verdict(m)
        rows.append(m)

    atco2 = [r for r in rows if r["dataset"] == "atco2-1h"]
    best = max(atco2, key=lambda r: (r["callsign_acc"], -r["digit_err"]), default=None)
    lines = [
        "# Day-1 go/no-go: Muse Voice Transcribe on ATC radio",
        "",
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, seed {args.seed}, "
        f"{args.distractors} distractor callsigns, model `muse-voice-transcribe-1.0`"
        + (" (**dry run: reference scored against itself**)" if args.dry_run else ""),
        "",
        "| Dataset | Keywords | Clips | Failed | WER | Digit error | Callsign accuracy "
        "| Median latency | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['dataset']} | {r['condition']} | {r['clips']} | {r['failed']} "
            f"| {r['wer']:.1%} | {r['digit_err']:.1%} "
            f"| {r['callsign_acc']:.1%} (n={r['callsign_n']}) | {r['latency']:.2f}s "
            f"| {r['verdict']} |"
        )
    lines += [
        "",
        f"**Decision (ATCO2, real VHF radio, best keyword condition): "
        f"{best['verdict'] if best else 'n/a'}**"
        + (f" with `{best['condition']}` keywords." if best else ""),
        "",
        f"- GO: callsign accuracy at least {GO['callsign_acc']:.0%} and digit error at most "
        f"{GO['digit_err']:.0%}. CAUTION: at least {CAUTION['callsign_acc']:.0%} and at most "
        f"{CAUTION['digit_err']:.0%}. Anything else is NO-GO.",
        "- Text is compared in a canonical form (spelled letters and digits as single tokens), "
        "so `CSA1DZ` and `csa one delta zulu` match.",
        f"- Airline designators found in the references: {', '.join(sorted(designators))}.",
        "",
        "Data: ATCO2 1h test set (ATCO2 project, end-user data agreement) and UWB-ATCC "
        "(University of West Bohemia, CC BY-NC-SA 4.0). Only aggregate numbers are published.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
