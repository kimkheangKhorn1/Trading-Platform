"""Converted TradingView scripts: Liquidity Reaper, Footprint IQ Pro, Triple Confluence Navigator, 3EMA and
Volume Supply and Demand. SYNTHETIC data only."""
from __future__ import annotations

import numpy as np
import pytest

from app.engine.bars import Bars
from app.engine.registry import get_script
from app.engine.runner import ScriptRunner
from app.market.resample import resample
from app.market.timeframes import TIMEFRAMES
from app.scripts.pinelib import (Pivot, binary_search_leftmost, fmt_pattern, fmt_percent, fmt_price, fmt_volume,
                                 from_gradient)
from tests.helpers import bars_from_ohlc, random_walk_bars

SCRIPTS = ['liquidity_reaper_joat', 'footprint_iq_pro', 'triple_confluence_markittick', 'three_ema',
           'volume_supply_demand_tradingiq', 'liquidity_swings_luxalgo', 'liquidity_reaper_pro']


class M1Provider:
    def __init__(self, m1: Bars):
        self.m1 = m1

    def load(self, tf, start, end, warmup):
        src = self.m1 if tf == 'M1' else resample(self.m1, TIMEFRAMES['M1'], TIMEFRAMES[tf], 'utc')
        if tf != 'M1':
            return src  # higher timeframes (trend filters): the whole synthetic history
        t = src.time
        return src.slice(int(np.searchsorted(t, start)), int(np.searchsorted(t, end, side='right')))


@pytest.fixture(scope='module')
def data():
    m1 = random_walk_bars(15 * 1600, step=60, seed=11)
    return m1, resample(m1, TIMEFRAMES['M1'], TIMEFRAMES['M15'], 'utc')


def run(sid, bars, m1, params=None):
    r = ScriptRunner(get_script(sid), params or {}, timeframe='M15', alignment='utc', htf_provider=M1Provider(m1),
                     ticker='XAUUSD')
    r.process(bars)
    return r


def strip_ids(out):
    out = dict(out)
    out['drawings'] = sorted((tuple(sorted((k, str(v)) for k, v in d.items() if k != 'id')) for d in out['drawings']))
    return out


@pytest.mark.parametrize('sid', SCRIPTS)
def test_incremental_and_live_updates_equal_full_run(sid, data):
    m1, bars = data
    full = run(sid, bars, m1).output()
    r = ScriptRunner(get_script(sid), {}, timeframe='M15', alignment='utc', htf_provider=M1Provider(m1),
                     ticker='XAUUSD')
    n = len(bars)
    for stop in list(range(200, n, 237)) + [n]:
        # a still-forming copy of the next bar first, then the confirmed bars: forming states must roll back
        if stop < n:
            part = bars.slice(0, stop + 1)
            comp = part.complete.copy()
            comp[-1] = False
            r.process(Bars(part.time, part.open, part.high, part.low, part.close, part.volume, comp))
        r.process(bars.slice(0, stop))
    assert strip_ids(r.output()) == strip_ids(full)


@pytest.mark.parametrize('sid', SCRIPTS)
def test_no_lookahead(sid, data):
    """Everything created on or before a bar is identical whether or not later bars exist."""
    m1, bars = data
    cut = 1100
    a = run(sid, bars.slice(0, cut), m1).output()
    b = run(sid, bars, m1).output()
    t_cut = int(bars.time[cut - 1])
    for key in a['plots']:
        assert a['plots'][key] == b['plots'][key][:cut]
    for key in a['shapes']:
        assert a['shapes'][key] == [s for s in b['shapes'][key] if s['time'] <= t_cut]
    labels = lambda out: sorted((d['x'], d['y'], d['text']) for d in out['drawings']
                                if d['kind'] == 'label' and d['created_at'] <= t_cut and d['group'] in ('signals',))
    assert labels(a) == labels(b)


