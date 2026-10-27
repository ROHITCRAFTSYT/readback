"""Evaluate readback error detection on real controller/pilot exchanges (text level).

1. Mine instruction/readback pairs from UWB-ATCC, whose clips are labeled controller or pilot.
2. Plant one readback error in each pair where possible: change a digit of a value the pilot
   echoed (flight level, heading, squawk, frequency, runway...) or a character of the
   callsign. These are errors by construction, so recall is measured exactly.
3. Run a parser + the comparator on the real pairs and on the planted ones.

Precision is approximate: an unmodified real pair that gets flagged counts as a false alarm,
but some of those are genuine readback errors or transcription quirks in the corpus. Flagged
real pairs are written to results/raw/ (gitignored) for manual review.

This measures parsing and comparison on reference transcripts, i.e. with perfect speech
recognition. The audio path is measured separately by spike_asr.py.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from readback import rules
from readback.compare import compare
from readback.datasets import DATASETS, load_transcripts
from readback.llm import MuseChat, SparkParser
from readback.metrics import discover_designators
from readback.pairs import inject_error, mine_pairs

ROOT = Path(__file__).resolve().parent.parent


def _flagged(findings) -> bool:
    return any(f.severity == "error" for f in findings)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--parser", choices=["rules", "spark", "both"], default="rules")
    ap.add_argument("--limit", type=int, default=0, help="evaluate a random subset of pairs")
    ap.add_argument("--concurrency", type=int, default=4, help="parallel Muse Spark calls")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "readback-eval.md")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    transcripts = {}
    for name, (_, filename) in DATASETS.items():
        path = ROOT / "data" / "raw" / filename
        if not path.exists():
            print(f"missing {path}; run scripts/fetch_data.py first", file=sys.stderr)
            return 1
        transcripts[name] = load_transcripts(path, name)
    designators = discover_designators(t.text for ts in transcripts.values() for t in ts)

    def rule_parse(text: str):
        return rules.parse(text, designators)

    pairs = mine_pairs(transcripts["uwb-atcc"], rule_parse)
    if args.limit:
        pairs = random.Random(args.seed).sample(pairs, min(args.limit, len(pairs)))
    planted = []
    for p in pairs:
        rng = random.Random(f"{args.seed}:{p.instruction.id}")
        if (res := inject_error(p.instruction.text, p.readback.text, designators, rng)) is not None:
            planted.append((p, *res))
    print(f"{len(pairs)} real pairs, {len(planted)} with a planted error")

    parsers = {}
    if args.parser in ("rules", "both"):
        parsers["rules"] = (rule_parse, 1)
    if args.parser in ("spark", "both"):
        chat = MuseChat()
        parsers[chat.model] = (SparkParser(chat, ROOT / "results" / "raw" / "llm-cache"), 0)

    rows = []
    for name, (parse, workers) in parsers.items():
        texts = {p.instruction.text for p in pairs} | {p.readback.text for p in pairs}
        texts |= {text for _, text, _ in planted}
        texts = sorted(texts)
        with ThreadPoolExecutor(max_workers=workers or args.concurrency) as pool:
            parsed = dict(zip(texts, pool.map(parse, texts), strict=True))

        real_flags, finding_types, review = 0, Counter(), []
        for p in pairs:
            findings = compare(parsed[p.instruction.text], parsed[p.readback.text])
            finding_types.update(f.type for f in findings)
            if _flagged(findings):
                real_flags += 1
                review.append(
                    {
                        "instruction": p.instruction.text,
                        "readback": p.readback.text,
                        "findings": [f.__dict__ for f in findings],
                    }
                )

        caught, by_type, total_type = 0, Counter(), Counter()
        subset_false_alarms = 0
        for p, text, label in planted:
            total_type[label["type"]] += 1
            if _flagged(compare(parsed[p.instruction.text], parsed[text])):
                caught += 1
                by_type[label["type"]] += 1
            if _flagged(compare(parsed[p.instruction.text], parsed[p.readback.text])):
                subset_false_alarms += 1

        recall = caught / max(len(planted), 1)
        precision = caught / max(caught + subset_false_alarms, 1)
        rows.append(
            {
                "parser": name,
                "pairs": len(pairs),
                "real_flag_rate": real_flags / max(len(pairs), 1),
                "planted": len(planted),
                "recall": recall,
                "recall_value": by_type["value"] / max(total_type["value"], 1),
                "recall_callsign": by_type["callsign"] / max(total_type["callsign"], 1),
                "precision": precision,
                "f1": 2 * precision * recall / max(precision + recall, 1e-9),
                "missing_warnings": finding_types["missing"],
                "n_value": total_type["value"],
                "n_callsign": total_type["callsign"],
            }
        )
        review_path = ROOT / "results" / "raw" / f"flagged-{name}.jsonl"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("\n".join(json.dumps(r) for r in review), encoding="utf-8")
        print(f"{name}: {len(review)} flagged real pairs written to {review_path}")

    lines = [
        "# Readback error detection: text-level evaluation",
        "",
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, seed {args.seed}. Instruction/readback "
        "pairs mined from UWB-ATCC (controller and pilot labels from the corpus); one error "
        "planted per pair where the pilot echoed a value or callsign.",
        "",
        "| Parser | Real pairs | Flagged real pairs | Planted errors | Recall | Value errors "
        "| Callsign errors | Precision (approx.) | F1 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['parser']} | {r['pairs']} | {r['real_flag_rate']:.1%} | {r['planted']} "
            f"| {r['recall']:.1%} | {r['recall_value']:.1%} (n={r['n_value']}) "
            f"| {r['recall_callsign']:.1%} (n={r['n_callsign']}) | {r['precision']:.1%} "
            f"| {r['f1']:.1%} |"
        )
    lines += [
        "",
        "- **Recall**: share of planted readback errors that raised an error-level finding.",
        "- **Precision (approx.)**: planted errors caught versus the same pairs flagged before "
        "an error was planted. Some of those flags are real readback errors or corpus quirks, "
        "so this is a lower bound.",
        "- Omitted readback items are reported as warnings, not errors, and don't count here.",
        "- Reference transcripts are used, so speech recognition errors are not included.",
        "- Pairs are mined with the rule-based callsign matcher, which flatters the rule-based "
        "parser on callsign errors.",
        "",
        "Data: UWB-ATCC (University of West Bohemia, CC BY-NC-SA 4.0). Only aggregate numbers "
        "are published.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
