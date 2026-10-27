"""Load open ATC speech test sets (downloaded by scripts/fetch_data.py, never committed)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

HF = "https://huggingface.co/datasets"
DATASETS = {
    # ATCO2 1h test set: real VHF radio from Czech airports. Terms: ATCO2 end-user data agreement.
    "atco2-1h": (
        f"{HF}/Jzuluaga/atco2_corpus_1h/resolve/main/data/"
        "test-00000-of-00001-112f39d2f116a22b.parquet",
        "atco2_1h_test.parquet",
    ),
    # UWB-ATCC test split: CC BY-NC-SA 4.0, 8 kHz, ids end in _AT (controller) or _PI (pilot).
    "uwb-atcc": (
        f"{HF}/Jzuluaga/uwb_atcc/resolve/main/data/test-00000-of-00001-3a021115ca23c2a5.parquet",
        "uwb_atcc_test.parquet",
    ),
}

_ATCO2_GROUP = re.compile(r"^atco2_test-set-1h_(.+?MHz)_")
_UWB_ID = re.compile(r"^uwb-atcc_([^_]+)_.*_(AT|PI)$")


@dataclass(frozen=True)
class Clip:
    id: str
    dataset: str
    text: str
    duration: float
    wav: bytes
    group: str  # airport/frequency (ATCO2) or recording session (UWB-ATCC)
    role: str | None  # "controller", "pilot", or None when unknown


def describe(clip_id: str) -> tuple[str, str | None]:
    """Return (group, role) parsed from a dataset clip id."""
    if m := _ATCO2_GROUP.match(clip_id):
        return m.group(1), None
    if m := _UWB_ID.match(clip_id):
        return m.group(1), {"AT": "controller", "PI": "pilot"}[m.group(2)]
    return "unknown", None


def load_clips(path: Path, dataset: str) -> list[Clip]:
    table = pq.read_table(path, columns=["id", "audio", "text", "duration"])
    clips = []
    for row in table.to_pylist():
        group, role = describe(row["id"])
        clips.append(
            Clip(
                id=row["id"],
                dataset=dataset,
                text=row["text"],
                duration=float(row["duration"]),
                wav=row["audio"]["bytes"],
                group=group,
                role=role,
            )
        )
    return clips
