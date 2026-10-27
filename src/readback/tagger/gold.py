"""Hand-labeled real transmissions: the tagger's test on actual radio, never trained on.

`data/gold/uwb-atcc-gold.jsonl` holds 287 UWB-ATCC transmissions (Prague tower and approach)
labeled by hand: utterance id, callsign and clearance items. It stores no transcript text;
the text is joined from the downloaded corpus. Ambiguous clips were left out. A fixed hash of
the id splits it in half: "dev" may guide training decisions, "test" is only ever reported.

Labeling conventions follow the decoder: a value cut off by the end of the clip is not an
item; a callsign is whatever is left of it when two or more tokens survive; a runway
clearance said apart from its runway takes the one runway mentioned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from readback.clearance import Clearance
from readback.datasets import load_transcripts
from readback.normalize import canonical

ROOT = Path(__file__).resolve().parents[3]
GOLD = ROOT / "data" / "gold" / "uwb-atcc-gold.jsonl"
CORPUS = ROOT / "data" / "raw" / "uwb_atcc_test.parquet"


@dataclass(frozen=True)
class GoldExample:
    id: str
    text: str
    tokens: list[str]
    clearance: Clearance
    split: str  # "dev" or "test"


def split_of(utterance_id: str) -> str:
    return "dev" if hashlib.sha1(utterance_id.encode()).digest()[0] % 2 == 0 else "test"


def load_gold(gold: Path = GOLD, corpus: Path = CORPUS) -> list[GoldExample]:
    """Empty when the corpus hasn't been downloaded (scripts/fetch_data.py)."""
    if not gold.exists() or not corpus.exists():
        return []
    texts = {t.id: t.text for t in load_transcripts(corpus, "uwb-atcc")}
    out = []
    for line in gold.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["id"] in texts:
            text = texts[row["id"]]
            out.append(GoldExample(row["id"], text, canonical(text),
                                   Clearance.from_dict(row), split_of(row["id"])))  # fmt: skip
    return out
