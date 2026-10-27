import json

import httpx
import pytest

from readback.asr import TranscribeError, VoiceTranscribe

OK_BODY = {
    "sessionId": "s1",
    "transcript": "CSA1DZ descend FL100",
    "audioDurationMs": 4000,
    "turns": [
        {"turnId": 0, "startMs": 0, "endMs": 1800, "transcript": "CSA1DZ", "speaker": "A"},
        {"turnId": 1, "startMs": 1900, "endMs": 4000, "transcript": "descend FL100"},
    ],
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _request_part(request: httpx.Request) -> dict:
    body = request.read().decode("latin-1")
    start = body.index('name="request"')
    payload = body[body.index("{", start) : body.index("}", start) + 1]
    return json.loads(payload)


def test_sends_multipart_with_keywords_and_parses_turns():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["session"] = request.url.params["sessionId"]
        seen["request"] = _request_part(request)
        return httpx.Response(200, json=OK_BODY)

    asr = VoiceTranscribe("key", client=_client(handler))
    result = asr.transcribe(b"RIFF....", mode="DIARIZATION", keywords=["csa one delta zulu"])

    assert seen["auth"] == "Bearer key"
    assert seen["session"].startswith("readback-")
    assert seen["request"] == {
        "model": "muse-voice-transcribe-1.0",
        "audioEncoding": "WAV",
        "mode": "DIARIZATION",
        "keywords": ["csa one delta zulu"],
    }
    assert result.transcript == "CSA1DZ descend FL100"
    assert [t.speaker for t in result.turns] == ["A", None]


def test_retries_rate_limits_then_succeeds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=OK_BODY)

    asr = VoiceTranscribe("key", client=_client(handler), sleep=sleeps.append)
    assert asr.transcribe(b"x").session_id == "s1"
    assert sleeps == [2.0, 2.0]


def test_raises_on_client_error_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"message": "unsupported WAV"})

    asr = VoiceTranscribe("key", client=_client(handler), sleep=lambda s: None)
    with pytest.raises(TranscribeError, match="HTTP 400"):
        asr.transcribe(b"x")
    assert len(calls) == 1


def test_missing_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(TranscribeError, match="MODEL_API_KEY"):
        VoiceTranscribe()
