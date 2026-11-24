# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** optimal, round 5842, level 4 (stress: every kind of noise at once)
- **Champion:** round 4138
- **Trained for:** 247.8 h on 114,358,624 examples (started 2026-10-27, updated 2026-11-24 15:47 UTC)

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

- `kind:cleared_for_takeoff` ×1.99
- `kind:direct_to` ×1.98
- `kind:line_up` ×1.55
- `noise:truncate` ×1.49
- `noise:repeat` ×1.38
- `kind:hold_short` ×1.28
- `kind:frequency` ×1.2
- `noise:hesitation` ×1.17

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
| 5842 | 4 | 97.8% | 95.6% |  |  |
| 5841 | 4 | 97.5% | 95.6% |  |  |
| 5840 | 4 | 97.8% | 97.5% |  |  |
| 5839 | 4 | 97.5% | 95.1% |  |  |
| 5838 | 4 | 97.6% | 97.5% |  |  |
| 5837 | 4 | 97.5% | 96.6% |  |  |
| 5836 | 4 | 97.8% | 97.5% |  |  |
| 5835 | 4 | 97.7% | 97.0% |  |  |
| 5834 | 4 | 97.7% | 97.1% |  |  |
| 5833 | 4 | 98.0% | 96.1% |  |  |
| 5832 | 4 | 97.8% | 96.0% |  |  |
| 5831 | 4 | 97.8% | 97.0% |  |  |
| 5830 | 4 | 97.7% | 95.0% |  |  |
| 5829 | 4 | 97.8% | 97.5% |  |  |
| 5828 | 4 | 97.8% | 97.5% |  |  |
