"""Web app: replays a scenario through the monitor in real time and streams it to a radar scope.

Run with `uv run readback-ui` (or `uv run uvicorn readback.server:app`), then open
http://127.0.0.1:8000.

A replay can run on the scripted text, or "listen": every transmission's radio audio goes to
Muse Voice Transcribe (keywords: the callsigns ADS-B shows in range) and the transcript to the
chosen parser (the tagger, Muse Spark or the rules), as it would on a live frequency. Listening
needs MODEL_API_KEY in .env; transcripts are cached in results/raw/asr-live/.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from readback import rules
from readback.adsb import KNOWN_DESIGNATORS, OpenSky, spoken_callsign
from readback.keywords import PHRASEOLOGY, fit
from readback.monitor import Monitor, describe
from readback.normalize import spoken
from readback.occurrence import AgentError, Log, investigate, to_markdown
from readback.replay import replay
from readback.sim import CENTER, generate
from readback.tagger.decode import tidy
from readback.tagger.report import read_jsonl

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "web"
SCENARIOS = ROOT / "scenarios"
TRAINING = ROOT / "training"
ASR_CACHE = ROOT / "results" / "raw" / "asr-live"
UPLOADS = SCENARIOS / "uploads"
MAX_UPLOAD = 100 * 1024 * 1024
load_dotenv(ROOT / ".env")

app = FastAPI(title="Readback")
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _rules_parse(text: str):
    return rules.parse(text, KNOWN_DESIGNATORS)


def _pick_parser():
    """The trained tagger (models/tagger.pt) when it and PyTorch are available, unless
    READBACK_PARSER=rules; otherwise the rule-based parser."""
    model = ROOT / "models" / "tagger.pt"
    if os.environ.get("READBACK_PARSER", "tagger") == "rules" or not model.exists():
        return "rules", _rules_parse
    try:
        from readback.tagger.model import Tagger
    except ImportError:
        return "rules", _rules_parse
    return "tagger", Tagger.load(model).parse


PARSER_NAME, _parse = _pick_parser()


def muse_ready() -> bool:
    return bool(os.environ.get("MODEL_API_KEY"))


@functools.cache
def _spark():
    from readback.llm import MuseChat, SparkParser

    return SparkParser(MuseChat(), ROOT / "results" / "raw" / "llm-cache")


SESSIONS: dict[str, Log] = {}  # what each replay or analysis saw, for the report agent
REPORTS: dict[tuple[str, int], dict] = {}


def _session(log: Log) -> str:
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = log
    while len(SESSIONS) > 50:  # keep the most recent sessions only
        old = next(iter(SESSIONS))
        SESSIONS.pop(old)
        for key in [k for k in REPORTS if k[0] == old]:
            REPORTS.pop(key)
    return sid


@functools.cache
def _chat():
    from readback.llm import MuseChat

    return MuseChat()


@functools.cache
def _asr():
    from readback.asr import VoiceTranscribe

    return VoiceTranscribe()


@functools.cache
def _tts():
    from readback.radio import default_tts

    return default_tts()


def _parser(name: str):
    """Every parser's output goes through the same tidy, as in the evaluations."""
    base = {"spark": lambda text: _spark()(text), "rules": _rules_parse}.get(name, _parse)
    return lambda text: tidy(base(text))


def _audio(scen: dict, seed: int, i: int, ev: dict) -> str:
    """The transmission's radio audio (relative to scenarios/), synthesized the first time."""
    if ev.get("audio") and (SCENARIOS / ev["audio"]).exists():
        return ev["audio"]
    from readback.radio import on_frequency

    rel = f"audio/{scen['name']}/{ev['id']}.wav"
    (SCENARIOS / rel).parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng([seed, i])
    (SCENARIOS / rel).write_bytes(on_frequency(_tts(), ev["text"], ev["role"], rng))
    return rel


def heard(scen: dict, seed: int) -> Iterator[dict]:
    """The scenario's events with each transmission's text replaced by what Voice Transcribe
    hears in its radio audio. Lazy, so each call happens when the replay reaches it."""
    from readback.asr import TranscribeError, drop_repeats

    states = {ev["state"]["callsign"] for ev in scen["events"] if ev["type"] == "state"}
    keywords = list(PHRASEOLOGY) + [fit(spoken(spoken_callsign(c).split())) for c in sorted(states)]
    for i, ev in enumerate(scen["events"]):
        if ev["type"] != "tx":
            yield ev
            continue
        rel = _audio(scen, seed, i, ev)
        wav = (SCENARIOS / rel).read_bytes()
        key = hashlib.sha1(wav + json.dumps(keywords).encode()).hexdigest()
        cache = ASR_CACHE / f"{key}.json"
        rec = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
        if rec is None:
            start = time.perf_counter()
            try:
                text = _asr().transcribe(wav, keywords=keywords).transcript
                rec = {"text": text, "asr_s": round(time.perf_counter() - start, 2)}
                ASR_CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(rec), encoding="utf-8")
            except TranscribeError as exc:
                rec = {"text": "", "asr_s": None, "asr_error": str(exc)[:200]}
        # The cache keeps what the API returned; the stutters it sometimes adds are dropped here.
        yield dict(ev, **rec, script=ev["text"], audio=rel) | {"text": drop_repeats(rec["text"])}


