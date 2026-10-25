"""SQLite ledger: positions, funding payments, events (alerts + "why" log), key-value settings.

The bot is the only writer; the API reads. One connection guarded by a lock so the bot thread and
FastAPI request threads can share it.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import threading
import time

from packages.domain.types import FundingPayment, PositionRecord

LEVELS = ("info", "warn", "action", "critical")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    opened_ts REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS funding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pos_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    usd REAL NOT NULL,
    UNIQUE (pos_id, ts, symbol)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _record(row_data: str, row_id: int) -> PositionRecord:
    d = json.loads(row_data)
    d["id"] = row_id
    return PositionRecord(**d)


class Ledger:
    def __init__(self, path: str = ":memory:", now=time.time):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._now = now

    # ---- positions ----
    def open_position(self, rec: PositionRecord) -> int:
        data = dataclasses.asdict(rec)
        data.pop("id")
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO positions (state, opened_ts, data) VALUES ('open', ?, ?)",
                                   (rec.opened_ts, json.dumps(data)))
            rec.id = cur.lastrowid
            return rec.id

    def current(self) -> PositionRecord | None:
        with self._lock:
            row = self._db.execute("SELECT id, data FROM positions WHERE state='open' ORDER BY id DESC LIMIT 1").fetchone()
        return _record(row[1], row[0]) if row else None

    def get(self, pos_id: int) -> PositionRecord | None:
        with self._lock:
            row = self._db.execute("SELECT id, data FROM positions WHERE id=?", (pos_id,)).fetchone()
        return _record(row[1], row[0]) if row else None

    def update(self, rec: PositionRecord) -> None:
        data = dataclasses.asdict(rec)
        data.pop("id")
        with self._lock, self._db:
            self._db.execute("UPDATE positions SET state=?, data=? WHERE id=?", (rec.state, json.dumps(data), rec.id))

    def close(self, pos_id: int, reason: str, pnl_usd: float | None, ts: float | None = None) -> None:
        rec = self.get(pos_id)
        if rec is None:
            raise KeyError(pos_id)
        rec.state, rec.close_reason, rec.realized_pnl_usd = "closed", reason, pnl_usd
        rec.closed_ts = ts if ts is not None else self._now()
        self.update(rec)

    def positions(self, limit: int = 50) -> list[PositionRecord]:
        with self._lock:
            rows = self._db.execute("SELECT id, data FROM positions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_record(d, i) for i, d in rows]

    # ---- funding ----
    def add_funding(self, pos_id: int, p: FundingPayment) -> bool:
        """Returns False if this payment was already recorded (idempotent)."""
        with self._lock, self._db:
            cur = self._db.execute("INSERT OR IGNORE INTO funding (pos_id, ts, symbol, usd) VALUES (?, ?, ?, ?)",
                                   (pos_id, p.ts, p.symbol, p.usd))
            return cur.rowcount == 1

    def funding_total(self, pos_id: int) -> float:
        with self._lock:
            return self._db.execute("SELECT COALESCE(SUM(usd), 0) FROM funding WHERE pos_id=?", (pos_id,)).fetchone()[0]

    def funding_payments(self, pos_id: int) -> list[FundingPayment]:
        with self._lock:
            rows = self._db.execute("SELECT ts, symbol, usd FROM funding WHERE pos_id=? ORDER BY ts", (pos_id,)).fetchall()
        return [FundingPayment(ts=t, symbol=s, usd=u) for t, s, u in rows]

    # ---- events ----
    def event(self, level: str, kind: str, message: str, data: dict | None = None) -> int:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO events (ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
                                   (self._now(), level, kind, message, json.dumps(data or {}, default=str)))
            return cur.lastrowid

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, level, kind, message, data FROM events ORDER BY id DESC LIMIT ?",
                                    (limit,)).fetchall()
        return [{"id": i, "ts": t, "level": lv, "kind": k, "message": m, "data": json.loads(d)}
                for i, t, lv, k, m, d in rows]

    # ---- settings / bot state ----
    def kv_get(self, k: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def kv_set(self, k: str, v) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                             (k, json.dumps(v, default=str)))
