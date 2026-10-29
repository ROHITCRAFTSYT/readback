"""Clearance parser on Muse Glimmer 30B fine-tuned for Readback (scripts/finetune_glimmer_modal.py).

The model runs where it is deployed (the Modal app `readback-glimmer`, or a unit's own GPU
with the same adapter); this client sends transmissions in canonical form and reads back
the clearance JSON. Results are cached on disk by adapter and text, like the Spark parser.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from readback.clearance import KINDS, Clearance, Item
from readback.normalize import canonical

APP = "readback-glimmer"
ADAPTER = "adapters/clearance"


def decode(answer: str) -> Clearance | None:
    """The clearance in a model answer, or None if it isn't clearance JSON. Unlike Spark,
    whose output is held to the schema, the model can name a kind that doesn't exist
    ("orbit", a misspelled "qhn"); those items are dropped and the rest is kept."""
    try:
        data = json.loads(answer)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        return None
    callsign = data.get("callsign")
    items = [
        Item(kind=i["kind"], value=i.get("value") or "", qualifier=i.get("qualifier") or "")
        for i in data.get("items", [])
        if isinstance(i, dict) and i.get("kind") in KINDS and isinstance(i.get("value", ""), str)
    ]
    return Clearance(callsign=callsign if isinstance(callsign, str) and callsign else None,
                     items=items)  # fmt: skip


class GlimmerParser:
    """Parse transmissions with the fine-tuned Glimmer; batches go to the model in one call."""

    def __init__(self, cache_dir: Path | None = None, adapter: str = ADAPTER, remote=None) -> None:
        self.adapter = adapter
        self.invalid = 0
        self._cache_dir = cache_dir
        self._remote = remote

    def _model(self):
        if self._remote is None:
            import modal

            self._remote = modal.Cls.from_name(APP, "Glimmer")(adapter=self.adapter).parse
        return self._remote

    def _cache(self, tokens: str) -> Path | None:
        if not self._cache_dir:
            return None
        key = hashlib.sha256(f"glimmer:{self.adapter}\n{tokens}".encode()).hexdigest()
        return self._cache_dir / f"{key[:32]}.json"

    def many(self, texts: Sequence[str]) -> list[Clearance]:
        tokens = [" ".join(canonical(t)) for t in texts]
        answers: dict[str, str] = {}
        for t in tokens:
            cache = self._cache(t)
            if cache and cache.exists():
                answers[t] = cache.read_text(encoding="utf-8")
        todo = sorted({t for t in tokens if t not in answers})
        if todo:
            for t, a in zip(todo, self._model().remote(todo), strict=True):
                answers[t] = a
                if cache := self._cache(t):
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(a, encoding="utf-8")
        out = []
        for t in tokens:
            clearance = decode(answers[t])
            if clearance is None:
                self.invalid += 1
                clearance = Clearance(callsign=None, items=[])
            out.append(clearance)
        return out

    def __call__(self, text: str) -> Clearance:
        return self.many([text])[0]
