"""Check a recorded frequency: any audio file in, every transmission parsed and checked out.

For post-incident review, training and quality assurance. A frequency is quiet between
push-to-talk transmissions, so the recording is cut at those silences and each transmission is
transcribed on its own, the same unit as the live path; a recording with no usable silences is
transcribed in diarization mode instead, one transmission per speaker turn. The monitor then
pairs instructions with readbacks exactly as it does live. Without ADS-B there are no
track checks, but callsigns known to be on frequency can be given as keywords, and heard
callsigns are matched to them.

Recordings can be hours long (a whole shift from the unit's voice recorder): only the single
transmissions go to Voice Transcribe, whose limit is 10 minutes per request.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import wave
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

from readback.adsb import spoken_callsign
from readback.asr import drop_repeats
from readback.audio import to_pcm16_wav
from readback.clearance import Clearance
from readback.keywords import PHRASEOLOGY, fit
from readback.monitor import Monitor, Transmission
from readback.normalize import canonical, spoken

MAX_SECONDS = 4 * 3600  # a long shift; memory use is about 30 MB per recorded minute
REQUEST_SECONDS = 590  # Voice Transcribe takes at most 10 minutes per request
FRAME = 0.03  # seconds per energy frame
MIN_GAP = 0.35  # silence that ends a transmission
MIN_SPEECH = 0.6  # shorter bursts are clicks and squelch tails
PAD = 0.15  # kept either side of each transmission

log = logging.getLogger(__name__)


class RecordingError(ValueError):
    pass


def to_wav(data: bytes) -> bytes:
    """16 kHz mono 16-bit WAV from a WAV file, or from any format ffmpeg reads."""
    if data[:4] == b"RIFF":
        try:
            return to_pcm16_wav(data)
        except (wave.Error, ValueError, EOFError):
            pass  # an unusual WAV encoding: let ffmpeg try
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RecordingError("only 16-bit PCM WAV is supported without ffmpeg installed")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", "pipe:1"],
        input=data, capture_output=True, timeout=120, check=False,
    )  # fmt: skip
    if proc.returncode != 0 or not proc.stdout:
        raise RecordingError(f"could not decode the audio: {proc.stderr.decode()[-200:]}")
    # ffmpeg writes a streaming header with unknown length; re-wrap the samples cleanly.
    pcm = proc.stdout[proc.stdout.index(b"data") + 8 :]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm[: len(pcm) // 2 * 2])
    return out.getvalue()


def duration(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


def _pcm(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def _wav(pcm: np.ndarray, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())
    return out.getvalue()


def segments(pcm: np.ndarray, rate: int = 16000) -> list[tuple[float, float]]:
    """(start, end) seconds of each transmission: runs of frames louder than the noise floor
    by 10 dB, joined across pauses shorter than MIN_GAP."""
    n = int(FRAME * rate)
    frames = len(pcm) // n
    if frames == 0:
        return []
    db = np.empty(frames)
    block = 20000  # frames at a time, so hours of audio never become one float array
    for i in range(0, frames, block):
        x = pcm[i * n : min(i + block, frames) * n].astype(np.float64).reshape(-1, n)
        db[i : i + len(x)] = 20 * np.log10(np.sqrt((x**2).mean(axis=1)) + 1)
    loud = db > np.percentile(db, 10) + 10
    runs, start = [], None
    for i, on in enumerate([*loud, False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append([start * FRAME, i * FRAME])
            start = None
    merged: list[list[float]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < MIN_GAP:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    total = len(pcm) / rate
    spans = []
    for a, b in merged:
        if b - a < MIN_SPEECH:
            continue
        a, b = max(a - PAD, 0.0), min(b + PAD, total)
        while b - a > REQUEST_SECONDS:  # minutes without a pause: cut to the request limit
            spans.append((a, a + REQUEST_SECONDS))
            a += REQUEST_SECONDS
        spans.append((a, b))
    return spans


def _turns(
    wav: bytes, asr, keywords: list[str], progress: Callable[[float], None] | None = None
) -> tuple[list, int]:
    """One turn per transmission, from silence segmentation, or from diarization; and how many
    transmissions Voice Transcribe failed on (they are skipped, not fatal)."""
    pcm = _pcm(wav)
    spans = segments(pcm)
    if len(spans) < 2:
        if len(pcm) / 16000 > REQUEST_SECONDS:
            raise RecordingError(
                "found no pauses between transmissions; recordings without them can be at most "
                "10 minutes long"
            )
        result = asr.transcribe(wav, mode="DIARIZATION", keywords=keywords)
        if result.turns:
            return result.turns, 0
        text = result.transcript.strip()
        return ([SimpleNamespace(start_ms=0, transcript=text, speaker=None)] if text else []), 0

    done, failed = 0, 0

    def one(span: tuple[float, float]) -> SimpleNamespace:
        nonlocal done, failed
        a, b = (int(t * 16000) for t in span)
        try:
            text = asr.transcribe(_wav(pcm[a:b]), mode="PUSH_TO_TALK", keywords=keywords).transcript
        except Exception as exc:  # one lost transmission must not lose the whole shift
            log.warning("transmission at %.1f s not transcribed: %s", span[0], exc)
            text, failed = "", failed + 1
        done += 1
        if progress:
            progress(done / len(spans))
        return SimpleNamespace(start_ms=int(span[0] * 1000), transcript=text, speaker=None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        turns = list(pool.map(one, spans))
    if failed == len(spans):
        raise RecordingError("Voice Transcribe failed on every transmission; try again later")
    return turns, failed


def callsign_phrase(callsign: str) -> str:
    """'SWR123' (as ADS-B shows it) or 'swiss one two three' -> canonical spoken form."""
    cs = callsign.strip()
    return (
        spoken_callsign(cs)
        if cs.isalnum() and cs[:3].isalpha() and cs.isupper()
        else " ".join(canonical(cs))
    )


def analyze(
    data: bytes,
    asr,
    parse: Callable[[str], Clearance],
    callsigns: Iterable[str] = (),
    progress: Callable[[float], None] | None = None,
) -> dict:
    """Transcribe and check a recording. Returns transmissions and alerts, times in seconds.
    progress, if given, is called with the share of transmissions transcribed so far."""
    wav = to_wav(data)
    seconds = duration(wav)
    if seconds > MAX_SECONDS:
        raise RecordingError(
            f"recording is {seconds / 60:.0f} minutes; the limit is {MAX_SECONDS // 3600} hours"
        )
    known = [callsign_phrase(c) for c in callsigns if c.strip()]
    keywords = list(PHRASEOLOGY) + [fit(spoken(c.split())) for c in known]
    turns, failed = _turns(wav, asr, keywords, progress)

    monitor = Monitor(parse)
    monitor.traffic = set(known)
    transmissions, alerts = [], []
    for k, turn in enumerate(turns):
        text = drop_repeats(turn.transcript).strip()
        if not text:
            continue
        t = turn.start_ms / 1000
        tx_id = f"r{k + 1}"
        clearance, role, raised = monitor.on_transmission(Transmission(t, text, None, tx_id))
        transmissions.append(
            {
                "t": t,
                "id": tx_id,
                "role": role,
                "text": text,
                "speaker": getattr(turn, "speaker", None),
                "clearance": clearance.to_dict(),
                "snapped_from": monitor.snapped.get(tx_id),
            }
        )
        alerts += raised
    end = (transmissions[-1]["t"] if transmissions else 0) + monitor.pairing_window + 1
    alerts += monitor.expire(end)
    return {
        "wav": wav,
        "duration": seconds,
        "failed_transmissions": failed,
        "transmissions": transmissions,
        "alerts": [dict(a.to_dict(), t=a.time) for a in alerts],
    }
