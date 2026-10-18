"""Executes a :class:`Script` over bars - full history and incremental updates.

Incremental contract (mirrors Pine's realtime model):

* confirmed (complete) bars are processed once and *committed*;
* a trailing still-forming bar is processed provisionally: the script state is snapshotted first
  and restored ("rolled back") before the next update, so intrabar updates never accumulate.

``ScriptRunner.process(bars)`` may be called repeatedly with a growing series; only bars newer than
the last committed bar are executed. Running the same bars in one call or in many calls yields
identical results (tested in ``test_engine_runner.py``).
"""
from __future__ import annotations

import copy
import math
from typing import Protocol

import numpy as np

from ..market.timeframes import get_timeframe
from .bars import Bars, concat_bars
from .drawings import DrawingStore
from .mtf import SecurityRequest, evaluate_security
from .script import Context, Intrabars, Script, SecurityHandle
from .signals import SignalLog


class HtfProvider(Protocol):
    def load(self, timeframe: str, start: int, end: int, warmup_bars: int) -> Bars:  # pragma: no cover
        ...


class DataRevisionError(RuntimeError):
    """Raised when bars that were already committed have changed (caller must start a new runner)."""


def _clean(values) -> list:
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in values]


class ScriptRunner:
    def __init__(
        self,
        script_cls: type[Script],
        params: dict | None = None,
        *,
        timeframe: str,
        alignment: str = "ny17",
        precision: int = 3,
        htf_provider: HtfProvider | None = None,
        htf_warmup_bars: int = 500,
        ticker: str = "",
        ltf_start: int | None = None,
    ):
        self.script = script_cls(params)
        self.meta = script_cls.meta
        self.tf = get_timeframe(timeframe)
        self.alignment = alignment
        self.precision = precision
        self.htf_provider = htf_provider
        self.htf_warmup_bars = htf_warmup_bars
        self.ticker = ticker
        # first displayed bar: intrabars are loaded from `lookback` bars before it (all bars when None)
        self.ltf_start = ltf_start

        self.drawings = DrawingStore(self.meta.max_boxes, self.meta.max_lines, self.meta.max_labels,
                                     self.meta.group_limits)
        self.plots: dict[str, list[float]] = {p.id: [] for p in self.meta.plots}
        self.plot_colors: dict[str, dict[int, str]] = {p.id: {} for p in self.meta.plots}
        self.shapes: dict[str, list[tuple]] = {s.id: [] for s in self.meta.shapes}
        self.bar_colors: dict[int, str] = {}
        self.candle_colors: dict[int, tuple[str | None, str, str]] = {}
        self.signals = SignalLog()
        self.signal_seq: dict[tuple[str, int], int] = {}
        self.series_vars: list = []
        self.securities: list[SecurityRequest] = []
        self.security_values: list[np.ndarray | None] = []
        # request.security_lower_tf: intrabar seconds per request, the 1-minute source candles and, per request,
        # the intrabar candles plus for every chart bar the [start, stop) range of its intrabars
        self.lower_tfs: list[int] = []
        self.ltf_lookback: int | None = None
        self.ltf_source: Bars | None = None
        self.ltf_values: list[tuple[Bars, np.ndarray, np.ndarray] | None] = []

        cap = 1024
        self.buf_time = np.empty(cap, dtype=np.int64)
        self.buf_open = np.empty(cap)
        self.buf_high = np.empty(cap)
        self.buf_low = np.empty(cap)
        self.buf_close = np.empty(cap)
        self.buf_volume = np.empty(cap)
        self.buf_complete = np.empty(cap, dtype=bool)
        self.n = 0  # bars in buffers (including a provisional live bar)
        self.n_committed = 0
        self._snapshot: tuple | None = None
        self.ctx = Context(self)
        self.script.setup(self.ctx)

    # ----------------------------------------------------------------- buffers
    def _ensure_capacity(self, needed: int) -> None:
        cap = self.buf_time.shape[0]
        if needed <= cap:
            return
        new_cap = max(needed, cap * 2)
        for name in ("time", "open", "high", "low", "close", "volume", "complete"):
            old = getattr(self, f"buf_{name}")
            new = np.empty(new_cap, dtype=old.dtype)
            new[: self.n] = old[: self.n]
            setattr(self, f"buf_{name}", new)

    def bars(self) -> Bars:
        n = self.n
        return Bars(self.buf_time[:n].copy(), self.buf_open[:n].copy(), self.buf_high[:n].copy(),
                    self.buf_low[:n].copy(), self.buf_close[:n].copy(), self.buf_volume[:n].copy(),
                    self.buf_complete[:n].copy())

    @property
    def last_committed_time(self) -> int | None:
        return int(self.buf_time[self.n_committed - 1]) if self.n_committed else None

    # -------------------------------------------------------------- securities
    def register_security(self, timeframe, expr, lookahead, offset, allow_future_leak) -> SecurityHandle:
        req = SecurityRequest(timeframe or "", expr, lookahead, offset, allow_future_leak)
        self.securities.append(req)
        self.security_values.append(None)
        return SecurityHandle(len(self.securities) - 1)

    def register_lower_tf(self, seconds: int, lookback: int | None = None) -> int:
        self.lower_tfs.append(int(seconds))
        if lookback is not None:
            self.ltf_lookback = max(self.ltf_lookback or 0, int(lookback))
        self.ltf_values.append(None)
        return len(self.lower_tfs) - 1

    def _refresh_lower_tfs(self) -> None:
        """1-minute candles of the same feed inside the chart bars, grouped into each request's intrabars.

        Only candles that open inside a chart bar are used, so a bar never sees data from a later bar. Confirmed
        source candles are kept between refreshes; only the newest ones are reloaded."""
        if not self.lower_tfs or self.n == 0:
            return
        from ..market.timeframes import bar_close_times

        chart_t = self.buf_time[: self.n]
        closes = bar_close_times(chart_t, self.tf, self.alignment)
        first = 0
        if self.ltf_start is not None and self.ltf_lookback is not None:
            first = max(0, int(np.searchsorted(chart_t, self.ltf_start, side="left")) - self.ltf_lookback)
        start, end = int(chart_t[first]), int(closes[-1]) - 1
        if self.tf.code == "M1" or self.htf_provider is None:
            src = self.bars().slice(first) if self.tf.code == "M1" else Bars.empty()
        else:
            old = self.ltf_source
            keep_upto = None
            if old is not None and len(old) and int(old.time[0]) <= start:
                done = np.flatnonzero(old.complete)
                keep_upto = int(old.time[done[-1]]) if len(done) else None
            if keep_upto is None:
                src = self.htf_provider.load("M1", start, end, 0)
            else:
                kept = old.slice(0, int(np.searchsorted(old.time, keep_upto, side="right")))
                new = self.htf_provider.load("M1", keep_upto + 1, end, 0)
                new = new.slice(int(np.searchsorted(new.time, keep_upto, side="right")))
                src = concat_bars(kept, new) if len(new) else kept
            lo = int(np.searchsorted(src.time, start, side="left"))
            hi = int(np.searchsorted(src.time, end, side="right"))
            src = src.slice(lo, hi)
        self.ltf_source = src
        # chart bar of every source candle (-1 if it falls in no chart bar, e.g. a gap in the chart series)
        j = np.searchsorted(chart_t, src.time, side="right") - 1
        inside = (j >= 0) & (src.time < closes[np.maximum(j, 0)])
        for k, secs in enumerate(self.lower_tfs):
            jj = j[inside]
            t = src.time[inside]
            bucket = (t - chart_t[jj]) // secs if len(t) else t
            key = jj.astype(np.int64) * 1_000_000 + bucket
            if len(key):
                heads = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
                last = np.r_[heads[1:], len(key)] - 1
                o, h, l, c, v, comp = (getattr(src, f)[inside] for f in ("open", "high", "low", "close", "volume",
                                                                         "complete"))
                bars = Bars(chart_t[jj[heads]] + bucket[heads] * secs, o[heads], np.maximum.reduceat(h, heads),
                            np.minimum.reduceat(l, heads), c[last], np.add.reduceat(v, heads),
                            np.logical_and.reduceat(comp, heads))
                owner = jj[heads]
            else:
                bars, owner = Bars.empty(), np.empty(0, dtype=np.int64)
            idx = np.arange(self.n)
            lo_i = np.searchsorted(owner, idx, side="left")
            hi_i = np.searchsorted(owner, idx, side="right")
            self.ltf_values[k] = (bars, lo_i, hi_i)

    def intrabars(self, handle: int, i: int) -> Intrabars:
        entry = self.ltf_values[handle]
        if entry is None:
            return Intrabars.empty()
        bars, lo, hi = entry
        a, b = int(lo[i]), int(hi[i])
        prev = float(bars.close[a - 1]) if a > 0 else float("nan")
        return Intrabars(bars.slice(a, b), prev)

    def _refresh_securities(self) -> None:
        self._refresh_lower_tfs()
        if not self.securities or self.n == 0:
            return
        chart = self.bars()
        # Several expressions (e.g. previous high/low/time) use the same source candles.
        # Reuse one load within this refresh; never retain market data between observations.
        htf_cache: dict[str, Bars] = {}
        for k, req in enumerate(self.securities):
            if req.timeframe in ("", self.tf.code):
                raw = np.asarray(req.expr(chart), dtype=np.float64)
                vals = np.full(len(chart), np.nan)
                if req.offset:
                    vals[req.offset:] = raw[: len(chart) - req.offset]
                else:
                    vals[:] = raw
                self.security_values[k] = vals
                continue
            if self.htf_provider is None:
                raise RuntimeError("script requests another timeframe but no data provider was given")
            htf_tf = get_timeframe(req.timeframe, reference=True)
            if htf_tf.code not in htf_cache:
                htf_cache[htf_tf.code] = self.htf_provider.load(
                    htf_tf.code, int(chart.time[0]), int(chart.time[-1]), self.htf_warmup_bars)
            htf = htf_cache[htf_tf.code]
            self.security_values[k] = evaluate_security(req, chart, self.tf, htf, htf_tf, self.alignment)

    # --------------------------------------------------------------- outputs
    def set_plot(self, plot_id: str, i: int, value: float, color: str | None) -> None:
        arr = self.plots.get(plot_id)
        if arr is None:
            raise KeyError(f"plot {plot_id!r} is not declared in the script meta")
        while len(arr) <= i:
            arr.append(math.nan)
        arr[i] = math.nan if value is None else float(value)
        if color is not None:
            self.plot_colors[plot_id][i] = color

    def add_shape(self, shape_id, i, text, color, tooltip, price) -> None:
        if shape_id not in self.shapes:
            raise KeyError(f"shape {shape_id!r} is not declared in the script meta")
        self.shapes[shape_id].append((i, text, color, tooltip, price))

    # ------------------------------------------------------------- execution
    def _rollback(self) -> None:
        if self._snapshot is None:
            return
        (self.script, self.drawings, self.series_vars, self.signal_seq, lens) = self._snapshot
        plot_lens, shape_lens, n_intents, n_cancels, n_rejected = lens
        for pid, ln in plot_lens.items():
            del self.plots[pid][ln:]
        for pid in self.plot_colors:
            self.plot_colors[pid].pop(self.n - 1, None)
        for sid, ln in shape_lens.items():
            del self.shapes[sid][ln:]
        self.bar_colors.pop(self.n - 1, None)
        self.candle_colors.pop(self.n - 1, None)
        del self.signals.intents[n_intents:]
        del self.signals.cancels[n_cancels:]
        del self.signals.rejected[n_rejected:]
        self.n = self.n_committed
        self._snapshot = None

    def _take_snapshot(self) -> None:
        lens = (
            {pid: len(v) for pid, v in self.plots.items()},
            {sid: len(v) for sid, v in self.shapes.items()},
            len(self.signals.intents), len(self.signals.cancels), len(self.signals.rejected),
        )
        state = copy.deepcopy((self.script, self.drawings, self.series_vars, self.signal_seq))
        self._snapshot = (*state, lens)

    def _step(self, i: int) -> None:
        self.ctx.i = i
        self.script.on_bar(self.ctx)
        for arr in self.plots.values():
            while len(arr) <= i:
                arr.append(math.nan)
        for s in self.series_vars:
            s.commit()

    def process(self, bars: Bars) -> int:
        """Execute bars newer than the last committed one. Returns the number of bars executed."""
        self._rollback()
        if len(bars) == 0:
            return 0
        start = 0
        if self.n_committed:
            last = self.last_committed_time
            pos = bars.index_at_or_after(last)  # type: ignore[arg-type]
            if pos < len(bars) and int(bars.time[pos]) == last:
                if (bars.close[pos] != self.buf_close[self.n_committed - 1]
                        or bars.high[pos] != self.buf_high[self.n_committed - 1]
                        or bars.low[pos] != self.buf_low[self.n_committed - 1]):
                    raise DataRevisionError(f"committed bar {last} changed")
                start = pos + 1
            else:
                start = bars.index_after(last)  # type: ignore[arg-type]
        new = bars.slice(start)
        m = len(new)
        if m == 0:
            return 0
        if np.any(~new.complete[:-1]):
            raise ValueError("only the last bar may be incomplete")
        self._ensure_capacity(self.n + m)
        a, b = self.n, self.n + m
        self.buf_time[a:b] = new.time
        self.buf_open[a:b] = new.open
        self.buf_high[a:b] = new.high
        self.buf_low[a:b] = new.low
        self.buf_close[a:b] = new.close
        self.buf_volume[a:b] = new.volume
        self.buf_complete[a:b] = new.complete
        self.n = b
        self._refresh_securities()
        for i in range(a, b):
            if self.buf_complete[i]:
                self._step(i)
                self.n_committed = i + 1
            else:
                self.n = i  # snapshot reflects the committed state
                self._take_snapshot()
                self.n = i + 1
                self._step(i)
        return m

    # ----------------------------------------------------------------- result
    def _last_bar_view(self) -> tuple[DrawingStore, dict]:
        """Drawings and extras after ``on_last_bar`` ran on a copy of the script state."""
        if self.n == 0 or type(self.script).on_last_bar is Script.on_last_bar:
            return self.drawings, self.script.extra_output()
        state = (self.script, self.drawings)
        self.script, self.drawings = copy.deepcopy(state)
        try:
            self.ctx.i = self.n - 1
            self.script.on_last_bar(self.ctx)
            return self.drawings, self.script.extra_output()
        finally:
            self.script, self.drawings = state

    def output(self, start_time: int | None = None, end_time: int | None = None) -> dict:
        n = self.n
        times = self.buf_time[:n]
        lo = int(np.searchsorted(times, start_time, side="left")) if start_time is not None else 0
        hi = int(np.searchsorted(times, end_time, side="right")) if end_time is not None else n
        t_slice = times[lo:hi]
        plots = {pid: _clean(arr[lo:hi]) for pid, arr in self.plots.items()}
        plot_colors = {
            pid: [[int(times[i]), c] for i, c in sorted(cols.items()) if lo <= i < hi]
            for pid, cols in self.plot_colors.items() if cols
        }
        shapes = {
            sid: [{"time": int(times[i]), "text": text, "color": color, "tooltip": tooltip, "price": price}
                  for (i, text, color, tooltip, price) in items if lo <= i < hi]
            for sid, items in self.shapes.items()
        }
        bar_colors = [[int(times[i]), c] for i, c in sorted(self.bar_colors.items()) if lo <= i < hi]
        candle_colors = [[int(times[i]), *c] for i, c in sorted(self.candle_colors.items()) if lo <= i < hi]
        drawings, extras = self._last_bar_view()
        t0 = int(t_slice[0]) if len(t_slice) else None
        t1 = int(t_slice[-1]) if len(t_slice) else None
        signals = [s.to_dict() for s in self.signals.intents if t0 is not None and t0 <= s.time <= t1]
        return {
            "times": [int(t) for t in t_slice],
            "plots": plots,
            "plot_colors": plot_colors,
            "shapes": shapes,
            "bar_colors": bar_colors,
            "candle_colors": candle_colors,
            "drawings": drawings.to_json(),
            "signals": signals,
            "rejected_signals": [{"intent": i.to_dict(), "reason": r} for i, r in self.signals.rejected
                                 if t0 is not None and t0 <= i.time <= t1],
            "bars_processed": self.n,
            "last_confirmed_time": self.last_committed_time,
            "provisional_bar": self.n > self.n_committed,
            "tables": extras.get("tables", []),
            "warnings": extras.get("warnings", []),
        }


def apply_display_filter(out: dict, hide: dict | None) -> dict:
    """Remove what :meth:`Script.display_filter` hides from a runner output (display only, nothing is recomputed)."""
    if not hide:
        return out
    groups, shapes, plots = set(hide.get("groups", ())), set(hide.get("shapes", ())), set(hide.get("plots", ()))
    out = dict(out)
    out["drawings"] = [d for d in out["drawings"] if d.get("group") not in groups]
    out["shapes"] = {k: ([] if k in shapes else v) for k, v in out["shapes"].items()}
    out["plots"] = {k: ([None] * len(v) if k in plots else v) for k, v in out["plots"].items()}
    out["plot_colors"] = {k: v for k, v in out["plot_colors"].items() if k not in plots}
    if hide.get("tables"):
        out["tables"] = []
    if hide.get("candles"):
        out["bar_colors"], out["candle_colors"] = [], []
    return out

