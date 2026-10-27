"""Find instruction/readback pairs in a corpus and plant labeled readback errors in them."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from readback.clearance import READBACK_REQUIRED, Clearance
from readback.compare import same_callsign
from readback.datasets import Transcript
from readback.metrics import extract_callsign
from readback.normalize import canonical, spoken

# A digit run is a readback-required value when one of these words sits right before or after it.
VALUE_BEFORE = {"level", "heading", "squawk", "qnh", "runway", "altitude", "speed", "decimal"}
VALUE_AFTER = {"feet", "knots", "decimal"}


@dataclass(frozen=True)
class Pair:
    instruction: Transcript
    readback: Transcript


def mine_pairs(
    transcripts: list[Transcript],
    parse: Callable[[str], Clearance],
    max_gap: float = 8.0,
    lookahead: int = 3,
) -> list[Pair]:
    """Pair each controller instruction with the pilot transmission that reads it back.

    The pilot reply must start within max_gap seconds. A reply that isn't the very next
    transmission is accepted only when its callsign matches the instruction's.
    """
    by_recording: dict[str, list[Transcript]] = defaultdict(list)
    for t in transcripts:
        by_recording[t.recording].append(t)

    pairs = []
    for clips in by_recording.values():
        clips.sort(key=lambda t: t.start)
        parsed = [parse(t.text) for t in clips]
        for i, (clip, clr) in enumerate(zip(clips, parsed, strict=True)):
            if clip.role != "controller" or not any(
                it.kind in READBACK_REQUIRED for it in clr.items
            ):
                continue
            for k in range(i + 1, min(i + 1 + lookahead, len(clips))):
                reply, reply_clr = clips[k], parsed[k]
                if reply.start - clip.end > max_gap:
                    break
                if reply.role != "pilot":
                    continue
                if clr.callsign and reply_clr.callsign:
                    if same_callsign(clr.callsign, reply_clr.callsign):
                        pairs.append(Pair(clip, reply))
                        break
                elif k == i + 1:
                    pairs.append(Pair(clip, reply))
                    break
    return pairs


def _digit_runs(toks: list[str]) -> list[tuple[int, int]]:
    runs, i = [], 0
    while i < len(toks):
        if toks[i].isdigit():
            j = i
            while j < len(toks) and toks[j].isdigit():
                j += 1
            if j - i >= 2:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _occurs(span: list[str], toks: list[str]) -> bool:
    return any(toks[k : k + len(span)] == span for k in range(len(toks) - len(span) + 1))


def _mutate(token: str, rng: random.Random) -> str:
    pool = "0123456789" if token.isdigit() else "abcdefghijklmnopqrstuvwxyz"
    return rng.choice([c for c in pool if c != token])


def inject_error(
    instruction_text: str, readback_text: str, designators: set[str], rng: random.Random
) -> tuple[str, dict] | None:
    """Change one value or callsign character in a readback so it no longer matches the
    instruction. Only values the readback originally echoed are touched, so the result is a
    true readback error by construction. Returns (spoken readback, label) or None."""
    ins, rb = canonical(instruction_text), canonical(readback_text)
    options: list[tuple[str, int, int]] = []
    for a, b in _digit_runs(rb):
        before = rb[a - 1] if a > 0 else ""
        after = rb[b] if b < len(rb) else ""
        if (before in VALUE_BEFORE or after in VALUE_AFTER) and _occurs(rb[a:b], ins):
            options.append(("value", a, b))
    cs_rb, cs_ins = extract_callsign(rb, designators), extract_callsign(ins, designators)
    if cs_rb and cs_ins and same_callsign(" ".join(cs_rb), " ".join(cs_ins)):
        start = next(k for k in range(len(rb)) if rb[k : k + len(cs_rb)] == cs_rb)
        chars = [k for k in range(start, start + len(cs_rb)) if len(rb[k]) == 1]
        if chars:
            options.append(("callsign", chars[0], chars[-1] + 1))
    if not options:
        return None

    kind, a, b = rng.choice(options)
    pos = rng.randrange(a, b)
    if kind == "callsign":
        pos = rng.choice([k for k in range(a, b) if len(rb[k]) == 1])
    mutated = list(rb)
    mutated[pos] = _mutate(rb[pos], rng)
    if kind == "value" and _occurs(mutated[a:b], ins):
        return None  # the "wrong" value also appears in the instruction, so the label is unsafe
    if kind == "callsign":
        span = mutated[start : start + len(cs_rb)]
        if same_callsign(" ".join(span), " ".join(cs_ins)):
            return None  # still reads as an abbreviation of the instructed callsign
    label = {"type": kind, "original": " ".join(rb[a:b]), "mutated": " ".join(mutated[a:b])}
    return spoken(mutated), label
