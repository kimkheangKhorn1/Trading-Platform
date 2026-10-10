"""Incremental synchronisation of OANDA candles into the local database.

The stored range of every series is kept contiguous: history is only ever extended backwards from
the oldest stored candle and forwards from the newest one, so there are no silent holes besides
genuine market closures (weekends, daily maintenance breaks). When a backward request returns no
older candles the series is flagged ``oldest_reached`` and no further backfill is attempted.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Callable

from .oanda import MAX_CANDLES_PER_REQUEST, OandaClient
from .repository import Feed, MarketStore, SeriesInfo
from .timeframes import get_timeframe

PRICE_NAMES = {"M": "mid", "B": "bid", "A": "ask"}


def oanda_feed_id(instrument: str, price: str) -> str:
    return f"oanda-{instrument.lower()}-{PRICE_NAMES[price]}"


def display_symbol(instrument: str) -> str:
    return instrument.replace("_", "")


class OandaSync:
    def __init__(self, store: MarketStore, client: OandaClient, *, price: str = "M",
                 account_id: str | None = None):
        self.store = store
        self.client = client
        self.price = price
        self.account_id = account_id
        self._locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock(self, feed_id: str, tf: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[(feed_id, tf)]

    def ensure_feed(self, instrument: str) -> Feed:
        feed_id = oanda_feed_id(instrument, self.price)
        existing = self.store.get_feed(feed_id)
        if existing:
            return existing
        precision, meta = 3, {}
        if self.account_id:
            try:
                details = self.client.instrument_details(self.account_id, instrument)
                if details:
                    precision = int(details.get("displayPrecision", precision))
                    meta = {k: details.get(k) for k in ("displayName", "pipLocation", "tradeUnitsPrecision",
                                                        "minimumTradeSize", "marginRate") if k in details}
            except Exception:  # details are optional; candles do not need an account id
                pass
        return self.store.ensure_feed(
            feed_id, provider="oanda", symbol=display_symbol(instrument), instrument=instrument,
            price_type=PRICE_NAMES[self.price],
            display_name=f"{display_symbol(instrument)} · OANDA ({PRICE_NAMES[self.price]})",
            precision=precision, alignment="ny17", meta=meta,
        )

    def _series(self, feed: Feed, tf: str) -> SeriesInfo:
        get_timeframe(tf)
        return self.store.ensure_series(feed.id, tf, "native")

    def sync_forward(self, feed: Feed, tf: str, *, max_retries: int | None = None) -> list[tuple]:
        """Fetch everything newer than the last stored candle (re-fetching that candle, which may have
        been incomplete). Returns the candle rows that were inserted or changed.

        ``max_retries=0`` makes a failed request raise immediately (live polling reports the outage and
        backs off itself instead of blocking inside the client's retry loop)."""
        assert feed.instrument
        with self._lock(feed.id, tf):
            series = self._series(feed, tf)
            last = self.store.latest_time(series.id)
            changed: list[tuple] = []
            if last is None:
                rows = self.client.candles(feed.instrument, tf, price=self.price, count=MAX_CANDLES_PER_REQUEST,
                                           max_retries=max_retries)
                if rows:
                    self.store.upsert_candles(series.id, rows)
                return rows
            cursor = last
            for _ in range(10_000):
                rows = self.client.candles(feed.instrument, tf, price=self.price, start=cursor,
                                           count=MAX_CANDLES_PER_REQUEST, include_first=True,
                                           max_retries=max_retries)
                rows = [r for r in rows if r[0] >= cursor]
                if not rows:
                    break
                existing = {int(t): (o, h, l, c, v, k) for t, o, h, l, c, v, k in
                            self.store.conn.execute(
                                "SELECT t,o,h,l,c,v,complete FROM candles WHERE series_id=? AND t>=? AND t<=?",
                                (series.id, rows[0][0], rows[-1][0]))}
                fresh = [r for r in rows if existing.get(r[0]) != tuple(r[1:])]
                if fresh:
                    self.store.upsert_candles(series.id, fresh)
                    changed.extend(fresh)
                if len(rows) < MAX_CANDLES_PER_REQUEST or rows[-1][0] == cursor:
                    break
                cursor = rows[-1][0]
            return changed

    def backfill(self, feed: Feed, tf: str, *, before: int | None = None, min_bars: int = 2000,
                 until: int | None = None, progress: Callable[[int, int | None], None] | None = None) -> int:
        """Extend history backwards until at least ``min_bars`` candles exist before ``before`` (or the
        oldest stored candle reaches ``until``), or the provider has no older data. Returns the number of
        candles added."""
        assert feed.instrument
        added = 0
        with self._lock(feed.id, tf):
            series = self._series(feed, tf)
            if self.store.latest_time(series.id) is None:
                rows = self.client.candles(feed.instrument, tf, price=self.price, count=MAX_CANDLES_PER_REQUEST)
                if rows:
                    added += self.store.upsert_candles(series.id, rows)
            for _ in range(100_000):
                series = self.store.get_series(feed.id, tf)
                assert series
                oldest = self.store.oldest_time(series.id)
                if oldest is None or series.oldest_reached:
                    break
                if until is not None:
                    if oldest <= until:
                        break
                else:
                    have = self.store.count_before(series.id, before) if before is not None else 0
                    if before is not None and have >= min_bars:
                        break
                    if before is None and added >= min_bars:
                        break
                rows = self.client.candles(feed.instrument, tf, price=self.price, end=oldest,
                                           count=MAX_CANDLES_PER_REQUEST)
                rows = [r for r in rows if r[0] < oldest]
                if not rows:
                    self.store.set_oldest_reached(series.id, True)
                    break
                added += self.store.upsert_candles(series.id, rows)
                if progress:
                    progress(added, rows[0][0])
        return added


class HistoryDownloadJobs:
    """Background downloads of deep history (e.g. M1 back to a given date), with progress."""

    def __init__(self, sync: OandaSync):
        self.sync = sync
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, feed: Feed, tf: str, until: int) -> dict:
        job_id = f"{feed.id}:{tf}:{until}:{int(time.time())}"
        job = {"id": job_id, "feed_id": feed.id, "timeframe": tf, "until": until, "status": "running",
               "added": 0, "oldest": None, "error": None, "started_at": int(time.time())}
        with self._lock:
            self.jobs[job_id] = job

        def progress(added: int, oldest: int | None) -> None:
            job["added"] = added
            job["oldest"] = oldest

        def run() -> None:
            try:
                self.sync.backfill(feed, tf, until=until, progress=progress)
                job["status"] = "done"
            except Exception as exc:  # surfaced to the UI
                job["status"] = "error"
                job["error"] = str(exc)

        threading.Thread(target=run, daemon=True, name=f"history-{job_id}").start()
        return job

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: -j["started_at"])
