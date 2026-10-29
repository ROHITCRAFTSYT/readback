import csv
import io
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_occurrence_recording import REPORT, FakeASR, FakeChat, _bursts, _wav

from readback import recording, server
from readback.asr import Transcription
from readback.recording import RecordingError, analyze, segments
from readback.store import Store


def _done(st: Store, alerts=2) -> str:
    aid = st.create("tower 0800", "rules")
    log = {
        "transmissions": [],
        "alerts": [{"t": 5.0, "severity": "error", "message": f"a{k}"} for k in range(alerts)],
    }
    summary = {"duration": 1800.0, "transmissions": 0, "alerts": alerts, "errors": alerts,
               "failed_transmissions": 0}  # fmt: skip
    st.update(aid, status="done", progress=1.0, duration=1800.0, summary=summary, log=log)
    return aid


def test_store_keeps_recordings_verdicts_and_reports(tmp_path):
    st = Store(tmp_path)
    aid = _done(st)
    st.review(aid, 0, "confirmed", "pilot corrected on next call", "R. Shah")
    st.review(aid, 1, "false_alarm")
    st.review(aid, 1, "false_alarm", "recognizer misheard")  # updates, doesn't duplicate
    with pytest.raises(ValueError):
        st.review(aid, 0, "maybe")
    st.save_report(aid, 0, {"markdown": "# r"})

    again = Store(tmp_path)  # a restart keeps everything
    assert again.get(aid)["log"]["alerts"][1]["message"] == "a1"
    assert again.reviews(aid)[1]["note"] == "recognizer misheard"
    assert again.report(aid, 0) == {"markdown": "# r"} and again.reported(aid) == {0}
    [item] = again.list()
    assert item["verdicts"] == {"confirmed": 1, "false_alarm": 1} and "log" not in item
    stats = again.stats()
    assert (stats["recordings"], stats["hours"], stats["alerts"]) == (1, 0.5, 2)
    assert stats["precision"] == 0.5 and stats["unreviewed"] == 0

    (again.audio / f"{aid}.wav").write_bytes(b"RIFF")
    again.delete(aid)
    assert again.get(aid) is None and again.reviews(aid) == {} and not list(again.audio.iterdir())


def test_units_and_frequencies_cut_listings_and_totals(tmp_path):
    st = Store(tmp_path)
    twr = _done(st, alerts=2)
    st._run("UPDATE analyses SET unit = 'LKPR TWR', frequency = '118.100' WHERE id = ?", (twr,))
    app = _done(st, alerts=3)
    st._run("UPDATE analyses SET unit = 'LKPR APP', frequency = '120.530' WHERE id = ?", (app,))
    bare = _done(st, alerts=1)
    st.review(twr, 0, "confirmed")
    st.review(app, 0, "false_alarm")

    assert [r["id"] for r in st.list(unit="LKPR TWR")] == [twr]
    assert [r["id"] for r in st.list(frequency="120.530")] == [app]
    assert [r["id"] for r in st.list(unit="")] == [bare]
    assert len(st.list()) == 3
    assert st.stats("LKPR TWR")["alerts"] == 2 and st.stats("LKPR TWR")["precision"] == 1.0
    assert st.stats("LKPR APP")["precision"] == 0.0 and st.stats()["precision"] == 0.5
    assert st.positions() == [
        {"unit": "", "frequency": "", "recordings": 1},
        {"unit": "LKPR APP", "frequency": "120.530", "recordings": 1},
        {"unit": "LKPR TWR", "frequency": "118.100", "recordings": 1},
    ]


def test_store_made_before_units_gains_the_columns(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "readback.db")
    db.execute("CREATE TABLE analyses (id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL "
               "NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT, "
               "parser TEXT NOT NULL, callsigns TEXT NOT NULL DEFAULT '', duration REAL, "
               "summary TEXT, log TEXT)")  # fmt: skip
    db.execute("INSERT INTO analyses (id, name, created, status, parser) "
               "VALUES ('old1', 'old', 1, 'done', 'rules')")  # fmt: skip
    db.commit()
    db.close()
    st = Store(tmp_path)
    assert st.get("old1")["unit"] == "" and st.list(unit="")[0]["id"] == "old1"


def test_restart_fails_unfinished_jobs_and_retention_purges(tmp_path):
    st = Store(tmp_path)
    stuck = st.create("half done", "rules")
    st.update(stuck, status="running", progress=0.4)
    old = _done(st)
    st._run("UPDATE analyses SET created = ? WHERE id = ?", (time.time() - 40 * 86400, old))
    st.interrupted()
    assert st.get(stuck)["status"] == "failed" and "restart" in st.get(stuck)["error"]
    assert st.purge(30) == [old] and st.get(stuck) is not None


