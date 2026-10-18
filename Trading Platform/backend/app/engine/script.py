"""Bar-by-bar script model (the Pine execution model).

A script is executed once per bar, oldest to newest. During ``on_bar`` it can only read the current
and *past* bars through :class:`Context` - there is no API that exposes later bars, so lookahead is
prevented structurally rather than by convention. State lives on the script instance (the analogue
of Pine ``var`` variables) and in :class:`Series` objects (the analogue of history-referenced
series such as ``x[1]``).
"""
from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, ClassVar

import numpy as np

from .inputs import ScriptMeta, resolve_inputs
from .signals import CancelIntent, OrderIntent, TakeProfit

if TYPE_CHECKING:  # pragma: no cover
    from .bars import Bars
    from .drawings import Box, Label, Line
    from .runner import ScriptRunner

nan = math.nan


class Series:
    """A value with history: ``s[0]`` is the current bar's value, ``s[1]`` the previous bar's...

    The runner commits every registered series after each bar, so the current value is carried
    into history exactly once per bar like a Pine series.
    """

    def __init__(self, maxlen: int = 5000, initial: Any = nan):
        self.hist: deque = deque(maxlen=maxlen)
        self.current: Any = initial
        self.initial = initial

    def set(self, value: Any) -> None:
        self.current = value

    def __getitem__(self, offset: int) -> Any:
        if offset < 0:
            raise IndexError("negative offsets would read future bars")
        if offset == 0:
            return self.current
        if offset > len(self.hist):
            return nan
        return self.hist[-offset]

    def commit(self) -> None:
        self.hist.append(self.current)
        self.current = self.initial


class Script:
    """Base class for indicators/strategies. Subclasses define ``meta`` and ``on_bar``."""

    meta: ClassVar[ScriptMeta]

    def __init__(self, params: dict | None = None):
        self.p = resolve_inputs(self.meta.inputs, params)

    def required_warmup(self) -> int:
        """Bars needed before a backtest window for state to settle (override per script)."""
        return 300

    def setup(self, ctx: "Context") -> None:  # pragma: no cover - optional hook
        pass

    def on_bar(self, ctx: "Context") -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def on_last_bar(self, ctx: "Context") -> None:
        """Drawings Pine makes under ``barstate.islast`` (current levels, labels to the right...).

        Runs at output time on a throwaway copy of the state, so nothing done here carries into later
        bars: a live update or replay step yields the same chart as a fresh run over the same bars."""

    def display_filter(self) -> dict | None:
        """Display-only hiding for the chart: ``{"groups": drawing groups, "shapes": plotshape ids, "plots": plot ids,
        "tables": bool, "candles": bool}``. Applied to what is sent to the chart only; the script, its drawings,
        signals and anything else that reads its state are unaffected. None shows everything."""
        return None

    def extra_output(self) -> dict:
        """State shown next to the chart after the last bar: ``tables`` (Pine ``table`` objects as
        ``{"id", "position", "rows": [[{"text", "bg_color", "text_color", "tooltip"}]]}``) and
        ``warnings`` (data problems the user should know about)."""
        return {}


class Intrabars:
    """``request.security_lower_tf`` result for one chart bar: its intrabar candles, oldest first, and the close
    of the intrabar before the first one (``close[1]`` evaluated on the lower timeframe; na if none)."""

    def __init__(self, bars: "Bars", prev_close: float):
        self.bars = bars
        self.prev_close = prev_close

    @staticmethod
    def empty() -> "Intrabars":
        from .bars import Bars
        return Intrabars(Bars.empty(), nan)

    def __len__(self) -> int:
        return len(self.bars)


class SecurityHandle:
    def __init__(self, index: int):
        self.index = index


