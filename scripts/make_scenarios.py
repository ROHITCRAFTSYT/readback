"""Generate simulated scenarios, optionally with radio audio for every transmission.

Writes scenarios/vobl-<seed>.json and, with --audio, scenarios/audio/vobl-<seed>/<tx>.wav
(Windows speech synthesis + VHF radio effect). Both directories are gitignored; scenarios are
deterministic, so anyone can regenerate them from the seed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from readback.radio import SapiTTS, on_frequency
from readback.sim import generate

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--seeds", default="1", help="comma-separated seeds, e.g. 1,2,3")
    ap.add_argument("--audio", action="store_true", help="synthesize radio audio (Windows)")
    ap.add_argument("--out", type=Path, default=ROOT / "scenarios")
    args = ap.parse_args()

    tts = SapiTTS() if args.audio else None
    for seed in (int(s) for s in args.seeds.split(",")):
        scen = generate(seed).to_dict()
        if tts:
            audio_dir = args.out / "audio" / scen["name"]
            audio_dir.mkdir(parents=True, exist_ok=True)
            rng = np.random.default_rng(seed)
            for ev in scen["events"]:
                if ev["type"] != "tx":
                    continue
                path = audio_dir / f"{ev['id']}.wav"
                path.write_bytes(on_frequency(tts, ev["text"], ev["role"], rng))
                ev["audio"] = path.relative_to(args.out).as_posix()
        args.out.mkdir(parents=True, exist_ok=True)
        dest = args.out / f"{scen['name']}.json"
        dest.write_text(json.dumps(scen), encoding="utf-8")
        n_tx = sum(e["type"] == "tx" for e in scen["events"])
        print(f"{dest}: {n_tx} transmissions, {len(scen['labels'])} planted problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
