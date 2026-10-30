# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** optimal, round 550, level 4 (stress: every kind of noise at once)
- **Champion:** round 499
- **Trained for:** 24.0 h on 10,849,318 examples (started 2026-10-27, updated 2026-10-30 15:47 UTC)

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
Generator unchanged since the freeze: **no** (see git history).

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

- `kind:runway` ×1.84
- `kind:hold_short` ×1.47
- `kind:cleared_to_land` ×1.33
- `kind:direct_to` ×1.3
- `kind:frequency` ×1.27
- `noise:truncate` ×1.18
- `kind:line_up` ×1.16
- `noise:repeat` ×1.12

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |
| 2026-10-28 | 109 | 96.0% | 98.1% | 92.4% |
| 2026-10-29 | 248 | 96.5% | 97.6% | 91.8% |
| 2026-10-30 | 499 | 98.2% | 98.1% | 91.5% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 550 | 4 | 97.8% | 96.5% |  |  |
| 549 | 4 | 97.9% | 96.5% |  |  |
| 548 | 4 | 97.5% | 96.5% |  |  |
| 547 | 4 | 97.8% | 98.5% |  |  |
| 546 | 4 | 97.7% | 97.0% |  |  |
| 545 | 4 | 97.5% | 95.6% |  |  |
| 544 | 4 | 97.7% | 97.5% |  |  |
| 543 | 4 | 97.7% | 98.0% |  |  |
| 542 | 4 | 97.8% | 97.5% |  |  |
| 541 | 4 | 97.4% | 96.5% |  |  |
| 540 | 4 | 97.6% | 95.6% |  |  |
| 539 | 4 | 97.8% | 97.0% |  |  |
| 538 | 4 | 97.4% | 97.0% |  |  |
| 537 | 4 | 97.9% | 96.5% |  |  |
| 536 | 4 | 97.7% | 94.6% |  |  |
