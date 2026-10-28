import json

import numpy as np
from fastapi.testclient import TestClient

from readback import rules, server
from readback.adsb import KNOWN_DESIGNATORS
from readback.monitor import Monitor
from readback.radio import RATE, from_wav, to_wav, vhf
from readback.replay import replay
from readback.server import app
from readback.sim import generate, say_altitude, say_callsign


def test_spoken_forms():
    assert say_callsign("IGO961") == "ifly niner six one"
    assert say_callsign("BAW12K") == "speedbird one two kilo"
    assert say_altitude(4500) == "four thousand five hundred feet"


def test_generate_is_deterministic_and_ordered():
    a, b = generate(11).to_dict(), generate(11).to_dict()
    assert a == b
    times = [e["t"] for e in a["events"]]
    assert times == sorted(times)
    assert any(e["type"] == "tx" for e in a["events"])
    assert {lb["source"] for lb in a["labels"]} <= {"readback", "track"}


def test_monitor_catches_planted_readback_errors():
    """Every planted wrong readback value must raise an error alert for that exchange."""
    for seed in range(5):
        scen = generate(seed, p_readback_error=0.5).to_dict()
        planted = [
            lb for lb in scen["labels"]
            if lb["source"] == "readback" and lb["kind"] not in ("missing", "no_readback")
        ]  # fmt: skip
        monitor = Monitor(lambda t: rules.parse(t, KNOWN_DESIGNATORS))
        alerts = [a for _, al, _ in replay(scen, monitor) for a in al]
        errors = [a for a in alerts if a.source == "readback" and a.severity == "error"]
        for lb in planted:
            assert any(abs(a.time - scen["start"] - lb["t"]) <= 5 for a in errors), (seed, lb)


def test_vhf_effect_and_wav_round_trip():
    rng = np.random.default_rng(0)
    tone = np.sin(np.linspace(0, 2 * np.pi * 800, RATE)) * 0.5
    out = vhf(tone, rng)
    assert len(out) > len(tone)  # squelch bursts added
    assert np.max(np.abs(out)) <= 0.8 + 1e-6
    back = from_wav(to_wav(out))
    assert np.allclose(back, out, atol=1 / 16000)


def test_scenario_info_and_replay_stream():
    client = TestClient(app)
    info = client.get("/api/scenario/3").json()
    assert info["name"] == "vobl-3"
    assert all(a["canonical"] for a in info["aircraft"])

    kinds = []
    with client.stream("GET", "/api/replay/3?speed=1000") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("event: "):
                kinds.append(line.removeprefix("event: "))
            elif line.startswith("data: ") and kinds[-1] == "tx":
                tx = json.loads(line.removeprefix("data: "))
                assert {"role", "text", "items", "callsign"} <= tx.keys()
    assert {"tx", "states", "end"} <= set(kinds)
    assert kinds[-1] == "end"


def test_listen_replays_what_voice_transcribe_heard(tmp_path, monkeypatch):
    class FakeASR:
        def transcribe(self, wav, keywords):
            assert "flight level" in keywords
            return type("T", (), {"transcript": wav.decode().capitalize() + "."})()

    def fake_audio(scen, seed, i, ev):  # the "audio" is the text, so the fake can hear it
        (tmp_path / f"{ev['id']}.wav").write_bytes(ev["text"].encode())
        return f"{ev['id']}.wav"

    monkeypatch.setattr(server, "SCENARIOS", tmp_path)
    monkeypatch.setattr(server, "ASR_CACHE", tmp_path / "asr")
    monkeypatch.setattr(server, "_audio", fake_audio)
    monkeypatch.setattr(server, "_asr", lambda: FakeASR())
    client = TestClient(app)

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert client.get("/api/replay/3?listen=true").status_code == 400

    monkeypatch.setenv("MODEL_API_KEY", "test")
    txs = []
    with client.stream("GET", "/api/replay/3?speed=1000&listen=true&parser=rules") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: ") and '"script"' in line:
                txs.append(json.loads(line.removeprefix("data: ")))
    assert txs and all(tx["text"] == tx["script"].capitalize() + "." for tx in txs)
    assert all(tx["asr_s"] is not None and tx["parse_s"] is not None for tx in txs)
    assert any(tx["items"] for tx in txs)  # the parser read the transcripts
    assert len(list((tmp_path / "asr").iterdir())) == len(txs)  # one cached transcript each


def test_audio_route_rejects_traversal():
    client = TestClient(app)
    assert client.get("/audio/..%2Fpyproject.toml").status_code == 404
    assert client.get("/audio/missing.wav").status_code == 404


def test_training_api(tmp_path, monkeypatch):
    import readback.server as server

    monkeypatch.setattr(server, "TRAINING", tmp_path)
    client = TestClient(app)
    assert client.get("/api/training").status_code == 404
    (tmp_path / "status.json").write_text(json.dumps({"round": 2}))
    entry = {"round": 1, "candidate": {"val": {"exact": 0.5, "kind_f1": {"speed": 1.0}}}}
    (tmp_path / "log.jsonl").write_text(
        json.dumps(entry) + "\n" + json.dumps({"event": "x"}) + "\n"
    )
    body = client.get("/api/training").json()
    assert body["status"] == {"round": 2} and body["checks"] == []
    assert body["log"] == [{"round": 1, "candidate": {"val": {"exact": 0.5}}}]
    assert client.get("/training").status_code == 200
