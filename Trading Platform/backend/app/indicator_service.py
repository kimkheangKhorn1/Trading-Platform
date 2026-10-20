"""Chart indicator calculations with cached, incrementally updated runners.

A runner is keyed by (feed, timeframe, script, inputs, first bar, mode). Repeated requests - e.g. a
new live candle or the next replay step - only execute the bars that are new since the previous
request; the still-forming candle is evaluated provisionally and rolled back next time.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict

from .engine.bars import concat_bars
from .engine.registry import get_script
from .engine.runner import DataRevisionError, ScriptRunner, apply_display_filter
from .market.providers import MarketSync, StoreHtfProvider
from .market.repository import MarketStore
from .market.timeframes import get_timeframe


class IndicatorRequestError(ValueError):
    pass


class IndicatorService:
    def __init__(self, store: MarketStore, sync: MarketSync | None = None, max_runners: int = 24):
        self.store = store
        self.sync = sync
        self.max_runners = max_runners
        self._cache: OrderedDict[tuple, tuple[ScriptRunner, threading.Lock]] = OrderedDict()
        self._guard = threading.Lock()

    def run(self, *, feed_id: str, timeframe: str, script_id: str, inputs: dict | None, start_time: int,
            end_time: int | None = None, replay_start: int | None = None) -> dict:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise IndicatorRequestError(f"unknown feed {feed_id!r}")
        tf = get_timeframe(timeframe)
        try:
            script_cls = get_script(script_id)
        except KeyError as exc:
            raise IndicatorRequestError(str(exc)) from exc
        script = script_cls(inputs)  # validates inputs (InputError -> 400)
        series = self.store.get_series(feed.id, tf.code)
        if series is None:
            raise IndicatorRequestError(f"no {tf.label} candles for {feed.display_name}")
        warmup = script.required_warmup()
        pre = self.store.load_bars(series.id, before=start_time, limit=warmup, complete_only=True)
        main = self.store.load_bars(series.id, start=start_time, end=end_time)
        bars = concat_bars(pre, main)
        if len(bars) == 0:
            raise IndicatorRequestError("no candles in the requested range")
        mode = "live" if end_time is None else "replay"
        # start_time is part of the key: when the stored history is shorter than the warm-up, the first bar is the same
        # for every start, but a runner only has intrabars from its own start on (ltf_start)
        # display-only inputs (e.g. Easy Mode) are left out: flipping them reuses the same calculation
        calc = {k: v for k, v in script.p.items() if k not in script_cls.meta.display_inputs}
        key = (feed.id, tf.code, script_id, json.dumps(calc, sort_keys=True), int(bars.time[0]), int(start_time), mode)
        provider = StoreHtfProvider(self.store, feed, sync=self.sync, sync_interval=20.0)

        def fresh() -> ScriptRunner:
            return ScriptRunner(script_cls, script.p, timeframe=tf.code, alignment=feed.alignment,
                                precision=feed.precision, htf_provider=provider,
                                ticker=feed.symbol, ltf_start=start_time)

        with self._guard:
            entry = self._cache.get(key)
            if entry is None:
                entry = (fresh(), threading.Lock())
                self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_runners:
                self._cache.popitem(last=False)
        runner, lock = entry
        with lock:
            last = runner.last_committed_time
            if mode == "replay" and last is not None and last > int(bars.time[-1]):
                runner = fresh()  # stepping backwards: recompute up to the cursor
            try:
                runner.process(bars)
            except DataRevisionError:
                runner = fresh()
                runner.process(bars)
            with self._guard:
                self._cache[key] = (runner, lock)
            out = apply_display_filter(runner.output(start_time=start_time, end_time=end_time,
                                                     replay_start=replay_start if mode == "replay" else None),
                                       script.display_filter())  # this request's display settings
        out["warmup_bars"] = len(pre)
        out["script_id"] = script_id
        out["inputs"] = script.p
        return out
