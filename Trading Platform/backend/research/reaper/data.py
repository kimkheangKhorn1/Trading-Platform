"""Exness XAUUSDm bid + ask candles for the Liquidity Reaper research (read-only access to data/market.db)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.engine.bars import Bars
from app.market.repository import MarketStore

DB = Path(__file__).resolve().parents[2] / 'data' / 'market.db'
BID, ASK = 'exness-archive-xauusdm-bid', 'exness-archive-xauusdm-ask'
TFS = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D')
TF_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D': 86400}


def ts(s: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp())


# Research windows. Tuning only ever sees TRAIN (+ VALID for the go / no-go check); TEST is evaluated once at the end.
START = ts('2024-01-01')
TRAIN = (ts('2024-01-01'), ts('2025-07-01'))
VALID = (ts('2025-07-01'), ts('2026-01-01'))
TEST = (ts('2026-01-01'), ts('2026-10-15'))


@dataclass
class Market:
    tf: str
    bid: Bars
    ask: Bars  # aligned to bid (same open times)
    m1_bid: Bars
    m1_ask: Bars
    first: int  # index of the first bar inside the research period (earlier bars are warm-up)


@lru_cache(maxsize=1)
def store() -> MarketStore:
    return MarketStore(DB)


def _load(feed: str, tf: str, start: int) -> Bars:
    st = store()
    s = st.get_series(feed, tf)
    return st.load_bars(s.id, start=start, complete_only=True)


def _align(bid: Bars, ask: Bars) -> tuple[Bars, Bars]:
    common, ib, ia = np.intersect1d(bid.time, ask.time, return_indices=True)
    take = lambda b, ix: Bars(b.time[ix], b.open[ix], b.high[ix], b.low[ix], b.close[ix], b.volume[ix], b.complete[ix])
    return take(bid, ib), take(ask, ia)


@lru_cache(maxsize=None)
def market(tf: str, warmup_bars: int = 3000) -> Market:
    """Bars of ``tf`` from ``warmup_bars`` before 2024-01-01 to the end of the archive, plus the 1-minute candles
    used to resolve the order of stop-loss and take-profit inside a bar."""
    sec = TF_SECONDS[tf]
    # trading days are ~5/7 of the calendar: ask for enough calendar time to hold the warm-up bars
    start = START - int(warmup_bars * sec * 1.6) - 86400 * 7
    bid, ask = _align(_load(BID, tf, start), _load(ASK, tf, start))
    m1b, m1a = _align(_load(BID, 'M1', START - 86400), _load(ASK, 'M1', START - 86400))
    first = int(np.searchsorted(bid.time, START))
    return Market(tf, bid, ask, m1b, m1a, first)