# ---------------------------------------------------------------- Liquidity Reaper
def reaper_rows():
    """A swing high at 110 (8 lower bars each side), a wick to 112 that closes back at 108, then a red close below."""
    rows = [(100, 101, 99, 100)] * 30
    rows += [(100, 102, 99, 101)] * 8 + [(101, 110, 100, 105)] + [(104, 105, 100, 103)] * 8
    rows += [(103, 105, 101, 104)] * 5
    rows += [(104, 112, 103, 108)]  # raid bar: high > 110; range 9, wick beyond the pool 2 (22%) ...
    rows += [(108, 113, 106, 107)]  # ... extreme 113 -> 3 / 9 = 33% >= 25%, red close below 110: sweep
    rows += [(107, 108, 105, 106)] * 5
    return rows


def test_reaper_confirms_sell_sweep_with_zone_and_targets():
    bars = bars_from_ohlc(reaper_rows(), step=900)
    vol = np.full(len(bars), 100.0)
    vol[-6] = 300.0  # volume expansion on the confirming bar
    bars = Bars(bars.time, bars.open, bars.high, bars.low, bars.close, vol, bars.complete)
    r = ScriptRunner(get_script('liquidity_reaper_joat'), {}, timeframe='M15', alignment='utc', ticker='XAUUSD')
    r.process(bars)
    out = r.output()
    sells = [d for d in out['drawings'] if d['kind'] == 'label' and d['text'].startswith('RAID SELL')]
    assert len(sells) == 1 and sells[0]['x'] == int(bars.time[-6]) and sells[0]['text'] == 'RAID SELL · 2.7x'
    zone = [d for d in out['drawings'] if d['kind'] == 'box' and d['group'] == 'zones']
    assert len(zone) == 1 and zone[0]['top'] == 113 and zone[0]['bottom'] == 110
    rail = [d for d in out['drawings'] if d['kind'] == 'line' and d['group'] == 'rails' and d['y1'] == 110]
    assert rail[0]['extend'] == 'none' and rail[0]['x2'] == int(bars.time[-7])  # cut at the raid bar
    entry = [d for d in out['drawings'] if d['kind'] == 'label' and d['text'].startswith('SELL ')]
    assert entry and entry[0]['text'] == 'SELL 107.000'
    table = out['tables'][0]
    assert table['rows'][0][2]['text'] == 'XAUUSD · 15'
    assert table['rows'][7][1]['text'] == '0 / 1'
    assert table['rows'][10][1]['text'] in ('● SHORT ACTIVE', '○ FLAT')


def test_reaper_volume_filter_blocks_signals_without_volume():
    bars = bars_from_ohlc(reaper_rows(), step=900)  # volume 0: volMA 0 -> 1.0x, below 1.1x
    r = ScriptRunner(get_script('liquidity_reaper_joat'), {}, timeframe='M15', alignment='utc')
    r.process(bars)
    assert not [d for d in r.output()['drawings'] if d['kind'] == 'label' and 'RAID' in d['text']]
    r = ScriptRunner(get_script('liquidity_reaper_joat'), {'useVolFilter': False}, timeframe='M15', alignment='utc')
    r.process(bars)
    assert [d for d in r.output()['drawings'] if d['kind'] == 'label' and d['text'].startswith('RAID SELL · 1x')]


# ---------------------------------------------------------------- Liquidity Swings
def test_liquidity_swings_counts_candles_into_the_swing_area():
    """Pivot high at 110 (body top 104, lookback 3); three later candles trade into 104-110, then a close above."""
    rows = [(100, 101, 99, 100)] * 5 + [(101, 103, 100, 102), (102, 110, 101, 104), (104, 103, 100, 101)]
    rows += [(101, 102, 99, 100)] * 2
    rows += [(100, 105, 99, 101), (101, 106, 100, 102), (102, 107, 101, 103), (103, 112, 102, 111)]
    rows += [(111, 112, 109, 110)] * 4
    bars = bars_from_ohlc(rows, step=900)
    bars = Bars(bars.time, bars.open, bars.high, bars.low, bars.close, np.full(len(bars), 10.0), bars.complete)
    r = ScriptRunner(get_script('liquidity_swings_luxalgo'), {'length': 3}, timeframe='M15', alignment='utc')
    r.process(bars)
    out = r.output()
    pivot_t = int(bars.time[6])
    zone = [d for d in out['drawings'] if d['kind'] == 'box' and d['left'] == pivot_t and d['bg_color'] == '#F2364580']
    assert len(zone) == 1 and zone[0]['top'] == 110 and zone[0]['bottom'] == 104
    level = [d for d in out['drawings'] if d['kind'] == 'line' and d['y1'] == 110]
    assert level and level[0]['style'] == 'dashed' and level[0]['x2'] == int(bars.time[13])  # cut at the close above
    assert level[0]['color'] == '#F23645FF'
    label = [d for d in out['drawings'] if d['kind'] == 'label' and d['y'] == 110]
    assert label and label[0]['style'] == 'label_down'


