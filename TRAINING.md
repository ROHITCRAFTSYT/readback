# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 74, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 70
- **Trained for:** 2.7 h on 1,467,634 examples (started 2026-10-27, updated 2026-10-27 14:05 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 79.5% | 34.6% | 92.1% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 81.1% | 36.6% | 92.8% | 85.4% |
| Real radio, held-out half (n=138) | 90.6% | 67.4% | 97.6% | 87.4% |
| Real radio, dev half (n=149) | 89.9% | 78.5% | 97.5% | 86.3% |
| Synthetic level 3 (n=2000) | 96.5% | 25.4% | 99.2% | 75.0% |

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

- `noise:truncate` ×1.64
- `kind:cleared_for_takeoff` ×1.41
- `noise:repeat` ×1.3
- `kind:squawk` ×1.22
- `kind:cleared_to_land` ×1.21
- `kind:line_up` ×1.16
- `kind:runway` ×1.15
- `kind:direct_to` ×1.14

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 74 | 3 | 96.5% | 99.5% |  |  |
| 73 | 3 | 96.3% | 97.5% |  |  |
| 72 | 3 | 96.2% | 98.5% |  |  |
| 71 | 3 | 96.2% | 98.5% |  |  |
| 70 | 3 | 96.5% | 97.5% | yes |  |
| 69 | 3 | 96.4% | 97.5% | yes |  |
| 68 | 3 | 96.2% | 97.5% |  |  |
| 67 | 3 | 95.9% | 99.0% |  |  |
| 66 | 3 | 96.2% | 97.5% |  |  |
| 65 | 3 | 96.3% | 99.5% |  |  |
| 64 | 3 | 96.2% | 96.5% |  |  |
| 63 | 3 | 96.1% | 98.5% |  |  |
| 62 | 3 | 96.1% | 97.5% |  |  |
| 61 | 3 | 96.2% | 99.0% |  |  |
| 60 | 3 | 96.3% | 97.5% |  |  |
