# Readback

A controller says "CSA1DZ, descend flight level one hundred". The pilot replies "descending flight level one one zero, CSA1DZ". Nobody notices. That missed **readback error** is a classic cause of altitude busts and loss of separation.

Readback listens to the radio frequency, works out what each aircraft was cleared to do, checks the pilot's readback against it, and then checks the aircraft's actual track (ADS-B) against the clearance. When something doesn't match it raises an alert with the audio clip and the flight track attached, and an agent can draft the occurrence report.

It runs three ways: on a live frequency with ADS-B, on recordings from the unit's voice recorder (post-incident review, training, quality assurance), and as a replay of simulated traffic with planted errors. For recordings there is a **review console**. Upload a shift's audio and it is checked in the background. Reviewers then give each alert a verdict and a note, the verdicts build an alert register that exports to CSV, and the confirmed share shows how precise Readback actually is on that unit's radio.

Built for the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) (Oct 26 – Nov 4, 2026), Indie track, "Push the Frontier" theme.

## How it works

```mermaid
flowchart LR
    R[VHF radio audio] --> T[Muse Voice Transcribe<br/>diarization + keyword biasing]
    A[ADS-B: aircraft in range<br/>OpenSky Network] -->|callsigns as keywords| T
    T --> P[Clearance parser<br/>own tagger / Muse Spark]
    P --> M{Readback matches<br/>clearance?}
    A --> C{Track conforms<br/>to clearance?}
    P --> C
    M -->|no| X[Alert + occurrence report agent<br/>Muse Spark, tool calls]
    C -->|no| X
```

- **Hearing the radio.** Each push-to-talk transmission goes to Muse Voice Transcribe on its own, and its keyword biasing gets the callsigns of aircraft that ADS-B says are actually nearby. Callsigns are the hardest part of ATC speech to recognize and the most important to get right.
- **Understanding clearances.** Each turn becomes a structured clearance: callsign, instruction, altitude, heading, speed, frequency, runway, squawk.
- **Two checks.** The readback against the clearance, and the aircraft's ADS-B track against the clearance (for example, cleared to FL100 but still descending through it).
- **Explaining what happened.** For any alert, a Muse Spark agent pulls the transmissions, ADS-B track and other alerts it needs through tool calls and drafts a structured occurrence report.
- **On-premises parsing.** Air navigation providers are wary of sending radio content to a cloud service. Readback's own tagger parses clearances on a CPU, with no network. For a unit with a GPU there is also **Muse Glimmer 30B fine-tuned as Readback's parser** (open weights, a 0.1B-parameter LoRA adapter). It is the most accurate parser on the blind real-radio test, and it can run on the unit's own hardware ([below](#fine-tuned-muse-glimmer)).

## Status

