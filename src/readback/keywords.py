"""Keyword lists for Voice Transcribe's recognition biasing."""

from __future__ import annotations

import random

from readback.normalize import spoken

MAX_CHARS = 30  # Voice Transcribe rejects longer keywords (HTTP 400)

PHRASEOLOGY = [
    "alfa", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliett",
    "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango",
    "uniform", "victor", "whiskey", "xray", "yankee", "zulu",
    "flight level", "squawk", "QNH", "descend", "climb", "maintain", "heading", "runway",
    "cleared to land", "cleared for takeoff", "line up and wait", "holding point", "hold short",
    "taxi", "contact", "decimal", "roger", "wilco", "affirm", "negative", "ILS", "approach",
    "tower", "radar", "ground", "information", "standby", "say again", "correction", "knots",
    "vacate", "established", "go around", "direct", "proceed",
]  # fmt: skip


def callsign_keywords(
    target: list[str], pool: list[list[str]], n_distractors: int, rng: random.Random
) -> list[str]:
    """Simulate the callsign list ADS-B would give: the aircraft actually speaking plus other
    aircraft in range, in random order so position leaks nothing."""
    others = [c for c in {tuple(c) for c in pool} if list(c) != target]
    picked = rng.sample(others, min(n_distractors, len(others)))
    phrases = [fit(spoken(list(c))) for c in [tuple(target), *picked]]
    rng.shuffle(phrases)
    return phrases


def fit(phrase: str) -> str:
    """Cut a phrase at the last word boundary within MAX_CHARS: "air portugal nine three one
    charlie" keeps "air portugal nine three one", which still biases toward the right callsign."""
    while len(phrase) > MAX_CHARS and " " in phrase:
        phrase = phrase.rsplit(" ", 1)[0]
    return phrase[:MAX_CHARS]
