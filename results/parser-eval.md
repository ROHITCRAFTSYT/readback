# Clearance parsers on hand-labeled real transmissions

Generated 2026-10-28 18:49 UTC by `scripts/eval_gold.py`. Reference
transcripts (perfect speech recognition); every parser's output goes through the same
`tidy`. *Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs.

| Test set | Parser | n | Exact | Item F1 | Callsign |
|---|---|---|---|---|---|
| Prague (UWB-ATCC), held-out half | rule-based | 138 | 67.4% | 87.4% | 76.8% |
| Prague (UWB-ATCC), held-out half | tagger (round 248) | 138 | 90.6% | 97.2% | 94.2% |
| Prague (UWB-ATCC), held-out half | muse-spark-1.3 | 138 | 87.7% | 95.7% | 94.2% |
| Prague (UWB-ATCC), dev half | rule-based | 149 | 78.5% | 86.3% | 87.9% |
| Prague (UWB-ATCC), dev half | tagger (round 248) | 149 | 91.3% | 98.5% | 92.6% |
| Prague (UWB-ATCC), dev half | muse-spark-1.3 | 149 | 94.0% | 97.5% | 96.6% |
| **ATCO2 set 2, blind** | rule-based | 263 | 34.6% | 86.6% | 35.7% |
| **ATCO2 set 2, blind** | tagger (round 248) | 263 | 80.2% | 91.7% | 86.3% |
| **ATCO2 set 2, blind** | muse-spark-1.3 | 263 | 85.9% | 91.2% | 95.1% |
| ATCO2 set 1 (retired, not blind) | rule-based | 265 | 36.6% | 85.4% | 43.4% |
| ATCO2 set 1 (retired, not blind) | tagger (round 248) | 265 | 83.0% | 95.4% | 85.7% |
| ATCO2 set 1 (retired, not blind) | muse-spark-1.3 | 265 | 87.5% | 93.6% | 93.2% |

The ATCO2 labels stay local (ATCO2 terms); their hashes are in `data/gold/`. Set 2 was
frozen before any parser ran on it and has never been used to change the tagger or its
generator. The rule-based parser's airline designators are discovered from each corpus.
