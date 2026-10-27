"""Freeze the ATCO2 blind-test labels: write the manifest that is committed in their place.

    uv run python scripts/freeze_blind.py

The labels (data/blind/, the current set) stay on this machine because ATCO2's terms don't
allow sharing derived data. The manifest records their SHA-256 and the generator's, so the
commit that adds it shows when the labels were fixed and which generator they were scored
against. load_blind() refuses labels that no longer match. This runs once; --force exists
only to correct a manifest written by mistake, and that shows in the git history.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import UTC, datetime

from readback.datasets import describe
from readback.tagger.gold import BLIND, BLIND_MANIFEST, SYNTH, sha256
from readback.tagger.synth import VERSION


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sampled", type=int, default=300, help="clips drawn for labeling")
    ap.add_argument("--seed", default="atco2-blind-2-2026")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if BLIND_MANIFEST.exists() and not args.force:
        sys.exit(f"{BLIND_MANIFEST.name} exists; the blind labels are already frozen")
    rows = [json.loads(line) for line in BLIND.read_text(encoding="utf-8").splitlines()]
    airports = collections.Counter(describe(r["id"])[0].split("_")[0] for r in rows)
    manifest = {
        "dataset": "ATCO2 1h test set (atco2-1h)",
        "labels": f"data/blind/{BLIND.name}",
        "labels_sha256": sha256(BLIND),
        "transmissions": len(rows),
        "sampled": args.sampled,
        "left_out_ambiguous": args.sampled - len(rows),
        "sample_seed": args.seed,
        "airports": dict(airports.most_common()),
        "items": sum(len(r["items"]) for r in rows),
        "with_callsign": sum(r["callsign"] is not None for r in rows),
        "generator_version": VERSION,
        "generator_sha256": sha256(SYNTH),
        "frozen": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    BLIND_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
