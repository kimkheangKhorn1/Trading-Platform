"""Glue between the candle store, the OANDA sync service and the engine's data needs."""
from __future__ import annotations

import threading
import time

from ..config import Settings
from ..engine.bars import Bars, concat_bars
from .oanda import OandaClient
from .repository import Feed, MarketStore
from .resample import resample
from .sync import OandaSync
from .timeframes import REFERENCE_TIMEFRAMES, TIMEFRAMES, bucket_start


def make_oanda_sync(settings: Settings, store: MarketStore) -> OandaSync | None:
    if not settings.oanda_enabled:
        return None
    client = OandaClient(settings.oanda_base_url, settings.oanda_token or "",
                         max_requests_per_second=settings.oanda_max_requests_per_second)
    return OandaSync(store, client, price=settings.oanda_price_component, account_id=settings.oanda_account_id)


_last_sync: dict[tuple[str, str], float] = {}
_last_sync_lock = threading.Lock()


class StoreHtfProvider:
    """Serves other-timeframe candles of the same feed from the database (syncing OANDA if needed).

    ``sync_interval`` throttles provider requests for chart refreshes (0 = always sync, used by
    backtests so they never run on stale higher-timeframe data)."""

    def __init__(self, store: MarketStore, feed: Feed, *, complete_only: bool = False,
                 sync: OandaSync | None = None, sync_interval: float = 0.0):
        self.store = store
        self.feed = feed
        self.complete_only = complete_only
        self.sync = sync
        self.sync_interval = sync_interval

    def load(self, timeframe: str, start: int, end: int, warmup_bars: int) -> Bars:
        if timeframe in REFERENCE_TIMEFRAMES:
            return self._load_calendar(REFERENCE_TIMEFRAMES[timeframe], start, end, warmup_bars)
        if self.sync is not None and self.feed.provider == "oanda":
            key = (self.feed.id, timeframe)
            now = time.monotonic()
            with _last_sync_lock:
                due = now - _last_sync.get(key, -1e18) >= self.sync_interval
                if due:
                    _last_sync[key] = now
            if due:
                self.sync.sync_forward(self.feed, timeframe)
            self.sync.backfill(self.feed, timeframe, before=start, min_bars=warmup_bars)
        series = self.store.get_series(self.feed.id, timeframe)
        if series is None:
            return Bars.empty()
        pre = self.store.load_bars(series.id, before=start, limit=warmup_bars, complete_only=self.complete_only)
        main = self.store.load_bars(series.id, start=start, end=end, complete_only=self.complete_only)
        return concat_bars(pre, main)

    def _load_calendar(self, tf, start: int, end: int, warmup_bars: int) -> Bars:
        """Weekly/monthly candles built from the feed's daily candles (same session alignment)."""
        align = self.feed.alignment
        daily = self.load("D", bucket_start(start, tf, align), end, warmup_bars)
        out = resample(daily, TIMEFRAMES["D"], tf, align)
        # A leading period whose first daily candle comes later than market closures allow (a Monday
        # holiday for a week; weekend plus 1 January for a month) is cut off by the start of the daily
        # history: its high/low would not be the period's, so it is dropped instead of shown.
        allowed = (86400 if tf.code == "W" else 3 * 86400) + 2 * 3600
        if len(out) and int(daily.time[0]) - int(out.time[0]) > allowed:
            out = out.slice(1)
        return out
