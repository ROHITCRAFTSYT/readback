import csv
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import islice

import httpx
import pytest
from fastapi.testclient import TestClient
from test_occurrence_recording import REPORT, FakeASR, FakeChat, _log, _wav
from test_store_server import _done, _wait

from readback import recording, server
from readback.clearance import Clearance
from readback.llm import ChatError, MuseChat, SparkParser
from readback.occurrence import investigate
from readback.replay import replay
from readback.store import Store

TURNS = [
    (0.0, "ifly nine six one, descend flight level one hundred"),
    (6.0, "descending flight level niner zero, ifly nine six one"),
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.delenv("READBACK_TOKEN", raising=False)
    server.configure(tmp_path)
    monkeypatch.setattr(server, "_asr", lambda: FakeASR(TURNS))
    return TestClient(server.app)


def _events(client, url) -> list[tuple[str, dict]]:
    out, kind = [], None
    with client.stream("GET", url) as resp:
        for line in resp.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                out.append((kind, json.loads(line.removeprefix("data: "))))
    return out


# ---------- report agent ----------
def test_muse_errors_reach_the_ui_as_502(client, monkeypatch):
    body = client.post("/api/analyze?parser=rules", content=_wav()).json()

    class Down:
        def chat(self, messages, tools):
            raise ChatError("HTTP 429: rate limited")

    monkeypatch.setattr(server, "_chat", lambda: Down())
    r = client.post(f"/api/report/{body['session']}/0")
    assert r.status_code == 502 and "HTTP 429" in r.json()["detail"]


def test_a_200_that_is_not_a_reply_is_a_chat_error():
    for body in (b"<html>gateway</html>", b'{"choices": []}', b'{"error": "busy"}'):
        transport = httpx.MockTransport(
            lambda request, body=body: httpx.Response(200, content=body)
        )
        chat = MuseChat("key", client=httpx.Client(transport=transport))
        with pytest.raises(ChatError, match="unexpected response"):
            chat.chat([{"role": "user", "content": "hi"}])


def test_spark_cache_entry_of_the_wrong_shape_is_asked_again(tmp_path):
    answer = {"callsign": "csa 1 d z", "items": []}
    content = json.dumps(answer)
    reply = {"choices": [{"message": {"content": content}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
    parser = SparkParser(MuseChat("key", client=httpx.Client(transport=transport)), tmp_path)
    parser("csa one delta zulu")
    [entry] = tmp_path.glob("*.json")
    entry.write_text('{"items": 7}', encoding="utf-8")  # valid JSON, wrong shape
    assert parser("csa one delta zulu") == Clearance.from_dict(answer)
    assert json.loads(entry.read_text(encoding="utf-8"))["callsign"] == "csa 1 d z"


def test_wrong_typed_report_is_sent_back_to_the_model():
    bad = dict(REPORT, timeline=["00:12 descend FL100"], contributing_factors="fatigue")
    chat = FakeChat([("submit_report", bad), ("submit_report", REPORT)])
    log = _log()
    assert investigate(chat, log, log.alerts[0])["report"] == REPORT
    error = json.loads(chat.seen[1]["content"])["error"]
    assert "timeline" in error and "contributing_factors" in error


def test_bad_tool_arguments_come_back_as_errors():
    chat = FakeChat([("adsb_track", {"callsign": 123}), ("transmissions", {"callsign": None}),
                     ("submit_report", REPORT)])  # fmt: skip
    log = _log()
    assert investigate(chat, log, log.alerts[0])["report"] == REPORT
    assert "error" in json.loads(chat.seen[1]["content"])
    assert len(json.loads(chat.seen[2]["content"])) == 2  # no callsign: every transmission


# ---------- replay stream ----------
def test_a_failed_parse_does_not_stop_the_replay(monkeypatch):
    calls = []
    rules = server._parser("rules")

    def flaky(text):
        calls.append(text)
        if len(calls) == 2:
            raise ChatError("HTTP 500: model unavailable")
        return rules(text)

    monkeypatch.setattr(server, "_parser", lambda name: flaky)
    events = _events(TestClient(server.app), "/api/replay/7?speed=1000&parser=rules")
    txs = [data for kind, data in events if kind == "tx"]
    assert events[-1][0] == "end" and len(txs) == len(calls) > 2
    assert "HTTP 500" in txs[1]["parse_error"] and txs[1]["items"] == []
    assert all(tx["parse_error"] is None for k, tx in enumerate(txs) if k != 1)


def test_an_unexpected_failure_ends_the_stream_with_an_error(monkeypatch):
    def broken(scen, monitor):
        yield from islice(replay(scen, monitor), 3)
        raise RuntimeError("speech synthesis failed")

    monkeypatch.setattr(server, "replay", broken)
    events = _events(TestClient(server.app), "/api/replay/7?speed=1000&parser=rules")
    assert [kind for kind, _ in events][-1] == "error"
    assert "speech synthesis failed" in events[-1][1]["detail"]


def test_replay_ends_with_its_scorecard():
    events = _events(TestClient(server.app), "/api/replay/7?speed=1000&parser=rules")
    kind, end = events[-1]
    alerts = {data["index"]: data for kind, data in events if kind == "alert"}
    score = end["score"]
    assert kind == "end" and end["labels"]
    assert score["planted"] == len(end["labels"]) == score["caught"]
    assert score["false_errors"] == 0
    for lb in score["labels"]:
        assert lb["caught"] and lb["alert_index"] in alerts and lb["delay_s"] is not None
        assert set(lb) == {"t", "callsign", "source", "kind", "detail", "cls", "caught",
                           "alert_index", "delay_s"}  # fmt: skip
        assert -60 <= lb["delay_s"] <= 420


# ---------- uploads and the queue ----------
def test_chunked_upload_over_the_limit_is_refused(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD", 20)

    def body():  # no Content-Length: sent chunked
        for _ in range(4):
            yield b"x" * 8

    r = client.post("/api/recordings", content=body())
    assert r.status_code == 413
    assert list(server.store().uploads.iterdir()) == [] and server.store().list() == []


def test_chunked_upload_within_the_limit_is_checked(client):
    wav = _wav()
    r = client.post("/api/recordings?parser=rules", content=(wav[k : k + 1000]
                    for k in range(0, len(wav), 1000)))  # fmt: skip
    rec = _wait(client, r.json()["id"])
    assert rec["status"] == "done" and rec["summary"]["alerts"] == 1


def test_full_queue_refuses_new_checks(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_QUEUE", 2)
    server.store().create("waiting 1", "rules")
    server.store().create("waiting 2", "rules")
    r = client.post("/api/recordings?parser=rules", content=_wav())
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
    assert client.post("/api/analyze?parser=rules", content=_wav()).status_code == 503
    assert list(server.store().uploads.iterdir()) == []


def test_uploads_still_arriving_count_towards_the_queue(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_QUEUE", 2)
    server.store().create("waiting", "rules")
    monkeypatch.setattr(server, "_RECEIVING", 1)  # another upload is half received
    assert client.post("/api/recordings?parser=rules", content=_wav()).status_code == 503
    monkeypatch.setattr(server, "_RECEIVING", 0)
    assert client.post("/api/recordings?parser=rules", content=_wav()).status_code == 202
    assert server._RECEIVING == 0


def test_unexpected_job_failure_keeps_internals_out_of_the_store(client, monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError(r"D:\readback\var\uploads\abc.upload")

    monkeypatch.setattr(recording, "analyze", boom)
    rec = _wait(client, client.post("/api/recordings?parser=rules", content=_wav()).json()["id"])
    assert rec["error"] == "internal error (FileNotFoundError); see the server log"


# ---------- listings and the register ----------
def test_listing_never_reads_the_logs(tmp_path, monkeypatch):
    st = Store(tmp_path)
    _done(st)
    sql = []
    run = st._run
    monkeypatch.setattr(st, "_run", lambda q, args=(): sql.append(q) or run(q, args))
    [item] = st.list()
    assert "log" not in item and item["summary"]["alerts"] == 2
    assert not any("*" in q or "log" in q for q in sql if "FROM analyses" in q)


def test_alert_register_is_complete_and_reads_in_bulk(client, monkeypatch):
    st = server.store()
    ids = [_done(st, alerts=k) for k in (1, 2, 3)]
    alert = {"t": 5.0, "severity": "error", "source": "readback", "kind": "flight_level",
             "callsign": "ifly 9 6 1", "message": "m", "expected": "100",
             "heard": "90"}  # fmt: skip
    for k, aid in enumerate(ids):
        st.update(aid, log={"transmissions": [], "alerts": [alert] * (k + 1)})
        st._run("UPDATE analyses SET created = ? WHERE id = ?", (1000.0 + k, aid))
    st.review(ids[2], 1, "false_alarm", "misheard")
    st.create("still queued", "rules")
    monkeypatch.setattr(Store, "get", lambda *a: pytest.fail("one query per recording"))
    text = client.get("/api/alerts.csv").text
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    assert [(r["recording_id"], r["alert"]) for r in rows] == [
        (ids[2], "0"), (ids[2], "1"), (ids[2], "2"), (ids[1], "0"), (ids[1], "1"), (ids[0], "0")
    ]  # fmt: skip
    assert [r["verdict"] for r in rows[:2]] == ["open", "false_alarm"] and rows[1]["note"]


# ---------- shutdown, readiness, metrics, retention ----------
def test_shutdown_leaves_queued_checks_for_the_next_start(tmp_path, monkeypatch, caplog):
    gate, started = threading.Event(), threading.Event()
    parsed = []
    monkeypatch.setattr(server, "_parser", lambda name: parsed.append)

    class Slow(FakeASR):
        def transcribe(self, wav, mode, keywords):
            started.set()
            gate.wait(10)
            return super().transcribe(wav, mode, keywords)

    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(server, "JOBS", pool)
    monkeypatch.setattr(server, "_STORE", None)
    monkeypatch.setattr(server, "_asr", lambda: Slow(TURNS))
    monkeypatch.setenv("READBACK_DATA", str(tmp_path))
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.delenv("READBACK_TOKEN", raising=False)
    with TestClient(server.app) as c:
        ids = [c.post("/api/recordings?parser=rules", content=_wav()).json()["id"]
               for _ in range(3)]  # fmt: skip
        assert started.wait(5)
    assert server._STORE is None  # closed at shutdown
    gate.set()
    pool.shutdown(wait=True)  # the check that was running is cut short
    assert parsed == []  # it stopped once its transcription came back, without parsing it
    assert not any("failed" in r.getMessage() for r in caplog.records)

    wal = tmp_path / "readback.db-wal"
    assert not wal.exists() or not wal.stat().st_size  # folded into readback.db

    st = Store(tmp_path)
    assert [st.get(aid)["status"] for aid in ids] == ["running", "queued", "queued"]
    assert sorted(st.interrupted()) == sorted(ids)  # every upload is kept for the next start


def test_readiness_and_metrics(client, monkeypatch):
    st = server.store()
    aid = _done(st, alerts=3)
    st.update(aid, unit="LKPR TWR", name="Secret shift")
    st.review(aid, 0, "confirmed")
    st.create("waiting", "rules", "IGO961")
    monkeypatch.setenv("READBACK_TOKEN", "s3cret-token")  # probes need no token

    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["queued"] == 1
    text = client.get("/metrics").text
    assert 'readback_recordings{status="done"} 1' in text
    assert 'readback_recordings{status="queued"} 1' in text
    assert "readback_queue_depth 1" in text and "readback_alerts 3" in text
    assert 'readback_reviews{verdict="confirmed"} 1' in text
    assert "readback_job_duration_seconds_count" in text and "readback_uptime_seconds" in text
    assert not any(s in text for s in ("LKPR", "Secret", "IGO961", "tower"))
    assert set(client.get("/healthz").json()) == {"ok", "parser", "muse", "glimmer", "auth",
                                                  "live"}  # fmt: skip

    st.close()
    assert client.get("/readyz").status_code == 503


def test_retention_runs_on_a_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_STORE", None)
    monkeypatch.setattr(server, "RETENTION_EVERY", 0.05)
    monkeypatch.setenv("READBACK_DATA", str(tmp_path))
    monkeypatch.setenv("READBACK_RETENTION_DAYS", "30")
    with TestClient(server.app):
        st = server.store()
        old = _done(st)
        st._run("UPDATE analyses SET created = ? WHERE id = ?", (time.time() - 40 * 86400, old))
        for _ in range(100):
            if st.get(old) is None:
                break
            time.sleep(0.05)
        assert st.get(old) is None
