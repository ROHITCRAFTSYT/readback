import json

import httpx
import pytest

from readback.asr import TranscribeError, VoiceTranscribe, drop_repeats

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


def test_retries_timeouts_then_gives_up():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=OK_BODY)

    asr = VoiceTranscribe("key", client=_client(handler), sleep=sleeps.append)
    assert asr.transcribe(b"x").session_id == "s1" and sleeps == [1.0]

    def hang(request):
        raise httpx.ReadTimeout("slow", request=request)

    asr = VoiceTranscribe("key", client=_client(hang), max_retries=2, sleep=lambda s: None)
    with pytest.raises(TranscribeError, match="ReadTimeout"):
        asr.transcribe(b"x")


@pytest.mark.parametrize(
    ("heard", "clean"),
    [
        ("right heading two two five, five, ifly seven eight five,",
         "right heading two two five, ifly seven eight five,"),
        ("spicejet four three one, three one.", "spicejet four three one,"),
        ("five thousand feet, feet, right heading", "five thousand feet, right heading"),
        ("descend flight level flight level one hundred", "descend flight level one hundred"),
        ("turn turn right heading two two zero", "turn right heading two two zero"),
        ("Singapore eight nine six. six, descend", "Singapore eight nine six. descend"),
        # real repeats stay
        ("climb flight level one one zero", "climb flight level one one zero"),
        ("oscar kilo kilo bravo", "oscar kilo kilo bravo"),
        ("level one zero zero, speed two five zero", "level one zero zero, speed two five zero"),
    ],
)  # fmt: skip
def test_drop_repeats(heard, clean):
    assert drop_repeats(heard) == clean
