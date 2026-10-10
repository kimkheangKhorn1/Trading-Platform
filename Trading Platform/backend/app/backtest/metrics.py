"""Performance statistics. Undefined ratios are returned as ``None`` (never as 0 or infinity)."""
from __future__ import annotations

import math

import numpy as np


def _f(x) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else x


def _streak(signs: list[int], target: int) -> int:
    best = cur = 0
    for s in signs:
        cur = cur + 1 if s == target else 0
        best = max(best, cur)
    return best


def trade_stats(trades: list[dict], initial_capital: float) -> dict:
    closed = [t for t in trades if not t.get("is_open")]
    pnl = np.array([t["pnl"] for t in closed], dtype=np.float64)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n = int(pnl.size)
    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(losses.sum()) if losses.size else 0.0
    r = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]
    rr = [t["planned_rr"] for t in closed if t.get("planned_rr") is not None]
    bars = [t["bars_held"] for t in closed if t.get("bars_held") is not None]
    signs = [1 if p > 0 else (-1 if p < 0 else 0) for p in pnl.tolist()]
    avg_win = float(wins.mean()) if wins.size else None
    avg_loss = float(losses.mean()) if losses.size else None
    return {
        "total_trades": n,
        "open_trades": len(trades) - n,
        "winning_trades": int(wins.size),
        "losing_trades": int(losses.size),
        "breakeven_trades": int(n - wins.size - losses.size),
        "win_rate": _f(wins.size / n * 100.0) if n else None,
        "net_profit": float(pnl.sum()) if n else 0.0,
        "net_profit_pct": float(pnl.sum()) / initial_capital * 100.0 if n else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": _f(gross_profit / -gross_loss) if gross_loss < 0 else None,
        "avg_trade": _f(pnl.mean()) if n else None,
        "avg_win": _f(avg_win),
        "avg_loss": _f(avg_loss),
        "win_loss_ratio": _f(avg_win / -avg_loss) if avg_win is not None and avg_loss else None,
        "largest_win": _f(wins.max()) if wins.size else None,
        "largest_loss": _f(losses.min()) if losses.size else None,
        "avg_r": _f(np.mean(r)) if r else None,
        "total_r": _f(np.sum(r)) if r else None,
        "avg_planned_rr": _f(np.mean(rr)) if rr else None,
        "max_consecutive_wins": _streak(signs, 1),
        "max_consecutive_losses": _streak(signs, -1),
        "avg_bars_in_trade": _f(np.mean(bars)) if bars else None,
        "commission_paid": float(sum(t["commission"] for t in closed)),
        "ambiguous_trades": int(sum(1 for t in closed if t.get("ambiguous"))),
    }


def drawdown_stats(equity: np.ndarray, initial_capital: float) -> dict:
    """Peak-to-trough drawdown of the bar-close mark-to-market equity curve (open trades valued at
    the executable side of the close), starting from the initial capital."""
    if equity.size == 0:
        return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0, "max_runup": 0.0}
    curve = np.r_[initial_capital, equity]
    peak = np.maximum.accumulate(curve)
    dd = peak - curve
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.where(peak > 0, dd / peak * 100.0, 0.0)
    trough = np.minimum.accumulate(curve)
    return {
        "max_drawdown": float(dd.max()),
        "max_drawdown_pct": float(dd_pct.max()),
        "max_runup": float((curve - trough).max()),
    }


def drawdown_series(equity: np.ndarray, initial_capital: float) -> np.ndarray:
    curve = np.r_[initial_capital, equity]
    peak = np.maximum.accumulate(curve)
    return (curve - peak)[1:]


def full_metrics(trades: list[dict], equity: np.ndarray, initial_capital: float) -> dict:
    out = {
        "all": trade_stats(trades, initial_capital),
        "long": trade_stats([t for t in trades if t["side"] == "long"], initial_capital),
        "short": trade_stats([t for t in trades if t["side"] == "short"], initial_capital),
    }
    out["all"].update(drawdown_stats(equity, initial_capital))
    final = float(equity[-1]) if equity.size else initial_capital
    out["all"]["final_equity"] = final
    out["all"]["return_pct"] = (final - initial_capital) / initial_capital * 100.0
    dd = out["all"]["max_drawdown"]
    out["all"]["return_over_max_dd"] = _f((final - initial_capital) / dd) if dd > 0 else None
    return out


def downsample(times: np.ndarray, values: np.ndarray, max_points: int = 2500) -> tuple[list, list]:
    """Reduce a series for display while keeping each bucket's min and max (drawdowns stay visible)."""
    n = int(times.size)
    if n <= max_points:
        return times.tolist(), values.tolist()
    buckets = max_points // 2
    edges = np.linspace(0, n, buckets + 1).astype(int)
    keep: list[int] = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = values[a:b]
        i_min, i_max = a + int(np.argmin(seg)), a + int(np.argmax(seg))
        keep.extend(sorted({i_min, i_max}))
    keep = sorted(set(keep) | {0, n - 1})
    idx = np.asarray(keep)
    return times[idx].tolist(), values[idx].tolist()
