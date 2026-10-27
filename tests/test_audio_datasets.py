import io
import wave

import numpy as np

from readback.audio import to_pcm16_wav
from readback.datasets import describe


def _wav(rate: int, channels: int = 1, seconds: float = 0.5) -> bytes:
    n = int(rate * seconds)
    tone = (np.sin(np.linspace(0, 2 * np.pi * 440 * seconds, n)) * 8000).astype("<i2")
    data = np.repeat(tone, channels)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return buf.getvalue()


def _info(wav_bytes: bytes) -> tuple[int, int, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.getnchannels(), w.getframerate(), w.getnframes()


def test_16k_mono_passes_through_unchanged():
    src = _wav(16000)
    assert to_pcm16_wav(src) is src


def test_8k_is_resampled_to_16k():
    assert _info(to_pcm16_wav(_wav(8000))) == (1, 16000, 8000)


def test_stereo_is_downmixed():
    channels, rate, _ = _info(to_pcm16_wav(_wav(24000, channels=2), target_rate=24000))
    assert (channels, rate) == (1, 24000)


def test_describe_clip_ids():
    atco2 = "atco2_test-set-1h_LKPR_RUZYNE_Radar_120_520MHz_20201028_151000-A__000000-000536"
    assert describe(atco2) == ("LKPR_RUZYNE_Radar_120_520MHz", None)
    assert describe("uwb-atcc_TWR-34720N_001666_001952_AT") == ("TWR-34720N", "controller")
    assert describe("uwb-atcc_TWR-34720N_003268_003531_PI") == ("TWR-34720N", "pilot")
