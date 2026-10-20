"""Order block trade signals: N 5m confirmation candles at an order block, then the D/S overlap trade rule."""
import pytest

from app.engine.runner import ScriptRunner
from app.ob_signals import nearest_live, ob_signal_candidates
from app.overlap_signals import overlap_signals
from app.scripts.smc_master import SmcMaster
from app.scripts.smc_master.ob_source import OrderBlockSource
from tests.helpers import random_walk_bars, ts

T = ts('2026-10-05 00:00')
S = 300


def rows(*ohlc):
    return [(T + k * S, o, h, l, c, 1) for k, (o, h, l, c) in enumerate(ohlc)]


def block(side=1, top=100.0, bottom=95.0, known_at=T, ended_at=None, tf='M15', i=1):
    return dict(id=i, timeframe=tf, kind='internal', side=side, top=top, bottom=bottom, origin=T - 3600,
                known_at=known_at, ended_at=ended_at)


FAR = (111, 112, 109, 110)  # bearish, above a bullish block, not touching it
DIP = (102, 103, 99, 99.5)    # wicks into 95-100 and closes inside
UP = (99.5, 103, 98, 102)     # touches and closes bullish above the top
UP2 = (102, 105, 101, 104)    # bullish above the top without touching


def clock(r):
    return r[-1][0] + S


def test_one_confirmation_buys_at_the_close_of_the_candle_that_touches_and_closes_back_above():
    r = rows(FAR, DIP, UP)
    [s], live = ob_signal_candidates(r, [block()], 1, clock(r))
    assert (s['time'], s['close'], s['price'], s['bullish'], s['touched_at']) == (T + 2 * S, 102, 98, True, T + S)
    assert s['lock_id'] == 'ob:M15:1' and s['meta']['ob_timeframe'] == 'M15' and live == []


def test_confirmation_right_after_a_touch_counts_but_not_one_far_from_the_block():
    r = rows(FAR, DIP, UP2)
    assert [s['time'] for s in ob_signal_candidates(r, [block()], 1, clock(r))[0]] == [T + 2 * S]
    r = rows(DIP, FAR, UP2)  # a candle in between: the block was left, no confirmation
    found, live = ob_signal_candidates(r, [block()], 1, clock(r))
    assert found == [] and len(live) == 1


def test_two_confirmations_need_two_in_a_row_and_a_break_restarts_the_count():
    r = rows(DIP, UP, UP2)
    [s], _ = ob_signal_candidates(r, [block()], 2, clock(r))
    assert (s['time'], s['price'], s['patterns']) == (T + 2 * S, 98, ['2 × 5m confirmation'])  # deepest shadow
    r = rows(DIP, UP, (103, 104, 101, 102.5), UP2)  # bearish candle in between, far from the block: no signal
    assert ob_signal_candidates(r, [block()], 2, clock(r))[0] == []
    r = rows(DIP, UP, DIP, UP, UP2)  # back into the block: a new streak
    assert [s['time'] for s in ob_signal_candidates(r, [block()], 2, clock(r))[0]] == [T + 4 * S]


def test_a_close_below_the_block_breaks_it_and_one_signal_per_block():
    r = rows(DIP, (99, 99.5, 93, 94), UP, UP2)
    found, live = ob_signal_candidates(r, [block()], 1, clock(r))
    assert found == [] and live == []
    r = rows(DIP, UP, DIP, UP)
    assert len(ob_signal_candidates(r, [block()], 1, clock(r))[0]) == 1


