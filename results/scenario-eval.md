# Full monitor on simulated scenarios

Run 2026-10-27 05:47 UTC: 200 scenarios (seeds 1000-1199), 5220 transmissions, rule-based parser, speaker roles inferred.

| Planted problem | Planted | Caught | Recall |
|---|---|---|---|
| Wrong readback value | 750 | 750 | 100.0% |
| Callsign confusion | 53 | 53 | 100.0% |
| Omitted readback item | 180 | 180 | 100.0% |
| Unanswered instruction | 53 | 53 | 100.0% |
| Vertical deviation | 823 | 823 | 100.0% |
| Heading deviation | 174 | 174 | 100.0% |
| Squawk deviation | 19 | 19 | 100.0% |
| **All** | 2052 | 2052 | 100.0% |

- Error-level alerts: 1351, of which 0 matched no planted problem (precision 100.0%).
- Warning-level alerts: 717, of which 16 matched no planted problem.
- Track deviations were flagged a median 61 s after the planted onset (onset is approximate: about 30 s after the readback for a bust).

Scenarios come from `readback.sim`: an illustrative Bengaluru approach frequency, not real aeronautical data. They use clean standard phraseology, so this checks the monitor's logic end to end; real-world accuracy is measured on recorded ATC speech (`results/readback-eval.md`, `results/spike-asr.md`).
