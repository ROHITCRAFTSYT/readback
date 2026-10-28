# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** training, round 189, level 3 (real radio: cut-offs, hesitations, misheard words)
- **Champion:** round 109
- **Trained for:** 7.8 h on 3,743,386 examples (started 2026-10-27, updated 2026-10-28 14:49 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 81.0% | 34.6% | 92.4% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 82.6% | 36.6% | 94.4% | 85.4% |
| Real radio, held-out half (n=138) | 90.6% | 67.4% | 98.1% | 87.4% |
| Real radio, dev half (n=149) | 90.6% | 78.5% | 97.5% | 86.3% |
| Synthetic level 3 (n=2000) | 96.8% | 25.4% | 99.3% | 75.0% |

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

- `noise:truncate` ×1.83
- `kind:cleared_for_takeoff` ×1.7
- `kind:qnh` ×1.44
- `kind:frequency` ×1.41
- `kind:cleared_to_land` ×1.27
- `kind:line_up` ×1.18
- `kind:runway` ×1.17
- `kind:direct_to` ×1.16

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |
| 2026-10-28 | 109 | 96.0% | 98.1% | 92.4% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 189 | 3 | 96.2% | 98.5% |  |  |
| 188 | 3 | 96.2% | 98.5% |  |  |
| 187 | 3 | 96.5% | 98.5% |  |  |
| 186 | 3 | 96.4% | 98.5% |  |  |
| 185 | 3 | 96.5% | 98.5% |  |  |
| 184 | 3 | 96.5% | 98.5% |  |  |
| 183 | 3 | 96.2% | 97.5% |  |  |
| 182 | 3 | 96.4% | 98.5% |  |  |
| 181 | 3 | 96.3% | 97.5% |  |  |
| 180 | 3 | 96.1% | 98.5% |  |  |
| 179 | 3 | 96.5% | 99.5% |  |  |
| 178 | 3 | 96.4% | 98.5% |  |  |
| 177 | 3 | 96.4% | 97.5% |  |  |
| 176 | 3 | 96.4% | 97.5% |  |  |
| 175 | 3 | 96.3% | 98.5% |  |  |
