"""Timeframe definitions and candle-bucket alignment.

All timestamps in the platform are UTC Unix seconds marking the *open* time of a candle.

Two alignment schemes are supported for building/merging higher-timeframe candles:

* ``ny17`` (default) - trading days start at 17:00 America/New_York, which is OANDA's default
  ``dailyAlignment=17`` / ``alignmentTimezone=America/New_York`` and the session boundary used for
  FX/metals on TradingView. H4 buckets start at 17:00, 21:00, 01:00, 05:00, 09:00, 13:00 New York
  time, so in UTC they shift by one hour when US daylight-saving time changes.
* ``utc`` - buckets are aligned to UTC midnight (what many CSV vendors, e.g. MT4 brokers on UTC,
  use for D1/H4).

Timeframes of one hour or less are identical under both schemes because New York's UTC offset is
a whole number of hours.

Weekly and monthly candles are *reference* timeframes: scripts may request them (previous week /
month levels) but they are not chart timeframes. Under ``ny17`` a week opens Sunday 17:00 New York
and a month opens at 17:00 New York on its eve (the session that trades on the 1st); under ``utc``
they open Monday 00:00 / the 1st at 00:00 UTC.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

NY_TZ = "America/New_York"
ALIGNMENTS = ("ny17", "utc")


@dataclass(frozen=True)
class Timeframe:
    code: str  # internal code, also the OANDA granularity
    label: str  # TradingView-style label shown in the UI
    seconds: int  # nominal duration
    pine: str  # timeframe.period string as Pine reports it

    @property
    def intraday(self) -> bool:
        return self.seconds < 86400


TIMEFRAMES: dict[str, Timeframe] = {
    tf.code: tf
    for tf in (
        Timeframe("M1", "1m", 60, "1"),
        Timeframe("M5", "5m", 300, "5"),
        Timeframe("M15", "15m", 900, "15"),
        Timeframe("M30", "30m", 1800, "30"),
        Timeframe("H1", "1H", 3600, "60"),
        Timeframe("H4", "4H", 14400, "240"),
        Timeframe("D", "1D", 86400, "D"),
    )
}
TIMEFRAME_ORDER = list(TIMEFRAMES)
# Calendar timeframes available to request_security only (built from daily candles); "seconds" is nominal.
REFERENCE_TIMEFRAMES: dict[str, Timeframe] = {
    tf.code: tf for tf in (Timeframe("W", "1W", 7 * 86400, "W"), Timeframe("M", "1M", 30 * 86400, "M"))
}


def get_timeframe(code: str, *, reference: bool = False) -> Timeframe:
    """A chart timeframe; ``reference=True`` also accepts the weekly/monthly reference timeframes."""
    table = {**TIMEFRAMES, **REFERENCE_TIMEFRAMES} if reference else TIMEFRAMES
    try:
        return table[code.upper() if code.upper() in table else code]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe {code!r}; expected one of {list(table)}") from exc


def _fixed_length(tf: Timeframe, alignment: str) -> bool:
    return tf.code not in REFERENCE_TIMEFRAMES and (tf.seconds <= 3600 or alignment == "utc")


def _calendar_bucket_starts(t: np.ndarray, tf: Timeframe, alignment: str) -> np.ndarray:
    ts = pd.to_datetime(t, unit="s", utc=True).tz_localize(None)
    if alignment == "utc":
        start = ts.to_period("W-SUN" if tf.code == "W" else "M").start_time
        return start.as_unit("s").asi8.astype(np.int64)
    # 17:00 New York opens the next calendar day's session: shift so each session lands on its date.
    shift = pd.Timedelta(hours=7)
    local = pd.to_datetime(t, unit="s", utc=True).tz_convert(NY_TZ).tz_localize(None) + shift
    start_local = local.to_period("W-SUN" if tf.code == "W" else "M").start_time - shift
    start_utc = start_local.tz_localize(
        NY_TZ, ambiguous=np.ones(len(start_local), dtype=bool), nonexistent="shift_forward"
    ).tz_convert("UTC")
    return start_utc.as_unit("s").asi8.astype(np.int64)


def bucket_starts(times: np.ndarray, tf: Timeframe, alignment: str = "ny17") -> np.ndarray:
    """Vectorised: return the open time of the ``tf`` bucket containing each timestamp."""
    if alignment not in ALIGNMENTS:
        raise ValueError(f"alignment must be one of {ALIGNMENTS}")
    t = np.asarray(times, dtype=np.int64)
    if _fixed_length(tf, alignment):
        return t - np.mod(t, tf.seconds)
    if t.size == 0:
        return t.copy()
    if tf.code in REFERENCE_TIMEFRAMES:
        return _calendar_bucket_starts(t, tf, alignment)
    local = pd.to_datetime(t, unit="s", utc=True).tz_convert(NY_TZ).tz_localize(None)
    shifted = local - pd.Timedelta(hours=17)
    floored = shifted.floor("D" if tf.code == "D" else "4h")
    start_local = floored + pd.Timedelta(hours=17)
    # The FX/metals market is closed around the Sunday 02:00 DST switch, so bucket starts are never
    # inside the skipped/repeated hour for real data; the flags below only make the call total.
    start_utc = start_local.tz_localize(
        NY_TZ, ambiguous=np.ones(len(start_local), dtype=bool), nonexistent="shift_forward"
    ).tz_convert("UTC")
    return start_utc.as_unit("s").asi8.astype(np.int64)


def bucket_start(ts: int, tf: Timeframe, alignment: str = "ny17") -> int:
    return int(bucket_starts(np.array([ts], dtype=np.int64), tf, alignment)[0])


def bucket_end(start: int, tf: Timeframe, alignment: str = "ny17") -> int:
    """Close time of the bucket opening at ``start`` (= open time of the following bucket)."""
    if _fixed_length(tf, alignment):
        return start + tf.seconds
    # Find the next aligned boundary; handles 23h/25h buckets across DST switches and 28-31 day months.
    probe = start + (32 * 86400 if tf.code == "M" else tf.seconds + 2 * 3600)
    nxt = bucket_start(probe, tf, alignment)
    if nxt <= start:  # pragma: no cover - defensive
        nxt = bucket_start(probe + tf.seconds, tf, alignment)
    return nxt


def bar_close_times(times: np.ndarray, tf: Timeframe, alignment: str = "ny17") -> np.ndarray:
    """Close time of each bar, computed from its own open time only (no look at later bars)."""
    t = np.asarray(times, dtype=np.int64)
    if _fixed_length(tf, alignment):
        return t + tf.seconds
    return np.array([bucket_end(int(x), tf, alignment) for x in t], dtype=np.int64)
