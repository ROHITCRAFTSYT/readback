# Day-1 go/no-go: Muse Voice Transcribe on ATC radio

Run 2026-10-28 18:13 UTC, seed 7, 14 distractor callsigns, model `muse-voice-transcribe-1.0`, transcripts cleaned by `asr.drop_repeats`

| Dataset | Keywords | Clips | Failed | WER | Digit error | Callsign accuracy | Median latency | Verdict |
|---|---|---|---|---|---|---|---|---|
| atco2-1h | none | 60 | 0 | 44.3% | 32.6% | 6.7% (n=30) | 1.99s | NO-GO |
| atco2-1h | phraseology | 60 | 0 | 30.2% | 21.5% | 13.3% (n=30) | 1.88s | NO-GO |
| atco2-1h | context | 60 | 0 | 23.7% | 17.4% | 56.7% (n=30) | 1.71s | CAUTION |
| uwb-atcc | none | 40 | 0 | 62.4% | 51.3% | 4.3% (n=23) | 1.53s | NO-GO |
| uwb-atcc | phraseology | 40 | 0 | 44.8% | 36.2% | 8.7% (n=23) | 1.54s | NO-GO |
| uwb-atcc | context | 40 | 0 | 35.6% | 30.9% | 56.5% (n=23) | 1.51s | CAUTION |

**Decision (ATCO2, real VHF radio, best keyword condition): CAUTION** with `context` keywords.

- GO: callsign accuracy at least 70% and digit error at most 25%. CAUTION: at least 50% and at most 40%. Anything else is NO-GO.
- Text is compared in a canonical form (spelled letters and digits as single tokens), so `CSA1DZ` and `csa one delta zulu` match.
- Airline designators found in the references: aeroflot, aerosvit, air, al, alitalia, austrian, beair, beline, bird, bline, britair, calibra, camber, check, checker, cimber, csa, easy, emirates, fedex, france, heli, jet, jetstar, jobair, klm, legend, lot, lufthansa, malev, mosquito, qantas, qlink, quality, rex, russia, shamrock, shuttle, singapore, skytravel, swedestar, swiss, trans, turkish, united, ups, wings.

Data: ATCO2 1h test set (ATCO2 project, end-user data agreement) and UWB-ATCC (University of West Bohemia, CC BY-NC-SA 4.0). Only aggregate numbers are published.
