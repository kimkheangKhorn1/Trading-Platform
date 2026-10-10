"""SQLite storage (WAL mode). One connection per thread; schema created on first use."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id            TEXT PRIMARY KEY,          -- e.g. 'oanda-xau_usd-mid', 'csv-xauusd-dukascopy'
    provider      TEXT NOT NULL,             -- 'oanda' | 'csv'
    symbol        TEXT NOT NULL,             -- display symbol, e.g. 'XAUUSD'
    instrument    TEXT,                      -- provider instrument, e.g. 'XAU_USD'
    price_type    TEXT NOT NULL,             -- 'mid' | 'bid' | 'ask'
    display_name  TEXT NOT NULL,
    precision     INTEGER NOT NULL DEFAULT 3,
    alignment     TEXT NOT NULL DEFAULT 'ny17',
    created_at    INTEGER NOT NULL,
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS series (
    id              INTEGER PRIMARY KEY,
    feed_id         TEXT NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    timeframe       TEXT NOT NULL,
    origin          TEXT NOT NULL,          -- 'native' (from provider/file) | 'resampled'
    oldest_reached  INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (feed_id, timeframe)
);
CREATE TABLE IF NOT EXISTS candles (
    series_id  INTEGER NOT NULL,
    t          INTEGER NOT NULL,             -- open time, UTC unix seconds
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
    v REAL NOT NULL DEFAULT 0,
    complete   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (series_id, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS backtests (
    id          TEXT PRIMARY KEY,
    created_at  INTEGER NOT NULL,
    status      TEXT NOT NULL,
    request     TEXT NOT NULL,
    result      BLOB,
    error       TEXT
);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialised: set[str] = set()


def connect(path: Path) -> sqlite3.Connection:
    """Return this thread's connection to ``path`` (created and initialised on demand)."""
    key = str(path)
    conns: dict[str, sqlite3.Connection] = getattr(_local, "conns", None) or {}
    _local.conns = conns
    conn = conns.get(key)
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, timeout=30, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        with _init_lock:
            if key not in _initialised:
                conn.executescript(SCHEMA)
                conn.commit()
                _initialised.add(key)
        conns[key] = conn
    return conn


def close_thread_connections() -> None:
    conns: dict[str, sqlite3.Connection] = getattr(_local, "conns", None) or {}
    for conn in conns.values():
        conn.close()
    conns.clear()
