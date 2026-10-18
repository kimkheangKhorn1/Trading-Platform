"""Signals -> trades -> statistics for the Liquidity Reaper research."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np

from .data import Market
from .events import Signal
from .sim import EXITS, Exit, Simulator, Trade


@dataclass(frozen=True)
class StopRule:
    mode: str = 'atr'  # 'atr': entry close +- mult*ATR (original); 'sweep': beyond the sweep extreme + buffer*ATR
    mult: float = 1.5
    buffer: float = 0.0


def stop_for(s: Signal, rule: StopRule) -> tuple[float, float]:
    """(stop price, planned risk from the signal close)."""
    if rule.mode == 'atr':
        stop = s.close - s.side * rule.mult * s.atr
    else:
        stop = s.extreme - s.side * rule.buffer * s.atr
    return stop, abs(s.close - stop)


def standalone(sim: Simulator, signals: list[Signal], rule: StopRule, plan: Exit) -> list[Trade | None]:
    out = []
    for s in signals:
        stop, risk_ref = stop_for(s, rule)
        if not (math.isfinite(stop) and risk_ref > 0):
            out.append(None)
            continue
        out.append(sim.run(s.i, s.side, stop, risk_ref, s.close, plan))
    return out


def one_at_a_time(signals: list[Signal], trades: list[Trade | None], keep, first_bar: int,
                  last_time: int | None = None, times=None) -> list[tuple[Signal, Trade]]:
    """Take a signal only when flat (the previous trade closed on or before its bar) and ``keep(signal)``."""
    taken, free_from = [], -1
    for s, t in zip(signals, trades):
        if t is None or s.i < first_bar or s.i < free_from or not keep(s):
            continue
        if last_time is not None and times[s.i] >= last_time:
            break
        taken.append((s, t))
        free_from = t.exit_bar
    return taken


def month_of(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime('%Y-%m')


def summarize(rows: list[tuple[Signal, Trade]]) -> dict:
    r = np.array([t.r for _, t in rows], dtype=float)
    n = len(r)
    if n == 0:
        return dict(trades=0, wins=0, losses=0, win_rate=0.0, net_r=0.0, avg_r=0.0, pf=0.0, max_dd_r=0.0,
                    tp1=0, tp2=0, tp3=0, sl=0)
    wins, losses = int((r > 0).sum()), int((r <= 0).sum())
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    hits = [t.tp_hits for _, t in rows]
    tp = [sum(1 for h in hits if len(h) > k and h[k]) for k in range(3)]
    return dict(trades=n, wins=wins, losses=losses, win_rate=wins / n, net_r=float(r.sum()), avg_r=float(r.mean()),
                pf=float(gp / gl) if gl > 0 else float('inf'), max_dd_r=dd, tp1=tp[0], tp2=tp[1], tp3=tp[2],
                sl=sum(1 for _, t in rows if t.outcome == 'sl'))


def in_window(rows, times, window):
    a, b = window
    return [(s, t) for s, t in rows if a <= times[s.i] < b]
