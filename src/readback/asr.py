"""Client for Muse Voice Transcribe's file endpoint (POST /v1/asr/transcribe)."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx

from readback.normalize import DIGITS, ICAO_LETTERS

API_BASE = "https://api.meta.ai/v1"
MODEL = "muse-voice-transcribe-1.0"
MODES = ("PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION")
RETRYABLE = {429, 500, 502, 503, 504}

_TOKEN = re.compile(r"[\w']+|[,.;:!?]")
_NUMBERISH = set(DIGITS) | set(ICAO_LETTERS) | {"hundred", "thousand", "decimal", "point"}


def _plain(word: str) -> bool:
    w = word.lower()
    return len(w) > 1 and not w.isdigit() and w not in _NUMBERISH


def drop_repeats(text: str) -> str:
    """Remove the stutters Voice Transcribe sometimes adds: words said again right after a
    comma or full stop ("heading two two five, five" -> "heading two two five") and a repeated
    phrase without digits or spelled letters ("turn turn", "flight level flight level").
    Repeated digits with no punctuation between them stay: "one one zero" is a real level."""
    toks = _TOKEN.findall(text)
    out: list[str] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in ",.;:!?":
            if out and out[-1] not in ",.;:!?":
                out.append(tok)
            for k in range(4, 0, -1):  # the longest phrase repeated across the break
                before, after = out[-1 - k : -1], toks[i + 1 : i + 1 + k]
                if len(before) == k and all(t not in ",.;:!?" for t in before + after):
                    if [t.lower() for t in before] == [t.lower() for t in after]:
                        i += k
                        break
            i += 1
            continue
        for k in range(3, 0, -1):
            phrase, again = toks[i : i + k], toks[i + k : i + 2 * k]
            if len(again) == k and all(_plain(t) for t in phrase):
                if [t.lower() for t in phrase] == [t.lower() for t in again]:
                    i += k  # skip the first copy; the second is kept on the next pass
                    break
        else:
            out.append(tok)
            i += 1
    text = " ".join(out)
    return re.sub(r" ([,.;:!?])", r"\1", text)


class TranscribeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Turn:
    turn_id: int
    start_ms: int
    end_ms: int
    transcript: str
    speaker: str | None = None


@dataclass(frozen=True)
class Transcription:
    session_id: str
    transcript: str
    audio_duration_ms: int
    turns: list[Turn]


def parse_response(body: dict) -> Transcription:
    turns = [
        Turn(
            turn_id=t["turnId"],
            start_ms=t["startMs"],
            end_ms=t["endMs"],
            transcript=t["transcript"],
            speaker=t.get("speaker"),
        )
        for t in body.get("turns", [])
    ]
    return Transcription(
        session_id=body["sessionId"],
        transcript=body["transcript"],
        audio_duration_ms=body["audioDurationMs"],
        turns=turns,
    )


class VoiceTranscribe:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = API_BASE,
        client: httpx.Client | None = None,
        max_retries: int = 5,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key or os.environ.get("MODEL_API_KEY")
        if not key:
            raise TranscribeError("MODEL_API_KEY is not set")
        self._client = client or httpx.Client(timeout=timeout)
        self._url = f"{base_url.rstrip('/')}/asr/transcribe"
        self._headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        self._max_retries = max_retries
        self._sleep = sleep

    def transcribe(
        self,
        wav: bytes,
        *,
        mode: str = "PUSH_TO_TALK",
        keywords: Iterable[str] | None = None,
        language_bias: Iterable[str] | None = None,
        session_id: str | None = None,
    ) -> Transcription:
        """Transcribe a mono 16-bit PCM WAV (16 or 24 kHz, at most 10 minutes)."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        request: dict = {"model": MODEL, "audioEncoding": "WAV", "mode": mode}
        if keywords:
            request["keywords"] = list(keywords)
            if too_long := [k for k in request["keywords"] if len(k) > 30]:
                raise ValueError(f"keywords over 30 characters are rejected by the API: {too_long}")
        if language_bias:
            request["languageBias"] = list(language_bias)
        sid = session_id or f"readback-{uuid.uuid4()}"
        files = {
            "request": (None, json.dumps(request), "application/json"),
            "audio": ("clip.wav", wav, "audio/wav"),
        }

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.post(
                    self._url, params={"sessionId": sid}, headers=self._headers, files=files
                )
            except httpx.TransportError as exc:  # timeout or dropped connection: try again
                if attempt == self._max_retries:
                    raise TranscribeError(f"{type(exc).__name__} (session {sid}): {exc}") from exc
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                break
            retry_after = resp.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else min(2.0**attempt, 30.0)
            self._sleep(delay)

        if resp.status_code != 200:
            raise TranscribeError(f"HTTP {resp.status_code} (session {sid}): {resp.text[:300]}")
        return parse_response(resp.json())
