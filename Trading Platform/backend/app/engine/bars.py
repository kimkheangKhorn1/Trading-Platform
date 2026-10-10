"""Columnar OHLCV container used by the data layer, the indicator engine and the backtester."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Bars:
    time: np.ndarray  # int64, candle open time (UTC unix seconds), strictly increasing
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    complete: np.ndarray  # bool; False only for a still-forming (live) candle

    def __len__(self) -> int:
        return int(self.time.shape[0])

    @staticmethod
    def empty() -> "Bars":
        f = np.empty(0, dtype=np.float64)
        return Bars(np.empty(0, dtype=np.int64), f, f.copy(), f.copy(), f.copy(), f.copy(), np.empty(0, dtype=bool))

    @staticmethod
    def from_rows(rows) -> "Bars":
        """Build from an iterable of (t, o, h, l, c, v, complete) tuples (already time-sorted)."""
        arr = np.asarray(rows, dtype=np.float64)
        if arr.size == 0:
            return Bars.empty()
        return Bars(
            time=arr[:, 0].astype(np.int64),
            open=arr[:, 1].copy(),
            high=arr[:, 2].copy(),
            low=arr[:, 3].copy(),
            close=arr[:, 4].copy(),
            volume=arr[:, 5].copy(),
            complete=arr[:, 6].astype(bool),
        )

    @staticmethod
    def from_arrays(time, open, high, low, close, volume=None, complete=None) -> "Bars":
        t = np.asarray(time, dtype=np.int64)
        n = t.shape[0]
        return Bars(
            time=t,
            open=np.asarray(open, dtype=np.float64),
            high=np.asarray(high, dtype=np.float64),
            low=np.asarray(low, dtype=np.float64),
            close=np.asarray(close, dtype=np.float64),
            volume=np.zeros(n) if volume is None else np.asarray(volume, dtype=np.float64),
            complete=np.ones(n, dtype=bool) if complete is None else np.asarray(complete, dtype=bool),
        )

    def slice(self, start: int, stop: int | None = None) -> "Bars":
        s = slice(start, stop)
        return Bars(self.time[s], self.open[s], self.high[s], self.low[s], self.close[s], self.volume[s], self.complete[s])

    def index_at_or_after(self, ts: int) -> int:
        return int(np.searchsorted(self.time, ts, side="left"))

    def index_after(self, ts: int) -> int:
        return int(np.searchsorted(self.time, ts, side="right"))

    def to_json_rows(self) -> list[list]:
        """Compact rows for the API: [t, o, h, l, c, v, complete(0/1)]."""
        return [
            [int(t), float(o), float(h), float(l), float(c), float(v), int(k)]
            for t, o, h, l, c, v, k in zip(
                self.time, self.open, self.high, self.low, self.close, self.volume, self.complete
            )
        ]


def concat_bars(*parts: Bars) -> Bars:
    parts = tuple(p for p in parts if len(p))
    if not parts:
        return Bars.empty()
    if len(parts) == 1:
        return parts[0]
    out = Bars(*(np.concatenate([getattr(p, f) for p in parts]) for f in
                 ("time", "open", "high", "low", "close", "volume", "complete")))
    if np.any(np.diff(out.time) <= 0):
        raise ValueError("concatenated bars are not strictly increasing in time")
    return out
