"""Reversal signals at overlaps: SL $3 beyond the signal candle shadow (stop at most $15), TP 1R/2R/3R, one at a time."""
import pytest

from app.engine.bars import Bars
from app.overlap_signals import overlap_signals
from tests.helpers import ts

T = ts('2026-10-05 00:00')
S = 300


def bars(*hl, start_close=2000.0):
    """Candle 0 is the signal candle (close = start_close); then (high, low) of the following candles."""
    rows = [(T, start_close, start_close + 1, start_close - 1, start_close, 1, True)]
    rows += [(T + (k + 1) * S, start_close, h, l, start_close, 1, True) for k, (h, l) in enumerate(hl)]
    return Bars.from_rows(rows), T + (len(rows)) * S


def reaction(t=T, bullish=True, kind='rejection', lock='a', close=2000.0, neutral=False, shadow=7.0):
    """shadow: signal candle close to its low (buy) / high (sell); the default gives a $10 stop (shadow + $3)."""
    return dict(time=t, confirmed_at=t + S, timeframe='M5', side='demand' if bullish else 'supply', bottom=1990,
                top=1995, lock_id=lock, code='H' if bullish else 'SS', name='Hammer', bullish=bullish,
                neutral=neutral, reaction=kind, patterns=['Hammer'], close=close, open=close - 2 if bullish else close + 2,
                price=close - shadow if bullish else close + shadow,
                locked_at=t - 3600)


def test_long_runs_to_tp3_with_the_stop_left_in_place():
    # a pullback to TP2 after it was reached no longer ends the trade: the SL stays at 1990
    b, clock = bars((2011, 2001), (2021, 2012), (2025, 2015), (2031, 2021))
    [t] = overlap_signals(b, [reaction()], 'M5', clock)
    assert (t['entry'], t['initial_sl'], t['tps'], t['sl']) == (2000.0, 1990.0, [2010.0, 2020.0, 2030.0], 1990.0)
    assert (t['status'], t['result_r'], t['exit_reason'], t['tp_hit'], t['exit_time']) == ('closed', 3.0, 'TP3', 3, T + 5 * S)


@pytest.mark.parametrize('hl,result,reason,tp_hit', [
    (((2005, 1989),), -1.0, 'SL', 0),                               # SL first: a loss
    (((2011, 2001), (2009, 1989)), 1.0, 'SL after TP1', 1),         # TP1 then SL: +1R
    (((2011, 2001), (2021, 2012), (2015, 1985)), 2.0, 'SL after TP2', 2),
    (((2031, 1989),), -1.0, 'SL', 0),                               # SL and TP3 in one candle, no 1m data: SL first
    (((2012, 1989),), -1.0, 'SL', 0),
])
def test_long_outcomes(hl, result, reason, tp_hit):
    b, clock = bars(*hl)
    [t] = overlap_signals(b, [reaction()], 'M5', clock)
    assert (t['result_r'], t['exit_reason'], t['tp_hit']) == (result, reason, tp_hit)


@pytest.mark.parametrize('minutes,result,reason', [
    ([(2012, 1999), (2031, 2020), (2000, 1989)], 3.0, 'TP3'),       # 1m order: TP3 before the SL
    ([(2021, 1999), (2000, 1989), (2031, 2020)], 2.0, 'SL after TP2'),
    ([(2005, 1989), (2031, 2000)], -1.0, 'SL'),
    ([(2031, 1989)], -1.0, 'SL'),                                   # both inside one minute: SL first
    ([], -1.0, 'SL'),                                               # no 1m data: SL first
])
def test_one_minute_candles_decide_a_candle_touching_sl_and_tp(minutes, result, reason):
    b, clock = bars((2031, 1989),)
    asked = []
    [t] = overlap_signals(b, [reaction()], 'M5', clock, minutes=lambda at: asked.append(at) or minutes)
    assert (t['result_r'], t['exit_reason'], asked) == (result, reason, [T + S])


def test_one_minute_data_only_for_ambiguous_candles():
    b, clock = bars((2011, 2001), (2015, 1995), (2031, 2021))
    [t] = overlap_signals(b, [reaction()], 'M5', clock, minutes=lambda at: pytest.fail('not ambiguous'))
    assert t['result_r'] == 3.0


def test_short_mirrors_long():
    b, clock = bars((1999, 1989), (2011, 1995))
    [t] = overlap_signals(b, [reaction(bullish=False)], 'M5', clock)
    assert (t['side'], t['initial_sl'], t['tps'][0], t['result_r'], t['exit_reason']) == ('short', 2010.0, 1990.0, 1.0, 'SL after TP1')


def test_open_trade_skips_later_signals_until_closed():
    b, clock = bars((2005, 1995), (2005, 1995), (2005, 1989), (2005, 1995))
    rs = [reaction(), reaction(T + S, lock='b'), reaction(T + 3 * S, lock='c'), reaction(T + 4 * S, lock='d')]
    got = overlap_signals(b, rs, 'M5', clock)
    # the candle that closes a trade may open the next one
    assert [t['status'] for t in got] == ['closed', 'skipped', 'open', 'skipped']
    assert got[0]['exit_time'] == T + 4 * S and got[2]['sl'] == 1990.0


def test_only_first_confirmed_reaction_of_a_lock_and_never_neutral_or_other_tf():
    b, clock = bars((2005, 1995), (2005, 1995))
    rs = [reaction(kind='continuation'), reaction(T + S)]
    assert overlap_signals(b, rs, 'M5', clock) == []
    assert overlap_signals(b, [reaction(neutral=True)], 'M5', clock) == []
    assert overlap_signals(b, [reaction()], 'M1', clock) == []
    assert overlap_signals(b, [{**reaction(), 'timeframe': 'M1'}], 'M5', clock) == []
    assert [t['time'] for t in overlap_signals(b, [reaction(kind='unconfirmed'), reaction(T + S)], 'M5', clock)] == [T + S]