def test_liquidity_swings_hidden_side_draws_nothing(data):
    m1, bars = data
    for side in ('showTop', 'showBtm'):
        out = run('liquidity_swings_luxalgo', bars, m1, {side: False}).output()
        stray = [d for d in out['drawings'] if d['kind'] == 'box' and d['left'] == int(bars.time[0])]
        assert all(d['bg_color'].endswith('00') for d in stray)


def test_liquidity_swings_warns_on_unusable_intrabar_timeframe(data):
    m1, bars = data
    out = run('liquidity_swings_luxalgo', bars, m1, {'intraPrecision': True, 'intrabarTf': '60'}).output()
    assert out['warnings'] and 'intrabar timeframe' in out['warnings'][0]


# ---------------------------------------------------------------- engine: intrabars
def test_intrabars_group_one_minute_candles_inside_each_chart_bar(data):
    m1, bars = data
    r = ScriptRunner(get_script('three_ema'), {}, timeframe='M15', alignment='utc', htf_provider=M1Provider(m1))
    h3 = r.register_lower_tf(180)
    r.process(bars.slice(0, 50))
    ib = r.intrabars(h3, 10).bars
    assert len(ib) == 5 and int(ib.time[0]) == int(bars.time[10])
    lo = int(np.searchsorted(m1.time, bars.time[10]))
    assert ib.volume[0] == m1.volume[lo:lo + 3].sum() and ib.high[1] == m1.high[lo + 3:lo + 6].max()
    assert ib.close[-1] == bars.close[10] and ib.open[0] == bars.open[10]


# ---------------------------------------------------------------- Pine helpers
def test_pine_number_formats():
    assert fmt_pattern(2.34, '#.#') == '2.3' and fmt_pattern(2.0, '#.#') == '2' and fmt_pattern(2.25, '#.#') == '2.3'
    assert fmt_pattern(1.5, '0.0') == '1.5' and fmt_pattern(3, '0.0') == '3.0' and fmt_pattern(-0.004, '#.##') == '0'
    assert fmt_pattern(99.6, '##0') == '100'
    assert fmt_price(2650.1234, 3) == '2650.123' and fmt_price(5, 3) == '5.000'
    assert fmt_volume(1234.5) == '1.23K' and fmt_volume(-2_500_000) == '-2.5M' and fmt_volume(12.345) == '12.35'
    assert fmt_percent(12.3456) == '12.35%' and fmt_percent(float('nan')) == 'NaN'


def test_gradient_pivot_and_search():
    assert from_gradient(5, 0, 10, '#000000FF', '#FFFFFF00') == '#808080' + '80'
    assert from_gradient(20, 0, 10, '#000000FF', '#FFFFFFFF') == '#FFFFFFFF'
    p = Pivot(2, 2, True)
    vals = [p.update(x) for x in [1, 3, 5, 4, 2, 5, 5, 1, 0]]
    assert vals[4] == 5 and np.isnan(vals[7])  # equal highs: a later equal value on the right disqualifies
    assert binary_search_leftmost([-2, 0, 1, 5, 9], 3) == 2 and binary_search_leftmost([-2, 0, 1, 1, 9], 1) == 2


