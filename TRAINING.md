# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 42, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 32
- **Trained for:** 1.6 h on 833,122 examples (started 2026-10-27, updated 2026-10-27 10:50 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| Real radio, held-out half (n=138) | 90.6% | 67.4% | 98.6% | 87.4% |
| Real radio, dev half (n=149) | 87.2% | 78.5% | 96.1% | 86.3% |
| Synthetic level 3 (n=2000) | 96.7% | 29.5% | 99.2% | 77.6% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: passed in round 2
3. real radio: cut-offs, hesitations, misheard words: current
4. stress: every kind of noise at once

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `noise:repeat` ×1.71
- `noise:truncate` ×1.66
- `kind:cleared_to_land` ×1.44
- `kind:squawk` ×1.42
- `kind:direct_to` ×1.41
- `kind:line_up` ×1.18
- `kind:frequency` ×1.08
- `kind:flight_level` ×1.06

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 |
|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 42 | 3 | 96.2% | 98.5% |  |  |
| 41 | 3 | 96.4% | 96.6% |  |  |
| 40 | 3 | 96.5% | 96.1% |  |  |
| 39 | 3 | 96.6% | 99.0% |  |  |
| 38 | 3 | 96.5% | 96.6% |  |  |
| 37 | 3 | 96.5% | 97.1% |  |  |
| 36 | 3 | 96.4% | 98.5% |  |  |
| 35 | 3 | 96.3% | 94.6% |  |  |
| 34 | 3 | 96.4% | 97.5% |  |  |
| 33 | 3 | 96.5% | 95.6% |  |  |
| 32 | 3 | 96.7% | 96.1% | yes |  |
| 31 | 3 | 96.4% | 97.6% |  |  |
| 30 | 3 | 96.2% | 97.1% |  |  |
| 29 | 3 | 96.4% | 95.6% |  |  |
| 28 | 3 | 96.5% | 96.1% | yes |  |