| Date | Work |
|---|---|
| Oct 27 | Project scaffold. Voice Transcribe client, ATC text normalizer, recognition metrics, and the day-1 go/no-go test of Voice Transcribe on real ATC radio. |
| Oct 27 | Clearance schema, rule-based clearance parser, readback comparator, Muse Spark parser (structured output), and a text-level evaluation on 432 real controller/pilot exchanges: 98.4% of planted readback errors caught at about 98% precision. |
| Oct 27 | ADS-B conformance checks (level bust, wrong direction, level-off short, heading, squawk), the live frequency monitor, a simulated Bengaluru approach with planted errors and VHF-style radio audio, and a radar-scope web UI with replay and live OpenSky traffic. |
| Oct 27 | Readback's own clearance parser: a small neural tagger that trains itself continuously on a four-level synthetic curriculum, is tested on 287 hand-labeled real transmissions, and reports to a live training dashboard and [`TRAINING.md`](TRAINING.md). |
| Oct 27 | A blind real-radio test for the tagger on ATCO2 (seven airports). Set 1 showed the tagger had overfit to Prague; generator v3 widened the phraseology, and a fresh set 2 now measures it. |
| Oct 28 | First live Meta API runs. The Voice Transcribe go/no-go on 300 real-radio transcriptions came back CAUTION: digits pass, callsigns don't. Muse Spark was scored as a parser. Voice Transcribe and Spark now run live in the radar UI. Heard callsigns are matched to ADS-B, and the recognizer's stutters are dropped. On transcribed radio audio, the monitor catches all 68 planted problems. |
| Oct 29 | Recordings: drop any audio file in the browser, and it is cut into transmissions at the silences, transcribed and checked. On a real Prague recording it catches a squawk read back as 1402 instead of 1404. Occurrence-report agent (Muse Spark with tool calls). Speaker roles from what is said ("ready for", "request") and from who was just addressed; a misheard callsign recovered from the flight number. Spark and the tagger scored on the blind set. Docker image, built and smoke-tested in CI. |
| Oct 29 | Muse Glimmer 30B fine-tuned as the clearance parser: QLoRA on one H100 (Modal), 20,000 synthetic transmissions, 49 minutes. On the blind seven-airport set it scores 86.3% exact and 92.1% item F1, the best of the four parsers, and it flags fewer real pairs than Spark. |
| Oct 29 | Generator v4: cut-off clips are labeled only from what can still be heard. Most of the tagger's last level-3 mistakes were labels no parser could learn. |
| Oct 29 | One product UI: Monitor, Review and Training share a sidebar with navigation, a UTC clock and live status for Voice Transcribe, Spark, Glimmer and the on-prem tagger; metric cards; keyboard shortcuts for reviewers; phone layouts. |
| Oct 29 | Review console for quality and safety teams. Recordings up to 4 hours are checked as background jobs with progress, and a transmission Voice Transcribe fails on is skipped instead of failing the shift. Everything is kept in SQLite: recordings, reviewer verdicts and notes, and drafted reports, so a restart loses nothing. Alert register as CSV, field precision from the verdicts, an optional access token, a retention period, and a configurable live airspace. |

Everything in this repository was written during the hackathon.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). ffmpeg is needed for recordings in formats other than WAV.

```bash
uv sync
cp .env.example .env   # then add your Meta Model API key
uv run readback-ui     # http://127.0.0.1:8000
uv run python scripts/fetch_data.py   # only for the evaluations
```

Or with Docker (ffmpeg, espeak-ng and the CPU tagger included):

```bash
docker build -t readback .
docker run -p 8000:8000 --env-file .env -v readback-data:/data readback
```

