# Readback error detection: text-level evaluation

Run 2026-10-29 06:49 UTC, seed 7. Instruction/readback pairs mined from UWB-ATCC (controller and pilot labels from the corpus); one error planted per pair where the pilot echoed a value or callsign.

| Parser | Real pairs | Flagged real pairs | Planted errors | Recall | Value errors | Callsign errors | Precision (approx.) | F1 |
|---|---|---|---|---|---|---|---|---|
| rules | 441 | 2.0% | 311 | 98.4% | 96.7% (n=153) | 100.0% (n=158) | 98.1% | 98.2% |
| tagger (models/tagger.pt) | 441 | 9.3% | 311 | 98.4% | 96.7% (n=153) | 100.0% (n=158) | 94.4% | 96.4% |
| muse-spark-1.3 | 441 | 16.8% | 311 | 99.4% | 98.7% (n=153) | 100.0% (n=158) | 89.8% | 94.4% |
| Muse Glimmer 30B, fine-tuned | 441 | 10.0% | 311 | 97.7% | 95.4% (n=153) | 100.0% (n=158) | 92.7% | 95.1% |

- **Recall**: share of planted readback errors that raised an error-level finding.
- **Precision (approx.)**: planted errors caught versus the same pairs flagged before an error was planted. Some of those flags are real readback errors or corpus quirks, so this is a lower bound.
- Omitted readback items are reported as warnings, not errors, and don't count here.
- Reference transcripts are used, so speech recognition errors are not included.
- Pairs are mined with the rule-based callsign matcher, which flatters the rule-based parser on callsign errors.

Data: UWB-ATCC (University of West Bohemia, CC BY-NC-SA 4.0). Only aggregate numbers are published.
