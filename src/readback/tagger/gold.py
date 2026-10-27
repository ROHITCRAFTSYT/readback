"""Hand-labeled real transmissions: the tagger's test on actual radio, never trained on.

`data/gold/uwb-atcc-gold.jsonl` holds 287 UWB-ATCC transmissions (Prague tower and approach)
labeled by hand: utterance id, callsign and clearance items. It stores no transcript text;
the text is joined from the downloaded corpus. Ambiguous clips were left out. A fixed hash of
the id splits it in half: "dev" may guide training decisions, "test" is only ever reported.

The **blind test** is a separate set of ATCO2 transmissions (Sion, Bern, Zurich, Sydney,
Bratislava, Prague, Brno), labeled the same way after the current generator was written and
never used to change it. ATCO2's terms don't allow sharing derived data, so the labels stay
local in `data/blind/` (gitignored); `data/gold/atco2-blind-2.manifest.json` is committed
instead, with the SHA-256 of the labels and of the generator when they were frozen. It is
scored only as a whole, and only for reporting. Set 1 (`atco2-blind.manifest.json`) was
the first such set; it is retired because generator v3 was written after labeling it.

Labeling conventions follow the decoder: a value cut off by the end of the clip is not an
item; a callsign is whatever is left of it when two or more tokens survive; a runway
clearance said apart from its runway takes the one runway mentioned.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from readback.clearance import Clearance
from readback.datasets import load_transcripts
from readback.normalize import canonical

ROOT = Path(__file__).resolve().parents[3]
GOLD = ROOT / "data" / "gold" / "uwb-atcc-gold.jsonl"
CORPUS = ROOT / "data" / "raw" / "uwb_atcc_test.parquet"
BLIND = ROOT / "data" / "blind" / "atco2-blind-2.jsonl"
BLIND_MANIFEST = ROOT / "data" / "gold" / "atco2-blind-2.manifest.json"
# The first blind set, retired: labeling it showed what generator v3 had to cover, so it is
# no longer blind. It is still scored, as "ATCO2 set 1 (seen)", and is never trained on.
SEEN = ROOT / "data" / "blind" / "atco2-blind.jsonl"
SEEN_MANIFEST = ROOT / "data" / "gold" / "atco2-blind.manifest.json"
BLIND_CORPUS = ROOT / "data" / "raw" / "atco2_1h_test.parquet"
SYNTH = ROOT / "src" / "readback" / "tagger" / "synth.py"


@dataclass(frozen=True)
class GoldExample:
    id: str
    text: str
    tokens: list[str]
    clearance: Clearance
    split: str  # "dev", "test" or "blind"


def split_of(utterance_id: str) -> str:
    return "dev" if hashlib.sha1(utterance_id.encode()).digest()[0] % 2 == 0 else "test"


def _join(labels: Path, corpus: Path, dataset: str, split: Callable[[str], str]) -> list:
    texts = {t.id: t.text for t in load_transcripts(corpus, dataset)}
    out = []
    for line in labels.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["id"] in texts:
            text = texts[row["id"]]
            out.append(GoldExample(row["id"], text, canonical(text),
                                   Clearance.from_dict(row), split(row["id"])))  # fmt: skip
    return out


def load_gold(gold: Path = GOLD, corpus: Path = CORPUS) -> list[GoldExample]:
    """Empty when the corpus hasn't been downloaded (scripts/fetch_data.py)."""
    if not gold.exists() or not corpus.exists():
        return []
    return _join(gold, corpus, "uwb-atcc", split_of)


def sha256(path: Path) -> str:
    """Of the file with Unix line endings, so a Windows checkout hashes the same."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class BlindChanged(Exception):
    """The blind labels no longer match the hash committed when they were frozen."""


def blind_info(manifest: Path = BLIND_MANIFEST, synth: Path = SYNTH) -> dict | None:
    """The committed manifest, plus whether the generator is still the one the labels were
    frozen against. None when there is no manifest."""
    if not manifest.exists():
        return None
    info = json.loads(manifest.read_text(encoding="utf-8"))
    info["generator_unchanged"] = sha256(synth) == info["generator_sha256"]
    return info


def load_blind(
    labels: Path = BLIND, corpus: Path = BLIND_CORPUS, manifest: Path = BLIND_MANIFEST
) -> list[GoldExample]:
    """The ATCO2 blind test. Empty when the labels or the corpus aren't on this machine.
    Raises BlindChanged if the labels were edited after they were frozen."""
    if not labels.exists() or not corpus.exists() or not manifest.exists():
        return []
    if sha256(labels) != json.loads(manifest.read_text(encoding="utf-8"))["labels_sha256"]:
        raise BlindChanged(f"{labels.name} does not match the hash frozen in {manifest.name}")
    return _join(labels, corpus, "atco2-1h", lambda _: "blind")


def load_seen() -> list[GoldExample]:
    """The retired first ATCO2 set, held to its own frozen hash."""
    return [GoldExample(g.id, g.text, g.tokens, g.clearance, "seen")
            for g in load_blind(SEEN, BLIND_CORPUS, SEEN_MANIFEST)]  # fmt: skip