| Variable | Default | |
|---|---|---|
| `MODEL_API_KEY` | (none) | Meta Model API key. Without it, scenarios replay from the script with the tagger or rules; Voice Transcribe, Spark, recordings and reports are off. |
| `READBACK_PARSER` | `tagger` | `rules` to use the rule-based parser in the monitor. |
| `READBACK_GLIMMER` | (none) | Offer the fine-tuned Glimmer parser: `1` for the adapter trained here, or another adapter's path in the volume. Needs the deployed Modal app and `uv sync --group modal`. |
| `READBACK_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Address the server listens on. |
| `READBACK_PORT` | `8000` | Port. |
| `READBACK_DATA` | `./var` (`/data` in Docker) | Where recordings, verdicts and reports are kept (`readback.db` plus `audio/`). Back up this folder. |
| `READBACK_TOKEN` | (none) | When set, every API and audio request needs this access token. The UI asks for it once and keeps it in an HttpOnly cookie for 12 hours. |
| `READBACK_RETENTION_DAYS` | `0` (keep) | Delete recordings, with their verdicts and reports, after this many days. |
| `READBACK_WORKERS` | `2` | Recordings checked at the same time. |
| `READBACK_MAX_UPLOAD_MB` | `500` | Largest upload accepted. |
| `READBACK_LIVE_CENTER`, `READBACK_LIVE_NAME` | Bengaluru | Centre (`"lat,lon"`) and name of the airspace the Live tab shows. |

`GET /healthz` reports the parser in use, whether the Muse models are available, and whether a token is required.

## Day-1 go/no-go test

Before building anything on top, we measure how well Voice Transcribe hears real ATC radio, with and without callsign biasing:

```bash
uv run python scripts/spike_asr.py --dry-run   # checks the pipeline, no API calls
uv run python scripts/spike_asr.py             # 100 clips x 3 keyword conditions
```

The report goes to [`results/spike-asr.md`](results/spike-asr.md). It scores word error rate, digit error rate (altitudes, headings, frequencies) and callsign accuracy. Recognizer output and references are compared in a canonical form, so `CSA1DZ descend FL100` matches `csa one delta zulu descend flight level one hundred`.

Result on 60 ATCO2 and 40 UWB-ATCC clips (real VHF radio), with each condition's keyword list:

| ATCO2 keywords | WER | Digit error | Callsign accuracy | Verdict |
|---|---|---|---|---|
| none | 44.4% | 32.6% | 6.7% | NO-GO |
| ICAO phraseology | 31.0% | 22.2% | 13.3% | NO-GO |
| phraseology + ADS-B callsigns in range | **25.3%** | **17.4%** | **56.7%** | **CAUTION** |

Keyword biasing is what makes Voice Transcribe usable on ATC radio. Giving it the callsigns ADS-B says are nearby takes callsign accuracy from 7% to 57%, and digit error is inside the GO bar (25%). Callsigns are still below the GO bar (70%), so the monitor doesn't trust a heard callsign on its own (see [Listening to the radio](#listening-to-the-radio)). Two things we learned along the way:

- The API rejects keywords longer than 30 characters, and most spoken callsigns are longer ("air portugal nine three one charlie"). `keywords.fit` cuts them at a word boundary.
- Voice Transcribe sometimes repeats a word or two where a phrase ends ("heading two two five, five", "flight level flight level"). `asr.drop_repeats` removes those, but never repeated digits with no break between them, because "one one zero" is a real level. On the same real-radio transcripts it lowers WER in every condition (ATCO2 with ADS-B keywords: 25.3% → 23.7%) and never raises digit error ([`results/spike-asr-drop-repeats.md`](results/spike-asr-drop-repeats.md)).

## Readback error detection

Each transmission is parsed into a structured clearance (callsign plus items such as `flight_level 100`, `heading 240 left`, `frequency 119.605`, `cleared_to_land 27l`), and the pilot's readback is compared with the controller's instruction. A wrong value or callsign is an error. An omitted item is a warning, because pilots legitimately shorten readbacks.

There are four parsers:
- a fast rule-based one for standard ICAO phraseology (`src/readback/rules.py`)
- Readback's own trained tagger ([below](#a-parser-that-trains-itself))
- Muse Spark with a strict JSON schema (`src/readback/llm.py`)
- Muse Glimmer 30B fine-tuned for this task ([below](#fine-tuned-muse-glimmer), `src/readback/glimmer.py`)

```bash
uv run python scripts/eval_readback.py                  # rule-based parser
uv run python scripts/eval_readback.py --parser both    # also Muse Spark (needs the API key)
uv run python scripts/eval_readback.py --parser all --glimmer   # all four
```

The evaluation pairs each controller instruction in UWB-ATCC with the pilot's reply, then plants one readback error per pair by changing a digit the pilot echoed (flight level, heading, squawk, frequency, runway) or a character of the callsign. Those are errors by construction, so recall is exact. Results: [`results/readback-eval.md`](results/readback-eval.md).

| Parser | Real pairs | Flagged | Planted errors | Recall | Precision (approx.) |
|---|---|---|---|---|---|
| Rule-based | 441 | 2.0% | 311 | 98.4% | 98.1% |
| Tagger (round 248) | 441 | 9.3% | 311 | 98.4% | 94.4% |
| Muse Spark | 441 | 16.8% | 311 | **99.4%** | 89.8% |
| Muse Glimmer, fine-tuned | 441 | 10.0% | 311 | 97.7% | 92.7% |

These pairs are from Prague, the phraseology the rules were written for, and they were mined with the rule-based callsign matcher, which flatters the rules here. On radio the rules weren't written for, they fall apart. On hand-labeled transmissions ([`results/parser-eval.md`](results/parser-eval.md), reference transcripts):

| Test set | Parser | Exact | Item F1 | Callsign |
|---|---|---|---|---|
| Prague (UWB-ATCC), held-out half (138) | rule-based | 67.4% | 87.4% | 76.8% |
| | tagger | **90.6%** | **97.2%** | 94.2% |
| | Muse Spark | 87.7% | 95.7% | 94.2% |
| | Muse Glimmer, fine-tuned | **92.0%** | 95.1% | **97.1%** |
| **ATCO2 set 2, blind**, seven airports (263) | rule-based | 34.6% | 86.6% | 35.7% |
| | tagger | 80.2% | 91.7% | 86.3% |
| | Muse Spark | 85.2% | 90.5% | **95.1%** |
| | Muse Glimmer, fine-tuned | **86.3%** | **92.1%** | 92.8% |

*Exact* means the callsign and every item are right. Spark is best on callsigns it has never seen. The fine-tuned Glimmer is best overall on the blind set: most exact clearances and the best item F1, with fewer false alarms on real pairs than Spark. The tagger is close on clearance items, runs offline on a CPU in milliseconds, and raises fewer false alarms still. The UI lets you pick any of them. Spark's blind score was 85.9% in an earlier run; its answers vary slightly from run to run.

Several of the flagged real pairs look like genuine readback errors in the recordings: for example, runway 31 read back as 13, and squawk 1404 read back as 1402.

## Live monitor and radar scope

`readback.monitor` follows a frequency turn by turn. It pairs each pilot reply with the open instruction for that callsign and raises readback alerts: wrong value, omitted item, a reply from a similar-sounding callsign, or no reply within 20 seconds. Once a clearance is read back, it watches the aircraft's ADS-B track (`readback.adsb`). It flags a bust of the cleared level by more than 300 ft, a climb when the clearance was to descend, levelling off short, a heading more than 20° off after the turn, and the wrong squawk.

```bash
uv run readback-ui    # then open http://127.0.0.1:8000
```

The **Scenario** tab replays a simulated Bengaluru (VOBL) approach frequency through the monitor in real time: radar scope, radio log with the parsed clearance under each transmission, and alerts as they fire. The **Live BLR traffic** tab shows real aircraft around Bengaluru from the OpenSky Network.

Scenarios come from `readback.sim`. Five aircraft get descents, turns, speed changes, frequency changes and squawks. Each fleet includes two callsigns with the same digits swapped, and the simulator plants readback errors, callsign confusions, omitted items, unanswered calls and track deviations. `scripts/make_scenarios.py --audio` also renders each transmission as speech (Windows voices, or espeak-ng on Linux) passed through a VHF radio effect, for the Voice Transcribe path.

On 200 scenarios (5,220 transmissions), the monitor with the rule-based parser caught all 2,052 planted problems, and none of its 1,351 error-level alerts were false. Details: [`results/scenario-eval.md`](results/scenario-eval.md). The scenarios use clean standard phraseology, so this checks the monitor's logic end to end. It is not a measure of accuracy on real radio.

```bash
uv run python scripts/eval_scenarios.py --scenarios 200
```

### Listening to the radio

In the Scenario tab, set **Speech** to *Voice Transcribe*, and each transmission's radio audio is sent to Muse Voice Transcribe as the replay reaches it. The keywords are the callsigns ADS-B shows in range. The transcript then goes to the chosen **Parser**: the tagger, Muse Spark, or the rules. The radio log shows what was heard, what was actually said when the two differ, and how long Voice Transcribe and the parser took. This needs `MODEL_API_KEY` in `.env`. Without it, the UI falls back to the script.

Three safeguards come from what speech recognition gets wrong:

- **Callsigns are matched to ADS-B.** A heard callsign that isn't on ADS-B becomes the aircraft it most likely is: the only one in range that matches it, or that is one word away ("ifly seven eight four" when only IFLY785 is nearby). If two aircraft fit, it stays as heard. A pilot who answers with another aircraft's real callsign still matches that aircraft, so callsign confusion is still caught. The radio log marks every match.
- **Stutters are dropped** (`asr.drop_repeats`, above).
- **Impossible instructions are ignored.** An instruction no controller could give (FL2770, squawk 7248) is treated as a mishearing and not checked. The same value in a pilot's readback is still an error.
- **Who is talking is inferred from what is said.** Controllers open with the callsign and pilots usually close with it, but a call asking for something ("ready for departure", "request descent") is the pilot's, and so is a transmission naming the aircraft that was just given an instruction. A callsign misheard beyond matching is recovered from the flight number when exactly one aircraft has it ("one one zero two, cleared ...").

`eval_scenarios.py --audio` measures the whole chain on 6 scenarios (146 transmissions). Each transmission is spoken, radio-filtered, transcribed by Voice Transcribe, parsed by the rule-based parser, and run through the monitor ([`results/scenario-audio-eval.md`](results/scenario-audio-eval.md)):

| | From the script | From the audio |
|---|---|---|
| Planted problems caught | 68 / 68 | **68 / 68** |
| Error alerts that matched no planted problem | 0 of 45 | 14 of 59 (precision 76%) |

Word error rate on this audio was 1.6%, with a median of 2.0 s per transmission. Before ADS-B matching and stutter removal, the same transcripts gave 58 of 68 caught at 56% precision. Most remaining false alarms come from a digit Voice Transcribe repeated inside a number ("one six six zero" for "one six zero"). That can't safely be told apart from a real readback error. Synthetic voices are cleaner than real radio, so this shows the chain working, not real-world accuracy.

```bash
uv run python scripts/eval_scenarios.py --audio    # Windows; about 150 Voice Transcribe calls, cached
```

## Checking a recording

The **Recording** tab and the review console take any audio file: a recorder export, a LiveATC archive, a phone recording of a scanner (WAV directly, other formats through ffmpeg, up to 4 hours). It is cut into transmissions at the silences between them, and each is transcribed by Voice Transcribe; only those single transmissions are sent, never the whole file. The monitor then pairs and checks them as it would live. Callsigns known to be on frequency can be given, in ADS-B form (`SWR123`) or spoken, and are used as keywords and for matching. The result is the radio log, alerts and an audio player. The same check is an API call:

```bash
curl -X POST --data-binary @frequency.wav "http://127.0.0.1:8000/api/analyze?callsigns=SWR123,DLH4CK&parser=tagger"
```

Tested on a 43-second real recording from Prague (UWB-ATCC) in which a pilot reads back squawk 1404 as 1402. Voice Transcribe's output differed from run to run on this accented radio. Of eight runs, three gave the exact alert ("read back squawk 1402, cleared squawk 1404"), three flagged the same squawk as not read back, and two blamed a wrong callsign after the recognizer dropped the word "squawk". So on real radio the error is flagged every time, but not always cleanly. That is why every alert goes to a human reviewer.

## Review console and alert register

`/review` is where a unit's quality or safety team works through recordings:

- **Upload** a recording with a name and, optionally, the callsigns on frequency. It is checked in the background with a progress bar, so a four-hour shift doesn't hold up the browser. A restart marks unfinished jobs as failed instead of leaving them hanging.
- **Review** each alert next to the audio. "▶" plays from just before it, and the transmission being played is highlighted in the radio log. Give a verdict (*Confirm*, *False alarm*, or *Reopen*) and a note; the reviewer's name and the time are recorded.
- **Report**: draft the occurrence report for any alert. The draft is kept with the recording.
- **Keyboard**: J and K move between alerts, C confirms, F marks a false alarm, R reopens, P plays from the alert, N jumps to the note, and Space pauses. A reviewer can work through a shift without the mouse.
- **Register**: every alert of every recording, with its verdict, note, reviewer and time, as CSV (`/api/alerts.csv`, or per recording). It opens cleanly in Excel, and transcript text can't run as a spreadsheet formula.
- **Field precision**: confirmed alerts as a share of all judged alerts. This is the number offline benchmarks can't give, measured on the unit's own radio, accents and phraseology.

All of it is kept in one SQLite file plus the audio under `READBACK_DATA`. Deleting a recording deletes its audio, verdicts and reports; `READBACK_RETENTION_DAYS` does the same automatically.

| API | |
|---|---|
| `POST /api/recordings?name&callsigns&parser` | Queue a recording (the body is the audio file). Returns `{"id"}`. |
| `GET /api/recordings` | All recordings with status, progress, summary and verdict counts, plus totals and field precision. |
| `GET /api/recordings/{id}` | Transmissions, alerts with their verdicts, and the audio URL. |
| `PUT /api/recordings/{id}/alerts/{i}/review` | `{"verdict": "confirmed" \| "false_alarm" \| "open", "note", "reviewer"}` |
| `POST /api/report/{id}/{i}` | Draft (or fetch the kept) occurrence report. |
| `GET /api/alerts.csv`, `GET /api/recordings/{id}/alerts.csv` | The alert register. |
| `DELETE /api/recordings/{id}` | Delete a recording and everything about it. |
| `POST /api/analyze` | Check a short recording and wait for the result (scripts). |

## Deploying for an ATC unit

Readback is **decision support for review**, not operational alerting. Its place is post-operations quality assurance, investigation support and training: the work a unit's QA or safety team does today by listening to recordings by hand. It is not a certified ATM system, and it does not sit in the controller's loop.

- **Install.** Run one container on a server inside the unit's network (`docker run ... -v readback-data:/data`). Put it behind the unit's reverse proxy for HTTPS, and set `READBACK_TOKEN`. The sign-in cookie is marked Secure when the proxy says the request came over HTTPS. Set `FORWARDED_ALLOW_IPS` to the proxy's address so the server trusts that header (it trusts only 127.0.0.1 by default).
- **What leaves the building.** Only the single transmissions go to Meta's Voice Transcribe, plus the transcripts if Muse Spark is chosen as the parser or a report is drafted. Readback's own tagger parses on the server's CPU, with no network. Recordings, verdicts and reports stay in `READBACK_DATA`.
- **Retention and backup.** Set `READBACK_RETENTION_DAYS` to the unit's recording-retention policy. Back up by copying `READBACK_DATA`.
- **Callsigns.** Give the callsigns on frequency when uploading, taken from the flight-plan or ADS-B log for the period. Recognition of callsigns is the weakest link without them.
- **Measure before you trust.** Review the first weeks of alerts and watch field precision. It is the honest measure for your radio.

## Occurrence reports

Every alert has a **Draft occurrence report** button. A Muse Spark agent (`readback.occurrence`) gets the alert and four tools: `transmissions` (filtered by callsign and time), `adsb_track` (sampled every 10 s), `alerts`, and `submit_report`. It decides what evidence to pull, then files a report against a strict schema: title, occurrence type, risk, summary, timeline, contributing factors, recommended actions, and a statement of evidence quality. The report lists the tool calls it made and downloads as Markdown. On the Prague recording above it made eight tool calls in 31 s and noted that the evidence came from speech recognition, with no ADS-B. It is a draft for a safety officer to review, not a filed report.

## How it compares

| | Readback | HAAWAII (SESAR research project) | Open-source readback checkers on GitHub |
|---|---|---|---|
| Input | Live frequency with ADS-B, recordings, simulated traffic | Recorded operations audio | Mostly audio files or batches (Whisper plus an LLM, or a knowledge graph) |
| Checks | Readback values, callsign confusion, omissions, unanswered calls, **and the aircraft's ADS-B track against the clearance** | Readback errors | Readback errors |
| Published accuracy | Rules: 98.4% recall at ~98% precision on 311 planted errors. Fine-tuned Glimmer: 86.3% exact clearances, 92.1% item F1 on a blind seven-airport set | 81% of readback errors detected at an 11% false-alarm rate, on real operations audio | Mostly none; one reports 66-78% fact precision |
| After an alert | Agent-drafted occurrence report with its evidence | Not in scope | Not in scope |
| Runs as | One command or a Docker image; the parser works offline on a CPU | Research prototype | Scripts or notebooks |

The numbers come from different data and setups, so they are not head to head. HAAWAII's figure is the stronger evidence on real operations audio. Ours on real radio is the blind parser score and the recording test above.

## A parser that trains itself

The rule-based parser only knows textbook phraseology. Real frequencies are messier: clips cut off mid-word ("…ared to land runway one three"), pilots drop keywords ("reducing one four zero"), recognizers mishear words, and airlines say their names in ways no list covers. So Readback has its own learned parser: a small BiLSTM tagger (`src/readback/tagger`, about 0.7M parameters, CPU-only) that marks the callsign and each clearance item in a transmission. A shared decoder then reads the values out of the marked spans.

It learns entirely from synthetic transmissions (`tagger/synth.py`). These are built piece by piece, so every token's label is known exactly, and a curriculum makes them harder level by level:

1. standard ICAO phraseology
2. everyday variation: readbacks without keywords, stations, greetings, wind and taxi distractors, spelled ICAO codes
3. real radio: clips cut off mid-word, hesitations, corrections, misheard words, airlines it has never seen
4. stress: every kind of noise at once, more often

Training runs in rounds (`tagger/train.py`). Each round, a candidate trains from the current champion on fresh data. It replaces the champion only if it does better on that level's fixed validation set without losing ground on real radio. The champion is then probed with new examples; whatever it gets wrong is replayed next round, and the generator draws more of those item kinds and noise types. That loop is how it finds and fixes its own weak spots. It is **optimal** once it has passed level 3 and, on the held-out half of the real transmissions, reaches 90% item F1 and beats the rule-based parser. After that it keeps training on level 4.

The real-radio test is 287 UWB-ATCC transmissions from Prague, labeled by hand (`data/gold/`, labels only). Half is a dev set that can block a promotion; the other half is only ever reported. The tagger never trains on either.

That set isn't fully blind, because the generator's phrasing was shaped after reading UWB transcripts. So there is a **blind** test on ATCO2 recordings from seven airports (Sion, Bern, Zurich, Sydney, Bratislava, Prague, Brno). Clips are sampled from ones never looked at during development, labeled by hand before any parser runs on them, and frozen (`scripts/freeze_blind.py`). ATCO2's terms don't allow sharing derived data, so the labels stay local (`data/blind/`, gitignored). A manifest is committed instead, with the SHA-256 of the labels and of the generator at freeze time. The trainer scores each new champion on these sets only as a whole, and never uses them for promotion, focus, levels or optimality. The dashboard and `TRAINING.md` show whether the generator has changed since the freeze.

The first blind set ([manifest](data/gold/atco2-blind.manifest.json), 265 transmissions) did its job. The tagger scored 98.6% item F1 on Prague but only 82.9% there, below the rule-based parser's 85.4%. It had learned Prague's phrasing, not radio in general. Generator v3 then widened the phraseology from ICAO Doc 9432 and CAP 413: VFR circuit calls, runways said without "runway", type-prefixed registrations, "heavy", gusting wind, traffic information. Labeling set 1 is what showed those gaps, so set 1 is retired. It is still reported, but marked as not blind. **Set 2** ([manifest](data/gold/atco2-blind-2.manifest.json), 263 transmissions) was drawn from the remaining clips and frozen after v3 was committed. The git history shows that order.

**Generator v4 (Oct 29) came after the set 2 freeze**, and the dashboard says so. It was found from the synthetic level-3 validation set alone, not from any real or blind transmission. Of the champion's 64 remaining validation mistakes, 40 were callsign-only, and most of them were labels no parser could learn. When a clip was cut off mid-word, v3 labeled it from what had been *said*: a fragment of a callsign word ("...ike 9 3 3 romeo") was left out of the callsign, and bare digits whose keyword had been cut away ("...0 3 0" after "heading") were still labeled a heading. v4 labels only what can still be heard. The tagger retrains on v4 by itself; blind-set scores from v4 onward are reported next to the v3 ones, not in place of them.

```bash
uv run python scripts/train_tagger.py --hours 1     # train for an hour, resuming the run
uv run python scripts/autostart.py install          # Windows: keep training forever, across reboots
uv run python scripts/autostart.py status           # what's running and how it's doing
uv run readback-ui                                  # then open http://127.0.0.1:8000/training
```

`autostart.py install` adds a Startup entry and an hourly Task Scheduler check. Both start a guardian that runs `scripts/supervise.py` and restarts it if it dies. The supervisor trains in one-hour stretches, runs a fresh date-seeded check once a day, and pushes the progress files (`training/`, [`TRAINING.md`](TRAINING.md), and at milestones `models/tagger.pt`). `autostart.py remove` undoes it. Once `models/tagger.pt` exists, the web UI's monitor uses the tagger (set `READBACK_PARSER=rules` to switch back), and `scripts/eval_readback.py --parser tagger` runs it on the real readback pairs.

## Fine-tuned Muse Glimmer

Muse Glimmer 30B is Meta's open-weights model (Apache-2.0). Fine-tuned on the tagger's curriculum, it becomes a clearance parser that is as accurate as Muse Spark or better, and it can run on a unit's own GPU, so transcripts need not leave the building.

- **Data.** `scripts/export_sft.py` writes the synthetic curriculum as chat examples: a transmission in canonical form, then the clearance as JSON. Levels 1-4 are mixed 1:2:4:1, so most examples carry real-radio noise. No real transmission is used for training.
- **Training** (`scripts/finetune_glimmer_modal.py`, on [Modal](https://modal.com)):
  - QLoRA: the base model in 4-bit nf4, with a rank-16 LoRA on the language model's attention and MLP projections. That is 105M trainable parameters (0.35%); the vision tower is untouched.
  - One H100, 1,250 steps of 16 (one epoch of 20,000 examples), 49 minutes. Loss fell from 0.27 to 0.003.
  - The answer turn is opened on Glimmer's user channel (`<|start|>assistant to=user<|message|>`) in training and at inference, so the model answers at once instead of reasoning first.
- **Inference.** The deployed app loads the base model and the adapter on one GPU and parses transmissions in batches. `src/readback/glimmer.py` is the client. It caches answers like the Spark parser does, and it drops any item kind the model invents (the answer isn't held to a schema the way Spark's is). The evaluations send only transcripts, and the scoring and hand labels stay on this machine.

```bash
uv sync --group modal
uv run python scripts/export_sft.py --n 20000
uv run modal run scripts/finetune_glimmer_modal.py::download            # 60 GB of weights, once
uv run modal deploy scripts/finetune_glimmer_modal.py
uv run modal run scripts/finetune_glimmer_modal.py::train --steps 1250 --spawn
uv run python scripts/eval_gold.py --spark --glimmer
```

Results are in the tables [above](#readback-error-detection). It is weaker on the retired ATCO2 set 1 (85.8% item F1, against the tagger's 95.4%), so it is not better everywhere. The fine-tune never saw real radio, and one epoch of one curriculum is a first run, not a tuned one.

## Tests

```bash
uv run pytest
uv run ruff check .
```

CI runs both, then builds the Docker image and smoke-tests it: the UI, the review console, the data volume, and speech synthesis.

## Data and licenses

The evaluation audio is downloaded at run time and never committed. Only aggregate metrics are published.

- **ATCO2 test set (1 h)**: real VHF recordings from Czech airports, from the [ATCO2 project](https://www.atco2.org/data) (Zuluaga-Gomez et al., 2022, [arXiv:2211.04054](https://arxiv.org/abs/2211.04054)). Used under the ATCO2 end-user data agreement, via the [Hugging Face mirror](https://huggingface.co/datasets/Jzuluaga/atco2_corpus_1h). The blind-test labels derived from it are kept local; only their hash and aggregate scores are published.
- **UWB-ATCC**: University of West Bohemia ATC corpus ([LINDAT](https://lindat.mff.cuni.cz/repository/xmlui/handle/11858/00-097C-0000-0001-CCA1-0)), CC BY-NC-SA 4.0, via the [Hugging Face mirror](https://huggingface.co/datasets/Jzuluaga/uwb_atcc). The hand labels in `data/gold/uwb-atcc-gold.jsonl` (utterance ids, callsigns and clearance values, no transcript text) are derived from it and shared under the same CC BY-NC-SA 4.0 license.

Code is MIT licensed. Readback is a research prototype, not a certified air traffic management tool.
