"""Deterministic SYNTHETIC test data. Used only by automated tests - never shown as market data."""
from __future__ import annotations

import datetime as dt

import numpy as np

from app.engine.bars import Bars


def ts(s: str) -> int:
    """'2026-03-02 14:05' (UTC) -> unix seconds."""
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp())


def random_walk_bars(n: int, *, start: str = "2026-03-02 00:00", step: int = 300, seed: int = 7,
                     price: float = 2000.0, skip_weekends: bool = True) -> Bars:
    rng = np.random.default_rng(seed)
    times = []
    t = ts(start)
    while len(times) < n:
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        # crude FX week: closed from Friday 21:00 UTC to Sunday 22:00 UTC
        closed = skip_weekends and (d.weekday() == 5 or (d.weekday() == 4 and d.hour >= 21)
                                    or (d.weekday() == 6 and d.hour < 22))
        if not closed:
            times.append(t)
        t += step
    close = price + np.cumsum(rng.normal(0, 1.2, n))
    open_ = np.r_[price, close[:-1]] + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + rng.random(n) * 1.5
    low = np.minimum(open_, close) - rng.random(n) * 1.5
    vol = rng.integers(10, 500, n).astype(float)
    return Bars.from_arrays(np.array(times), open_, high, low, close, vol)


def bars_from_ohlc(rows: list[tuple[float, float, float, float]], *, start: str = "2026-03-02 00:00",
                   step: int = 300) -> Bars:
    t0 = ts(start)
    arr = np.asarray(rows, dtype=float)
    return Bars.from_arrays(t0 + np.arange(len(rows)) * step, arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
