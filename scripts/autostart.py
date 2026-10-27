"""Keep the training supervisor running on this Windows PC, across reboots and crashes.

    uv run python scripts/autostart.py install   # start now, at every logon, and check hourly
    uv run python scripts/autostart.py status    # what is running and how training is doing
    uv run python scripts/autostart.py stop      # stop everything (it comes back at logon)
    uv run python scripts/autostart.py remove    # stop everything and undo `install`

`install` puts a small .cmd in the Startup folder and an hourly Task Scheduler entry. Both
run `ensure`, which starts the guardian if it isn't running. The guardian (`guard`) runs
scripts/supervise.py and restarts it within seconds if it dies, logging each exit code to
training/logs/autostart.log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LOGS = ROOT / "training" / "logs"
TASK = "Readback trainer watchdog"
STARTUP = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
           / "Programs" / "Startup" / "Readback trainer.cmd")  # fmt: skip
ME = Path(__file__).resolve()


def log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "autostart.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def processes() -> list[dict]:
    """Python processes belonging to this repository's training."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                         text=True).stdout.strip()  # fmt: skip
    rows = json.loads(out) if out else []
    rows = [rows] if isinstance(rows, dict) else rows
    mine = []
    for r in rows:
        cmd = r.get("CommandLine") or ""
        if str(ROOT).lower() not in cmd.lower() and "readback" not in cmd.lower():
            continue
        for role, marker in (("guardian", "autostart.py"), ("supervisor", "supervise.py"),
                             ("trainer", "train_tagger.py")):  # fmt: skip
            if marker in cmd and (role != "guardian" or "guard" in cmd.split(marker, 1)[1]):
                mine.append({"role": role, "pid": r["ProcessId"], "cmd": cmd})
    return mine


def launch_detached(args: list[str]) -> None:
    """Start a process through WMI so it doesn't belong to this console or its job object
    (a terminal closing would otherwise take it down)."""
    cmd = " ".join(f'"{a}"' for a in args)
    ps = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
        f"@{{CommandLine='{cmd}'; CurrentDirectory='{ROOT}'}}; $r.ReturnValue"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                         text=True).stdout.strip()  # fmt: skip
    if out != "0":
        raise RuntimeError(f"Win32_Process.Create returned {out!r}")


def ensure() -> None:
    if any(p["role"] == "guardian" for p in processes()):
        return
    launch_detached([str(PYTHONW), str(ME), "guard"])
    log("guardian was not running; started it")


def guard() -> None:
    """Run the supervisor and restart it whenever it exits."""
    delay = 15
    while True:
        start = time.time()
        code = subprocess.run([str(PYTHONW), str(ROOT / "scripts" / "supervise.py")],
                              cwd=ROOT).returncode  # fmt: skip
        ran = time.time() - start
        log(f"supervisor exited with code {code} after {ran:.0f} s")
        if code == 3:  # another supervisor holds the lock
            delay = 300
        else:
            delay = 15 if ran > 600 else min(delay * 2, 600)
        time.sleep(delay)


def install() -> None:
    if not PYTHONW.exists():
        sys.exit(f"{PYTHONW} not found; run `uv sync` first")
    STARTUP.write_text(f'@echo off\r\nstart "" "{PYTHONW}" "{ME}" ensure\r\n', encoding="utf-8")
    subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", TASK, "/TR",
                    f'"{PYTHONW}" "{ME}" ensure'], check=True, capture_output=True)  # fmt: skip
    ensure()
    print(f"installed: {STARTUP.name} in Startup, hourly task {TASK!r}; training is running")


def stop() -> None:
    for role in ("guardian", "supervisor", "trainer"):  # guardian first, or it restarts the rest
        for p in processes():
            if p["role"] == role:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p["pid"])], capture_output=True)
                log(f"stopped {role} {p['pid']}")


def remove() -> None:
    stop()
    STARTUP.unlink(missing_ok=True)
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True)
    print("removed the Startup entry and the hourly task; nothing is running")


def status() -> None:
    procs = processes()
    for role in ("guardian", "supervisor", "trainer"):
        pids = [p["pid"] for p in procs if p["role"] == role]
        print(f"{role:10} {'running ' + str(pids) if pids else 'not running'}")
    print(f"startup   {'installed' if STARTUP.exists() else 'not installed'}")
    task = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True)
    print(f"watchdog  {'installed' if task.returncode == 0 else 'not installed'}")
    path = ROOT / "training" / "status.json"
    if path.exists():
        st = json.loads(path.read_text(encoding="utf-8"))
        test = st["champion"].get("gold_test", {}).get("item_f1")
        f1 = "-" if test is None else f"{100 * test:.1f}%"
        state = "optimal" if st["optimal"] else "not optimal yet"
        print(
            f"training  round {st['round']}, level {st['level']}, {state}, champion round "
            f"{st['champion']['round']}, real held-out F1 {f1} (updated {st['updated']})"
        )


COMMANDS = {"install": install, "ensure": ensure, "guard": guard, "stop": stop,
            "remove": remove, "status": status}  # fmt: skip

if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("autostart is for Windows; elsewhere run scripts/supervise.py under your own "
                 "service manager")  # fmt: skip
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
