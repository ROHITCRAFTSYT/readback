"""Web app: replays a scenario through the monitor in real time and streams it to a radar scope.

Run with `uv run readback-ui` (or `uv run uvicorn readback.server:app`), then open
http://127.0.0.1:8000.

A replay can run on the scripted text, or "listen": every transmission's radio audio goes to
Muse Voice Transcribe (keywords: the callsigns ADS-B shows in range) and the transcript to the
chosen parser (the tagger, Muse Spark or the rules), as it would on a live frequency. Listening
needs MODEL_API_KEY in .env; transcripts are cached in results/raw/asr-live/.

Recordings are checked as background jobs and kept, with reviewer verdicts and drafted
reports, in a SQLite store under READBACK_DATA (default ./var). Set READBACK_TOKEN to require
an access token for every /api and audio request, and READBACK_RETENTION_DAYS to delete
recordings after that many days.
"""

from __future__ import annotations

import asyncio
import csv
import functools
import hashlib
import hmac
import io
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from readback import rules
from readback.adsb import KNOWN_DESIGNATORS, OpenSky, spoken_callsign
from readback.keywords import PHRASEOLOGY, fit
from readback.monitor import Monitor, describe
from readback.normalize import spoken
from readback.occurrence import AgentError, Log, investigate, to_markdown
from readback.replay import replay
from readback.sim import CENTER, generate
from readback.store import VERDICTS, Store
from readback.tagger.decode import tidy
from readback.tagger.report import read_jsonl

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "web"
SCENARIOS = ROOT / "scenarios"
TRAINING = ROOT / "training"
ASR_CACHE = ROOT / "results" / "raw" / "asr-live"
load_dotenv(ROOT / ".env")
MAX_UPLOAD = int(os.environ.get("READBACK_MAX_UPLOAD_MB", "500")) * 1024 * 1024
log = logging.getLogger("readback")

app = FastAPI(title="Readback")
app.mount("/static", StaticFiles(directory=WEB), name="static")

_STORE: Store | None = None
JOBS = ThreadPoolExecutor(max_workers=int(os.environ.get("READBACK_WORKERS", "2")))


def configure(data: Path) -> Store:
    """Open (or create) the store of recordings, verdicts and reports under `data`."""
    global _STORE
    _STORE = Store(data)
    _STORE.interrupted()
    _purge()
    return _STORE


def store() -> Store:
    return _STORE or configure(Path(os.environ.get("READBACK_DATA", ROOT / "var")))


def retention_days() -> float:
    return float(os.environ.get("READBACK_RETENTION_DAYS", "0") or 0)


def _purge() -> None:
    if retention_days() > 0 and _STORE is not None:
        gone = _STORE.purge(retention_days())
        if gone:
            log.info("retention: deleted %d recordings", len(gone))


def live_center() -> tuple[str, tuple[float, float]]:
    """The airspace the Live tab shows: READBACK_LIVE_CENTER="lat,lon" and READBACK_LIVE_NAME."""
    raw = os.environ.get("READBACK_LIVE_CENTER", "")
    if raw:
        lat, lon = (float(x) for x in raw.split(","))
        return os.environ.get("READBACK_LIVE_NAME", f"{lat:.2f}, {lon:.2f}"), (lat, lon)
    return os.environ.get("READBACK_LIVE_NAME", "Bengaluru"), CENTER


PUBLIC_API = {"/api/login", "/api/logout"}


@app.middleware("http")
async def guard(request: Request, call_next):
    """With READBACK_TOKEN set, every /api and audio request needs it, as a bearer token or
    the cookie /api/login sets. The page itself holds no data. Basic hardening headers on all."""
    token = os.environ.get("READBACK_TOKEN", "")
    path = request.url.path
    if token and path.startswith(("/api/", "/audio/")) and path not in PUBLIC_API:
        header = request.headers.get("authorization", "")
        given = header[7:].strip() if header.lower().startswith("bearer ") else ""
        given = given or request.cookies.get("readback_token", "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"detail": "access token required"}, status_code=401)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


class Login(BaseModel):
    token: str = Field(max_length=500)


@app.post("/api/login")
def login(body: Login, request: Request) -> JSONResponse:
    token = os.environ.get("READBACK_TOKEN", "")
    if token and not hmac.compare_digest(body.token.encode(), token.encode()):
        raise HTTPException(401, "wrong access token")
    response = JSONResponse({"ok": True})
    if token:
        response.set_cookie(
            "readback_token", token, max_age=12 * 3600, httponly=True, samesite="strict",
            secure=request.url.scheme == "https",
        )  # fmt: skip
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie("readback_token")
    return response


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


def _stored_log(aid: str) -> Log | None:
    rec = store().get(aid)
    if rec is None or rec["status"] != "done":
        return None
    out = Log()
    out.transmissions = rec["log"]["transmissions"]
    out.alerts = rec["log"]["alerts"]
    return out


@app.post("/api/report/{sid}/{index}")
def occurrence_report(sid: str, index: int) -> dict:
    """The occurrence-report agent's draft for one alert of a replay or a stored recording.
    Drafts are kept: a replay's while it is recent, a recording's with the recording."""
    stored = sid not in SESSIONS
    cached = store().report(sid, index) if stored else REPORTS.get((sid, index))
    if cached:
        return cached
    if not muse_ready():
        raise HTTPException(400, "the report agent needs MODEL_API_KEY in .env")
    session = _stored_log(sid) if stored else SESSIONS[sid]
    if session is None or not 0 <= index < len(session.alerts):
        raise HTTPException(404, "unknown session or alert; replay or analyze again")
    try:
        result = investigate(_chat(), session, session.alerts[index])
    except AgentError as exc:
        raise HTTPException(502, str(exc)) from exc
    report = {"markdown": to_markdown(**result), **result}
    if stored:
        store().save_report(sid, index, report)
    else:
        REPORTS[(sid, index)] = report
    return report


# ---------- recordings: background jobs, history, review ----------
async def _upload(request: Request) -> bytes:
    if not muse_ready():
        raise HTTPException(400, "recordings need MODEL_API_KEY in .env (Voice Transcribe)")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise HTTPException(413, f"recording too large ({MAX_UPLOAD // 2**20} MB limit)")
    data = await request.body()
    if not data:
        raise HTTPException(400, "send the audio file as the request body")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"recording too large ({MAX_UPLOAD // 2**20} MB limit)")
    return data