def load_scenario(seed: int) -> dict:
    """A pre-rendered scenario (with audio) if scripts/make_scenarios.py made one, else a fresh
    deterministic one."""
    path = SCENARIOS / f"vobl-{seed}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return generate(seed).to_dict()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/audio/{path:path}")
def audio(path: str) -> FileResponse:
    target = (SCENARIOS / path).resolve()
    if SCENARIOS.resolve() not in target.parents or target.suffix != ".wav" or not target.exists():
        raise HTTPException(404)
    return FileResponse(target, media_type="audio/wav")


@app.get("/api/scenario/{seed}")
def scenario_info(seed: int) -> dict:
    scen = load_scenario(seed)
    last = max(e["t"] for e in scen["events"])
    return {
        "name": scen["name"],
        "center": scen["center"],
        "aircraft": [dict(a, canonical=spoken_callsign(a["callsign"])) for a in scen["aircraft"]],
        "duration": last,
        "transmissions": sum(e["type"] == "tx" for e in scen["events"]),
        "has_audio": any(e.get("audio") for e in scen["events"] if e["type"] == "tx"),
        "parser": PARSER_NAME,
        "muse": muse_ready(),
    }


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


def _tx_payload(t: float, tx_id: str, role: str, text: str, clearance: dict, **extra) -> dict:
    cs = clearance["callsign"]
    return {
        "t": t,
        "id": tx_id,
        "role": role,
        "text": text,
        "callsign": cs,
        "callsign_label": describe("callsign", cs) if cs else None,
        "items": [
            {
                "kind": i["kind"],
                "label": describe(i["kind"], i["value"]),
                "qualifier": i["qualifier"],
            }
            for i in clearance["items"]
        ],
        **extra,
    }


async def _stream(seed: int, speed: float, listen: bool, parser: str) -> AsyncIterator[str]:
    scen = load_scenario(seed)
    parse, timing = _parser(parser), {}

    def timed(text: str):
        start = time.perf_counter()
        clearance = parse(text)
        timing["parse_s"] = round(time.perf_counter() - start, 2)
        return clearance

    monitor = Monitor(timed)
    if listen:
        scen = dict(scen, events=heard(scen, seed))
    log = Log()
    yield _sse("session", {"id": _session(log)})
    steps = replay(scen, monitor)
    t0 = time.monotonic()
    clock = 0.0
    batch: list[dict] = []
    batch_t = None
    while True:
        # Speech recognition and Spark block on the network: step the replay in a thread.
        step = await asyncio.to_thread(next, steps, None)
        if step is None:
            break
        ev, alerts, parsed = step
        if ev["t"] > clock:
            if batch:
                yield _sse("states", {"t": batch_t, "states": batch})
                batch = []
            await asyncio.sleep(max(0.0, t0 + ev["t"] / speed - time.monotonic()))
            clock = ev["t"]
        if ev["type"] == "state":
            batch.append(ev["state"])
            batch_t = ev["t"]
            log.states.setdefault(ev["state"]["callsign"], []).append(dict(ev["state"], t=ev["t"]))
        elif ev["type"] == "tx":
            clearance = parsed["clearance"]
            payload = _tx_payload(
                ev["t"], ev["id"], parsed["role"], ev["text"], clearance,
                audio=f"/audio/{ev['audio']}" if ev.get("audio") else None,
                script=ev.get("script"),
                asr_s=ev.get("asr_s"),
                asr_error=ev.get("asr_error"),
                parse_s=timing.get("parse_s"),
                snapped_from=monitor.snapped.get(ev["id"]),
            )  # fmt: skip
            log.transmissions.append(dict(payload, items=clearance["items"]))
            yield _sse("tx", payload)
        for alert in alerts:
            log.alerts.append(dict(alert.to_dict(), t=alert.time - scen["start"]))
            yield _sse("alert", dict(log.alerts[-1], index=len(log.alerts) - 1))
    if batch:
        yield _sse("states", {"t": batch_t, "states": batch})
    yield _sse("end", {"labels": scen["labels"]})


