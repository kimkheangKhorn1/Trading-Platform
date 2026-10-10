import numpy as np
import pytest

from app.backtest.metrics import downsample, drawdown_stats, trade_stats


def test_trade_stats_hand_computed():
    trades = [{"pnl": p, "side": "long", "r_multiple": r, "planned_rr": 2.0, "bars_held": 3, "commission": 1.0,
               "is_open": False} for p, r in [(100, 1.0), (-50, -0.5), (200, 2.0), (-50, -0.5), (0, 0.0)]]
    s = trade_stats(trades, 10_000)
    assert s["total_trades"] == 5 and s["winning_trades"] == 2 and s["losing_trades"] == 2
    assert s["win_rate"] == pytest.approx(40.0)
    assert s["gross_profit"] == 300 and s["gross_loss"] == -100
    assert s["profit_factor"] == pytest.approx(3.0)
    assert s["avg_trade"] == pytest.approx(40.0)
    assert s["avg_r"] == pytest.approx(0.4)
    assert s["max_consecutive_losses"] == 1 and s["max_consecutive_wins"] == 1
    assert trade_stats([], 10_000)["profit_factor"] is None


def test_drawdown_and_downsample():
    eq = np.array([10_100, 10_300, 9_900, 10_050, 10_400, 10_000.0])
    d = drawdown_stats(eq, 10_000)
    assert d["max_drawdown"] == pytest.approx(400) and d["max_drawdown_pct"] == pytest.approx(400 / 10_300 * 100)
    t = np.arange(100_000)
    v = np.sin(t / 500.0)
    v[54_321] = -5
    tt, vv = downsample(t, v, 1000)
    assert len(tt) <= 1002 and min(vv) == -5 and tt[0] == 0 and tt[-1] == 99_999
