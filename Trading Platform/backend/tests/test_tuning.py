"""Tuner search space / search strategy, trade scoring and the one-trade-at-a-time rule. SYNTHETIC data only."""
from __future__ import annotations

import pytest

from app.engine.registry import get_script
from app.engine.runner import ScriptRunner
from app.market.resample import resample
from app.market.timeframes import TIMEFRAMES
from app.trade_score import summarize, trade_r
from app.tuning import Search, TuningError, TuningRequest, build_space
from tests.helpers import random_walk_bars

TC = 'triple_confluence_markittick'


def test_trade_scores():
    win = dict(open_time=0, hits=3, outcome='tp3')
    sl_after_two = dict(open_time=0, hits=2, outcome='sl')
    sl_clean = dict(open_time=0, hits=0, outcome='sl')
    # the indicator's rule: +1R per TP, -1R at the stop
    assert [trade_r(t, 'rule') for t in (win, sl_after_two, sl_clean)] == [3, 1, -1]
    # real P&L holding everything to TP3 / stop
    assert [trade_r(t, 'hold') for t in (win, sl_after_two, sl_clean)] == [3, -1, -1]
    # a third closed at each TP: (1 + 2) / 3 banked, the last third stopped
    assert trade_r(sl_after_two, 'split') == pytest.approx(1 - 1 / 3)
    s = summarize([win, sl_after_two, sl_clean], 0, 10)
    assert (s['trades'], s['wins'], s['losses'], s['tp1'], s['tp2'], s['tp3'], s['rule']) == (3, 1, 2, 2, 2, 1, 3)


def test_space_counts_and_validation():
    cls = get_script(TC)
    dims = build_space(cls, [dict(id='rsiLen', min=2, max=14, step=1), dict(id='maType', options=['RMA', 'Kalman'])])
    assert [len(d.values) for d in dims] == [13, 2]
    assert Search(dims, 200, 1).total == 26
    with pytest.raises(TuningError):
        build_space(cls, [dict(id='rsiLen', min=1, max=14, step=1)])  # below the input's minimum (2)
    with pytest.raises(TuningError):
        build_space(cls, [dict(id='colEntry', options=['#000000FF'])])  # not a tunable setting
    with pytest.raises(TuningError):
        TuningRequest.from_dict(dict(feed_id='f', timeframe='M15', script_id=TC, start=10, end=5, ranges=[]))


def test_small_space_is_tested_exhaustively_without_repeats():
    dims = build_space(get_script(TC), [dict(id='swingLen', min=3, max=6, step=1), dict(id='stFactor', min=1, max=2, step=0.5)])
    s = Search(dims, 50, 7)
    assert s.exhaustive
    assert s.total == 12  # 4 swing lengths × 3 factors
    seen = [s.propose([]) for _ in range(s.total + 1)]
    assert seen[-1] is None and len(set(seen[:-1])) == s.total  # every combination once, then nothing left


def test_large_space_explores_then_refines_near_the_best():
    dims = build_space(get_script(TC), [dict(id='rsiLen', min=2, max=50, step=1), dict(id='arsiLen', min=2, max=50, step=1)])
    s = Search(dims, 100, 3)
    assert not s.exhaustive
    picks = [s.propose([]) for _ in range(s.explore_n)]
    assert len(set(picks)) == len(picks)
    best = (20, 20)
    near = [s.propose([best]) for _ in range(40)]
    close = [c for c in near if abs(c[0] - 20) <= 3 and abs(c[1] - 20) <= 3]
    assert len(close) >= 25  # mostly small steps around the best, some random jumps


def test_one_trade_at_a_time_and_records():
    m1 = random_walk_bars(15 * 3000, step=60, seed=5)
    bars = resample(m1, TIMEFRAMES['M1'], TIMEFRAMES['M15'], 'utc')
    r = ScriptRunner(get_script(TC), {}, timeframe='M15', alignment='utc', ticker='XAUUSD')
    r.process(bars)
    closed = r.script.closed
    assert closed
    for a, b in zip(closed, closed[1:]):
        assert b['open_time'] >= a['close_time']  # the next trade starts only after the previous one ended
    for t in closed:
        assert t['outcome'] in ('tp3', 'sl') and t['r'] == trade_r(t, 'rule')
    labels = [d for d in r.output()['drawings'] if d['kind'] == 'label' and d['group'] == 'signals']
    # every BUY / SELL label is a trade (closed, or the one still running)
    assert len(labels) == len(closed) + int(r.script.tradeActive)


def test_tuner_trades_start_inside_the_period():
    m1 = random_walk_bars(15 * 3000, step=60, seed=5)
    bars = resample(m1, TIMEFRAMES['M1'], TIMEFRAMES['M15'], 'utc')
    start = int(bars.time[1500])
    cls = type('T', (get_script(TC),), {'drawing': False, 'trade_from': start})
    r = ScriptRunner(cls, {}, timeframe='M15', alignment='utc', ticker='XAUUSD')
    r.process(bars)
    assert r.script.closed and all(t['open_time'] >= start for t in r.script.closed)
