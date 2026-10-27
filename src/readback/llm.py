"""Clearance parser on Muse Spark, using Chat Completions structured output."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from readback.asr import API_BASE, RETRYABLE
from readback.clearance import JSON_SCHEMA, Clearance
from readback.normalize import canonical

MODEL = "muse-spark-1.3"

SYSTEM_PROMPT = """\
You read one air traffic control radio transmission (a controller instruction or a pilot \
readback) and extract what it instructs, following ICAO phraseology.

The text is in canonical form: spelled letters are single lowercase letters (oscar = o) and \
numbers are single digits (one zero zero = 1 0 0). It may contain speech recognition errors.

callsign: the aircraft callsign in the same canonical form, e.g. "csa 1 d z" or \
"o k f a o"; null if no callsign was spoken. Do not invent one.

items: every instruction or readback element, in order. Value formats:
- flight_level: digits without leading zeros ("flight level 0 8 0" -> "80")
- altitude: feet as digits ("5 0 0 0 feet" -> "5000")
- heading: three digits ("heading 0 9 0" -> "090"); qualifier "left" or "right" if a turn \
direction is given, else ""
- speed: knots as digits
- squawk: four digits
- frequency: MHz with a dot ("1 1 9 decimal 6 0 5" -> "119.605")
- qnh: digits
- runway: digits plus l/r/c for left/right/center ("runway 2 7 left" -> "27l")
- cleared_to_land, cleared_for_takeoff, line_up, hold_short: value is the runway in the \
same format, or "" if none was said
- direct_to: the waypoint name
qualifier for flight_level and altitude is "climb", "descend" or "maintain" if said, else "". \
Use "" for qualifier on all other kinds.
Report only what was actually said. Wind, ATIS letters, times and traffic information are \
not items.\
"""


class ChatError(RuntimeError):
    pass


class MuseChat:
    """Minimal Chat Completions client for structured output."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = MODEL,
        base_url: str = API_BASE,
        client: httpx.Client | None = None,
        max_retries: int = 5,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key or os.environ.get("MODEL_API_KEY")
        if not key:
            raise ChatError("MODEL_API_KEY is not set")
        self.model = model
        self._client = client or httpx.Client(timeout=timeout)
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {key}"}
        self._max_retries = max_retries
        self._sleep = sleep

    def structured(self, system: str, user: str, schema: dict, name: str) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        for attempt in range(self._max_retries + 1):
            resp = self._client.post(self._url, headers=self._headers, json=body)
            if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                break
            retry_after = resp.headers.get("Retry-After", "")
            self._sleep(float(retry_after) if retry_after.isdigit() else min(2.0**attempt, 30.0))
        if resp.status_code != 200:
            raise ChatError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ChatError(f"model returned invalid JSON: {content[:200]}") from exc


class SparkParser:
    """Parse transmissions with Muse Spark; results are cached on disk by model and text."""

    def __init__(self, chat: MuseChat, cache_dir: Path | None = None) -> None:
        self._chat = chat
        self._cache_dir = cache_dir

    def __call__(self, text: str) -> Clearance:
        tokens = " ".join(canonical(text))
        key = hashlib.sha256(f"{self._chat.model}\n{SYSTEM_PROMPT}\n{tokens}".encode()).hexdigest()
        cache = self._cache_dir / f"{key[:32]}.json" if self._cache_dir else None
        if cache and cache.exists():
            return Clearance.from_dict(json.loads(cache.read_text(encoding="utf-8")))
        data = self._chat.structured(SYSTEM_PROMPT, tokens, JSON_SCHEMA, "clearance")
        clearance = Clearance.from_dict(data)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(clearance.to_dict()), encoding="utf-8")
        return clearance
