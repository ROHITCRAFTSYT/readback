"""Keep the tagger training forever and publish its progress.

    uv run python scripts/supervise.py            # what scripts/autostart.py runs
    uv run python scripts/supervise.py --no-push  # train and check, but don't touch git

Loop: an hour of training (scripts/train_tagger.py), the daily fresh check once a day, then
commit and push the progress files (training/*.json*, TRAINING.md and, at milestones and
daily, models/tagger.pt). It never exits on its own; failures are logged and retried.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "training"
LOGS = STATE / "logs"
MODEL = ROOT / "models" / "tagger.pt"
AUTHOR = ("ROHITCRAFTSYT", "ytrohitcrafts@gmail.com")
PUBLISHED = ["training/status.json", "training/log.jsonl", "training/checks.jsonl",
             "TRAINING.md", "models/tagger.pt"]  # fmt: skip


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "supervisor.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_training(hours: float) -> int:
    with (LOGS / "train.log").open("a", encoding="utf-8") as out:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "train_tagger.py"), "--hours", str(hours)],
            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
        )  # fmt: skip
    return proc.returncode


def _local_state() -> dict:
    path = LOGS / "supervisor_state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _save_local_state(state: dict) -> None:
    (LOGS / "supervisor_state.json").write_text(json.dumps(state, indent=2))


def publish_model(reason: str) -> None:
    if (STATE / "champion.pt").exists():
        MODEL.parent.mkdir(exist_ok=True)
        shutil.copyfile(STATE / "champion.pt", MODEL)
        log(f"published champion to models/tagger.pt ({reason})")


def milestones() -> None:
    """Publish the model when the run reaches a new level or becomes optimal."""
    status = json.loads((STATE / "status.json").read_text(encoding="utf-8"))
    mark = [len(status["levels_passed"]), status["optimal"]]
    local = _local_state()
    if local.get("milestone") != mark:
        publish_model(f"level {status['level']}, optimal={status['optimal']}")
        local["milestone"] = mark
        _save_local_state(local)


def daily_check() -> None:
    today = date.today().isoformat()
    local = _local_state()
    if local.get("daily") == today or not (STATE / "champion.pt").exists():
        return
    import torch

    from readback.tagger.model import Tagger
    from readback.tagger.report import render
    from readback.tagger.train import fresh_check

    torch.set_num_threads(2)
    tagger = Tagger.load(STATE / "champion.pt")
    result = fresh_check(tagger, today)
    result["champion_round"] = tagger.meta.get("round")
    with (STATE / "checks.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")
    (ROOT / "TRAINING.md").write_text(render(STATE), encoding="utf-8")
    publish_model("daily")
    local["daily"] = today  # only once the check really finished
    _save_local_state(local)
    log(f"daily check {today}: synthetic exact {result['synthetic']['exact']:.3f}, "
        f"real held-out F1 {result.get('gold_test', {}).get('item_f1', 0):.3f}")  # fmt: skip


def git(*args: str) -> subprocess.CompletedProcess:
    name, email = AUTHOR
    return subprocess.run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}", *args],
                          cwd=ROOT, capture_output=True, text=True)  # fmt: skip


def push() -> None:
    files = [p for p in PUBLISHED if (ROOT / p).exists()]
    git("add", "--", *files)
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    status = json.loads((STATE / "status.json").read_text(encoding="utf-8"))
    test = status["champion"].get("gold_test", {}).get("item_f1")
    msg = f"Training: round {status['round']}, level {status['level']}" + (
        f", real-radio F1 {100 * test:.1f}%" if test is not None else ""
    )
    # Commit only the progress files, never whatever else is staged in the working tree.
    res = git("commit", "-m", msg, "--", *files)
    if res.returncode != 0:
        log(f"commit failed: {res.stderr.strip() or res.stdout.strip()}")
        return
    for step in (("pull", "--rebase", "--autostash"), ("push",)):
        res = git(*step)
        if res.returncode != 0:
            log(f"git {step[0]} failed: {res.stderr.strip()[-300:]}")
            return
    log(f"pushed: {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--hours", type=float, default=1.0, help="training per cycle")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from readback.tagger.lock import FileLock, Locked

    try:
        lock = FileLock(LOGS / "supervisor.lock").__enter__()
    except Locked:
        print("a supervisor is already running", file=sys.stderr)
        return 3
    log(f"supervisor started (pid lock {lock.path})")
    failures = 0
    while True:
        try:
            code = run_training(args.hours)
            if code == 3:
                log("another trainer holds the state; waiting for it")
                time.sleep(120)
                continue
            if code != 0:
                failures += 1
                log(f"training exited with {code} (failure {failures} in a row)")
                time.sleep(60 if failures < 5 else 900)
            else:
                failures = 0
            milestones()
            daily_check()
            if not args.no_push:
                push()
        except Exception as exc:  # keep going whatever happens; the log says what
            log(f"supervisor error: {exc!r}")
            time.sleep(120)


if __name__ == "__main__":
    sys.exit(main())