@app.get("/api/replay/{seed}")
def replay_stream(
    seed: int,
    speed: float = Query(4.0, gt=0, le=1000),
    listen: bool = False,
    parser: str = Query(PARSER_NAME, pattern="^(tagger|spark|rules)$"),
) -> StreamingResponse:
    if (listen or parser == "spark") and not muse_ready():
        raise HTTPException(400, "Muse needs MODEL_API_KEY in .env")
    return StreamingResponse(
        _stream(seed, speed, listen, parser),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/report/{sid}/{index}")
def occurrence_report(sid: str, index: int) -> dict:
    """The occurrence-report agent's draft for one alert of a replay or recording."""
    if not muse_ready():
        raise HTTPException(400, "the report agent needs MODEL_API_KEY in .env")
    log = SESSIONS.get(sid)
    if log is None or not 0 <= index < len(log.alerts):
        raise HTTPException(404, "unknown session or alert; replay or analyze again")
    if (sid, index) not in REPORTS:
        try:
            result = investigate(_chat(), log, log.alerts[index])
        except AgentError as exc:
            raise HTTPException(502, str(exc)) from exc
        REPORTS[(sid, index)] = {"markdown": to_markdown(**result), **result}
    return REPORTS[(sid, index)]


@app.post("/api/analyze")
async def analyze_recording(
    request: Request,
    callsigns: str = "",
    parser: str = Query(PARSER_NAME, pattern="^(tagger|spark|rules)$"),
) -> dict:
    """Check a recorded frequency: the request body is the audio file (WAV, or any format
    ffmpeg reads), callsigns an optional comma-separated list of aircraft on frequency."""
    from readback.recording import RecordingError, analyze

    if not muse_ready():
        raise HTTPException(400, "recordings need MODEL_API_KEY in .env (Voice Transcribe)")
    data = await request.body()
    if not data:
        raise HTTPException(400, "send the audio file as the request body")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "recording too large (100 MB limit)")
    parse = _parser(parser)
    try:
        result = await asyncio.to_thread(analyze, data, _asr(), parse, callsigns.split(","))
    except RecordingError as exc:
        raise HTTPException(400, str(exc)) from exc
    log = Log()
    sid = _session(log)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / f"{sid}.wav").write_bytes(result["wav"])
    txs = []
    for tx in result["transmissions"]:
        payload = _tx_payload(
            tx["t"], tx["id"], tx["role"], tx["text"], tx["clearance"],
            speaker=tx["speaker"], snapped_from=tx["snapped_from"],
        )  # fmt: skip
        log.transmissions.append(dict(payload, items=tx["clearance"]["items"]))
        txs.append(payload)
    log.alerts = result["alerts"]
    return {
        "session": sid,
        "audio": f"/audio/uploads/{sid}.wav",
        "duration": result["duration"],
        "transmissions": txs,
        "alerts": [dict(a, index=k) for k, a in enumerate(log.alerts)],
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "parser": PARSER_NAME, "muse": muse_ready()}


@app.get("/training")
def training_page() -> FileResponse:
    return FileResponse(WEB / "training.html")


@app.get("/api/training")
def training(rounds: int = Query(400, gt=0, le=5000)) -> dict:
    """The tagger's training run: status, the last `rounds` log entries and daily checks."""
    status_path = TRAINING / "status.json"
    if not status_path.exists():
        raise HTTPException(404, "no training run yet; start one with scripts/train_tagger.py")
    log = [e for e in read_jsonl(TRAINING / "log.jsonl") if "round" in e][-rounds:]
    for e in log:  # the per-kind breakdown of every candidate isn't needed for the charts
        for split in e["candidate"].values():
            split.pop("kind_f1", None)
    return {
        "status": json.loads(status_path.read_text(encoding="utf-8")),
        "log": log,
        "checks": read_jsonl(TRAINING / "checks.jsonl"),
    }


@app.get("/api/live")
def live(radius_deg: float = Query(0.6, gt=0, le=3)) -> dict:
    """Current real traffic around Bengaluru from OpenSky (anonymous: 400 credits a day)."""
    lat, lon = CENTER
    try:
        states = OpenSky().states((lat - radius_deg, lon - radius_deg, lat + radius_deg,
                                   lon + radius_deg))  # fmt: skip
    except Exception as exc:  # network or rate limit: the UI shows the message
        raise HTTPException(502, f"OpenSky unavailable: {exc}") from exc
    return {"center": CENTER, "states": [s.__dict__ for s in states if not s.on_ground]}


def main() -> None:
    import uvicorn

    host = os.environ.get("READBACK_HOST", "127.0.0.1")
    uvicorn.run("readback.server:app", host=host, port=int(os.environ.get("READBACK_PORT", "8000")))
