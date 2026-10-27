"""Convert WAV audio into the format Voice Transcribe accepts: mono, 16-bit PCM, 16 or 24 kHz."""

from __future__ import annotations

import io
import wave
from math import gcd

import numpy as np
from scipy.signal import resample_poly

SUPPORTED_RATES = (16000, 24000)


def to_pcm16_wav(wav_bytes: bytes, target_rate: int = 16000) -> bytes:
    if target_rate not in SUPPORTED_RATES:
        raise ValueError(f"target_rate must be one of {SUPPORTED_RATES}")
    with wave.open(io.BytesIO(wav_bytes)) as src:
        channels, width, rate = src.getnchannels(), src.getsampwidth(), src.getframerate()
        frames = src.readframes(src.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {8 * width}-bit")
    if channels == 1 and rate == target_rate:
        return wav_bytes

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        g = gcd(rate, target_rate)
        samples = resample_poly(samples, target_rate // g, rate // g)
    pcm = np.clip(np.round(samples), -32768, 32767).astype("<i2")

    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(target_rate)
        dst.writeframes(pcm.tobytes())
    return out.getvalue()
