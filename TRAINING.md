# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 277, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 248
- **Trained for:** 11.5 h on 5,484,484 examples (started 2026-10-27, updated 2026-10-28 18:55 UTC)

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
Generator unchanged since the freeze: **yes**.

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

- `noise:truncate` ×1.52
- `kind:cleared_to_land` ×1.31
- `kind:cleared_for_takeoff` ×1.27
- `noise:misheard` ×1.23
- `kind:heading` ×1.2
- `noise:station` ×1.12
- `kind:speed` ×1.1
- `kind:qnh` ×1.06

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
| 277 | 3 | 96.7% | 97.5% |  |  |
| 276 | 3 | 96.4% | 97.5% |  |  |
| 275 | 3 | 96.6% | 98.5% |  |  |
| 274 | 3 | 96.4% | 98.5% |  |  |
| 273 | 3 | 96.5% | 97.5% |  |  |
| 272 | 3 | 96.5% | 97.5% |  |  |
| 271 | 3 | 96.2% | 98.5% |  |  |
| 270 | 3 | 96.3% | 98.5% |  |  |
| 269 | 3 | 96.4% | 98.5% |  |  |
| 268 | 3 | 96.3% | 98.5% |  |  |
| 267 | 3 | 96.5% | 98.5% |  |  |
| 266 | 3 | 96.2% | 98.5% |  |  |
| 265 | 3 | 96.4% | 98.0% |  |  |
| 264 | 3 | 96.5% | 99.5% |  |  |
| 263 | 3 | 96.4% | 98.5% |  |  |
