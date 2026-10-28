"""Make synthetic speech sound like VHF airband radio, and synthesize it: Windows speech on
Windows, espeak-ng elsewhere (Linux, the Docker image)."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt

RATE = 16000

_SAPI_SCRIPT = r"""
param([string]$TextFile, [string]$OutFile, [string]$Voice, [int]$Rate)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { $s.SelectVoice($Voice) }
$s.Rate = $Rate
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($OutFile, $fmt)
$s.Speak((Get-Content -Raw -Encoding UTF8 $TextFile))
$s.Dispose()
"""


def vhf(samples: np.ndarray, rng: np.random.Generator, snr_db: float = 15.0) -> np.ndarray:
    """Band-limit, compress and add hiss plus squelch bursts, like an AM airband receiver.
    Input and output are float arrays in [-1, 1] at 16 kHz."""
    band = butter(4, [300, 3000], btype="bandpass", fs=RATE, output="sos")
    voice = sosfilt(band, samples)
    voice = np.tanh(3.0 * voice / (np.max(np.abs(voice)) + 1e-9)) / np.tanh(3.0)
    power = np.mean(voice**2) + 1e-12
    hiss = sosfilt(band, rng.standard_normal(len(voice)))
    hiss *= np.sqrt(power / (np.mean(hiss**2) * 10 ** (snr_db / 10)))
    key_up = sosfilt(band, rng.standard_normal(int(0.12 * RATE))) * 0.25
    tail = sosfilt(band, rng.standard_normal(int(0.25 * RATE))) * 0.35
    out = np.concatenate([key_up, voice + hiss, tail])
    return 0.8 * out / (np.max(np.abs(out)) + 1e-9)


def to_wav(samples: np.ndarray) -> bytes:
    pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def from_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise ValueError("expected 16 kHz mono 16-bit WAV")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768


class SapiTTS:
    """Windows speech synthesis (System.Speech) through PowerShell. Windows only."""

    def __init__(self, voices: dict[str, str] | None = None) -> None:
        # Controller and pilots get different voices so the diarizer has something to separate.
        self.voices = voices or {
            "controller": "Microsoft Zira Desktop",
            "pilot": "Microsoft David Desktop",
        }
        self._dir = Path(tempfile.mkdtemp(prefix="readback-tts-"))
        self._script = self._dir / "speak.ps1"
        self._script.write_text(_SAPI_SCRIPT, encoding="utf-8")
        self._lock = threading.Lock()  # one pair of scratch files

    def speak(self, text: str, role: str, rate: int = 1) -> np.ndarray:
        with self._lock:
            return self._speak(text, role, rate)

    def _speak(self, text: str, role: str, rate: int) -> np.ndarray:
        text_file, out_file = self._dir / "text.txt", self._dir / "out.wav"
        text_file.write_text(text, encoding="utf-8")
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(self._script), "-TextFile", str(text_file), "-OutFile",
                str(out_file), "-Voice", self.voices.get(role, ""), "-Rate", str(rate),
            ],
            check=True,
            capture_output=True,
        )  # fmt: skip
        return from_wav(out_file.read_bytes())


class EspeakTTS:
    """espeak-ng (apt install espeak-ng): robotic, but on any Linux box."""

    def __init__(self, voices: dict[str, str] | None = None) -> None:
        self.voices = voices or {"controller": "en-gb", "pilot": "en-us"}
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if self.exe is None:
            raise RuntimeError("no speech synthesizer: install espeak-ng")

    def speak(self, text: str, role: str, rate: int = 1) -> np.ndarray:
        proc = subprocess.run(
            [self.exe, "-v", self.voices.get(role, "en"), "-s", str(160 + 15 * rate), "--stdout"],
            input=text.encode(), check=True, capture_output=True,
        )  # fmt: skip
        with wave.open(io.BytesIO(proc.stdout)) as w:
            rate_hz = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        speech = pcm.astype(np.float64) / 32768
        return speech if rate_hz == RATE else resample_poly(speech, RATE, rate_hz)


def default_tts() -> SapiTTS | EspeakTTS:
    return SapiTTS() if sys.platform == "win32" else EspeakTTS()


def on_frequency(tts: SapiTTS | EspeakTTS, text: str, role: str, rng: np.random.Generator) -> bytes:
    """One transmission as it sounds on frequency: spoken (pilots a little faster), then
    through the VHF effect at 10-20 dB SNR, as a 16 kHz WAV."""
    speech = tts.speak(text, role, rate=2 if role == "pilot" else 1)
    return to_wav(vhf(speech, rng, snr_db=float(rng.uniform(10, 20))))
