# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 1, level 2 (everyday variation)
- **Champion:** round 1
- **Trained for:** 0.1 h on 16,000 examples (started 2026-10-27, updated 2026-10-27 09:07 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| Real radio, held-out half (n=138) | 88.4% | 67.4% | 96.1% | 87.4% |
| Real radio, dev half (n=149) | 83.2% | 78.5% | 96.0% | 86.3% |
| Synthetic level 2 (n=2000) | 86.5% | 48.6% | 94.6% | 88.3% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: current
3. real radio: cut-offs, hesitations, misheard words
4. stress: every kind of noise at once

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `kind:hold_short` ×2.48
- `kind:cleared_for_takeoff` ×2.25
- `kind:frequency` ×1.78
- `kind:line_up` ×1.68
- `noise:distractor` ×1.21

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 1 | 1 | 98.7% | 96.0% | yes | passed level 1 |
