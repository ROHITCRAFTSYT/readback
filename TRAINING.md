# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** optimal, round 1077, level 4 (stress: every kind of noise at once)
- **Champion:** round 499
- **Trained for:** 46.1 h on 21,158,128 examples (started 2026-10-27, updated 2026-11-02 19:30 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 83.7% | 34.6% | 91.5% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 86.0% | 36.6% | 94.4% | 85.4% |
| Real radio, held-out half (n=138) | 89.1% | 67.4% | 98.1% | 87.4% |
| Real radio, dev half (n=149) | 89.9% | 78.5% | 97.0% | 86.3% |
| Synthetic level 4 (n=2000) | 98.0% | 16.1% | 99.5% | 67.3% |

*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real
transmissions are 287 UWB-ATCC clips from Prague, labeled by hand (`data/gold/`); the held-out half never influences training.

The **blind** test is 263 ATCO2 transmissions (LSGS, LSZB, LKPR, LSZH, YSSY, LZIB, LKTB),
labeled by hand and frozen on 2026-10-27 before any parser saw them. The
generator was written without them and is never changed because of them, so this
is the honest estimate of accuracy on unseen radio. The labels stay local (ATCO2
terms); `data/gold/atco2-blind-2.manifest.json` holds their hash.
Generator changed since the freeze: **yes**, now v4, from synthetic validation mistakes only (see the README and git history).

ATCO2 set 1 was the first blind set. Labeling it showed what the generator was
missing, so generator v3 was written after it and it is no longer blind. It is
still scored, as a check on those changes, and is never trained on.

## Levels

1. standard phraseology: passed in round 1
2. everyday variation: passed in round 2
3. real radio: cut-offs, hesitations, misheard words: passed in round 384
4. stress: every kind of noise at once: current

## Current focus

Practice is weighted toward what the champion got wrong in its last probe:

- `kind:cleared_for_takeoff` ×1.9
- `kind:direct_to` ×1.88
- `kind:line_up` ×1.83
- `noise:truncate` ×1.39
- `kind:hold_short` ×1.32
- `noise:misheard` ×1.18
- `kind:cleared_to_land` ×1.17
- `noise:repeat` ×1.04

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |
| 2026-10-28 | 109 | 96.0% | 98.1% | 92.4% |
| 2026-10-29 | 248 | 96.5% | 97.6% | 91.8% |
| 2026-10-30 | 499 | 98.2% | 98.1% | 91.5% |
| 2026-10-31 | 499 | 98.6% | 98.1% | 91.5% |
| 2026-11-01 | 499 | 98.6% | 98.1% | 91.5% |
| 2026-11-02 | 499 | 98.6% | 98.1% | 91.5% |
| 2026-11-03 | 499 | 98.2% | 98.1% | 91.5% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 1077 | 4 | 97.9% | 97.0% |  |  |
| 1076 | 4 | 97.7% | 95.1% |  |  |
| 1075 | 4 | 97.5% | 96.0% |  |  |
| 1074 | 4 | 97.5% | 98.0% |  |  |
| 1073 | 4 | 97.3% | 97.5% |  |  |
| 1072 | 4 | 97.6% | 96.1% |  |  |
| 1071 | 4 | 97.5% | 97.5% |  |  |
| 1070 | 4 | 97.8% | 97.0% |  |  |
| 1069 | 4 | 97.3% | 97.0% |  |  |
| 1068 | 4 | 97.7% | 95.0% |  |  |
| 1067 | 4 | 97.5% | 97.0% |  |  |
| 1066 | 4 | 97.4% | 97.5% |  |  |
| 1065 | 4 | 97.7% | 96.5% |  |  |
| 1064 | 4 | 97.7% | 97.5% |  |  |
| 1063 | 4 | 97.6% | 97.0% |  |  |
