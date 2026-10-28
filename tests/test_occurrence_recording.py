import io
import json
import shutil
import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from readback import rules, server
from readback.adsb import KNOWN_DESIGNATORS
from readback.asr import Transcription, Turn
from readback.occurrence import REPORT_SCHEMA, AgentError, Log, investigate, to_markdown
from readback.recording import RecordingError, analyze, callsign_phrase, segments, to_wav

REPORT = {
    "title": "Readback error: FL090 for FL100",
    "occurrence_type": "readback error",
    "risk": "medium",
    "summary": "IFLY 961 read back FL090 after being cleared to FL100.",
    "timeline": [{"time": "00:00", "event": "Cleared FL100"}],
    "contributing_factors": [],
    "recommended_actions": ["Challenge incorrect readbacks."],
    "evidence_quality": "Transcript only.",
}


def _log() -> Log:
    log = Log()
    fl = "flight_level"
    log.transmissions = [
        {"t": 0.0, "role": "controller", "callsign": "ifly 9 6 1",
         "text": "ifly nine six one descend flight level one hundred",
         "items": [{"kind": fl, "value": "100", "qualifier": "descend"}]},
        {"t": 6.0, "role": "pilot", "callsign": "ifly 9 6 1",
         "text": "descending flight level niner zero ifly nine six one",
         "items": [{"kind": fl, "value": "90", "qualifier": "descend"}]},
    ]  # fmt: skip
    log.alerts = [
        {"t": 6.0, "callsign": "ifly 9 6 1", "source": "readback", "kind": "flight_level",
         "severity": "error", "message": "IFLY 961 read back FL090, cleared FL100",
         "expected": "100", "heard": "90"},
    ]  # fmt: skip
    log.states = {
        "IGO961": [{"t": float(t), "altitude_ft": 12000 - 20 * t} for t in range(0, 60, 5)]
    }
    return log


