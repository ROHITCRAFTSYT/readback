"""Write the tagger's curriculum as chat fine-tuning data for an open-weights model.

    uv run python scripts/export_sft.py --n 20000 --out data/sft/clearances.jsonl

Each line is {"messages": [system, user, assistant]}: the user turn is a transcript in
canonical form, the assistant turn is the clearance as JSON matching clearance.JSON_SCHEMA.
Levels are mixed (default 1:2:3:4 = 1:2:4:1), so the model sees mostly real-radio noise.
This is the data for fine-tuning Muse Glimmer to run the parser on-premises.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = (
    "You read one air traffic control radio transmission and return the clearance it "
    "contains as JSON: the callsign in spoken canonical form (or null) and every item with "
    "its kind, value and qualifier. Leave out values that were cut off."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--mix", default="1,2,4,1", help="relative share of levels 1-4")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "sft" / "clearances.jsonl")
    args = ap.parse_args()

    from readback.tagger.synth import MAX_LEVEL, dataset

    shares = [float(x) for x in args.mix.split(",")]
    if len(shares) != MAX_LEVEL:
        ap.error(f"--mix needs {MAX_LEVEL} numbers")
    rows = []
    for level, share in enumerate(shares, start=1):
        n = round(args.n * share / sum(shares))
        for e in dataset(level, n, f"sft:{args.seed}:{level}"):
            rows.append({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": " ".join(e.tokens)},
                {"role": "assistant", "content": json.dumps(e.clearance.to_dict())},
            ], "level": level})  # fmt: skip
    random.Random(args.seed).shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} examples to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
