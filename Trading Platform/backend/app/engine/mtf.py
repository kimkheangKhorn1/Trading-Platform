"""Multi-timeframe data access - emulation of Pine's ``request.security()``.

Historical semantics implemented (TradingView documentation, "Other timeframes and data"):

* ``lookahead=False`` (``barmerge.lookahead_off``): on chart bar *i* the value of higher-timeframe
  bar *k* is visible only once *k* has closed, i.e. when ``close_time(chart i) >= close_time(htf k)``.
  On the last chart bar inside an HTF period the just-closed HTF value appears; on every other bar
  the previous HTF bar's value is returned. This never uses future data.
* ``lookahead=True`` (``barmerge.lookahead_on``): the HTF bar *containing* chart bar *i* is used. With
  ``offset=0`` that leaks the HTF bar's final value into its own earlier chart bars (future data on
  historical bars). It is only allowed with ``offset >= 1`` (the non-repainting ``expr[1]`` idiom)
  unless the caller passes ``allow_future_leak=True`` - which exists solely so a repainting Pine
  script can be reproduced faithfully for comparison, and must never feed a backtest.

Close times are derived from each bar's own open time and timeframe, never from the next bar, so a
missing chart bar can delay but not advance the moment an HTF value becomes visible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..market.timeframes import Timeframe, bar_close_times, bucket_starts
from .bars import Bars


class LookaheadError(ValueError):
    pass


def map_htf_index(
    chart_times: np.ndarray,
    chart_tf: Timeframe,
    htf_times: np.ndarray,
    htf_tf: Timeframe,
    *,
    alignment: str = "ny17",
    lookahead: bool = False,
) -> np.ndarray:
    """For each chart bar return the index of the HTF bar whose value it may see (-1 if none)."""
    chart_times = np.asarray(chart_times, dtype=np.int64)
    htf_times = np.asarray(htf_times, dtype=np.int64)
    if htf_times.size == 0:
        return np.full(chart_times.shape, -1, dtype=np.int64)
    if lookahead:
        bucket = bucket_starts(chart_times, htf_tf, alignment)
        return np.searchsorted(htf_times, bucket, side="right").astype(np.int64) - 1
    chart_close = bar_close_times(chart_times, chart_tf, alignment)
    htf_close = bar_close_times(htf_times, htf_tf, alignment)
    return np.searchsorted(htf_close, chart_close, side="right").astype(np.int64) - 1


@dataclass
class SecurityRequest:
    """A registered ``request.security`` call: ``expr`` maps HTF Bars -> per-HTF-bar values."""

    timeframe: str  # timeframe code, '' or None = chart timeframe
    expr: Callable[[Bars], np.ndarray]
    lookahead: bool = False
    offset: int = 0
    allow_future_leak: bool = False
    values: np.ndarray | None = None  # aligned to chart bars

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("offset must be >= 0")
        if self.lookahead and self.offset == 0 and not self.allow_future_leak:
            raise LookaheadError(
                "lookahead=True with offset=0 reads the final value of a higher-timeframe bar before it "
                "closes (future leak). Use offset=1, lookahead=False, or allow_future_leak=True only for "
                "Pine-reproduction comparisons."
            )


def evaluate_security(req: SecurityRequest, chart: Bars, chart_tf: Timeframe, htf: Bars,
                      htf_tf: Timeframe, alignment: str) -> np.ndarray:
    raw = np.asarray(req.expr(htf), dtype=np.float64)
    if raw.shape[0] != len(htf):
        raise ValueError("security expression must return one value per higher-timeframe bar")
    idx = map_htf_index(chart.time, chart_tf, htf.time, htf_tf, alignment=alignment, lookahead=req.lookahead)
    idx = idx - req.offset
    out = np.full(len(chart), np.nan)
    ok = idx >= 0
    out[ok] = raw[idx[ok]]
    return out
