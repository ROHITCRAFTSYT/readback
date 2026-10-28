# Full monitor on simulated radio audio

Run 2026-10-28 18:45 UTC: 6 scenarios (seeds 1000-1005), 146 transmissions, rule-based parser, speaker roles inferred.

Every transmission was spoken by Windows speech synthesis (one voice for the controller, another for pilots), band-limited to 300-3000 Hz with VHF noise at 10-20 dB SNR, and transcribed by `muse-voice-transcribe-1.0` with the scenario's ADS-B callsigns as keywords, its stutters dropped (`asr.drop_repeats`): WER 1.6%, median latency 2.00 s, 0 failed calls.

| Planted problem | Planted | Caught from the script | Caught from the audio |
|---|---|---|---|
| Wrong readback value | 23 | 23 (100.0%) | 23 (100.0%) |
| Callsign confusion | 1 | 1 (100.0%) | 1 (100.0%) |
| Omitted readback item | 5 | 5 (100.0%) | 5 (100.0%) |
| Unanswered instruction | 1 | 1 (100.0%) | 1 (100.0%) |
| Vertical deviation | 31 | 31 (100.0%) | 31 (100.0%) |
| Heading deviation | 7 | 7 (100.0%) | 7 (100.0%) |
| Squawk deviation | 0 | 0 (0.0%) | 0 (0.0%) |
| **All** | 68 | 68 (100.0%) | 68 (100.0%) |

From the script:

- Error-level alerts: 45, of which 0 matched no planted problem (precision 100.0%).
- Warning-level alerts: 23, of which 0 matched no planted problem.
- Track deviations were flagged a median 59 s after the planted onset (onset is approximate: about 30 s after the readback for a bust).

From the audio:

- Error-level alerts: 59, of which 14 matched no planted problem (precision 76.3%). False alarms by class: {'readback-value': 10, 'track-vertical': 3, 'callsign': 1}.
- Warning-level alerts: 26, of which 3 matched no planted problem.
- Track deviations were flagged a median 57 s after the planted onset (onset is approximate: about 30 s after the readback for a bust).

Synthetic speech is cleaner than real radio (no accents, no clipped words, no overlapping transmissions), so this shows the pipeline working end to end, not real-world accuracy; `results/spike-asr.md` measures Voice Transcribe on recorded ATC radio.
