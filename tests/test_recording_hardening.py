import io
import itertools
import shutil
import subprocess
import sys
import wave

import numpy as np
import pytest
from test_occurrence_recording import FakeASR, _bursts, _wav

from readback import recording, rules
from readback.adsb import KNOWN_DESIGNATORS
from readback.asr import Transcription
from readback.recording import RecordingError, analyze, to_wav

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _parse(text):
    if "squawk" in text.lower():
        raise RuntimeError("HTTP 429 from the parser")
    return rules.parse(text, KNOWN_DESIGNATORS)


def test_a_parser_failure_loses_one_transmission_not_the_recording():
    asr = FakeASR([
        (0.0, "Ifly nine six one, descend flight level one hundred."),
        (6.0, "Descending flight level niner zero, ifly nine six one."),
        (9.0, "Ifly nine six one, squawk four three one two."),
    ])  # fmt: skip
    result = analyze(_wav(), asr, _parse)
    assert {"wav", "duration", "transmissions", "alerts", "failed_transmissions"} <= set(result)
    assert (result["failed_parses"], result["failed_transmissions"]) == (1, 0)
    txs = result["transmissions"]
    assert len(txs) == 3
    assert txs[2]["clearance"] == {"callsign": None, "items": []}
    assert txs[2]["text"].startswith("Ifly nine six one, squawk")
    assert [(a["kind"], a["expected"], a["heard"]) for a in result["alerts"]] == [
        ("flight_level", "100", "90")
    ]


def _chunk(cid: bytes, body: bytes) -> bytes:
    return cid + len(body).to_bytes(4, "little") + body + b"\0" * (len(body) % 2)


def _riff(*chunks: bytes) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + (len(body)).to_bytes(4, "little") + body


FMT = (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
FMT += (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")


def test_samples_are_found_by_walking_the_chunks():
    samples = np.array([1000, -1000, 2000], dtype="<i2").tobytes()
    # A tag holding "data" at an odd offset, padded to an even length, before the real chunk.
    tags = _chunk(b"LIST", b"INFOINAM" + (9).to_bytes(4, "little") + b"LKPR data")
    unknown = b"data" + (0xFFFFFFFF).to_bytes(4, "little") + samples  # as ffmpeg writes to a pipe
    assert bytes(recording._samples(_riff(_chunk(b"fmt ", FMT), tags) + unknown)) == samples
    assert bytes(recording._samples(_riff(_chunk(b"fmt ", FMT), tags, _chunk(b"data", samples))))
    stereo = FMT[:2] + (2).to_bytes(2, "little") + FMT[4:]
    with pytest.raises(RecordingError, match="decode"):
        recording._samples(_riff(_chunk(b"fmt ", stereo), _chunk(b"data", samples)))
    with pytest.raises(RecordingError, match="no samples"):
        recording._samples(_riff(_chunk(b"fmt ", FMT), tags))
    with pytest.raises(RecordingError, match="WAV"):
        recording._samples(b"not a riff file at all")


def _encode(tmp_path, seconds: float, *tags: str) -> bytes:
    src = tmp_path / "tone.wav"
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(_bursts([(0.0, seconds)], seconds=seconds).tobytes())
    src.write_bytes(out.getvalue())
    mp3 = tmp_path / f"tone{len(tags)}.mp3"
    meta = [arg for tag in tags for arg in ("-metadata", tag)]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), *meta, str(mp3)],
                   check=True)  # fmt: skip
    return mp3.read_bytes()


def _levels(wav: bytes) -> tuple[float, int]:
    pcm = recording._pcm(wav).astype(np.float64)
    return float(np.sqrt((pcm**2).mean())), int(np.abs(pcm).max())


@needs_ffmpeg
def test_tags_containing_data_do_not_corrupt_the_samples(tmp_path):
    rms, peak = _levels(to_wav(_encode(tmp_path, 2.0)))
    for title in ("title=LKPR data", "title=radar data"):
        tagged_rms, tagged_peak = _levels(to_wav(_encode(tmp_path, 2.0, title)))
        assert tagged_rms == pytest.approx(rms, rel=0.02)
        assert tagged_peak == pytest.approx(peak, rel=0.02)


@needs_ffmpeg
def test_the_decode_stops_at_the_length_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "MAX_SECONDS", 1)
    with pytest.raises(RecordingError, match="longer than the limit"):
        to_wav(_encode(tmp_path, 3.0))


class FlakyASR:
    """Fails every call, or every other call; each failure is noted."""

    def __init__(self, every: int):
        self.every, self.order, self.failures = every, itertools.count(), []

    def transcribe(self, wav, mode, keywords):
        k = next(self.order)
        if k % self.every == 0:
            self.failures.append(k)
            raise RuntimeError("HTTP 503")
        return Transcription("s", "Ifly nine six one, squawk four three one two.", 100, [])


def _many(n: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(_bursts([(1.0 + 1.5 * k, 1.8 + 1.5 * k) for k in range(n)],
                              seconds=2.0 + 1.5 * n).tobytes())  # fmt: skip
    return out.getvalue()


@pytest.fixture
def busy_threads():
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    yield
    sys.setswitchinterval(interval)


def test_counters_from_the_asr_threads_are_exact(busy_threads):
    n, seen = 40, []
    with pytest.raises(RecordingError, match="every transmission"):
        analyze(_many(n), FlakyASR(1), lambda t: None, progress=seen.append)
    assert len(seen) == n and seen == sorted(seen) and seen[-1] == 1.0

    asr = FlakyASR(2)
    result = analyze(_many(n), asr, lambda t: rules.parse(t, KNOWN_DESIGNATORS))
    assert result["failed_transmissions"] == len(asr.failures) == n // 2
    assert len(result["transmissions"]) == n // 2


def test_a_short_fmt_chunk_is_a_decode_error():
    samples = np.array([1000, -1000], dtype="<i2").tobytes()
    with pytest.raises(RecordingError, match="decode"):
        recording._samples(_riff(_chunk(b"fmt ", FMT[:4]), _chunk(b"data", samples)))
    with pytest.raises(RecordingError, match="decode"):
        recording._samples(_riff() + b"fmt " + (16).to_bytes(4, "little") + FMT[:6])


def _tone_8k(seconds: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(np.zeros(int(8000 * seconds), dtype="<i2").tobytes())
    return out.getvalue()


@pytest.mark.parametrize("ffmpeg", [None, pytest.param("ffmpeg", marks=needs_ffmpeg)])
def test_a_long_wav_is_refused_before_it_is_converted(monkeypatch, ffmpeg):
    def convert(data):
        raise AssertionError("the whole file was converted")

    monkeypatch.setattr(recording, "MAX_SECONDS", 1)
    monkeypatch.setattr(recording, "to_pcm16_wav", convert)
    if ffmpeg is None:
        monkeypatch.setattr(recording.shutil, "which", lambda name: None)
    with pytest.raises(RecordingError, match="longer than the limit"):
        to_wav(_tone_8k(3.0))
