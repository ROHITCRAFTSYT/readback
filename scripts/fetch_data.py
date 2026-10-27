"""Download the open ATC test sets into data/raw/ (gitignored)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from readback.datasets import DATASETS

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, (url, filename) in DATASETS.items():
        dest = RAW / filename
        if dest.exists():
            print(f"{name}: already at {dest}")
            continue
        print(f"{name}: downloading {url}")
        part = dest.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
            resp.raise_for_status()
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
        part.replace(dest)
        print(f"{name}: saved {dest.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
