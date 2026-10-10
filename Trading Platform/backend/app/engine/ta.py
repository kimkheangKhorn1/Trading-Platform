"""Technical-analysis primitives with Pine ``ta.*`` semantics.

Two flavours are provided and tested against each other:

* streaming classes (``EMA(9).update(x)``) used inside bar-by-bar scripts - their state is plain
  Python so it can be snapshotted/rolled back for the live (unconfirmed) bar exactly like Pine's
  ``var`` rollback;
* vectorised functions over whole numpy arrays (``ema(arr, 9)``) for analytics, higher-timeframe
  expressions and tests. Every vectorised function is *causal*: element ``i`` depends only on
  elements ``0..i``.

Seeding/warm-up conventions must match TradingView bar-for-bar for converted indicators. They follow
the reference implementations published in the Pine v5 manual (``ta.rma``/``ta.sma`` seed with an
SMA; ``ta.atr`` = ``ta.rma`` of true range with ``high - low`` on the first bar). Conventions that the
manual does not pin down - flagged ``VERIFY`` below - have to be confirmed with the Pine-vs-Python
comparison harness before a converted indicator is declared equivalent.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

nan = math.nan


def _isnan(x: float) -> bool:
    return x != x


# ----------------------------------------------------------------------------- streaming
class SMA:
    """``ta.sma``: na until ``length`` values; any na inside the window yields na."""

    def __init__(self, length: int):
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.buf: deque[float] = deque()
        self.total = 0.0
        self.nans = 0
        self.value = nan

    def update(self, x: float) -> float:
        x = float(x)
        self.buf.append(x)
        if _isnan(x):
            self.nans += 1
        else:
            self.total += x
        if len(self.buf) > self.length:
            old = self.buf.popleft()
            if _isnan(old):
                self.nans -= 1
            else:
                self.total -= old
        if len(self.buf) < self.length or self.nans:
            self.value = nan
        else:
            self.value = self.total / self.length
        return self.value


class _Recursive:
    """Shared logic for EMA/RMA: SMA seed, then ``alpha*x + (1-alpha)*prev``."""

    alpha: float

    def __init__(self, length: int):
        self.length = length
        self.seed = SMA(length)
        self.value = nan

    def update(self, x: float) -> float:
        x = float(x)
        if _isnan(self.value):
            self.value = self.seed.update(x)
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class EMA(_Recursive):
    """``ta.ema`` with an SMA seed (first value on bar ``length-1``). VERIFY against TradingView."""

    def __init__(self, length: int):
        super().__init__(length)
        self.alpha = 2.0 / (length + 1)


class RMA(_Recursive):
    """``ta.rma`` (Wilder smoothing), SMA-seeded per the Pine manual."""

    def __init__(self, length: int):
        super().__init__(length)
        self.alpha = 1.0 / length


class TrueRange:
    """``ta.tr(true)``: ``high - low`` on the first bar, then the classic true range."""

    def __init__(self):
        self.prev_close = nan

    def update(self, high: float, low: float, close: float) -> float:
        if _isnan(self.prev_close):
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.prev_close = close
        return tr


class ATR:
    def __init__(self, length: int):
        self.tr = TrueRange()
        self.rma = RMA(length)
        self.value = nan

    def update(self, high: float, low: float, close: float) -> float:
        self.value = self.rma.update(self.tr.update(high, low, close))
        return self.value


class Highest:
    """``ta.highest(src, length)`` over the current and previous ``length-1`` bars.
    Returns na until ``length`` bars exist. VERIFY partial-window behaviour against TradingView."""

    def __init__(self, length: int):
        self.length = length
        self.buf: deque[float] = deque(maxlen=length)
        self.value = nan

    def update(self, x: float) -> float:
        self.buf.append(float(x))
        self.value = max(self.buf) if len(self.buf) == self.length else nan
        return self.value


class Lowest(Highest):
    def update(self, x: float) -> float:
        self.buf.append(float(x))
        self.value = min(self.buf) if len(self.buf) == self.length else nan
        return self.value


def crossover(a: float, a_prev: float, b: float, b_prev: float) -> bool:
    """``ta.crossover``: a > b now and a <= b on the previous bar (na anywhere -> False)."""
    if _isnan(a) or _isnan(a_prev) or _isnan(b) or _isnan(b_prev):
        return False
    return a > b and a_prev <= b_prev


def crossunder(a: float, a_prev: float, b: float, b_prev: float) -> bool:
    if _isnan(a) or _isnan(a_prev) or _isnan(b) or _isnan(b_prev):
        return False
    return a < b and a_prev >= b_prev


# ---------------------------------------------------------------------------- vectorised
def _run(stream, values: np.ndarray) -> np.ndarray:
    out = np.empty(len(values), dtype=np.float64)
    upd = stream.update
    for i, x in enumerate(values.tolist()):
        out[i] = upd(x)
    return out


def sma(values: np.ndarray, length: int) -> np.ndarray:
    return _run(SMA(length), np.asarray(values, dtype=np.float64))


def ema(values: np.ndarray, length: int) -> np.ndarray:
    return _run(EMA(length), np.asarray(values, dtype=np.float64))


def rma(values: np.ndarray, length: int) -> np.ndarray:
    return _run(RMA(length), np.asarray(values, dtype=np.float64))


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    prev = np.r_[nan, close[:-1]]
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev), np.abs(low - prev)))
    if len(tr):
        tr[0] = high[0] - low[0]
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    return rma(true_range(high, low, close), length)


def source(bars, name: str) -> np.ndarray:
    o, h, l, c = bars.open, bars.high, bars.low, bars.close
    if name == "open":
        return o
    if name == "high":
        return h
    if name == "low":
        return l
    if name == "close":
        return c
    if name == "hl2":
        return (h + l) / 2
    if name == "hlc3":
        return (h + l + c) / 3
    if name == "ohlc4":
        return (o + h + l + c) / 4
    if name == "hlcc4":
        return (h + l + c + c) / 4
    raise ValueError(f"unknown source {name!r}")


def source_value(name: str, o: float, h: float, l: float, c: float) -> float:
    if name == "open":
        return o
    if name == "high":
        return h
    if name == "low":
        return l
    if name == "close":
        return c
    if name == "hl2":
        return (h + l) / 2
    if name == "hlc3":
        return (h + l + c) / 3
    if name == "ohlc4":
        return (o + h + l + c) / 4
    if name == "hlcc4":
        return (h + l + c + c) / 4
    raise ValueError(f"unknown source {name!r}")