def test_block_is_used_only_between_known_and_removed_on_its_own_timeframe():
    r = rows(DIP, UP, DIP, UP)
    # known at the close of candle 1: candles from 2 on
    assert [s['time'] for s in ob_signal_candidates(r, [block(known_at=T + 2 * S)], 1, clock(r))[0]] == [T + 3 * S]
    # removed at the close of candle 1: only candles 0-1 (closed by then)
    assert [s['time'] for s in ob_signal_candidates(r, [block(ended_at=T + 2 * S)], 1, clock(r))[0]] == [T + S]
    assert ob_signal_candidates(r, [block(ended_at=T + S)], 1, clock(r))[0] == []
    assert ob_signal_candidates(r, [block(known_at=T + 9 * S)], 1, clock(r)) == ([], [])
    # a floor (replay history start) hides earlier candles
    assert [s['time'] for s in ob_signal_candidates(r, [block()], 1, clock(r), floor=T + 2 * S)[0]] == [T + 3 * S]


def test_future_candles_are_never_read():
    r = rows(DIP, UP)
    assert ob_signal_candidates(r, [block()], 1, T + S + S - 1)[0] == []
    assert len(ob_signal_candidates(r, [block()], 1, T + 2 * S)[0]) == 1


def test_bearish_block_mirrors():
    r = rows((90, 91, 89, 89.5), (93, 96, 92, 95.5), (95.5, 97, 93, 94))
    [s], _ = ob_signal_candidates(r, [block(side=-1, top=100, bottom=95)], 1, clock(r))
    assert (s['time'], s['bullish'], s['side'], s['price'], s['close']) == (T + 2 * S, False, 'supply', 97, 94)


def test_trade_uses_the_overlap_rule_and_keeps_the_block_details():
    r = rows(DIP, UP, (102, 107, 101, 106), (106, 112, 105, 111), (111, 119, 110, 118))
    found, _ = ob_signal_candidates(r, [block()], 1, clock(r))
    [t] = overlap_signals(r, found, 'M5', clock(r))
    # shadow 4 (close 102, low 98) + $3 = 7: TP 109 / 116 / 123
    assert (t['entry'], t['initial_sl'], t['risk'], t['tps'], t['tp_hit']) == (102, 95, 7, [109, 116, 123], 2)
    assert (t['ob_timeframe'], t['ob_kind'], t['confirmations'], t['code']) == ('M15', 'internal', 1, 'OB')


def test_blocks_of_several_timeframes_trade_one_at_a_time():
    r = rows(DIP, UP, UP2)
    found, _ = ob_signal_candidates(r, [block(tf='M15'), block(tf='H1', i=7)], 1, clock(r))
    got = overlap_signals(r, found, 'M5', clock(r))
    assert [(t['ob_timeframe'], t['status']) for t in got] == [('H1', 'open'), ('M15', 'skipped')]


def test_nearest_live_keeps_the_closest_per_timeframe_and_side():
    live = [block(top=t, bottom=t - 2, i=t) for t in (90, 98, 80)] + [block(side=-1, top=130, bottom=120, i=5)]
    assert [b['id'] for b in nearest_live(live, 100, 2)] == [98, 90, 5]


def run_source(bars, params, split=None):
    r = ScriptRunner(OrderBlockSource, params, timeframe='M15')
    if split:
        r.process(bars.slice(0, split))
    r.process(bars)
    return r.script


def test_source_log_matches_the_indicator_pools_and_incremental_runs():
    bars = random_walk_bars(1500, step=900, seed=11)
    p = SmcMaster({}).p
    script = run_source(bars, p)
    ends = {i for i, _, _ in script.ob_ends}
    live = [b for b in script.ob_births if b[0] not in ends]
    assert script.ob_births and script.ob_ends
    held = sorted((z.kind, z.side, z.top, z.bottom, z.origin) for pool in script.obs for z in pool)
    assert sorted(b[1:6] for b in live) == held
    assert all(why in ('broken', 'cap') for _, _, why in script.ob_ends)
    again = run_source(bars, p, split=700)
    assert (again.ob_births, again.ob_ends) == (script.ob_births, script.ob_ends)


def test_unfinished_candle_never_logs_a_block():
    bars = random_walk_bars(800, step=900, seed=5)
    p = SmcMaster({}).p
    done = run_source(bars.slice(0, 799), p)
    bars.complete[-1] = False
    partial = run_source(bars, p)
    assert (partial.ob_births, partial.ob_ends) == (done.ob_births, done.ob_ends)


