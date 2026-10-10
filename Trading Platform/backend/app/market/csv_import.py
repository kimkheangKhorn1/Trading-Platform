"""CSV candle import (fallback when OANDA access is unavailable).

Accepted layouts (auto-detected):

* header row with time/date(+time) + open/high/low/close (+ volume/tickvol) columns, any order;
  delimiter ``,`` ``;`` or tab; MT4/MT5 ``<DATE> <TIME> ...`` exports included
* header-less MT4 history: ``2024.01.02,00:00,2062.5,2064.1,2061.9,2063.0,123``
* header-less HistData: ``20240102 000000;2062.5;2064.1;2061.9;2063.0;0``
* timestamps as text dates or Unix epoch seconds/milliseconds

Timestamps are converted to UTC using the ``timezone`` option. Invalid rows are rejected (never
"repaired" silently) and reported back to the caller.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..engine.bars import Bars
from .timeframes import TIMEFRAMES, Timeframe, bucket_starts

TIME_COLS = ("time", "datetime", "timestamp", "date_time", "gmt time", "gmttime", "local time", "utc", "dt")
DATE_COLS = ("date", "day")
HOUR_COLS = ("time", "hour")
VOLUME_COLS = ("volume", "vol", "tickvol", "tick_volume", "tickvolume", "ticks", "real_volume")


@dataclass
class ImportReport:
    rows_read: int = 0
    rows_imported: int = 0
    rejected_invalid_ohlc: int = 0
    rejected_unparsable: int = 0
    rejected_misaligned: int = 0
    duplicates_dropped: int = 0
    timeframe: str | None = None
    first_time: int | None = None
    last_time: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class CsvImportError(ValueError):
    pass


def _norm(col: str) -> str:
    return re.sub(r"\s+", " ", str(col).strip().strip("<>").strip().lower())


def _detect_sep(sample: str) -> str:
    first = next((ln for ln in sample.splitlines() if ln.strip()), "")
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    sep = max(counts, key=counts.get)
    if counts[sep] == 0:
        raise CsvImportError("could not detect a delimiter (expected comma, semicolon or tab)")
    return sep


def _has_header(first_line: str, sep: str) -> bool:
    cells = [c.strip().strip("<>").lower() for c in first_line.split(sep)]
    return any(c in ("open", "high", "low", "close") for c in cells)


def _parse_times(values: pd.Series, *, dayfirst: bool) -> pd.Series:
    """Parse to naive datetimes (still in the file's timezone)."""
    s = values.astype(str).str.strip()
    numeric = pd.to_numeric(s, errors="coerce")
    if numeric.notna().all():
        med = float(numeric.median())
        if med > 1e14:  # microseconds
            return pd.to_datetime(numeric, unit="us")
        if med > 1e11:  # milliseconds
            return pd.to_datetime(numeric, unit="ms")
        if med > 1e8:  # seconds
            return pd.to_datetime(numeric, unit="s")
        # e.g. 20240102 (date only, HistData daily)
    compact = s.str.fullmatch(r"\d{8}( \d{4,6})?")
    if compact.all():
        fmt = {8: "%Y%m%d", 13: "%Y%m%d %H%M", 15: "%Y%m%d %H%M%S"}
        lengths = s.str.len().unique()
        if len(lengths) == 1 and int(lengths[0]) in fmt:
            return pd.to_datetime(s, format=fmt[int(lengths[0])], errors="coerce")
    s = s.str.replace(r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", regex=True)  # MT4 2024.01.02
    return pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")


def _localize_to_utc(naive: pd.Series, timezone: str) -> pd.Series:
    tz = timezone.strip()
    if tz.upper() in ("UTC", "GMT", "Z", ""):
        return naive.dt.tz_localize("UTC")
    m = re.fullmatch(r"(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", tz.upper())
    if m:
        sign = 1 if m.group(1) == "+" else -1
        offset = pd.Timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return (naive - sign * offset).dt.tz_localize("UTC")
    if tz.upper() in ("NY+7", "MT4", "BROKER_NY+7"):
        # Typical MT4/MT5 broker server clock: New York time + 7h (midnight = NY 17:00 close)
        return (naive - pd.Timedelta(hours=7)).dt.tz_localize("America/New_York", ambiguous="NaT",
                                                              nonexistent="NaT").dt.tz_convert("UTC")
    try:
        return naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    except Exception as exc:  # unknown zone name
        raise CsvImportError(f"unknown timezone {timezone!r}") from exc


def detect_timeframe(times: np.ndarray) -> Timeframe | None:
    if times.size < 3:
        return None
    diffs = np.diff(times)
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        return None
    values, counts = np.unique(diffs, return_counts=True)
    step = int(values[np.argmax(counts)])  # most common spacing; robust to gaps and stray rows
    for tf in TIMEFRAMES.values():
        if step == tf.seconds:
            return tf
    return None


def parse_csv(
    content: bytes | str,
    *,
    timezone: str = "UTC",
    timeframe: str | None = None,
    dayfirst: bool | None = None,
    alignment: str = "ny17",
) -> tuple[Bars, Timeframe, ImportReport]:
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    if not text.strip():
        raise CsvImportError("file is empty")
    sample = text[:4096]
    sep = _detect_sep(sample)
    first_line = next(ln for ln in sample.splitlines() if ln.strip())
    header = _has_header(first_line, sep)
    df = pd.read_csv(io.StringIO(text), sep=sep, header=0 if header else None, dtype=str,
                     skipinitialspace=True, engine="c")
    report = ImportReport(rows_read=len(df))
    if header:
        df.columns = [_norm(c) for c in df.columns]
        cols = list(df.columns)
        ohlc = {}
        for name in ("open", "high", "low", "close"):
            match = next((c for c in cols if c == name or c == name[0]), None)
            if match is None:
                raise CsvImportError(f"missing '{name}' column (found: {cols})")
            ohlc[name] = match
        vol_col = next((c for c in cols if c in VOLUME_COLS), None)
        time_col = next((c for c in cols if c in TIME_COLS and c not in ("time",)), None)
        date_col = next((c for c in cols if c in DATE_COLS), None)
        if date_col is not None and "time" in cols:
            raw_time = df[date_col].astype(str) + " " + df["time"].astype(str)
        elif time_col is not None:
            raw_time = df[time_col]
        elif "time" in cols:
            raw_time = df["time"]
        elif date_col is not None:
            raw_time = df[date_col]
        else:
            raise CsvImportError(f"no time/date column found (found: {cols})")
        o, h, l, c = (df[ohlc[k]] for k in ("open", "high", "low", "close"))
        v = df[vol_col] if vol_col else None
    else:
        ncol = df.shape[1]
        first = str(df.iloc[0, 0])
        second = str(df.iloc[0, 1]) if ncol > 1 else ""
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", second.strip()):  # date, time, o, h, l, c[, v]
            raw_time = df[0].astype(str) + " " + df[1].astype(str)
            base = 2
        else:
            raw_time = df[0]
            base = 1
        if ncol < base + 4:
            raise CsvImportError("expected at least time + open/high/low/close columns")
        o, h, l, c = (df[base + k] for k in range(4))
        v = df[base + 4] if ncol > base + 4 else None
        del first

    if dayfirst is None:
        probe = str(raw_time.iloc[0])
        dayfirst = bool(re.match(r"^\d{1,2}[./-]\d{1,2}[./-]\d{4}", probe))
    naive = _parse_times(raw_time, dayfirst=dayfirst)
    utc = _localize_to_utc(naive, timezone)
    frame = pd.DataFrame({
        "t": utc,
        "o": pd.to_numeric(o, errors="coerce"),
        "h": pd.to_numeric(h, errors="coerce"),
        "l": pd.to_numeric(l, errors="coerce"),
        "c": pd.to_numeric(c, errors="coerce"),
        "v": pd.to_numeric(v, errors="coerce").fillna(0.0) if v is not None else 0.0,
    })
    bad = frame[["t", "o", "h", "l", "c"]].isna().any(axis=1)
    report.rejected_unparsable = int(bad.sum())
    frame = frame[~bad]
    invalid = (
        (frame["o"] <= 0) | (frame["l"] <= 0)
        | (frame["h"] < frame[["o", "c"]].max(axis=1)) | (frame["l"] > frame[["o", "c"]].min(axis=1))
        | (frame["h"] < frame["l"])
    )
    report.rejected_invalid_ohlc = int(invalid.sum())
    frame = frame[~invalid]
    frame["t"] = frame["t"].dt.as_unit("s").astype("int64")
    frame = frame.sort_values("t", kind="stable")
    dup = frame.duplicated("t", keep="last")
    report.duplicates_dropped = int(dup.sum())
    frame = frame[~dup]
    if frame.empty:
        raise CsvImportError("no valid candles found in file")

    times = frame["t"].to_numpy(np.int64)
    if timeframe:
        tf = TIMEFRAMES.get(timeframe.upper())
        if tf is None:
            raise CsvImportError(f"unsupported timeframe {timeframe!r}")
    else:
        tf = detect_timeframe(times)
        if tf is None:
            raise CsvImportError("could not detect the candle timeframe; please specify it")
    aligned = bucket_starts(times, tf, alignment) == times
    report.rejected_misaligned = int((~aligned).sum())
    if report.rejected_misaligned:
        report.warnings.append(
            f"{report.rejected_misaligned} candles were not aligned to {tf.label} boundaries "
            f"(check the timezone option) and were rejected"
        )
    frame = frame[aligned]
    if frame.empty:
        raise CsvImportError("no candles aligned to the timeframe; is the timezone option correct?")
    bars = Bars.from_arrays(
        frame["t"].to_numpy(np.int64), frame["o"].to_numpy(), frame["h"].to_numpy(), frame["l"].to_numpy(),
        frame["c"].to_numpy(), frame["v"].to_numpy(np.float64),
    )
    report.rows_imported = len(bars)
    report.timeframe = tf.code
    report.first_time = int(bars.time[0])
    report.last_time = int(bars.time[-1])
    if report.rejected_invalid_ohlc:
        report.warnings.append(f"{report.rejected_invalid_ohlc} rows had inconsistent OHLC values and were rejected")
    if report.rejected_unparsable:
        report.warnings.append(f"{report.rejected_unparsable} rows could not be parsed and were rejected")
    return bars, tf, report