# ---------------------------------------------------------------- Liquidity Reaper Pro (research-backed improvements)
@pytest.mark.parametrize('confirm', ['Close back (original)', 'Structure shift (MSS)'])
def test_reaper_pro_signals_match_the_research_engine(data, monkeypatch, confirm):
    import research.reaper.events as ev
    from research.reaper.data import Market
    m1, bars = data
    monkeypatch.setattr(ev, '_htf_trend', lambda m: np.zeros(len(m.bid), dtype=int))
    sigs = ev.generate(Market('M15', bars, bars, m1, m1, 0),
                       ev.GenParams(pivot_len=5, confirm=ev.GenParams().confirm if confirm.startswith('Close') else 'mss',
                                    use_vol=False))
    r = ScriptRunner(get_script('liquidity_reaper_pro'), {'preset': 'Custom', 'pivotLen': 5, 'confirm': confirm,
                                                          'useVolFilter': False, 'showTargets': False},
                     timeframe='M15', alignment='utc', htf_provider=M1Provider(m1))
    r.process(bars)
    labels = sorted((d['x'], 1 if 'BUY' in d['text'] else -1) for d in r.output()['drawings']
                    if d['kind'] == 'label' and d['text'].startswith('RAID'))
    assert labels and labels == sorted((int(bars.time[s.i]), s.side) for s in sigs)


def test_reaper_pro_presets_load_and_emit_strategy_orders(data):
    from app.scripts.liquidity_reaper_pro import PRESETS, preset_inputs, LiquidityReaperPro
    assert set(PRESETS) == {'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D'}
    for tf, p in PRESETS.items():
        LiquidityReaperPro(preset_inputs(p['config']))  # every preset is a valid input set
        assert isinstance(p['validated'], bool) and {'train', 'valid', 'test'} <= set(p)
    m1, bars = data
    r = ScriptRunner(get_script('liquidity_reaper_pro'), {'preset': 'Custom', 'useVolFilter': False,
                                                          'entryMode': 'Limit on retest of the swept level',
                                                          'stopMode': 'Beyond sweep + 0.5 ATR', 'plan': '1R'},
                     timeframe='M15', alignment='utc', htf_provider=M1Provider(m1))
    r.process(bars)
    sig = r.output()['signals']
    assert sig and all(s['order_type'] == 'limit' and s['expiry_bars'] == 5 for s in sig)
    assert all((s['stop_loss'] < s['entry']) == (s['side'] == 'long') for s in sig)


def test_reaper_4h_trend_filter_keeps_only_raids_with_the_last_completed_4h_trend():
    """Same sweeps as the research engine, minus those against the 4H EMA50/EMA200 of the last 4H candle that had
    closed by the signal candle's close (no lookahead)."""
    import research.reaper.events as ev
    from research.reaper.data import Market
    from app.engine.ta import ema
    m1 = random_walk_bars(90 * 1440, step=60, seed=5, skip_weekends=False)  # enough 4H candles for an EMA200
    bars = resample(m1, TIMEFRAMES['M1'], TIMEFRAMES['M15'], 'utc')
    sigs = ev.generate(Market('M15', bars, bars, m1, m1, 0), ev.GenParams(pivot_len=5, min_wick_pct=10.0, use_vol=False))
    h4 = resample(m1, TIMEFRAMES['M1'], TIMEFRAMES['H4'], 'utc')
    sign = np.where(np.isfinite(ema(h4.close, 200)), np.sign(ema(h4.close, 50) - ema(h4.close, 200)), 0)
    def trend(i):
        k = np.searchsorted(h4.time + 14400, bars.time[i] + 900, side='right') - 1
        return sign[k] if k >= 0 else 0
    params = {'pivotLen': 5, 'minWickPct': 10.0, 'useVolFilter': False, 'show_targets': False}
    labels = lambda p: sorted((d['x'], 1 if 'BUY' in d['text'] else -1) for d in run('liquidity_reaper_joat', bars, m1, p).output()['drawings']
                              if d['kind'] == 'label' and d['text'].startswith('RAID'))
    assert labels(params) == sorted((int(bars.time[s.i]), s.side) for s in sigs)  # filter off: unchanged
    kept = sorted((int(bars.time[s.i]), s.side) for s in sigs if s.side * trend(s.i) > 0)
    assert kept and len(kept) < len(sigs) and labels({**params, 'trendFilter': True}) == kept