def _run_job(aid: str, data: bytes, callsigns: list[str], parser: str) -> None:
    from readback.recording import RecordingError, analyze

    st = store()
    st.update(aid, status="running")
    try:
        result = analyze(data, _asr(), _parser(parser), callsigns,
                         progress=lambda f: st.update(aid, progress=round(f, 3)))  # fmt: skip
    except RecordingError as exc:
        st.update(aid, status="failed", error=str(exc))
        return
    except Exception as exc:
        log.exception("recording %s failed", aid)
        st.update(aid, status="failed", error=f"internal error: {exc}"[:300])
        return
    (st.audio / f"{aid}.wav").write_bytes(result["wav"])
    txs = []
    for tx in result["transmissions"]:
        payload = _tx_payload(
            tx["t"], tx["id"], tx["role"], tx["text"], tx["clearance"],
            speaker=tx["speaker"], snapped_from=tx["snapped_from"],
        )  # fmt: skip
        # The report agent reads the parsed items; the UI shows their labels.
        txs.append(dict(payload, labels=payload["items"], items=tx["clearance"]["items"]))
    alerts = result["alerts"]
    summary = {
        "duration": result["duration"],
        "transmissions": len(txs),
        "alerts": len(alerts),
        "errors": sum(a["severity"] == "error" for a in alerts),
        "failed_transmissions": result["failed_transmissions"],
    }
    st.update(aid, status="done", progress=1.0, duration=result["duration"], summary=summary,
              log={"transmissions": txs, "alerts": alerts})  # fmt: skip
    _purge()


def _detail(aid: str) -> dict:
    st = store()
    rec = st.get(aid)
    if rec is None:
        raise HTTPException(404, "no such recording")
    saved = rec.pop("log") or {"transmissions": [], "alerts": []}
    reviews, reported = st.reviews(aid), st.reported(aid)
    unreviewed = {"verdict": "open", "note": "", "reviewer": "", "updated": None}
    rec["transmissions"] = [
        {k: v for k, v in dict(tx, items=tx["labels"]).items() if k != "labels"}
        for tx in saved["transmissions"]
    ]
    rec["alerts"] = [
        dict(a, index=k, reported=k in reported,
             review=reviews.get(k, unreviewed))
        for k, a in enumerate(saved["alerts"])
    ]  # fmt: skip
    rec["audio"] = f"/api/recordings/{aid}/audio" if (st.audio / f"{aid}.wav").exists() else None
    return rec


@app.post("/api/recordings", status_code=202)
async def submit_recording(
    request: Request,
    name: str = Query("", max_length=200),
    callsigns: str = Query("", max_length=2000),
    parser: str = Query(PARSER_NAME, pattern="^(tagger|spark|rules)$"),
) -> dict:
    """Queue a recording (the request body: WAV, or any format ffmpeg reads) for checking.
    Poll GET /api/recordings/{id} for progress and results."""
    data = await _upload(request)
    aid = store().create(name.strip() or time.strftime("Recording %Y-%m-%d %H:%M"), parser,
                         callsigns)  # fmt: skip
    JOBS.submit(_run_job, aid, data, callsigns.split(","), parser)
    return {"id": aid}


@app.get("/api/recordings")
def list_recordings(limit: int = Query(500, gt=0, le=5000)) -> dict:
    st = store()
    return {"items": st.list(limit), "stats": st.stats(), "retention_days": retention_days()}


@app.get("/api/recordings/{aid}")
def recording(aid: str) -> dict:
    return _detail(aid)


@app.get("/api/recordings/{aid}/audio")
def recording_audio(aid: str) -> FileResponse:
    path = store().audio / f"{aid}.wav"
    if not aid.isalnum() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.delete("/api/recordings/{aid}")
