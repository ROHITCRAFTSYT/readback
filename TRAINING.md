# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 22, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 22
- **Trained for:** 0.9 h on 437,320 examples (started 2026-10-27, updated 2026-10-27 10:04 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| Real radio, held-out half (n=138) | 89.9% | 67.4% | 97.2% | 87.4% |
| Real radio, dev half (n=149) | 85.9% | 78.5% | 95.2% | 86.3% |
| Synthetic level 3 (n=2000) | 96.2% | 29.5% | 99.2% | 77.6% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: passed in round 2
3. real radio: cut-offs, hesitations, misheard words: current
4. stress: every kind of noise at once

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `noise:truncate` ×1.8
- `kind:cleared_to_land` ×1.75
- `kind:line_up` ×1.46
- `noise:greeting` ×1.27
- `kind:cleared_for_takeoff` ×1.23
- `kind:qnh` ×1.19
- `kind:frequency` ×1.13
- `noise:station` ×1.11

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 |
|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 22 | 3 | 96.2% | 95.2% | yes |  |
| 21 | 3 | 96.1% | 97.1% |  |  |
| 20 | 3 | 96.2% | 95.7% | yes |  |
| 19 | 3 | 96.0% | 95.2% | yes |  |
| 18 | 3 | 96.5% | 94.6% |  |  |
| 17 | 3 | 95.5% | 96.1% |  |  |
| 16 | 3 | 95.5% | 95.6% |  |  |
| 15 | 3 | 95.8% | 99.0% |  |  |
| 14 | 3 | 95.7% | 94.7% |  |  |
| 13 | 3 | 95.8% | 97.0% | yes |  |
| 12 | 3 | 95.5% | 95.6% | yes |  |
| 11 | 3 | 95.2% | 95.2% | yes |  |
| 10 | 3 | 94.8% | 96.6% | yes |  |
| 9 | 3 | 94.5% | 98.5% |  |  |
| 8 | 3 | 94.6% | 97.5% | yes |  |