def test_long_recordings_are_cut_to_the_request_limit(monkeypatch):
    monkeypatch.setattr(recording, "REQUEST_SECONDS", 2.0)
    spans = segments(_bursts([(1.0, 6.0)], seconds=8))
    assert [round(b - a, 1) for a, b in spans] == [2.0, 2.0, 1.3]
    monkeypatch.setattr(recording, "MAX_SECONDS", 5)
    with pytest.raises(RecordingError, match="limit"):
        analyze(_wav(seconds=6, rate=8000), FakeASR([]), lambda t: None)


def test_one_failed_transmission_does_not_lose_the_recording():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(_bursts([(1.0, 2.0), (4.0, 5.0), (7.0, 8.0)]).tobytes())

    class Flaky:
        calls = 0

        def transcribe(self, wav, mode, keywords):
            Flaky.calls += 1
            with wave.open(io.BytesIO(wav)) as w:
                start = w.getnframes()  # every segment is the same length; fail the 2nd call
            if Flaky.calls == 2:
                raise ConnectionError("dropped")
            return Transcription("s", "ifly nine six one, roger", start, [])

    seen = []
    result = analyze(out.getvalue(), Flaky(), lambda t: server._parser("rules")(t),
                     progress=seen.append)  # fmt: skip
    assert result["failed_transmissions"] == 1 and len(result["transmissions"]) == 2
    assert sorted(seen) == pytest.approx([1 / 3, 2 / 3, 1.0])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.delenv("READBACK_TOKEN", raising=False)
    server.configure(tmp_path)
    monkeypatch.setattr(server, "_asr", lambda: FakeASR([
        (0.0, "ifly nine six one, descend flight level one hundred"),
        (6.0, "descending flight level niner zero, ifly nine six one"),
    ]))  # fmt: skip
    return TestClient(server.app)


def _wait(client, aid):
    for _ in range(200):
        rec = client.get(f"/api/recordings/{aid}").json()
        if rec["status"] in ("done", "failed"):
            return rec
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_recording_job_review_and_export(client):
    url = "/api/recordings?name=Tower%20shift&parser=rules&callsigns=IGO961"
    aid = client.post(f"{url}&unit=VOBL%20TWR&frequency=118.700", content=_wav()).json()["id"]
    rec = _wait(client, aid)
    assert rec["status"] == "done" and rec["name"] == "Tower shift" and rec["progress"] == 1.0
    assert (rec["unit"], rec["frequency"]) == ("VOBL TWR", "118.700")
    assert rec["summary"]["alerts"] == 1 and rec["transmissions"][1]["items"][0]["label"] == "FL090"
    [alert] = rec["alerts"]
    assert alert["review"]["verdict"] == "open" and not alert["reported"]
    assert client.get(rec["audio"]).headers["content-type"] == "audio/wav"

    url = f"/api/recordings/{aid}/alerts/0/review"
    r = client.put(url, json={"verdict": "confirmed", "note": "real bust", "reviewer": "RS"})
    assert r.json()["verdict"] == "confirmed"
    assert client.put(url, json={"verdict": "nope"}).status_code == 422
    assert client.put(f"/api/recordings/{aid}/alerts/9/review",
                      json={"verdict": "confirmed"}).status_code == 404  # fmt: skip

    listing = client.get("/api/recordings").json()
    assert listing["stats"]["precision"] == 1.0
    assert listing["items"][0]["verdicts"]["confirmed"] == 1
    assert listing["positions"] == [{"unit": "VOBL TWR", "frequency": "118.700", "recordings": 1}]
    other = client.get("/api/recordings?unit=VOBL%20APP").json()
    assert other["items"] == [] and other["stats"]["alerts"] == 0
    assert len(client.get("/api/alerts.csv?unit=VOBL%20APP").text.splitlines()) == 1  # header
    url = f"/api/recordings/{aid}/alerts/0/review"
    client.put(url, json={"verdict": "confirmed", "note": "=HYPERLINK(1)", "reviewer": "RS"})
    text = client.get("/api/alerts.csv").text
    assert text.startswith("﻿")  # Excel reads it as UTF-8
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert [(r["unit"], r["frequency"], r["recording"], r["kind"], r["verdict"], r["note"],
             r["time"]) for r in rows] == [
        ("VOBL TWR", "118.700", "Tower shift", "flight_level", "confirmed", "'=HYPERLINK(1)",
         "00:06")
    ]  # fmt: skip
    one = client.get(f"/api/recordings/{aid}/alerts.csv").text
    assert one == client.get("/api/alerts.csv").text

    moved = client.patch(f"/api/recordings/{aid}", json={"unit": " VOBL APP ", "name": "Shift 2"})
    assert (moved.json()["unit"], moved.json()["name"], moved.json()["frequency"]) == (
        "VOBL APP", "Shift 2", "118.700")  # fmt: skip
    assert client.get("/api/recordings?unit=VOBL%20APP").json()["stats"]["alerts"] == 1
    assert client.patch(f"/api/recordings/{aid}", json={"name": ""}).status_code == 422
    assert client.patch("/api/recordings/nope", json={"unit": "X"}).status_code == 404

    assert client.delete(f"/api/recordings/{aid}").json() == {"ok": True}
    assert client.get(f"/api/recordings/{aid}").status_code == 404


