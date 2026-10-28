"""Compare the clearance parsers on the hand-labeled real transmissions.

    uv run python scripts/eval_gold.py                  # rules + tagger
    uv run python scripts/eval_gold.py --spark          # also Muse Spark (needs the API key)

Test sets: the UWB-ATCC gold set (Prague, dev and held-out halves), the retired ATCO2 set 1
and the blind ATCO2 set 2 (`readback.tagger.gold`). Every parser's output goes through the
same `tidy`, and is scored on the same reference text (perfect speech recognition). Only
aggregate scores are written (results/parser-eval.md); per-clip output stays in results/raw/.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from readback import rules
from readback.datasets import load_transcripts
from readback.metrics import discover_designators
from readback.tagger.decode import tidy
from readback.tagger.evaluate import score
from readback.tagger.gold import BLIND_CORPUS, CORPUS, load_blind, load_gold, load_seen

ROOT = Path(__file__).resolve().parent.parent
SETS = {
    "gold_test": "Prague (UWB-ATCC), held-out half",
    "gold_dev": "Prague (UWB-ATCC), dev half",
    "blind": "**ATCO2 set 2, blind**",
    "atco2_seen": "ATCO2 set 1 (retired, not blind)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--spark", action="store_true", help="also evaluate Muse Spark")
    ap.add_argument("--concurrency", type=int, default=4, help="parallel Muse Spark calls")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "parser-eval.md")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    gold = load_gold()
    sets = {
        "gold_test": [g for g in gold if g.split == "test"],
        "gold_dev": [g for g in gold if g.split == "dev"],
        "blind": load_blind(),
        "atco2_seen": load_seen(),
    }
    sets = {k: v for k, v in sets.items() if v}
    if not sets:
        sys.exit("no labeled sets on this machine; run scripts/fetch_data.py first")

    uwb = discover_designators(t.text for t in load_transcripts(CORPUS, "uwb-atcc"))
    atco2 = discover_designators(t.text for t in load_transcripts(BLIND_CORPUS, "atco2-1h"))
    parsers = {
        "rule-based": lambda g: rules.parse(g.text, atco2 if g.id.startswith("atco2") else uwb)
    }
    from readback.tagger.model import Tagger

    model = ROOT / "training" / "champion.pt"
    tagger = Tagger.load(model)
    parsers[f"tagger (round {tagger.meta.get('round', '?')})"] = lambda g: tagger.parse(g.text)
    if args.spark:
        from readback.llm import MuseChat, SparkParser

        chat = MuseChat()
        spark = SparkParser(chat, ROOT / "results" / "raw" / "llm-cache")
        parsers[chat.model] = lambda g: spark(g.text)

    rows = []
    for name, parse in parsers.items():
        workers = args.concurrency if name not in ("rule-based",) and "tagger" not in name else 1
        for key, examples in sets.items():
            with ThreadPoolExecutor(max_workers=workers) as pool:
                preds = [tidy(p) for p in pool.map(parse, examples)]
            m = score(preds, [g.clearance for g in examples])
            rows.append((key, name, m))
            print(f"{key:11} {name:28} exact {m['exact']:.3f}  item F1 {m['item_f1']:.3f}  "
                  f"callsign {m['callsign']:.3f}  (n={m['n']})")  # fmt: skip

    lines = [
        "# Clearance parsers on hand-labeled real transmissions",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/eval_gold.py`. Reference",
        "transcripts (perfect speech recognition); every parser's output goes through the same",
        "`tidy`. *Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs.",
        "",
        "| Test set | Parser | n | Exact | Item F1 | Callsign |",
        "|---|---|---|---|---|---|",
    ]
    for key in SETS:
        for k, name, m in rows:
            if k == key:
                lines.append(
                    f"| {SETS[key]} | {name} | {m['n']} | {100 * m['exact']:.1f}% | "
                    f"{100 * m['item_f1']:.1f}% | {100 * m['callsign']:.1f}% |"
                )
    lines += [
        "",
        "The ATCO2 labels stay local (ATCO2 terms); their hashes are in `data/gold/`. Set 2 was",
        "frozen before any parser ran on it and has never been used to change the tagger or its",
        "generator. The rule-based parser's airline designators are discovered from each corpus.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
