"""Liquidity Reaper research simulator (SYNTHETIC candles): fills, spread, stop/target order, gaps, breakeven."""
import numpy as np
import pytest

from app.engine.bars import Bars
from research.reaper.data import Market
from research.reaper.sim import EXITS, SLIPPAGE, Exit, Simulator

T0 = 1_767_225_600  # 2026-01-01


def m5_market(m1_rows, spread=0.2):
    """M5 bars aggregated from 1-minute bid rows (o, h, l, c); ask = bid + spread."""
    rows = np.asarray(m1_rows, dtype=float)
    t1 = T0 + np.arange(len(rows)) * 60
    m1b = Bars.from_arrays(t1, rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3])
    m1a = Bars.from_arrays(t1, rows[:, 0] + spread, rows[:, 1] + spread, rows[:, 2] + spread, rows[:, 3] + spread)
    k = len(rows) // 5
    agg = lambda r: [r[0, 0], r[:, 1].max(), r[:, 2].min(), r[-1, 3]]
    m5 = np.array([agg(rows[5 * q:5 * q + 5]) for q in range(k)])
    t5 = T0 + np.arange(k) * 300
    bid = Bars.from_arrays(t5, *m5.T)
    ask = Bars.from_arrays(t5, *(m5.T + spread))
    return Market('M5', bid, ask, m1b, m1a, 0)


FLAT = [(100, 100.1, 99.9, 100)] * 5


def test_long_target_fill_uses_ask_entry_and_bid_exit():
    rows = FLAT + [(100, 100.2, 99.9, 100.1)] * 5 + [(100.1, 103.5, 100, 103)] * 5
    sim = Simulator(m5_market(rows))
    t = sim.run(0, 1, stop=98.0, risk_ref=2.0, close_ref=100.0, plan=EXITS['single_1R'])
    entry = 100 + 0.2 + SLIPPAGE  # bar 1 open at the ask
    assert t.entry == pytest.approx(entry) and t.outcome == 'tp' and t.exit_bar == 2
    assert t.r == pytest.approx((102.0 - entry) / (entry - 98.0))  # target 102 on the bid


def test_stop_and_target_in_one_bar_resolved_by_minutes():
    up_first = [(100, 102.5, 100, 102)] + [(102, 102, 97, 97.5)] + [(97.5, 98, 97.5, 98)] * 3
    down_first = [(100, 100, 97, 97.5)] + [(97.5, 102.5, 97.5, 102)] + [(102, 102, 102, 102)] * 3
    for path, outcome in ((up_first, 'tp'), (down_first, 'sl')):
        sim = Simulator(m5_market(FLAT + FLAT + path))
        t = sim.run(0, 1, stop=98.0, risk_ref=2.0, close_ref=100.0, plan=EXITS['single_1R'])
        assert t.outcome == outcome and t.exit_bar == 2
    same_minute = [(100, 102.5, 97, 100)] + [(100, 100, 100, 100)] * 4
    t = Simulator(m5_market(FLAT + FLAT + same_minute)).run(0, 1, 98.0, 2.0, 100.0, EXITS['single_1R'])
    assert t.outcome == 'sl'  # both inside one minute: the worse case


def test_gap_through_stop_fills_at_the_open():
    rows = FLAT + FLAT + [(95, 95.5, 94.5, 95)] * 5
    t = Simulator(m5_market(rows)).run(0, 1, 98.0, 2.0, 100.0, EXITS['single_1R'])
    assert t.outcome == 'sl' and t.r == pytest.approx((95 - SLIPPAGE - t.entry) / t.risk)


def test_scaled_exit_moves_stop_to_breakeven_after_tp1():
    rows = FLAT + FLAT + [(100, 102.3, 100, 102)] + [(102, 102, 100.0, 100.1)] * 4
    t = Simulator(m5_market(rows)).run(0, 1, 98.0, 2.0, 100.0, EXITS['scaled_123'])
    assert t.tp_hits == [True, False, False] and t.outcome == 'partial+be'
    expected = (102 - t.entry) / t.risk / 3 + (2 / 3) * (t.entry - SLIPPAGE - t.entry) / t.risk
    assert t.r == pytest.approx(expected)


def test_short_pays_spread_on_exit():
    rows = FLAT + FLAT + [(100, 100, 97.5, 98)] * 5
    t = Simulator(m5_market(rows, spread=0.3)).run(0, -1, 102.0, 2.0, 100.0, Exit('x', ((1.0, 1.0),)))
    # target 98 must be reached by the ask (bid 97.7)
    assert t.outcome == 'tp' and t.r == pytest.approx((t.entry - 98.0) / (102.0 - t.entry))


def test_limit_retest_entry_manages_from_the_fill_minute():
    # signal on bar 0; bar 1 rises first (no fill), bar 2 dips to the limit 99.5 in its 3rd minute, then rallies
    rows = FLAT + [(100, 100.4, 100, 100.3)] * 5 + [(100.3, 100.3, 100, 100), (100, 100, 99.8, 99.9),
                                                    (99.9, 99.9, 99.2, 99.4), (99.4, 101.5, 99.4, 101.4),
                                                    (101.4, 101.6, 101.3, 101.5)]
    sim = Simulator(m5_market(rows, spread=0.2))
    t = sim.run_limit(0, 1, price=99.5, stop=98.5, plan=EXITS['single_1R'], expiry=3)
    # buy limit fills when the ask (bid + 0.2) reaches 99.5: minute 3 of bar 2 (ask low 99.4)
    assert t.entry == pytest.approx(99.5) and t.entry_bar == 2 and t.outcome == 'tp'
    assert t.r == pytest.approx((100.5 - 99.5) / 1.0)
    # never retested within the expiry: no trade
    assert sim.run_limit(0, 1, price=95.0, stop=94.0, plan=EXITS['single_1R'], expiry=2) is None
