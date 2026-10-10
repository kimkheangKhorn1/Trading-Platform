"""Aggregate lower-timeframe candles into higher-timeframe candles."""
from __future__ import annotations

import numpy as np

from ..engine.bars import Bars
from .timeframes import Timeframe, bucket_end, bucket_starts


def resample(src: Bars, src_tf: Timeframe, dst_tf: Timeframe, alignment: str = "ny17") -> Bars:
    """OHLCV aggregation: open=first, high=max, low=min, close=last, volume=sum.

    A destination candle is ``complete`` only when every source candle in it is complete and the
    source data reaches the end of the bucket (i.e. the last source candle closes at or after the
    bucket's close time). The final bucket of a file that stops mid-period is therefore flagged as
    incomplete instead of being presented as a finished candle.
    """
    if dst_tf.seconds < src_tf.seconds:
        raise ValueError("cannot resample to a lower timeframe")
    if dst_tf.seconds % src_tf.seconds and dst_tf.seconds < 86400:
        raise ValueError(f"{dst_tf.code} is not a multiple of {src_tf.code}")
    n = len(src)
    if n == 0:
        return Bars.empty()
    keys = bucket_starts(src.time, dst_tf, alignment)
    # Start index of each group (src is sorted, so equal keys are contiguous)
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    ends = np.r_[starts[1:], n]
    time = keys[starts]
    open_ = src.open[starts]
    close = src.close[ends - 1]
    high = np.maximum.reduceat(src.high, starts)
    low = np.minimum.reduceat(src.low, starts)
    volume = np.add.reduceat(src.volume, starts)
    all_complete = np.logical_and.reduceat(src.complete, starts)
    complete = all_complete.copy()
    # Only the last bucket can be cut short by the end of the source data.
    last_src_close = int(src.time[-1]) + src_tf.seconds
    if last_src_close < bucket_end(int(time[-1]), dst_tf, alignment):
        complete[-1] = False
    return Bars(time.astype(np.int64), open_, high, low, close, volume, complete)
