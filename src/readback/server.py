"""Web app: replays a scenario through the monitor in real time and streams it to a radar scope.

Run with `uv run readback-ui` (or `uv run uvicorn readback.server:app`), then open
http://127.0.0.1:8000.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from readback import rules
from readback.adsb import KNOWN_DESIGNATORS, OpenSky, spoken_callsign
from readback.monitor import Monitor, describe
from readback.replay import replay
from readback.sim import CENTER, generate
from readback.tagger.report import read_jsonl

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "web"
SCENARIOS = ROOT / "scenarios"
TRAINING = ROOT / "training"

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
    }


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


async def _stream(seed: int, speed: float) -> AsyncIterator[str]:
    scen = load_scenario(seed)
    monitor = Monitor(_parse)
    clock = 0.0
    batch: list[dict] = []
    batch_t = None
    for ev, alerts, parsed in replay(scen, monitor):
        if ev["t"] > clock:
            if batch:
                yield _sse("states", {"t": batch_t, "states": batch})
                batch = []
            await asyncio.sleep((ev["t"] - clock) / speed)
            clock = ev["t"]
        if ev["type"] == "state":
            batch.append(ev["state"])
            batch_t = ev["t"]
        elif ev["type"] == "tx":
            clearance = parsed["clearance"]
            yield _sse(
                "tx",
                {
                    "t": ev["t"],
                    "id": ev["id"],
                    "role": parsed["role"],
                    "text": ev["text"],
                    "callsign": clearance["callsign"],
                    "callsign_label": describe("callsign", clearance["callsign"])
                    if clearance["callsign"]
                    else None,
                    "items": [
                        {"kind": i["kind"], "label": describe(i["kind"], i["value"]),
                         "qualifier": i["qualifier"]}
                        for i in clearance["items"]
                    ],
                    "audio": f"/audio/{ev['audio']}" if ev.get("audio") else None,
                },
            )  # fmt: skip
        for alert in alerts:
            yield _sse("alert", dict(alert.to_dict(), t=alert.time - scen["start"]))
    if batch:
        yield _sse("states", {"t": batch_t, "states": batch})
    yield _sse("end", {"labels": scen["labels"]})


@app.get("/api/replay/{seed}")
def replay_stream(seed: int, speed: float = Query(4.0, gt=0, le=1000)) -> StreamingResponse:
    return StreamingResponse(
        _stream(seed, speed),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    uvicorn.run("readback.server:app", host="127.0.0.1", port=8000)
