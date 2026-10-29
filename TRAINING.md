# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 382, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 248
- **Trained for:** 16.7 h on 7,560,862 examples (started 2026-10-27, updated 2026-10-29 09:52 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 79.8% | 34.6% | 91.8% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 82.3% | 36.6% | 94.4% | 85.4% |
| Real radio, held-out half (n=138) | 90.6% | 67.4% | 97.6% | 87.4% |
| Real radio, dev half (n=149) | 90.6% | 78.5% | 97.5% | 86.3% |
| Synthetic level 3 (n=2000) | 96.8% | 25.4% | 99.4% | 75.0% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

The **blind** test is 263 ATCO2 transmissions (LSGS, LSZB, LKPR, LSZH, YSSY, LZIB, LKTB),
labeled by hand and frozen on 2026-10-27 before any parser saw them. The
generator was written without them and is never changed because of them, so this
is the honest estimate of accuracy on unseen radio. The labels stay local (ATCO2
terms); `data/gold/atco2-blind-2.manifest.json` holds their hash.
Generator unchanged since the freeze: **no** (see git history).

ATCO2 set 1 was the first blind set. Labeling it showed what the generator was
missing, so generator v3 was written after it and it is no longer blind. It is
still scored, as a check on those changes, and is never trained on.

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: passed in round 2
3. real radio: cut-offs, hesitations, misheard words: current
4. stress: every kind of noise at once

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `noise:truncate` ×1.78
- `kind:direct_to` ×1.67
- `kind:cleared_to_land` ×1.61
- `kind:frequency` ×1.56
- `noise:misheard` ×1.5
- `kind:line_up` ×1.19
- `noise:hesitation` ×1.09
- `noise:repeat` ×1.09

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |
| 2026-10-28 | 109 | 96.0% | 98.1% | 92.4% |
| 2026-10-29 | 248 | 96.5% | 97.6% | 91.8% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 382 | 3 | 96.0% | 97.5% |  |  |
| 381 | 3 | 96.5% | 98.5% |  |  |
| 380 | 3 | 96.6% | 98.5% |  |  |
| 379 | 3 | 96.6% | 98.5% |  |  |
| 378 | 3 | 96.4% | 98.5% |  |  |
| 377 | 3 | 96.4% | 98.5% |  |  |
| 376 | 3 | 96.2% | 98.5% |  |  |
| 375 | 3 | 96.7% | 96.5% |  |  |
| 374 | 3 | 96.5% | 99.5% |  |  |
| 373 | 3 | 96.5% | 98.5% |  |  |
| 372 | 3 | 96.5% | 98.5% |  |  |
| 371 | 3 | 96.4% | 98.5% |  |  |
| 370 | 3 | 96.5% | 98.5% |  |  |
| 369 | 3 | 96.5% | 99.5% |  |  |
| 368 | 3 | 96.5% | 99.5% |  |  |