class Context:
    """Read access to bars up to the current one + output/drawing/signal API."""

    def __init__(self, runner: "ScriptRunner"):
        self._r = runner
        self.i = -1

    # ------------------------------------------------------------------ bar data
    @property
    def bar_index(self) -> int:
        return self.i

    @property
    def time(self) -> int:
        return int(self._r.buf_time[self.i])

    @property
    def open(self) -> float:
        return float(self._r.buf_open[self.i])

    @property
    def high(self) -> float:
        return float(self._r.buf_high[self.i])

    @property
    def low(self) -> float:
        return float(self._r.buf_low[self.i])

    @property
    def close(self) -> float:
        return float(self._r.buf_close[self.i])

    @property
    def volume(self) -> float:
        return float(self._r.buf_volume[self.i])

    @property
    def is_confirmed(self) -> bool:
        """``barstate.isconfirmed`` - False only while processing a still-forming live bar."""
        return bool(self._r.buf_complete[self.i])

    @property
    def timeframe(self):
        return self._r.tf

    @property
    def alignment(self) -> str:
        """Session alignment of the feed's candles ("ny17" or "utc")."""
        return self._r.alignment

    @property
    def ticker(self) -> str:
        """``syminfo.ticker`` (the feed's symbol, empty when the runner was not given one)."""
        return self._r.ticker

    @property
    def precision(self) -> int:
        """Price decimals of the symbol (``syminfo.mintick == 10 ** -precision``)."""
        return self._r.precision

    @property
    def mintick(self) -> float:
        return 10.0 ** (-self._r.precision)

    def hist(self, field: str, offset: int) -> float:
        """Pine ``field[offset]`` for a built-in series (open/high/low/close/volume/time)."""
        if offset < 0:
            raise IndexError("negative offsets would read future bars")
        j = self.i - offset
        if j < 0:
            return nan
        return float(getattr(self._r, f"buf_{field}")[j])

    def time_at(self, index: int) -> int:
        """Open time of bar ``index``; bars after the current one are extrapolated by the timeframe
        (used only to place drawings to the right, never to read data)."""
        if index <= self.i:
            return int(self._r.buf_time[max(index, 0)])
        return int(self._r.buf_time[self.i]) + (index - self.i) * self._r.tf.seconds

    # ------------------------------------------------------------------ helpers
    def series(self, maxlen: int = 5000, initial: Any = nan) -> Series:
        s = Series(maxlen=maxlen, initial=initial)
        self._r.series_vars.append(s)
        return s

    def request_security(self, timeframe: str, expr: Callable[["Bars"], np.ndarray], *, lookahead: bool = False,
                         offset: int = 0, allow_future_leak: bool = False) -> SecurityHandle:
        """Register a higher-timeframe expression (call from ``setup`` only)."""
        return self._r.register_security(timeframe, expr, lookahead, offset, allow_future_leak)

    def history_before_start(self, start: int, timeframe: str | None = None) -> "Bars":
        """Stored, confirmed candles of the chart timeframe (or of ``timeframe``) with open times from ``start``
        up to (not including) the first bar this run was given - for state that must start at a fixed date
        whatever part of the chart is loaded. Only candles that ended before every bar of the run are returned
        (no lookahead, and no overlap with the run's bars); empty without a data provider."""
        from ..market.timeframes import bucket_end, get_timeframe
        from .bars import Bars

        r = self._r
        if r.htf_provider is None or r.n == 0:
            return Bars.empty()
        first = int(r.buf_time[0])
        if first <= start:
            return Bars.empty()
        tf = r.tf if timeframe is None else get_timeframe(timeframe)
        load = getattr(r.htf_provider, "load_stored", None)
        bars = load(tf.code, start, first - 1) if load is not None else r.htf_provider.load(tf.code, start, first - 1, 0)
        if len(bars) == 0:
            return bars
        keep = (bars.time >= start) & (bars.time < first) & bars.complete
        if timeframe is not None:  # a longer candle must have ended before the run's first bar
            keep &= np.array([bucket_end(int(t), tf, r.alignment) <= first for t in bars.time], dtype=bool)
        if keep.all():
            return bars
        return Bars(bars.time[keep], bars.open[keep], bars.high[keep], bars.low[keep], bars.close[keep],
                    bars.volume[keep], bars.complete[keep])

    def request_lower_tf(self, seconds: int, lookback: int | None = None) -> int:
        """Register a ``request.security_lower_tf`` call (``setup`` only). Intrabars are built from the feed's
        1-minute candles, so ``seconds`` must be a multiple of 60 and divide the chart timeframe. ``lookback``: chart
        bars before the first displayed bar that need intrabars (older warm-up bars get none, like the oldest bars on
        TradingView, whose intrabar history is limited too); None loads them for every bar."""
        if seconds < 60 or seconds % 60:
            raise ValueError("lower timeframes are built from 1-minute candles: seconds must be a multiple of 60")
        return self._r.register_lower_tf(seconds, lookback)

    def intrabars(self, handle: int, offset: int = 0) -> Intrabars:
        """Intrabars of the current bar (or of the bar ``offset`` bars back, Pine ``arr[offset]``) for a handle from
        :meth:`request_lower_tf`."""
        if offset < 0:
            raise IndexError("negative offsets would read future bars")
        if self.i - offset < 0:
            return Intrabars.empty()
        return self._r.intrabars(handle, self.i - offset)

    def security(self, handle: SecurityHandle) -> float:
        values = self._r.security_values[handle.index]
        return float(values[self.i]) if values is not None and self.i < len(values) else nan

    # ------------------------------------------------------------------ outputs
    def plot(self, plot_id: str, value: float, color: str | None = None) -> None:
        self._r.set_plot(plot_id, self.i, value, color)

    def shape(self, shape_id: str, *, text: str | None = None, color: str | None = None,
              tooltip: str | None = None, price: float | None = None) -> None:
        self._r.add_shape(shape_id, self.i, text, color, tooltip, price)

    def bar_color(self, color: str) -> None:
        self._r.bar_colors[self.i] = color

    def plot_candle(self, color: str | None, wick_color: str, border_color: str) -> None:
        """``plotcandle`` drawn over the chart candle; ``color=None`` leaves the body hollow."""
        self._r.candle_colors[self.i] = (color, wick_color, border_color)

    def box(self, **kw) -> "Box":
        kw.setdefault("created_at", self.time)
        return self._r.drawings.new_box(**kw)

    def line(self, **kw) -> "Line":
        kw.setdefault("created_at", self.time)
        return self._r.drawings.new_line(**kw)

    def label(self, **kw) -> "Label":
        kw.setdefault("created_at", self.time)
        return self._r.drawings.new_label(**kw)

    def delete(self, obj) -> None:
        self._r.drawings.delete(obj)

    # ------------------------------------------------------------------ signals
    def signal(
        self,
        strategy: str,
        side: str,
        *,
        order_type: str = "market",
        stop_loss: float,
        take_profits: list[tuple[float, float]] | tuple = (),
        entry: float | None = None,
        expiry_bars: int | None = None,
        breakeven_after_tp1: bool = False,
        breakeven_offset: float = 0.0,
        cancel_if_sl_before_entry: bool = True,
        comment: str = "",
    ) -> OrderIntent | None:
        """Emit a trade intent on the close of the current bar.

        Only confirmed bars produce signals - a still-forming live bar can change before it closes,
        so it never creates orders (it may still draw tentative visuals).
        """
        if not self.is_confirmed:
            return None
        n = self._r.signal_seq.get((strategy, self.i), 0)
        self._r.signal_seq[(strategy, self.i)] = n + 1
        intent = OrderIntent(
            id=f"{strategy}@{self.time}#{n}",
            strategy=strategy,
            side=side,  # type: ignore[arg-type]
            order_type=order_type,  # type: ignore[arg-type]
            stop_loss=float(stop_loss),
            take_profits=tuple(TakeProfit(float(p), float(f)) for p, f in take_profits),
            entry=float(entry) if entry is not None else (self.close if order_type == "market" else None),
            expiry_bars=expiry_bars,
            breakeven_after_tp1=breakeven_after_tp1,
            breakeven_offset=breakeven_offset,
            cancel_if_sl_before_entry=cancel_if_sl_before_entry,
            comment=comment,
            bar_index=self.i,
            time=self.time,
        )
        problem = intent.validate()
        if problem:
            self._r.signals.rejected.append((intent, problem))
            return None
        self._r.signals.intents.append(intent)
        return intent

    def cancel(self, order_id: str, strategy: str, reason: str = "cancelled by strategy") -> None:
        if self.is_confirmed:
            self._r.signals.cancels.append(CancelIntent(order_id, strategy, reason, self.i, self.time))
