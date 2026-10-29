"""What a unit's safety team keeps: every checked recording, each reviewer's verdict on each
alert, and the drafted occurrence reports, in one SQLite file next to the recordings' audio.

Reviewer verdicts close the loop: the share of alerts confirmed is the precision Readback
actually achieves on that unit's radio, which no offline benchmark can tell them.

An upload waits under uploads/ until its check finishes, so a restart puts unfinished checks
back in the queue instead of losing them.

Each recording names its unit (e.g. "LKPR TWR") and frequency (e.g. "118.100"), so one store
serves several positions, and listings, totals and the alert register can be cut by either.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

VERDICTS = ("open", "confirmed", "false_alarm")
STATUSES = ("queued", "running", "done", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    parser TEXT NOT NULL,
    callsigns TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    duration REAL,
    summary TEXT,
    log TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    analysis_id TEXT NOT NULL,
    alert_index INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    updated REAL NOT NULL,
    PRIMARY KEY (analysis_id, alert_index)
);
CREATE TABLE IF NOT EXISTS reports (
    analysis_id TEXT NOT NULL,
    alert_index INTEGER NOT NULL,
    report TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY (analysis_id, alert_index)
);
"""


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.audio = root / "audio"
        self.audio.mkdir(parents=True, exist_ok=True)
        self.uploads = root / "uploads"
        self.uploads.mkdir(exist_ok=True)
        self._lock = threading.Lock()  # one connection, shared by the job threads
        self._db = sqlite3.connect(root / "readback.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(analyses)")}
        for col in ("unit", "frequency"):  # stores made before units existed
            if col not in have:
                self._db.execute(f"ALTER TABLE analyses ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        self._db.execute("CREATE INDEX IF NOT EXISTS analyses_unit ON analyses (unit, frequency)")

    def _run(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    # ---------- analyses ----------
    def create(
        self, name: str, parser: str, callsigns: str = "", unit: str = "", frequency: str = ""
    ) -> str:
        aid = uuid.uuid4().hex[:12]
        self._run(
            "INSERT INTO analyses (id, name, created, status, parser, callsigns, unit, frequency) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
            (aid, name, time.time(), parser, callsigns, unit, frequency),
        )
        return aid

    def update(self, aid: str, **fields) -> None:
        for key in ("log", "summary"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(fields["status"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE analyses SET {cols} WHERE id = ?", (*fields.values(), aid))

    def get(self, aid: str) -> dict | None:
        rows = self._run("SELECT * FROM analyses WHERE id = ?", (aid,))
        if not rows:
            return None
        out = self._row(rows[0])
        out["log"] = json.loads(rows[0]["log"]) if rows[0]["log"] else None
        return out

    @staticmethod
    def _where(unit: str | None, frequency: str | None, prefix: str = "") -> tuple[str, tuple]:
        """SQL conditions for a unit and/or frequency filter (None: any)."""
        conds, args = [], []
        for col, value in (("unit", unit), ("frequency", frequency)):
            if value is not None:
                conds.append(f"{prefix}{col} = ?")
                args.append(value)
        return " AND ".join(conds) or "1", tuple(args)

    def list(
        self, limit: int = 500, unit: str | None = None, frequency: str | None = None
    ) -> list[dict]:
        where, args = self._where(unit, frequency)
        rows = self._run(
            f"SELECT * FROM analyses WHERE {where} ORDER BY created DESC LIMIT ?", (*args, limit)
        )
        verdicts = self._verdict_counts()
        return [dict(self._row(r), verdicts=verdicts.get(r["id"], {})) for r in rows]

    def positions(self) -> list[dict]:
        """Every unit and frequency in the store, with how many recordings each has."""
        rows = self._run(
            "SELECT unit, frequency, COUNT(*) AS recordings FROM analyses "
            "GROUP BY unit, frequency ORDER BY unit, frequency"
        )
        return [dict(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        out = {k: row[k] for k in row.keys() if k != "log"}
        out["summary"] = json.loads(row["summary"]) if row["summary"] else None
        return out

    def upload(self, aid: str) -> Path:
        """Where a recording's upload waits until its check finishes."""
        return self.uploads / f"{aid}.upload"

    def claim(self, aid: str) -> bool:
        """Take a queued job for this worker; False if another worker already has it."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE analyses SET status = 'running' WHERE id = ? AND status = 'queued'", (aid,)
            )
            return cur.rowcount == 1

    def interrupted(self) -> list[str]:
        """Jobs a restart cut short. Those whose upload is still here go back in the queue, oldest
        first, and their ids are returned; the rest are marked failed so nobody waits on them."""
        rows = self._run(
            "SELECT id FROM analyses WHERE status IN ('queued', 'running') ORDER BY created"
        )
        again = [r["id"] for r in rows if self.upload(r["id"]).exists()]
        for aid in again:
            self._run(
                "UPDATE analyses SET status = 'queued', progress = 0, error = NULL WHERE id = ?",
                (aid,),
            )
        self._run(
            "UPDATE analyses SET status = 'failed', error = 'interrupted by a server restart; "
            "upload the recording again' WHERE status IN ('queued', 'running') AND id NOT IN "
            f"({', '.join('?' * len(again))})",
            tuple(again),
        )
        return again

    def delete(self, aid: str) -> None:
        for table, col in (("reviews", "analysis_id"), ("reports", "analysis_id"),
                           ("analyses", "id")):  # fmt: skip
            self._run(f"DELETE FROM {table} WHERE {col} = ?", (aid,))
        (self.audio / f"{aid}.wav").unlink(missing_ok=True)
        self.upload(aid).unlink(missing_ok=True)

    def purge(self, older_than_days: float) -> list[str]:
        """Delete everything about recordings older than the retention period."""
        cutoff = time.time() - older_than_days * 86400
        old = [r["id"] for r in self._run("SELECT id FROM analyses WHERE created < ?", (cutoff,))]
        for aid in old:
            self.delete(aid)
        return old

    # ---------- reviews ----------
    def review(
        self, aid: str, index: int, verdict: str, note: str = "", reviewer: str = ""
    ) -> dict:
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        self._run(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (analysis_id, alert_index) "
            "DO UPDATE SET verdict = excluded.verdict, note = excluded.note, "
            "reviewer = excluded.reviewer, updated = excluded.updated",
            (aid, index, verdict, note, reviewer, time.time()),
        )
        return self.reviews(aid)[index]

    def reviews(self, aid: str) -> dict[int, dict]:
        rows = self._run("SELECT * FROM reviews WHERE analysis_id = ?", (aid,))
        return {
            r["alert_index"]: {k: r[k] for k in ("verdict", "note", "reviewer", "updated")}
            for r in rows
        }

    def _verdict_counts(self) -> dict[str, dict[str, int]]:
        rows = self._run(
            "SELECT analysis_id, verdict, COUNT(*) AS n FROM reviews GROUP BY analysis_id, verdict"
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["analysis_id"], {})[r["verdict"]] = r["n"]
        return out

    def stats(self, unit: str | None = None, frequency: str | None = None) -> dict:
        """Totals across recordings (all, or one unit/frequency), and the precision reviewers
        have measured on them."""
        where, args = self._where(unit, frequency)
        done = self._run(f"SELECT summary FROM analyses WHERE status = 'done' AND {where}", args)
        alerts = sum(json.loads(r["summary"])["alerts"] for r in done if r["summary"])
        seconds = sum(json.loads(r["summary"])["duration"] for r in done if r["summary"])
        counts = {v: 0 for v in VERDICTS}
        where, args = self._where(unit, frequency, "a.")
        for r in self._run(
            "SELECT verdict, COUNT(*) AS n FROM reviews r JOIN analyses a ON a.id = r.analysis_id "
            f"WHERE {where} GROUP BY verdict",
            args,
        ):
            counts[r["verdict"]] = r["n"]
        judged = counts["confirmed"] + counts["false_alarm"]
        return {
            "recordings": len(done),
            "hours": round(seconds / 3600, 2),
            "alerts": alerts,
            "confirmed": counts["confirmed"],
            "false_alarm": counts["false_alarm"],
            "unreviewed": alerts - judged,
            "precision": round(counts["confirmed"] / judged, 3) if judged else None,
        }

    # ---------- reports ----------
    def report(self, aid: str, index: int) -> dict | None:
        rows = self._run(
            "SELECT report FROM reports WHERE analysis_id = ? AND alert_index = ?", (aid, index)
        )
        return json.loads(rows[0]["report"]) if rows else None

    def save_report(self, aid: str, index: int, report: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?)",
            (aid, index, json.dumps(report), time.time()),
        )

    def reported(self, aid: str) -> set[int]:
        rows = self._run("SELECT alert_index FROM reports WHERE analysis_id = ?", (aid,))
        return {r["alert_index"] for r in rows}