def service(tmp_path, monkeypatch, blocks):
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    m1 = [(T + i * 60, 102, 103, 101.5, 102.5, 1, 1) for i in range(150)]
    # 5m candle 30: dips into the 95-100 block and closes bullish at 102 (low 98); then a run up to TP3
    m1 += [(T + 150 * 60, 101, 101.5, 98, 99, 1, 1)] + [(T + (151 + k) * 60, 99, 102, 99, 102, 1, 1) for k in range(4)]
    m1 += [(T + (155 + k) * 60, 102, 130, 101.5, 125, 1, 1) for k in range(5)]
    store = MarketStore(tmp_path / 'ob.db')
    store.ensure_feed('f', provider='csv', symbol='XAUUSD', instrument=None, price_type='bid', display_name='Test')
    store.upsert_candles(store.ensure_series('f', 'M1').id, m1)
    m5 = [(t, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], 5, 1)
          for t in range(T, T + 160 * 60, S) for g in [[x for x in m1 if t <= x[0] < t + S]]]
    store.upsert_candles(store.ensure_series('f', 'M5').id, m5)
    svc = BadgeOverlapService(store)
    asked = []
    monkeypatch.setattr(svc, 'order_blocks', lambda feed, tf, as_of, params, anchor=None:
                        asked.append((tf, params['show_iob'], params['show_ob'])) or [b for b in blocks if b['timeframe'] == tf])
    return svc, asked


def test_service_trades_the_selected_timeframes_and_types(tmp_path, monkeypatch):
    blocks = [block(tf='M15'), {**block(tf='H1', i=2), 'kind': 'swing'}, block(tf='H4', i=3, top=200, bottom=190)]
    svc, asked = service(tmp_path, monkeypatch, blocks)
    req = dict(feed_id='f', inputs={'show_iob': False}, timeframes=['M15', 'H1'], confirmations=1)
    got = svc.ob_signals(**req, as_of=T + 160 * 60)
    assert [tf for tf, *_ in asked] == ['M15', 'H1'] and all(a[1] and a[2] for a in asked)
    assert [(t['ob_timeframe'], t['status']) for t in got['signals']] == [('H1', 'closed'), ('M15', 'skipped')]
    t = got['signals'][0]
    assert (t['entry'], t['initial_sl'], t['result_r']) == (102, 95, 3.0)  # low 98 - $3
    only_internal = svc.ob_signals(**req, ob_types=['internal'], as_of=T + 160 * 60)
    assert [t['ob_timeframe'] for t in only_internal['signals']] == ['M15']
    assert svc.ob_signals(**req, chart_timeframe='M15', as_of=T + 160 * 60)['signals'] == []
    before = svc.ob_signals(**req, as_of=T + 155 * 60)
    assert before['signals'][0]['status'] == 'open'


def test_service_zones_are_the_waiting_blocks(tmp_path, monkeypatch):
    svc, _ = service(tmp_path, monkeypatch, [block(tf='H4', i=3, top=90, bottom=85)])
    got = svc.ob_signals(feed_id='f', inputs={'aoi_max_dist': 0}, timeframes=['H4'], as_of=T + 160 * 60)
    assert got['signals'] == [] and [(z['timeframe'], z['top']) for z in got['zones']] == [('H4', 90)]


@pytest.mark.parametrize('bad', [dict(timeframes=[]), dict(timeframes=['D']), dict(confirmations=0),
                                 dict(confirmations=11), dict(confirmations=True), dict(ob_types=['fvg']),
                                 dict(signal_max_risk=0)])
def test_service_rejects_bad_settings(tmp_path, monkeypatch, bad):
    svc, _ = service(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError):
        svc.ob_signals(**{**dict(feed_id='f', inputs={}, timeframes=['M15']), **bad}, as_of=T + 160 * 60)