class FakeChat:
    """Asks for the transmissions and the track, then files the report."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def chat(self, messages, tools):
        assert {t["function"]["name"] for t in tools} >= {"transmissions", "submit_report"}
        self.seen.append(messages[-1])
        name, args = self.script.pop(0)
        if name is None:
            return {"content": "thinking"}
        call = {"id": f"c{len(self.seen)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}  # fmt: skip
        return {"content": "", "tool_calls": [call]}


def test_agent_uses_tools_then_files_a_report():
    chat = FakeChat([
        ("transmissions", {"callsign": "ifly 9 6 1"}),
        ("adsb_track", {"callsign": "ifly 9 6 1", "start": 0, "end": 30}),
        (None, {}),  # a turn without tools gets a nudge
        ("submit_report", REPORT),
    ])  # fmt: skip
    log = _log()
    result = investigate(chat, log, log.alerts[0])
    assert result["report"] == REPORT
    assert [t["tool"] for t in result["trace"]] == ["transmissions", "adsb_track"]
    heard = json.loads(chat.seen[1]["content"])
    assert [tx["time"] for tx in heard] == ["00:00", "00:06"]
    track = json.loads(chat.seen[2]["content"])
    assert track["adsb_callsign"] == "IGO961" and len(track["states"]) == 4  # every 10 s, to 30 s
    assert chat.seen[3]["content"] == "Call submit_report now."
    md = to_markdown(**result)
    assert md.startswith("# Readback error") and "`adsb_track(callsign='ifly 9 6 1'" in md


def test_agent_rejects_incomplete_reports_and_gives_up():
    partial = {k: v for k, v in REPORT.items() if k != "risk"}
    chat = FakeChat([("submit_report", partial), ("submit_report", REPORT)])
    log = _log()
    assert investigate(chat, log, log.alerts[0])["report"] == REPORT
    assert "missing fields" in chat.seen[1]["content"]
    with pytest.raises(AgentError):
        investigate(FakeChat([(None, {})] * 3), log, log.alerts[0], max_steps=3)
    assert set(REPORT_SCHEMA["required"]) == set(REPORT)


def _wav(seconds=2.0, rate=8000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.zeros(int(seconds * rate), dtype="<i2").tobytes())
    return out.getvalue()


class FakeASR:
    def __init__(self, turns):
        self.turns, self.calls = turns, []

    def transcribe(self, wav, mode, keywords):
        self.calls.append((mode, keywords))
        with wave.open(io.BytesIO(wav)) as w:
            assert w.getframerate() == 16000
        turns = [Turn(k, int(t * 1000), int(t * 1000) + 2000, text, None)
                 for k, (t, text) in enumerate(self.turns)]  # fmt: skip
        return Transcription("s", " ".join(x for _, x in self.turns), 1000, turns)


def test_analyze_a_recording_finds_the_readback_error():
    asr = FakeASR([
        (0.0, "Ifly nine six one, descend flight level one hundred."),
        (6.0, "Descending flight level niner zero, ifly nine six two, two."),
    ])  # fmt: skip
    result = analyze(_wav(), asr, lambda t: rules.parse(t, KNOWN_DESIGNATORS), ["IGO961", ""])
    mode, keywords = asr.calls[0]
    assert mode == "DIARIZATION" and "ifly nine six one" in keywords
    txs = result["transmissions"]
    assert [tx["role"] for tx in txs] == ["controller", "pilot"]
    assert txs[1]["text"].endswith("ifly nine six two,")  # stutter dropped
    assert txs[1]["snapped_from"] == "ifly 9 6 2"  # matched to the known callsign
    assert [(a["kind"], a["expected"], a["heard"]) for a in result["alerts"]] == [
        ("flight_level", "100", "90")
    ]


def _bursts(spans, seconds=10.0, rate=16000) -> np.ndarray:
    """Radio hiss with a louder tone where each (start, end) transmission is."""
    rng = np.random.default_rng(0)
    x = rng.normal(0, 30, int(seconds * rate))
    t = np.arange(len(x)) / rate
    for a, b in spans:
        on = (t >= a) & (t < b)
        x[on] += 3000 * np.sin(2 * np.pi * 440 * t[on])
    return x.astype("<i2")


def test_silences_split_the_recording_into_transmissions():
    pcm = _bursts([(1.0, 2.0), (2.2, 3.0), (5.0, 7.0), (8.5, 8.7)])
    spans = segments(pcm)
    # A 0.2 s pause stays inside one transmission; a 0.2 s click is dropped.
    assert np.allclose(spans, [(0.85, 3.15), (4.85, 7.15)], atol=0.04)
    assert segments(np.zeros(16000, dtype="<i2")) == []


class SegmentASR:
    """Answers each push-to-talk segment by its length."""

    def __init__(self, by_seconds):
        self.by_seconds, self.calls = by_seconds, []

    def transcribe(self, wav, mode, keywords):
        self.calls.append(mode)
        with wave.open(io.BytesIO(wav)) as w:
            seconds = round(w.getnframes() / w.getframerate())
        return Transcription("s", self.by_seconds[seconds], 100, [])


def test_analyze_transcribes_each_transmission_on_its_own():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(_bursts([(1.0, 3.7), (5.0, 6.7)]).tobytes())
    asr = SegmentASR({
        3: "Ifly nine six one, squawk four three one two.",
        2: "Squawk four three one three, ifly nine six one.",
    })  # fmt: skip
    result = analyze(out.getvalue(), asr, lambda t: rules.parse(t, KNOWN_DESIGNATORS))
    assert asr.calls == ["PUSH_TO_TALK", "PUSH_TO_TALK"]
    assert [round(tx["t"], 1) for tx in result["transmissions"]] == [0.8, 4.8]
    assert [tx["role"] for tx in result["transmissions"]] == ["controller", "pilot"]
    assert [(a["kind"], a["expected"], a["heard"]) for a in result["alerts"]] == [
        ("squawk", "4312", "4313")
    ]


def test_recording_limits_and_callsign_forms():
    assert callsign_phrase("SWR123") == callsign_phrase("swiss one two three") == "swiss 1 2 3"
    with pytest.raises(RecordingError, match="limit"):
        analyze(_wav(seconds=601, rate=8000), FakeASR([]), lambda t: None)
    if shutil.which("ffmpeg") is None:
        with pytest.raises(RecordingError, match="ffmpeg"):
            to_wav(b"ID3 not a wav")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_any_format_ffmpeg_reads_is_accepted(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(_wav(seconds=1.5))
    mp3 = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), str(mp3)], check=True)
    wav = to_wav(mp3.read_bytes())
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
        assert 1.4 < w.getnframes() / 16000 < 1.7
    with pytest.raises(RecordingError, match="decode"):
        to_wav(b"definitely not audio")


def test_analyze_and_report_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.setattr(server, "UPLOADS", tmp_path)
    monkeypatch.setattr(server, "_asr", lambda: FakeASR([
        (0.0, "ifly nine six one, descend flight level one hundred"),
        (6.0, "descending flight level niner zero, ifly nine six one"),
    ]))  # fmt: skip
    chat = FakeChat([("alerts", {}), ("submit_report", REPORT)])
    monkeypatch.setattr(server, "_chat", lambda: chat)
    client = TestClient(server.app)
    assert client.get("/healthz").json()["muse"] is True

    body = client.post("/api/analyze?parser=rules&callsigns=IGO961", content=_wav()).json()
    assert [tx["role"] for tx in body["transmissions"]] == ["controller", "pilot"]
    assert body["transmissions"][1]["items"][0]["label"] == "FL090"
    [alert] = body["alerts"]
    assert alert["index"] == 0 and (tmp_path / f"{body['session']}.wav").exists()

    report = client.post(f"/api/report/{body['session']}/0").json()
    assert report["report"] == REPORT and report["markdown"].startswith("# Readback error")
    assert client.post(f"/api/report/{body['session']}/0").json() == report  # cached
    assert client.post(f"/api/report/{body['session']}/5").status_code == 404
    assert client.post("/api/analyze", content=b"").status_code == 400
    monkeypatch.delenv("MODEL_API_KEY")
    assert client.post(f"/api/report/{body['session']}/0").status_code == 400
