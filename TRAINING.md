# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** optimal, round 5802, level 4 (stress: every kind of noise at once)
- **Champion:** round 4138
- **Trained for:** 245.0 h on 113,576,788 examples (started 2026-10-27, updated 2026-11-24 12:02 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 83.7% | 34.6% | 92.5% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 85.3% | 36.6% | 93.5% | 85.4% |
| Real radio, held-out half (n=138) | 89.1% | 67.4% | 97.6% | 87.4% |
| Real radio, dev half (n=149) | 89.3% | 78.5% | 96.5% | 86.3% |
| Synthetic level 4 (n=2000) | 98.1% | 16.1% | 99.6% | 67.3% |

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

- `kind:cleared_for_takeoff` ×1.94
- `kind:cleared_to_land` ×1.67
- `kind:squawk` ×1.51
- `noise:repeat` ×1.43
- `kind:line_up` ×1.27
- `noise:truncate` ×1.19
- `noise:correction` ×1.18
- `noise:misheard` ×1.17

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-11-11 | 1233 | 98.2% | 98.1% | 93.3% |
| 2026-11-12 | 1233 | 98.2% | 98.1% | 93.3% |
| 2026-11-13 | 1233 | 98.8% | 98.1% | 93.3% |
| 2026-11-14 | 1233 | 98.4% | 98.1% | 93.3% |
| 2026-11-15 | 1233 | 98.5% | 98.1% | 93.3% |
| 2026-11-16 | 1233 | 98.3% | 98.1% | 93.3% |
| 2026-11-17 | 1233 | 98.7% | 98.1% | 93.3% |
| 2026-11-18 | 1233 | 98.3% | 98.1% | 93.3% |
| 2026-11-19 | 1233 | 98.2% | 98.1% | 93.3% |
| 2026-11-20 | 4138 | 98.4% | 97.6% | 92.5% |
| 2026-11-21 | 4138 | 98.3% | 97.6% | 92.5% |
| 2026-11-22 | 4138 | 98.8% | 97.6% | 92.5% |
| 2026-11-23 | 4138 | 98.7% | 97.6% | 92.5% |
| 2026-11-24 | 4138 | 99.1% | 97.6% | 92.5% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 5802 | 4 | 97.7% | 96.5% |  |  |
| 5801 | 4 | 97.8% | 96.1% |  |  |
| 5800 | 4 | 97.6% | 95.1% |  |  |
| 5799 | 4 | 97.7% | 97.1% |  |  |
| 5798 | 4 | 97.7% | 96.5% |  |  |
| 5797 | 4 | 97.5% | 94.1% |  |  |
| 5796 | 4 | 97.7% | 96.5% |  |  |
| 5795 | 4 | 97.5% | 97.5% |  |  |
| 5794 | 4 | 97.5% | 96.5% |  |  |
| 5793 | 4 | 97.8% | 97.5% |  |  |
| 5792 | 4 | 97.8% | 96.5% |  |  |
| 5791 | 4 | 97.7% | 96.5% |  |  |
| 5790 | 4 | 97.4% | 96.5% |  |  |
| 5789 | 4 | 97.7% | 96.5% |  |  |
| 5788 | 4 | 97.7% | 95.6% |  |  |