def test_failed_job_reports_why(client, monkeypatch):
    monkeypatch.setattr(server, "_asr", lambda: FakeASR([]))
    aid = client.post("/api/recordings", content=b"not audio at all").json()["id"]
    rec = _wait(client, aid)
    assert rec["status"] == "failed" and rec["error"]


def test_stored_report_survives_restart(client, tmp_path, monkeypatch):
    body = client.post("/api/analyze?parser=rules", content=_wav()).json()
    chat = FakeChat([("submit_report", REPORT)])
    monkeypatch.setattr(server, "_chat", lambda: chat)
    first = client.post(f"/api/report/{body['session']}/0").json()
    server.configure(tmp_path)  # as after a restart
    assert client.post(f"/api/report/{body['session']}/0").json() == first
    assert client.get(f"/api/recordings/{body['session']}").json()["alerts"][0]["reported"]


def test_access_token(client, monkeypatch):
    monkeypatch.setenv("READBACK_TOKEN", "s3cret-token")
    assert client.get("/api/recordings").status_code == 401
    assert client.get("/api/scenario/7").status_code == 401
    assert client.get("/").status_code == 200  # the page holds no data
    assert client.get("/healthz").json()["auth"] is True
    ok = {"Authorization": "Bearer s3cret-token"}
    assert client.get("/api/recordings", headers=ok).status_code == 200
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    r = client.post("/api/login", json={"token": "s3cret-token"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/recordings").status_code == 200  # the cookie now
    assert r.headers["x-frame-options"] == "DENY"
    client.post("/api/logout")
    assert client.get("/api/recordings").status_code == 401


def test_clock_and_cells():
    assert (server._clock(65), server._clock(4325)) == ("01:05", "1:12:05")
    assert (server._cell("-1"), server._cell("ok"), server._cell(3)) == ("'-1", "ok", 3)


def test_live_airspace_is_configurable(monkeypatch):
    monkeypatch.setenv("READBACK_LIVE_CENTER", "50.10,14.26")
    monkeypatch.setenv("READBACK_LIVE_NAME", "Prague")
    assert server.live_center() == ("Prague", (50.10, 14.26))
    monkeypatch.delenv("READBACK_LIVE_CENTER")
    monkeypatch.delenv("READBACK_LIVE_NAME")
    assert server.live_center()[0] == "Bengaluru"


def test_upload_limits(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD", 10)
    assert client.post("/api/recordings", content=np.zeros(20, "<i2").tobytes()).status_code == 413
    assert client.post("/api/recordings", content=b"").status_code == 400
    monkeypatch.delenv("MODEL_API_KEY")
    assert client.post("/api/recordings", content=b"x").status_code == 400


def test_glimmer_parser_only_when_deployed(client, monkeypatch):
    monkeypatch.delenv("READBACK_GLIMMER", raising=False)
    assert client.get("/healthz").json()["glimmer"] is False
    r = client.post("/api/recordings?parser=glimmer", content=_wav())
    assert r.status_code == 400 and "READBACK_GLIMMER" in r.json()["detail"]

    from readback.glimmer import GlimmerParser

    class Fake:
        def remote(self, texts):
            return ['{"callsign": "ifly 9 6 1", "items": [{"kind": "flight_level", '
                    '"value": "90", "qualifier": "descend"}]}' if "9 0" in t else
                    '{"callsign": "ifly 9 6 1", "items": [{"kind": "flight_level", '
                    '"value": "100", "qualifier": "descend"}]}' for t in texts]  # fmt: skip

    monkeypatch.setenv("READBACK_GLIMMER", "1")
    monkeypatch.setattr(server, "glimmer_ready", lambda: True)
    monkeypatch.setattr(server, "_glimmer", lambda: GlimmerParser(remote=Fake()))
    rec = _wait(client, client.post("/api/recordings?parser=glimmer", content=_wav()).json()["id"])
    assert rec["status"] == "done" and rec["parser"] == "glimmer" and rec["summary"]["alerts"] == 1