def test_future_candles_never_change_an_earlier_view():
    b, clock = bars((2005, 1995), (2005, 1989))
    early = overlap_signals(b, [reaction()], 'M5', T + 2 * S)
    assert early[0]['status'] == 'open' and early[0]['result_r'] is None
    assert overlap_signals(b, [reaction()], 'M5', clock)[0]['result_r'] == -1.0
    b.complete[-1] = False
    assert overlap_signals(b, [reaction()], 'M5', clock)[0]['status'] == 'open'


def service(tmp_path, monkeypatch):
    """A store with a D/S overlap locked at 104-108, then touched by a 5m hammer; returns the service and request."""
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    from app.scripts.smc_master import SmcMaster
    from tests.test_badge_overlap import badge_label
    toggles = ('d_', 'be_', 'hs_', 'dg_', 'tw_', 'st_', 'pc_', 'bh_', 'ls_')
    p = {**SmcMaster({**dict.fromkeys(toggles, False), 'hs_': True}).p, 'aoi_max_dist': 0}
    m1 = [(T + i * 60, 112, 113, 111.5, 112.5, 1, 1) for i in range(150)]
    # first 5m candle of the touch: a hammer into demand 104-108, closing above it; then a rise to TP3 ($9 stop)
    m1 += [(T + 150 * 60, 110, 111.02, 105, 110.5, 1, 1)] + [(T + (151 + k) * 60, 110.5, 111.02, 109, 111, 1, 1) for k in range(4)]
    m1 += [(T + (155 + k) * 60, 112, 145, 111.5, 140, 1, 1) for k in range(5)]
    store = MarketStore(tmp_path / 'signals.db')
    store.ensure_feed('f', provider='csv', symbol='XAUUSD', instrument=None, price_type='bid', display_name='Test')
    store.upsert_candles(store.ensure_series('f', 'M1').id, m1)
    m5 = [(t, rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows), rows[-1][4], 5, 1)
          for t in range(T, T + 160 * 60, S) for rows in [[r for r in m1 if t <= r[0] < t + S]]]
    store.upsert_candles(store.ensure_series('f', 'M5').id, m5)
    svc = BadgeOverlapService(store)
    monkeypatch.setattr(svc, 'source_drawings', lambda *a: ([badge_label(bottom=104, top=108, origin=T - 3600)], T))
    req = dict(feed_id='f', inputs=p, timeframes=['M1', 'M5'], lock_id='x', chart_timeframe='M5', signals=True)
    svc.run(**req, as_of=T + 150 * 60)  # locks the overlap before the touch
    return svc, req


def test_service_returns_signals_for_touched_lock_on_5m(tmp_path, monkeypatch):
    svc, req = service(tmp_path, monkeypatch)
    got = svc.run(**req, as_of=T + 160 * 60)
    [t] = got['signals']
    assert (t['side'], t['entry'], t['initial_sl'], t['status'], t['result_r']) == ('long', 111.0, 102.0, 'closed', 3.0)  # SL = 5m low 105 - $3
    assert 'signal_zones' not in got
    assert svc.run(**{**req, 'signals': False}, as_of=T + 160 * 60)['signals'] == []
    fast = svc.run(**req, as_of=T + 160 * 60, reactions_only=True)
    assert fast['signals'] == got['signals']


def test_order_block_overlap_options_are_gone(tmp_path, monkeypatch):
    svc, req = service(tmp_path, monkeypatch)
    for extra in ({'kind': 'ob'}, {'ob_types': ['swing']}, {'confluence_ob': {'timeframes': ['M1', 'M5']}}):
        with pytest.raises(TypeError):
            svc.run(**req, **extra, as_of=T + 160 * 60)
    got = svc.run(**req, as_of=T + 160 * 60)
    assert 'all_zones' not in got and 'kind' not in got
    assert all('ob_confluence' not in t for t in got['signals'])


@pytest.mark.parametrize('shadow,status,risk', [(0.5, 'closed', 3.5), (7.0, 'closed', 10.0), (12.0, 'closed', 15.0),
                                                (14.0, 'closed', 15.0), (15.0, 'closed', 15.0), (15.01, 'no_trade', 18.01)])
def test_stop_is_shadow_plus_3_reduced_to_stay_at_most_15(shadow, status, risk):
    b, clock = bars((2100, 1980),)
    [t] = overlap_signals(b, [reaction(shadow=shadow)], 'M5', clock)
    assert (t['status'], t['risk'], t['initial_sl']) == (status, risk, round(2000 - risk, 6))
    if status == 'closed':
        assert t['tps'] == [2000 + risk, 2000 + 2 * risk, 2000 + 3 * risk] and t['result_r'] == -1.0
        assert t['risk'] <= 15


def test_too_wide_stop_opens_nothing_and_leaves_the_slot_free():
    b, clock = bars((2005, 1996), (2005, 1996))
    got = overlap_signals(b, [reaction(shadow=16), reaction(T + S, lock='b')], 'M5', clock)
    assert [t['status'] for t in got] == ['no_trade', 'open']
    assert 'tps' not in got[0]


def test_short_stop_is_above_the_high():
    b, clock = bars((2000, 1990),)
    [t] = overlap_signals(b, [reaction(bullish=False, shadow=5)], 'M5', clock)
    assert (t['initial_sl'], t['risk'], t['tps'][0]) == (2008.0, 8.0, 1992.0)