def delete_recording(aid: str) -> dict:
    if store().get(aid) is None:
        raise HTTPException(404, "no such recording")
    store().delete(aid)
    return {"ok": True}


class Review(BaseModel):
    verdict: str = Field(pattern="^(" + "|".join(VERDICTS) + ")$")
    note: str = Field("", max_length=4000)
    reviewer: str = Field("", max_length=200)


@app.put("/api/recordings/{aid}/alerts/{index}/review")
def review_alert(aid: str, index: int, body: Review) -> dict:
    rec = store().get(aid)
    if rec is None or rec["status"] != "done" or not 0 <= index < len(rec["log"]["alerts"]):
        raise HTTPException(404, "no such alert")
    return store().review(aid, index, body.verdict, body.note.strip(), body.reviewer.strip())


CSV_FIELDS = ["recording", "recording_id", "uploaded", "alert", "time", "severity", "source",
              "kind", "callsign", "message", "expected", "heard", "verdict", "note", "reviewer",
              "reviewed"]  # fmt: skip


def _iso(t: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else ""


def _clock(t: float) -> str:
    """Offset into the recording: 04:07, or 1:12:05 past the first hour."""
    s = int(t)
    return (
        f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
        if s >= 3600
        else f"{s // 60:02d}:{s % 60:02d}"
    )


def _cell(value):
    """Text a spreadsheet would run as a formula (=, +, -, @) is quoted to stay text."""
    return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value


def _csv(recs: list[dict]) -> Response:
    out = io.StringIO()
    out.write("﻿")  # so Excel reads the file as UTF-8
    w = csv.DictWriter(out, CSV_FIELDS)
    w.writeheader()
    for rec in recs:
        for a in rec["alerts"]:
            r = a["review"]
            w.writerow({k: _cell(v) for k, v in {
                "recording": rec["name"], "recording_id": rec["id"],
                "uploaded": _iso(rec["created"]), "alert": a["index"], "time": _clock(a["t"]),
                "severity": a["severity"], "source": a["source"], "kind": a["kind"],
                "callsign": describe("callsign", a["callsign"]), "message": a["message"],
                "expected": a["expected"], "heard": a["heard"], "verdict": r["verdict"],
                "note": r["note"], "reviewer": r["reviewer"], "reviewed": _iso(r["updated"]),
            }.items()})  # fmt: skip
    return Response(
        out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="readback-alerts.csv"'},
    )  # fmt: skip


@app.get("/api/recordings/{aid}/alerts.csv")
def recording_csv(aid: str) -> Response:
    return _csv([_detail(aid)])


@app.get("/api/alerts.csv")
def all_alerts_csv() -> Response:
    """Every alert of every checked recording with its verdict: the safety team's register."""
    return _csv([_detail(r["id"]) for r in store().list(5000) if r["status"] == "done"])


@app.post("/api/analyze")
async def analyze_recording(
    request: Request,
    callsigns: str = "",
    parser: str = Query(PARSER_NAME, pattern="^(tagger|spark|rules)$"),
) -> dict:
    """Check a recording and wait for the result (scripts and short files). It is stored like
    any other; long recordings are better sent to POST /api/recordings."""
    data = await _upload(request)
    aid = store().create(time.strftime("Recording %Y-%m-%d %H:%M"), parser, callsigns)
    await asyncio.to_thread(_run_job, aid, data, callsigns.split(","), parser)
    rec = _detail(aid)
    if rec["status"] != "done":
        raise HTTPException(400, rec["error"] or "the recording could not be checked")
    return {
        "session": aid,
        "audio": rec["audio"],
        "duration": rec["duration"],
        "transmissions": rec["transmissions"],
        "alerts": rec["alerts"],
    }


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "parser": PARSER_NAME,
        "muse": muse_ready(),
        "auth": bool(os.environ.get("READBACK_TOKEN")),
        "live": live_center()[0],
    }


@app.get("/training")
def training_page() -> FileResponse:
    return FileResponse(WEB / "training.html")


@app.get("/review")
def review_page() -> FileResponse:
    return FileResponse(WEB / "review.html")


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
    """Current real traffic around the configured centre (Bengaluru by default) from OpenSky
    (anonymous: 400 credits a day)."""
    name, (lat, lon) = live_center()
    try:
        states = OpenSky().states((lat - radius_deg, lon - radius_deg, lat + radius_deg,
                                   lon + radius_deg))  # fmt: skip
    except Exception as exc:  # network or rate limit: the UI shows the message
        raise HTTPException(502, f"OpenSky unavailable: {exc}") from exc
    return {"name": name, "center": [lat, lon],
            "states": [s.__dict__ for s in states if not s.on_ground]}  # fmt: skip


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store()  # open the store and apply retention before the first request
    host = os.environ.get("READBACK_HOST", "127.0.0.1")
    uvicorn.run("readback.server:app", host=host, port=int(os.environ.get("READBACK_PORT", "8000")))
