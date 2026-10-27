"""Train the clearance tagger for a while, resuming wherever the last run stopped.

    uv run python scripts/train_tagger.py --hours 1
    uv run python scripts/train_tagger.py --rounds 3 --state scratch/run

Writes training/status.json, log.jsonl, champion.pt and TRAINING.md after every round. Runs
at below-normal priority on two threads, so the computer stays usable.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def lower_priority() -> None:
    if sys.platform == "win32":
        BELOW_NORMAL = 0x4000
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL)
    else:
        os.nice(10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--state", type=Path, default=ROOT / "training")
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()
    if not (args.hours or args.rounds):
        ap.error("give --hours or --rounds")

    import torch

    from readback.tagger.lock import FileLock, Locked
    from readback.tagger.report import render
    from readback.tagger.train import Trainer

    try:
        with FileLock(args.state / "logs" / "train.lock"):
            return train(args, torch, render, Trainer)
    except Locked:
        print(f"another trainer is using {args.state}", file=sys.stderr)
        return 3


def train(args, torch, render, Trainer) -> int:
    lower_priority()
    torch.set_num_threads(args.threads)
    trainer = Trainer(args.state)
    deadline = time.time() + args.hours * 3600 if args.hours else None
    done = 0
    while True:
        e = trainer.round()
        done += 1
        cand = e["candidate"]
        print(
            f"round {e['round']} level {e['level']}: candidate {cand['val']['exact']:.3f} exact, "
            f"real dev F1 {cand.get('gold_dev', {}).get('item_f1', 0):.3f}, "
            f"{'promoted' if e['promoted'] else 'kept champion'}"
            + (f" [{e['event']}]" if e["event"] else "")
            + f" ({e['seconds']:.0f} s)",
            flush=True,
        )
        if args.state == ROOT / "training":
            (ROOT / "TRAINING.md").write_text(render(args.state), encoding="utf-8")
        if args.rounds and done >= args.rounds:
            break
        if deadline and time.time() > deadline:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
