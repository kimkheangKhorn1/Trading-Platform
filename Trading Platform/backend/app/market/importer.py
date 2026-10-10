"""Store imported CSV candles and derive the higher timeframes from them."""
from __future__ import annotations

import re

import numpy as np

from ..engine.bars import Bars
from .csv_import import CsvImportError, ImportReport, parse_csv
from .repository import Feed, MarketStore
from .resample import resample
from .timeframes import TIMEFRAMES, Timeframe, bucket_start, get_timeframe


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "data"


def csv_feed_id(symbol: str, dataset: str) -> str:
    return f"csv-{slug(symbol)}-{slug(dataset)}"


def detect_precision(values: np.ndarray, max_decimals: int = 5) -> int:
    sample = values[: min(values.size, 5000)]
    for d in range(max_decimals + 1):
        scaled = sample * 10**d
        if np.all(np.abs(scaled - np.round(scaled)) < 1e-6 * np.maximum(1.0, np.abs(scaled))):
            return d
    return max_decimals


def _rows(bars: Bars):
    return zip(bars.time.tolist(), bars.open.tolist(), bars.high.tolist(), bars.low.tolist(),
               bars.close.tolist(), bars.volume.tolist(), bars.complete.astype(int).tolist())


def import_csv(
    store: MarketStore,
    content: bytes | str,
    *,
    symbol: str = "XAUUSD",
    dataset: str = "import",
    price_type: str = "bid",
    timezone: str = "UTC",
    timeframe: str | None = None,
    alignment: str = "ny17",
    filename: str | None = None,
) -> tuple[Feed, ImportReport]:
    if price_type not in ("bid", "mid", "ask"):
        raise CsvImportError("price_type must be bid, mid or ask")
    bars, base_tf, report = parse_csv(content, timezone=timezone, timeframe=timeframe, alignment=alignment)
    symbol = symbol.strip().upper() or "XAUUSD"
    feed_id = csv_feed_id(symbol, dataset)
    existing = store.get_feed(feed_id)
    if existing is not None:
        if existing.meta.get("base_timeframe") != base_tf.code:
            raise CsvImportError(
                f"dataset '{dataset}' already holds {existing.meta.get('base_timeframe')} candles; "
                f"import {base_tf.code} data under a different dataset name"
            )
        if existing.price_type != price_type or existing.alignment != alignment:
            raise CsvImportError(f"dataset '{dataset}' was imported with different price type/alignment")
        meta = dict(existing.meta)
        meta.setdefault("files", []).append(filename)
        precision = existing.precision
    else:
        meta = {"base_timeframe": base_tf.code, "timezone": timezone, "files": [filename]}
        precision = detect_precision(np.concatenate([bars.open, bars.close]))
    feed = store.ensure_feed(
        feed_id, provider="csv", symbol=symbol, instrument=None, price_type=price_type,
        display_name=f"{symbol} · CSV {dataset} ({price_type})", precision=precision,
        alignment=alignment, meta=meta,
    )
    base_series = store.ensure_series(feed.id, base_tf.code, "native")
    store.upsert_candles(base_series.id, _rows(bars))
    store.set_oldest_reached(base_series.id, True)
    rebuild_resampled(store, feed, base_tf, from_time=int(bars.time[0]))
    return feed, report


def rebuild_resampled(store: MarketStore, feed: Feed, base_tf: Timeframe, from_time: int | None = None) -> None:
    """(Re)build every higher timeframe from the base series, starting at the daily bucket that
    contains ``from_time``. Every supported timeframe nests inside the daily bucket for both
    alignments, so each rebuilt bucket is covered in full by the loaded window."""
    base_series = store.get_series(feed.id, base_tf.code)
    assert base_series is not None
    start = None
    if from_time is not None:
        start = bucket_start(from_time, get_timeframe("D"), feed.alignment)
    src = store.load_bars(base_series.id, start=start)
    if len(src) == 0:
        return
    for tf in TIMEFRAMES.values():
        if tf.seconds <= base_tf.seconds:
            continue
        if tf.seconds < 86400 and tf.seconds % base_tf.seconds:
            continue
        out = resample(src, base_tf, tf, feed.alignment)
        series = store.ensure_series(feed.id, tf.code, "resampled")
        store.upsert_candles(series.id, _rows(out))
        store.set_oldest_reached(series.id, True)
