# Tagger training

Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains
itself here on synthetic radio traffic and is checked against hand-labeled real
transmissions it never trains on. This page is rewritten after every round by
`scripts/train_tagger.py`; the live dashboard is the Training tab of
`uv run readback-ui`.

- **State:** optimal, round 400, level 4 (stress: every kind of noise at once)
- **Champion:** round 396
- **Trained for:** 18.3 h on 7,914,604 examples (started 2026-10-27, updated 2026-10-29 13:44 UTC)

## Champion vs the rule-based parser

| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |
|---|---|---|---|---|
| **Blind: ATCO2 set 2, 7 airports** (n=263) | 84.0% | 34.6% | 92.3% | 86.6% |
| ATCO2 set 1 (retired, not blind) (n=265) | 86.4% | 36.6% | 94.4% | 85.4% |
| Real radio, held-out half (n=138) | 89.9% | 67.4% | 98.6% | 87.4% |
| Real radio, dev half (n=149) | 90.6% | 78.5% | 97.5% | 86.3% |
| Synthetic level 4 (n=2000) | 97.5% | 16.1% | 99.4% | 67.3% |

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

- `kind:hold_short` ×1.99
- `kind:line_up` ×1.76
- `kind:cleared_for_takeoff` ×1.44
- `kind:speed` ×1.29
- `noise:truncate` ×1.14
- `kind:squawk` ×1.13
- `noise:repeat` ×1.11
- `kind:direct_to` ×1.09

## Daily fresh checks

A new date-seeded level-3 test set every day, plus the real transmissions.

| Date | Champion round | Synthetic exact | Real (held-out) item F1 | Blind item F1 |
|---|---|---|---|---|
| 2026-10-27 | 22 | 96.5% | 97.2% | – |
| 2026-10-28 | 109 | 96.0% | 98.1% | 92.4% |
| 2026-10-29 | 248 | 96.5% | 97.6% | 91.8% |

## Recent rounds

| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |
|---|---|---|---|---|---|
| 400 | 4 | 97.1% | 97.0% |  |  |
| 399 | 4 | 97.5% | 98.5% |  |  |
| 398 | 4 | 97.4% | 97.5% |  |  |
| 397 | 4 | 97.0% | 97.5% |  |  |
| 396 | 4 | 97.5% | 97.5% | yes |  |
| 395 | 4 | 97.3% | 96.5% | yes |  |
| 394 | 4 | 96.7% | 99.0% |  |  |
| 393 | 4 | 97.0% | 95.6% |  |  |
| 392 | 4 | 97.0% | 96.0% |  |  |
| 391 | 4 | 97.0% | 99.5% |  |  |
| 390 | 4 | 97.2% | 98.0% | yes |  |
| 389 | 4 | 96.6% | 95.6% | yes |  |
| 388 | 4 | 96.5% | 99.5% |  |  |
| 387 | 4 | 96.5% | 96.5% | yes |  |
| 386 | 4 | 95.8% | 96.5% | yes |  |
