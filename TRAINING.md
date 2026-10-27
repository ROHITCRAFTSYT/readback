# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 48, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 45
- **Trained for:** 1.8 h on 951,178 examples (started 2026-10-27, updated 2026-10-27 12:59 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2, 7 airports** (n=265) | 64.9% | 36.6% | 81.6% | 85.4% |
| Real radio, held-out half (n=138) | 88.4% | 67.4% | 97.2% | 87.4% |
| Real radio, dev half (n=149) | 87.2% | 78.5% | 96.6% | 86.3% |
| Synthetic level 3 (n=2000) | 96.8% | 29.5% | 99.3% | 77.6% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

The **blind** test is 265 ATCO2 transmissions (LSGS, LSZB, LSZH, YSSY, LZIB, LKPR, LKTB),
labeled by hand and frozen on 2026-10-27 before any parser saw them. The
generator was written without them and is never changed because of them, so this
is the honest estimate of accuracy on unseen radio. The labels stay local (ATCO2
terms); `data/gold/atco2-blind.manifest.json` holds their hash.
Generator unchanged since the freeze: **no** (see git history).

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: passed in round 2
3. real radio: cut-offs, hesitations, misheard words: current
4. stress: every kind of noise at once

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `noise:truncate` ×1.6
- `kind:hold_short` ×1.33
- `kind:line_up` ×1.28
- `kind:cleared_for_takeoff` ×1.26
- `kind:squawk` ×1.25
- `noise:misheard` ×1.2
- `noise:station` ×1.2
- `noise:hesitation` ×1.18

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 48 | 3 | 96.8% | 99.0% |  |  |
| 47 | 3 | 96.7% | 97.6% |  |  |
| 46 | 3 | 96.6% | 96.1% |  |  |
| 45 | 3 | 96.8% | 96.6% | yes |  |
| 44 | 3 | 96.5% | 97.6% |  |  |
| 43 | 3 | 96.8% | 98.0% | yes |  |
| 42 | 3 | 96.2% | 98.5% |  |  |
| 41 | 3 | 96.4% | 96.6% |  |  |
| 40 | 3 | 96.5% | 96.1% |  |  |
| 39 | 3 | 96.6% | 99.0% |  |  |
| 38 | 3 | 96.5% | 96.6% |  |  |
| 37 | 3 | 96.5% | 97.1% |  |  |
| 36 | 3 | 96.4% | 98.5% |  |  |
| 35 | 3 | 96.3% | 94.6% |  |  |
| 34 | 3 | 96.4% | 97.5% |  |  |
