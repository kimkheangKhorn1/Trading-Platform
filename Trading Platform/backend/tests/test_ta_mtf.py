import math

import numpy as np
import pytest

from app.engine import ta
from app.engine.bars import Bars
from app.engine.mtf import LookaheadError, SecurityRequest, evaluate_security, map_htf_index
from app.market.resample import resample
from app.market.timeframes import TIMEFRAMES, bar_close_times
from tests.helpers import random_walk_bars

M5, H1, H4 = TIMEFRAMES["M5"], TIMEFRAMES["H1"], TIMEFRAMES["H4"]


def test_sma_window_and_na():
    out = ta.sma(np.array([1, 2, 3, 4, math.nan, 6, 7, 8.0]), 3)
    assert np.isnan(out[:2]).all()
    assert out[2] == 2 and out[3] == 3
    assert np.isnan(out[4:7]).all()  # window contains na
    assert out[7] == 7


def test_ema_rma_seeded_with_sma_then_recursive():
    x = np.arange(1.0, 11.0)
    e = ta.ema(x, 3)
    assert np.isnan(e[:2]).all() and e[2] == pytest.approx(2.0)
    assert e[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
    r = ta.rma(x, 4)
    assert r[3] == pytest.approx(2.5) and r[4] == pytest.approx(0.25 * 5 + 0.75 * 2.5)


def test_atr_first_bar_uses_high_minus_low():
    h, l, c = np.array([10, 12, 11.0]), np.array([8, 9, 7.0]), np.array([9, 11, 8.0])
    tr = ta.true_range(h, l, c)
    assert tr.tolist() == [2.0, 3.0, 4.0]
    a = ta.atr(h, l, c, 2)
    assert np.isnan(a[0]) and a[1] == pytest.approx(2.5) and a[2] == pytest.approx(0.5 * 4 + 0.5 * 2.5)


def test_streaming_matches_vectorised():
    b = random_walk_bars(500)
    s_ema, s_atr = ta.EMA(20), ta.ATR(14)
    se = [s_ema.update(x) for x in b.close]
    sa = [s_atr.update(h, l, c) for h, l, c in zip(b.high, b.low, b.close)]
    np.testing.assert_allclose(se, ta.ema(b.close, 20), equal_nan=True)
    np.testing.assert_allclose(sa, ta.atr(b.high, b.low, b.close, 14), equal_nan=True)


def test_crossover_definition_includes_equal_previous():
    assert ta.crossover(2, 1, 1.5, 1)  # previous equal counts
    assert not ta.crossover(2, 1.2, 1.5, 1)  # was already above
    assert not ta.crossover(2, math.nan, 1.5, 1)
    assert ta.crossunder(1, 2, 1.5, 2)


def test_highest_lowest():
    h, lo = ta.Highest(3), ta.Lowest(3)
    vals = [3, 1, 4, 1, 5]
    assert [h.update(v) for v in vals][2:] == [4, 4, 5]
    assert [lo.update(v) for v in vals][2:] == [1, 1, 1]


# ------------------------------------------------------------------ request.security
def _chart_and_htf():
    chart = random_walk_bars(2000, step=300, seed=3)
    htf = resample(chart, M5, H1)
    return chart, htf


def test_lookahead_off_only_sees_closed_htf_bars():
    chart, htf = _chart_and_htf()
    idx = map_htf_index(chart.time, M5, htf.time, H1)
    chart_close = bar_close_times(chart.time, M5)
    htf_close = bar_close_times(htf.time, H1)
    for i, k in enumerate(idx):
        if k >= 0:
            assert htf_close[k] <= chart_close[i]  # never a bar that closes later
            if k + 1 < len(htf):
                assert htf_close[k + 1] > chart_close[i]  # and always the latest closed one


def test_lookahead_off_value_changes_on_last_chart_bar_of_period():
    chart, htf = _chart_and_htf()
    req = SecurityRequest("H1", lambda b: b.close)
    vals = evaluate_security(req, chart, M5, htf, H1, "ny17")
    # On a chart bar that closes an hour, the HTF close equals that bar's own close
    closes_hour = (chart.time + 300) % 3600 == 0
    np.testing.assert_allclose(vals[closes_hour][1:], chart.close[closes_hour][1:])
    # On other bars the previous hour's close is shown (never the current hour's final close)
    first_in_hour = np.flatnonzero(chart.time % 3600 == 0)
    for i in first_in_hour[1:50]:
        assert vals[i] == vals[i - 1]


def test_lookahead_guard_and_offset_idiom():
    chart, htf = _chart_and_htf()
    with pytest.raises(LookaheadError):
        SecurityRequest("H1", lambda b: b.close, lookahead=True)
    on1 = evaluate_security(SecurityRequest("H1", lambda b: b.close, lookahead=True, offset=1),
                            chart, M5, htf, H1, "ny17")
    off = evaluate_security(SecurityRequest("H1", lambda b: b.close), chart, M5, htf, H1, "ny17")
    # lookahead_on + [1] shows the previous hour from the first bar of the new hour; it differs from
    # lookahead_off only on the hour's last bar, where lookahead_off already shows the closed hour.
    last_of_hour = (chart.time + 300) % 3600 == 0
    mask = ~last_of_hour & ~np.isnan(on1) & ~np.isnan(off)
    np.testing.assert_allclose(on1[mask], off[mask])


def test_lookahead_on_without_offset_leaks_and_needs_explicit_flag():
    chart, htf = _chart_and_htf()
    leak = evaluate_security(SecurityRequest("H1", lambda b: b.close, lookahead=True, allow_future_leak=True),
                             chart, M5, htf, H1, "ny17")
    first_in_hour = np.flatnonzero(chart.time % 3600 == 0)[5]
    # the first 5m bar of the hour already "knows" the hour's final close -> future data
    assert leak[first_in_hour] == chart.close[first_in_hour + 11]


def test_htf_expression_is_causal_under_truncation():
    chart, _ = _chart_and_htf()
    full_htf = resample(chart, M5, H4)
    req = SecurityRequest("H4", lambda b: ta.ema(b.close, 5))
    full = evaluate_security(req, chart, M5, full_htf, H4, "ny17")
    k = 1234
    part_chart = chart.slice(0, k)
    part_htf = resample(part_chart, M5, H4)
    part = evaluate_security(req, part_chart, M5, part_htf, H4, "ny17")
    np.testing.assert_allclose(part, full[:k], equal_nan=True)


def test_missing_htf_data_gives_na():
    chart, _ = _chart_and_htf()
    vals = evaluate_security(SecurityRequest("H1", lambda b: b.close), chart, M5, Bars.empty(), H1, "ny17")
    assert np.isnan(vals).all()