def test_reaper_adx_filter_and_stop_beyond_the_sweep(data):
    import research.reaper.events as ev
    from research.reaper.data import Market
    from app.engine.ta import ATR
    from app.scripts.smc_master.technical import DMI
    m1, bars = data
    sigs = ev.generate(Market('M15', bars, bars, m1, m1, 0), ev.GenParams(pivot_len=3, min_wick_pct=25.0, use_vol=False, confirm_window=5))
    dmi, atr = DMI(14, 14), ATR(14)
    adx = [dmi.update(h, l, c)[2] for h, l, c in zip(bars.high, bars.low, bars.close)]
    atrs = [atr.update(h, l, c) for h, l, c in zip(bars.high, bars.low, bars.close)]
    params = {'pivotLen': 3, 'minWickPct': 25.0, 'useVolFilter': False, 'confirmWindow': 5, 'adxFilter': True,
              'sl_beyond_sweep': True}
    out = run('liquidity_reaper_joat', bars, m1, params).output()['drawings']
    labels = sorted((d['x'], 1 if 'BUY' in d['text'] else -1) for d in out if d['kind'] == 'label' and d['text'].startswith('RAID'))
    kept = [s for s in sigs if adx[s.i] >= 25]
    assert kept and len(kept) < len(sigs) and labels == sorted((int(bars.time[s.i]), s.side) for s in kept)
    last = kept[-1]  # the levels on the chart belong to the last signal: stop 0.2 ATR beyond its sweep wick
    sl = next(d for d in out if d['kind'] == 'label' and d['text'].startswith('SL '))
    assert sl['text'] == f"SL {last.extreme - last.side * 0.2 * atrs[last.i]:.3f}"


@pytest.mark.parametrize('mode', ['Best in 2026', 'Most consistent 2024-2026'])
def test_reaper_timeframe_preset_equals_its_settings_and_warns_where_untested(data, mode):
    from app.scripts.liquidity_reaper import TF_PRESETS
    m1, bars = data
    preset = run('liquidity_reaper_joat', bars, m1, {'tfPresetMode': mode}).output()
    explicit = run('liquidity_reaper_joat', bars, m1, TF_PRESETS[mode]['M15']).output()
    assert strip_ids(preset)['drawings'] == strip_ids(explicit)['drawings'] and not preset['warnings']
    r = ScriptRunner(get_script('liquidity_reaper_joat'), {'tfPresetMode': mode}, timeframe='M1', alignment='utc',
                     htf_provider=M1Provider(m1))
    r.process(m1.slice(0, 3000))
    out = r.output()
    assert out['warnings'] and '1m lost money' in out['warnings'][0]
    plain = ScriptRunner(get_script('liquidity_reaper_joat'), {}, timeframe='M1', alignment='utc', htf_provider=M1Provider(m1))
    plain.process(m1.slice(0, 3000))
    assert strip_ids(out)['drawings'] == strip_ids(plain.output())['drawings']  # 1m: the user's inputs are used


def test_reaper_engulf_and_delta_filters_match_the_research_inputs(data):
    import research.reaper.events as ev
    from research.reaper.data import Market
    m1, bars = data
    sigs = ev.generate(Market('M15', bars, bars, m1, m1, 0), ev.GenParams(pivot_len=3, min_wick_pct=10.0, use_vol=False, confirm_window=5))
    o, c = bars.open, bars.close
    lo = np.searchsorted(m1.time, bars.time); hi = np.searchsorted(m1.time, bars.time + 900)
    sv = np.where(m1.close > m1.open, m1.volume, np.where(m1.close < m1.open, -m1.volume, 0.0))
    def delta(i):
        tot = m1.volume[lo[i]:hi[i]].sum()
        return sv[lo[i]:hi[i]].sum() / tot if tot > 0 else np.nan
    def engulf(s):
        i = s.i; a, b = min(o[i - 1], c[i - 1]), max(o[i - 1], c[i - 1])
        return (c[i] >= b and o[i] <= a) if s.side == 1 else (c[i] <= a and o[i] >= b)
    kept = sorted((int(bars.time[s.i]), s.side) for s in sigs if engulf(s) and delta(s.i) * s.side > 0)
    out = run('liquidity_reaper_joat', bars, m1, {'pivotLen': 3, 'minWickPct': 10.0, 'useVolFilter': False, 'confirmWindow': 5,
                                                  'engulfFilter': True, 'deltaFilter': True}).output()['drawings']
    labels = sorted((d['x'], 1 if 'BUY' in d['text'] else -1) for d in out if d['kind'] == 'label' and d['text'].startswith('RAID'))
    assert kept and len(kept) < len(sigs) and labels == kept


