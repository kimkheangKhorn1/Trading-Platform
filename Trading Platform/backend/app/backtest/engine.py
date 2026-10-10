"""Runs a script's strategies over a date window.

* Warm-up: bars *before* ``start`` are fed to the script so indicators/state are settled when the
  window opens, but signals generated on warm-up bars are never traded.
* Eligibility: only intents generated on bars whose open time lies inside ``[start, end]`` create
  orders; orders and positions are processed on window bars only.
* End of window: pending orders are cancelled; open trades are closed at the last window bar's
  close (``close_at_end``) and labelled ``END`` so they can be told apart from strategy exits.
"""
from __future__ import annotations

import time as _time
from collections import defaultdict

import numpy as np

from ..engine.bars import Bars
from ..engine.runner import HtfProvider, ScriptRunner
from ..engine.script import Script
from .broker import Broker, equity_arrays
from .config import ExecutionConfig
from .metrics import downsample, drawdown_series, full_metrics


class BacktestError(ValueError):
    pass


def run_backtest(
    bars: Bars,
    *,
    script_cls: type[Script],
    inputs: dict | None,
    timeframe: str,
    start: int,
    end: int,
    execution: ExecutionConfig,
    strategies: list[str] | None = None,
    htf_provider: HtfProvider | None = None,
    alignment: str = "ny17",
    precision: int = 3,
    max_equity_points: int = 2500,
) -> dict:
    t0 = _time.perf_counter()
    if len(bars) == 0:
        raise BacktestError("no candles available")
    if np.any(~bars.complete):
        raise BacktestError("backtests only run on complete candles")
    available = [s.id for s in script_cls.meta.strategies]
    if not available:
        raise BacktestError(f"{script_cls.meta.name} does not emit strategy signals")
    selected = available if not strategies else [s for s in available if s in strategies]
    unknown = sorted(set(strategies or []) - set(available))
    if unknown:
        raise BacktestError(f"unknown strategies for this script: {unknown}")
    if not selected:
        raise BacktestError("no strategy selected")
    i0 = bars.index_at_or_after(start)
    i1 = bars.index_after(end) - 1
    if i0 >= len(bars) or i1 < i0:
        raise BacktestError("no candles inside the selected date range")

    runner = ScriptRunner(script_cls, inputs, timeframe=timeframe, alignment=alignment, precision=precision,
                          htf_provider=htf_provider)
    t_script = _time.perf_counter()
    runner.process(bars.slice(0, i1 + 1))  # bars after the window are never shown to the script
    script_ms = (_time.perf_counter() - t_script) * 1000

    intents_at: dict[int, list] = defaultdict(list)
    cancels_at: dict[int, list] = defaultdict(list)
    n_signals = 0
    for it in runner.signals.intents:
        if it.strategy in selected and i0 <= it.bar_index <= i1:
            intents_at[it.bar_index].append(it)
            n_signals += 1
    for c in runner.signals.cancels:
        if c.strategy in selected and i0 <= c.bar_index <= i1:
            cancels_at[c.bar_index].append(c)

    broker = Broker(execution, bars, selected)
    t_sim = _time.perf_counter()
    for i in range(i0, i1 + 1):
        broker.process_bar(i)  # fills for orders placed on earlier closes
        for c in cancels_at.get(i, ()):
            broker.add_cancel(c, i)
        for it in intents_at.get(i, ()):
            broker.add_intent(it, i)  # generated on this bar's close -> active from i+1
    broker.finish(i1)
    sim_ms = (_time.perf_counter() - t_sim) * 1000

    trades = broker.trade_records()
    times, per = equity_arrays(broker)
    cap = execution.initial_capital
    combined = cap + sum((eq - cap) for eq in per.values()) if per else np.full(times.shape, cap)
    titles = {s.id: s.title for s in script_cls.meta.strategies}

    per_strategy = {}
    for sid in selected:
        st_trades = [t for t in trades if t["strategy"] == sid]
        per_strategy[sid] = full_metrics(st_trades, per[sid], cap)
    cancelled = sorted((rec for b in broker.books.values() for rec in b.cancelled),
                       key=lambda r: (r["time"], r["order_id"]))

    def series(values: np.ndarray) -> dict:
        t, v = downsample(times, values, max_equity_points)
        return {"times": t, "values": v}

    warnings = []
    if i0 < 50:
        warnings.append(f"only {i0} warm-up bars were available before the start date")
    rejected = [{"intent": it.to_dict(), "reason": why} for it, why in runner.signals.rejected
                if it.strategy in selected and i0 <= it.bar_index <= i1]
    if rejected:
        warnings.append(f"{len(rejected)} signals were rejected as invalid by the order validator")
    if n_signals == 0 and not rejected:
        warnings.append("the strategy emitted no trade signals in this window, so there are no trades")

    return {
        "summary": {
            "first_bar": int(bars.time[i0]),
            "last_bar": int(bars.time[i1]),
            "bars": i1 - i0 + 1,
            "warmup_bars": i0,
            "signals": n_signals,
            "rejected_signals": len(rejected),
            "ambiguous_bars": broker.ambiguous_bars,
            "script_ms": round(script_ms, 1),
            "simulation_ms": round(sim_ms, 1),
            "total_ms": round((_time.perf_counter() - t0) * 1000, 1),
        },
        "strategies": [{"id": sid, "title": titles.get(sid, sid)} for sid in selected],
        "metrics": {"combined": full_metrics(trades, combined, cap), "per_strategy": per_strategy},
        "trades": trades,
        "cancelled_orders": cancelled,
        "rejected_signals": rejected,
        "equity": {
            "combined": series(combined),
            "drawdown": series(drawdown_series(combined, cap)),
            "per_strategy": {sid: series(per[sid]) for sid in selected},
        },
        "assumptions": execution.to_dict(),
        "warnings": warnings,
    }
