"""Native Exness bid/ask history plus provisional tick updates, isolated from other feeds."""
from __future__ import annotations

import hashlib
import threading
from collections import defaultdict

from .exness import ExnessClient, ExnessError, numeric, timestamp
from .repository import Feed, MarketStore
from .timeframes import bucket_start, get_timeframe


class ExnessSync:
    provider = "exness"

    def __init__(self, store: MarketStore, client: ExnessClient):
        self.store, self.client = store, client
        self._locks = defaultdict(threading.RLock)
        self._guard = threading.Lock()
        self._available: set[str] | None = None
        self._last_tick: dict[str, float] = {}

    def _lock(self, feed_id: str, tf: str):
        with self._guard:
            return self._locks[(feed_id, tf)]

    def _feed_id(self, instrument: str) -> str:
        account = hashlib.sha256(self.client.account_id.encode()).hexdigest()[:12]
        return f"exness-{account}-{instrument.lower()}-{self.client.price}"

    def owns(self, feed: Feed) -> bool:
        return feed.provider == "exness" and feed.instrument is not None and feed.id == self._feed_id(feed.instrument)

    def ensure_feed(self, instrument: str) -> Feed:
        if self._available is None:
            self.client.configure_limits()
            self._available = set(self.client.instruments())
        if instrument not in self._available:
            raise ExnessError("Requested instrument is not available to this API account; check EXNESS_INSTRUMENTS.", "MARKET_INSTRUMENT_NOT_FOUND")
        details = self.client.conditions(instrument)
        digits = details.get("point_digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 10:
            raise ExnessError("Instrument conditions did not supply valid point_digits.", "INVALID_PAYLOAD")
        feed_id = self._feed_id(instrument)
        existing = self.store.get_feed(feed_id)
        if existing and existing.alignment != self.client.alignment:
            raise ExnessError("This stored feed uses another alignment. Choose a separate feed before changing session boundaries.", "SESSION_MISMATCH")
        meta = {"description": details.get("description", instrument),
                "live_candle_source": "sampled ticks, provisional until reconciled with native history",
                "volume_source": "native candle history; ticks contain no volume"}
        return self.store.ensure_feed(feed_id, provider="exness", symbol=instrument, instrument=instrument,
                                      price_type=self.client.price, display_name=f"{instrument} · Exness ({self.client.price})",
                                      precision=digits, alignment=self.client.alignment, meta=meta)

    def _series(self, feed: Feed, tf: str):
        get_timeframe(tf, reference=True)
        if not self.owns(feed):
            raise ExnessError("Cannot write Exness prices into another account/provider/price-side feed.", "LOCAL_FEED")
        return self.store.ensure_series(feed.id, tf, "native")

    def _save_changed(self, series_id: int, rows: list[tuple]) -> list[tuple]:
        if not rows:
            return []
        existing = {int(r[0]): tuple(r[1:]) for r in self.store.conn.execute(
            "SELECT t,o,h,l,c,v,complete FROM candles WHERE series_id=? AND t>=? AND t<=?",
            (series_id, rows[0][0], rows[-1][0]))}
        changed = [r for r in rows if existing.get(r[0]) != tuple(r[1:])]
        if changed:
            self.store.upsert_candles(series_id, changed)
        return changed

    def sync_forward(self, feed: Feed, tf: str, *, max_retries: int | None = None) -> list[tuple]:
        # One HTTP attempt per page; live outages are handled by the hub, not hidden client retries.
        with self._lock(feed.id, tf):
            series = self._series(feed, tf)
            last = self.store.latest_time(series.id)
            limit = self.client.max_candles
            if limit is None:
                self.client.configure_limits()
                limit = self.client.max_candles
            if last is None:
                return self._save_changed(series.id, self.client.candles(feed.instrument, tf, start=int(self.client.clock()), count=-limit))
            # Re-read the previous candle too: at rollover, REST is the authority for final OHLC/volume.
            previous = self.store.load_bars(series.id, limit=2)
            cursor = int(previous.time[0])
            changed = []
            for _ in range(4096):
                rows = self.client.candles(feed.instrument, tf, start=cursor, count=limit)
                rows = [r for r in rows if r[0] >= cursor]
                changed.extend(self._save_changed(series.id, rows))
                if not rows or len(rows) < limit or rows[-1][0] <= cursor:
                    return changed
                cursor = rows[-1][0] + 1  # avoid inclusive duplicate on the next page
            raise ExnessError("History catch-up exceeded its page budget; continue in a later request.", "LOCAL_PAGE_LIMIT")

    def backfill(self, feed: Feed, tf: str, *, before=None, min_bars=2000, until=None, progress=None) -> int:
        added = 0
        with self._lock(feed.id, tf):
            series = self._series(feed, tf)
            if self.store.latest_time(series.id) is None:
                added += len(self.sync_forward(feed, tf))
            for _ in range(4096):
                series = self.store.get_series(feed.id, tf)
                oldest = self.store.oldest_time(series.id)
                if oldest is None or series.oldest_reached:
                    return added
                if until is not None and oldest <= until:
                    return added
                if until is None and ((before is not None and self.store.count_before(series.id, before) >= min_bars)
                                      or (before is None and added >= min_bars)):
                    return added
                rows = self.client.candles(feed.instrument, tf, start=oldest, count=-self.client.max_candles)
                rows = [r for r in rows if r[0] < oldest]
                if not rows:
                    self.store.set_oldest_reached(series.id, True)
                    return added
                added += self.store.upsert_candles(series.id, rows)
                if progress:
                    progress(added, rows[0][0])
            raise ExnessError("History download exceeded its page budget; resume it later.", "LOCAL_PAGE_LIMIT")

    def apply_tick(self, feed: Feed, tf: str, tick: dict) -> list[tuple]:
        if tick.get("instrument") != feed.instrument:
            return []
        t = timestamp(tick["timestamp"])
        bid, ask = numeric(tick["bid"]), numeric(tick["ask"])
        if not 0 < bid <= ask:
            raise ExnessError("Invalid Exness bid/ask tick.", "INVALID_PAYLOAD")
        # Per-series ordering lets the same tick update different subscribed timeframes.
        key = f"{feed.id}:{tf}"
        with self._lock(feed.id, tf):
            if t < self._last_tick.get(key, -1):
                return []
            self._last_tick[key] = t
            series = self._series(feed, tf)
            start = bucket_start(int(t), get_timeframe(tf), feed.alignment)
            last = self.store.load_bars(series.id, limit=1)
            reconciled = []
            if len(last) and start > int(last.time[-1]) and not last.complete[-1]:
                reconciled = self.sync_forward(feed, tf)
                last = self.store.load_bars(series.id, limit=1)
                if len(last) and start > int(last.time[-1]) and not last.complete[-1]:
                    return reconciled  # do not leave an incomplete bar in the middle of the series
            if len(last) and start < int(last.time[-1]):
                return []
            price = bid if feed.price_type == "bid" else ask
            if len(last) and start == int(last.time[-1]):
                if last.complete[-1]:
                    return []  # never rewrite a confirmed bar from a delayed tick
                row = (start, float(last.open[-1]), max(float(last.high[-1]), price),
                       min(float(last.low[-1]), price), price, float(last.volume[-1]), 0)
            else:
                # Tentative first observed tick, not a reconstructed broker open or true tick volume.
                row = (start, price, price, price, price, 0., 0)
            return reconciled + self._save_changed(series.id, [row])