# ---------------------------------------------------------------- REAPER ML filter (app/scripts/reaper_ml.py)
def test_reaper_ml_recording_does_not_change_signals_and_rows_follow_the_features(data):
    from app.scripts.liquidity_reaper import LiquidityReaper
    from app.scripts.reaper_ml import FEATURES
    m1, bars = data

    class Rec(LiquidityReaper):
        collect = True

    params = {'pivotLen': 3, 'useVolFilter': False, 'minWickPct': 10.0}
    plain = run('liquidity_reaper_joat', bars, m1, params).output()
    r = ScriptRunner(Rec, params, timeframe='M15', alignment='utc', htf_provider=M1Provider(m1))
    r.process(bars)
    assert strip_ids(r.output())['drawings'] == strip_ids(plain)['drawings']  # recording changes nothing
    rows = r.script.signal_rows
    labels = sorted((d['x'], 1 if 'BUY' in d['text'] else -1) for d in plain['drawings'] if d['kind'] == 'label' and d['text'].startswith('RAID'))
    assert rows and sorted((int(bars.time[i]), s) for i, s, _ in rows) == labels
    for i, side, x in rows:
        assert len(x) == len(FEATURES) and all(np.isfinite(v) or np.isnan(v) for v in x)
        f = dict(zip(FEATURES, x))
        assert 1 <= f['bars_since_raid'] <= 3 and f['close_from_level'] > 0 and f['risk_atr'] > 0
        assert f['hour'] == (int(bars.time[i]) // 3600) % 24


def test_reaper_ml_model_evaluators_and_the_missing_model_warning(data, tmp_path, monkeypatch):
    import app.scripts.reaper_ml as ml
    feats = list(ml.FEATURES)
    zero = [0.0] * len(feats)
    logistic = ml.Model(dict(kind='logistic', features=feats, threshold=0.5, fill=zero, mean=zero, std=[1.0] * len(feats),
                             coef=[1.0] + [0.0] * (len(feats) - 1), intercept=0.0))
    assert abs(logistic.prob([2.0] + zero[1:]) - 1 / (1 + np.exp(-2))) < 1e-12
    tree = dict(feature=[0, 0, 0], threshold=[1.0, 0, 0], left=[1, 0, 0], right=[2, 0, 0], leaf=[False, True, True],
                missing_left=[True, False, False], value=[0.0, -1.0, 2.0])
    trees = ml.Model(dict(kind='trees', features=feats, threshold=0.5, baseline=0.5, trees=[tree]))
    assert abs(trees.prob([0.5] + zero[1:]) - 1 / (1 + np.exp(0.5))) < 1e-12  # left leaf: 0.5 - 1
    assert abs(trees.prob([float('nan')] + zero[1:]) - 1 / (1 + np.exp(0.5))) < 1e-12  # missing goes left
    forest = ml.Model(dict(kind='forest', features=feats, threshold=0.5, fill=[3.0] + zero[1:],
                           trees=[dict(feature=[0, -2, -2], threshold=[1.0, -2, -2], left=[1, -1, -1], right=[2, -1, -1], value=[0.3, 0.2, 0.8])]))
    assert forest.prob([0.0] + zero[1:]) == 0.2 and forest.prob([float('nan')] + zero[1:]) == 0.8  # NaN -> fill 3.0
    monkeypatch.setattr(ml, 'MODEL_PATH', tmp_path / 'none.json')
    m1, bars = data
    out = run('liquidity_reaper_joat', bars, m1, {'mlFilter': True}).output()
    assert any('no trained model' in w for w in out['warnings'])
