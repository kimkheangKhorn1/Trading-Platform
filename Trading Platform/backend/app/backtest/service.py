"""Backtest requests: validation, data loading, execution and background jobs."""
from __future__ import annotations

import json
import threading
import time
import uuid
import zlib
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

from ..config import Settings, load_settings
from ..engine.bars import concat_bars
from ..engine.inputs import InputError
from ..engine.registry import get_script
from ..market.providers import StoreHtfProvider, make_market_sync
from ..market.repository import MarketStore
from ..market.timeframes import get_timeframe
from .config import ExecutionConfig
from .engine import BacktestError, run_backtest


@dataclass(frozen=True)
class BacktestRequest:
    feed_id: str
    timeframe: str
    start: int
    end: int
    script_id: str
    inputs: dict
    strategies: list[str] | None
    execution: dict
    warmup_bars: int | None = None

    @staticmethod
    def from_dict(d: dict) -> "BacktestRequest":
        try:
            req = BacktestRequest(
                feed_id=str(d["feed_id"]),
                timeframe=str(d["timeframe"]),
                start=int(d["start"]),
                end=int(d["end"]),
                script_id=str(d["script_id"]),
                inputs=dict(d.get("inputs") or {}),
                strategies=list(d["strategies"]) if d.get("strategies") else None,
                execution=dict(d.get("execution") or {}),
                warmup_bars=int(d["warmup_bars"]) if d.get("warmup_bars") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BacktestError(f"invalid backtest request: {exc}") from exc
        return req

    def validate(self) -> None:
        get_timeframe(self.timeframe)
        if self.end < self.start:
            raise BacktestError("end date is before start date")
        try:
            cls = get_script(self.script_id)
        except KeyError as exc:
            raise BacktestError(str(exc)) from exc
        try:
            cls(self.inputs)
        except InputError as exc:
            raise BacktestError(str(exc)) from exc
        try:
            ExecutionConfig.from_dict(self.execution)
        except (TypeError, ValueError) as exc:
            raise BacktestError(str(exc)) from exc
        if self.warmup_bars is not None and not (0 <= self.warmup_bars <= 100_000):
            raise BacktestError("warm-up bars must be between 0 and 100000")

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def execute_backtest(request: dict, store: MarketStore, settings: Settings) -> dict:
    req = BacktestRequest.from_dict(request)
    req.validate()
    feed = store.get_feed(req.feed_id)
    if feed is None:
        raise BacktestError(f"unknown data feed {req.feed_id!r}")
    tf = get_timeframe(req.timeframe)
    script_cls = get_script(req.script_id)
    script = script_cls(req.inputs)
    warmup = req.warmup_bars if req.warmup_bars is not None else script.required_warmup()
    cfg = ExecutionConfig.from_dict(req.execution)
    sync = make_market_sync(settings, store) if feed.provider == settings.market_data_provider else None
    if sync is not None and not sync.owns(feed):
        sync.client.close()
        sync = None
    if sync is not None:
        sync.sync_forward(feed, tf.code)
        sync.backfill(feed, tf.code, until=req.start)
        sync.backfill(feed, tf.code, before=req.start, min_bars=warmup)
    series = store.get_series(feed.id, tf.code)
    if series is None:
        raise BacktestError(f"no {tf.label} candles stored for {feed.display_name}")
    stats = store.series_stats(series.id)
    pre = store.load_bars(series.id, before=req.start, limit=warmup, complete_only=True) if warmup else None
    window = store.load_bars(series.id, start=req.start, end=req.end, complete_only=True)
    if len(window) == 0:
        raise BacktestError(
            f"no complete {tf.label} candles between the selected dates; stored data covers "
            f"{_fmt(stats['first'])} – {_fmt(stats['last'])}"
        )
    bars = concat_bars(pre, window) if pre is not None else window
    provider = StoreHtfProvider(store, feed, complete_only=True, sync=sync)
    result = run_backtest(
        bars, script_cls=script_cls, inputs=req.inputs, timeframe=tf.code, start=req.start, end=req.end,
        execution=cfg, strategies=req.strategies, htf_provider=provider, alignment=feed.alignment,
        precision=feed.precision,
    )
    if stats["first"] is not None and req.start < stats["first"]:
        result["warnings"].append(f"stored data begins {_fmt(stats['first'])}, after the requested start date")
    if pre is not None and len(pre) < warmup:
        result["warnings"].append(f"requested {warmup} warm-up bars but only {len(pre)} exist before the start")
    result["request"] = req.to_dict()
    result["data"] = {"feed": feed.to_dict(), "timeframe": tf.code, "stored_first": stats["first"],
                      "stored_last": stats["last"], "warmup_requested": warmup}
    return result


def _fmt(ts: int | None) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _worker(request: dict, db_path: str) -> dict:
    """Entry point executed in a worker process."""
    settings = load_settings()
    return execute_backtest(request, MarketStore(Path(db_path)), settings)


class BacktestJobs:
    """Runs backtests in worker processes so the API stays responsive during heavy runs."""

    def __init__(self, store: MarketStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        self._running: dict[str, Future] = {}

    def _executor(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=max(1, self.settings.backtest_workers),
                                                 mp_context=get_context("spawn"))
            return self._pool

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, request: dict) -> str:
        req = BacktestRequest.from_dict(request)
        req.validate()  # fail fast with a 400 instead of a failed job
        job_id = uuid.uuid4().hex[:12]
        c = self.store.conn
        c.execute("INSERT INTO backtests (id, created_at, status, request) VALUES (?,?,?,?)",
                  (job_id, int(time.time()), "running", json.dumps(req.to_dict())))
        c.commit()
        fut = self._executor().submit(_worker, req.to_dict(), str(self.store.db_path))
        self._running[job_id] = fut
        fut.add_done_callback(lambda f, jid=job_id: self._finished(jid, f))
        return job_id

    def _finished(self, job_id: str, fut: Future) -> None:
        c = self.store.conn
        try:
            result = fut.result()
            blob = zlib.compress(json.dumps(result, separators=(",", ":")).encode())
            c.execute("UPDATE backtests SET status='done', result=? WHERE id=?", (blob, job_id))
        except Exception as exc:  # reported to the UI
            c.execute("UPDATE backtests SET status='error', error=? WHERE id=?", (str(exc), job_id))
        c.commit()
        self._running.pop(job_id, None)

    def get(self, job_id: str) -> dict | None:
        row = self.store.conn.execute("SELECT * FROM backtests WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        interrupted = False
        if row["status"] == "running" and job_id not in self._running:
            # Completion commits the final row before removing the future. A poll
            # can read the old row just before that removal; refresh before claiming
            # a restart. Read membership once so completion cannot race a later check.
            row = self.store.conn.execute("SELECT * FROM backtests WHERE id=?", (job_id,)).fetchone()
            interrupted = row["status"] == "running"
        out = {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
               "request": json.loads(row["request"]), "error": row["error"]}
        if interrupted:
            out["status"] = "error"
            out["error"] = "interrupted (the server restarted while this backtest was running)"
        if row["result"] is not None:
            out["result"] = json.loads(zlib.decompress(row["result"]))
        return out

    def list(self, limit: int = 30) -> list[dict]:
        rows = self.store.conn.execute(
            "SELECT id, created_at, status, request, error FROM backtests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "status": r["status"],
                 "request": json.loads(r["request"]), "error": r["error"]} for r in rows]
