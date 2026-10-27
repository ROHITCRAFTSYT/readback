"""Recognition metrics that matter for readback checking: words, digits and callsigns."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import jiwer

from readback.normalize import canonical

# Words that come right before letters/digits but are not airline designators.
NOT_DESIGNATORS = {
    "runway", "level", "squawk", "heading", "information", "qnh", "qfe", "via", "decimal",
    "to", "and", "frequency", "contact", "descend", "climb", "maintain", "altitude", "point",
    "stand", "gate", "taxiway", "holding", "number", "time", "wind", "degrees", "knots",
    "speed", "feet", "miles", "direct", "proceed", "turn", "left", "right", "on", "at", "of",
    "the", "is", "for", "cleared", "ils", "approach", "tower", "radar", "ground", "departure",
    "flight", "correction", "with", "after", "behind", "report", "visibility", "temperature",
    "dew", "transition", "sid", "star", "route", "until", "hold", "expect", "vector", "steer",
    "track", "reaching", "passing", "leaving", "climbing", "descending", "turning",
    "bye", "hello", "roger", "sorry", "ok", "continue", "established", "final", "land", "line",
    "takeoff", "up", "wait", "way", "position", "ready", "reducing", "maximum", "sight",
    "localizer", "fraction", "contract", "airbus", "boeing", "sion", "baltu", "voz", "sa", "tel",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r",
    "s", "t", "u", "v", "w", "x", "y", "z",
}  # fmt: skip


def _is_char(token: str) -> bool:
    return len(token) == 1 and token.isalnum()


def wer(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    """Return (edit errors, reference length) so callers can pool across clips."""
    if not ref:
        return len(hyp), 0
    if not hyp:
        return len(ref), len(ref)
    out = jiwer.process_words(" ".join(ref), " ".join(hyp))
    return out.substitutions + out.deletions + out.insertions, len(ref)


def digit_errors(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    """Edit errors over the digit tokens only: altitudes, headings, squawks, frequencies."""
    return wer([t for t in ref if t.isdigit()], [t for t in hyp if t.isdigit()])


def discover_designators(texts: Iterable[str], min_count: int = 3) -> set[str]:
    """Find airline telephony designators: words that open or close an utterance and are
    followed by at least two spelled letters/digits (e.g. "csa one delta zulu ...")."""
    counts: Counter[str] = Counter()
    for text in texts:
        toks = canonical(text)
        for i, w in enumerate(toks[:-2]):
            if w in NOT_DESIGNATORS or _is_char(w) or not w.isalpha():
                continue
            j = i + 1
            while j < len(toks) and _is_char(toks[j]):
                j += 1
            if j - i - 1 >= 2 and (i == 0 or j == len(toks)):
                counts[w] += 1
    return {w for w, n in counts.items() if n >= min_count}


def extract_callsign(tokens: list[str], designators: set[str]) -> list[str] | None:
    """Return the first callsign span: a designator plus letters/digits, or a registration of
    four or more spelled letters (e.g. "o k f a o")."""
    for i, w in enumerate(tokens):
        if w in designators:
            j = i + 1
            while j < len(tokens) and _is_char(tokens[j]):
                j += 1
            if j > i + 1:
                return tokens[i:j]
    run_start = None
    for i, t in enumerate([*tokens, ""]):
        if len(t) == 1 and t.isalpha():
            run_start = i if run_start is None else run_start
            continue
        if run_start is not None and i - run_start >= 4:
            return tokens[run_start:i]
        run_start = None
    return None


def contains(span: list[str], tokens: list[str]) -> bool:
    n = len(span)
    return any(tokens[i : i + n] == span for i in range(len(tokens) - n + 1))
