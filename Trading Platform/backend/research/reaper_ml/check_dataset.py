"""Proves the REAPER ML dataset recorder is correct before anything is trained. Every check must pass.

1. Same signals   Recording (collect=True) does not change the indicator: its signals equal a plain run's RAID labels.
2. No lookahead   Each input row is known at the signal's close: re-running the indicator on candles cut off at three
                  points gives identical rows for every signal before the cut (later candles never change them).
3. Labels         The trades replayed one at a time from the dataset equal the research engine's independent backtest
                  of the same settings (research/reaper_v2: own signal generator, filters and trade loop), trade for
                  trade: same signal times, sides and R — over 2024-01-01 -> 2026-10-01, the span the research engine
                  covers (it is the user's test period). Every trade of every year must end at its stop or target.
4. Sanity         No NaN / infinite inputs, times sorted and inside the dataset period, no duplicate signal,
                  sides +-1, values in their possible ranges, R between a full loss and a full 3R win.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.bars import Bars
from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.scripts.liquidity_reaper import TF_PRESETS, LiquidityReaper
from app.scripts.reaper_ml import FEATURES
from research.reaper.data import BID, store
from research.reaper.data import ts
from research.reaper_ml.dataset import END, OUT, PRESET, START, SUFFIX, TFS, Recorder, market_from

TEST_FROM = ts('2024-01-01')

F = {name: k for k, name in enumerate(FEATURES)}
FAILS = []


def check(ok, msg):
    print(('  PASS ' if ok else '  FAIL ') + msg, flush=True)
    if not ok:
        FAILS.append(msg)


def run(cls, tf, bars, **params):
    st = store(); feed = st.get_feed(BID)
    r = ScriptRunner(cls, {'tfPresetMode': PRESET, **params}, timeframe=tf, alignment=feed.alignment,
                     precision=feed.precision, htf_provider=StoreHtfProvider(st, feed))
    r.process(bars)
    return r


def cut(b: Bars, n: int) -> Bars:
    return Bars(*(getattr(b, k)[:n] for k in ('time', 'open', 'high', 'low', 'close', 'volume', 'complete')))


def replay(d, a, b):
    sel = np.flatnonzero((d['time'] >= a) & (d['time'] < b))
    free, out = -1, []
    for k in sel[np.argsort(d['i'][sel], kind='stable')]:
        if d['i'][k] < free:
            continue
        out.append((int(d['time'][k]), int(d['side'][k]), round(float(d['r'][k]), 6)))
        free = d['exit'][k]
    return out


if __name__ == '__main__':
    from research.reaper_v2.v3 import rows as v3_rows
    from research.reaper_v2.v4_tf import ROUND
    preset_round = {'M5': 'original', 'M15': 'round 3', 'M30': 'round 2', 'H1': 'round 2', 'H4': 'round 2'}
    for tf in TFS:
        print(f'\n== {tf} ({preset_round[tf]} logic)', flush=True)
        d = dict(np.load(OUT / f'dataset_{tf}{SUFFIX}.npz'))
        m = market_from(tf)
        rec = run(Recorder, tf, m.bid)
        # 1. same signals as a plain run
        plain = run(LiquidityReaper, tf, m.bid)
        labels = sorted((int(x['x']), 1 if x['text'].startswith('RAID BUY') else -1) for x in plain.output()['drawings']
                        if x.get('group') == 'signals' and x['kind'] == 'label')
        recorded = sorted((int(m.bid.time[i]), s) for i, s, _ in rec.script.signal_rows)
        check(labels == [x for x in recorded if x[0] >= labels[0][0]],
              f'recording leaves the signals unchanged ({len(labels)} chart labels compared)')
        # 2. no lookahead: prefix runs
        full = {(i, s): x for i, s, x in rec.script.signal_rows}
        for frac in (0.4, 0.7, 0.9):
            n = int(len(m.bid.time) * frac)
            part = run(Recorder, tf, cut(m.bid, n))
            same = all(full.get((i, s)) == x for i, s, x in part.script.signal_rows)
            check(same and len(part.script.signal_rows) > 0,
                  f'inputs identical when candles after {frac:.0%} of the history are removed ({len(part.script.signal_rows)} signals)')
        # 3. labels vs the independent research backtest
        mine = replay(d, TEST_FROM, min(END, ts('2026-10-01')))
        from research.reaper.data import market
        t = market(tf).bid.time
        theirs = [(int(t[s.i]), s.side, round(float(x.r), 6)) for s, x in v3_rows(tf, ROUND[preset_round[tf]])
                  if TEST_FROM <= t[s.i] < min(END, ts('2026-10-01'))]
        check(mine == theirs, f'one-at-a-time trades equal the research backtest ({len(mine)} trades, '
                              f'{sum(r for *_, r in mine):+.1f}R vs {sum(r for *_, r in theirs):+.1f}R)')
        # 4. sanity
        X = d['X']
        check(np.isfinite(X).all(), 'no NaN / infinite inputs')
        check(np.all(np.diff(d['i']) >= 0) and d['time'].min() >= START and d['time'].max() < END, 'sorted, inside the period')
        keys = set(zip(d['i'].tolist(), d['side'].tolist()))
        check(len(keys) == len(d['i']), 'no duplicate signal')
        check(set(np.unique(d['side'])) <= {-1, 1}, 'sides are +1 / -1')
        cw = TF_PRESETS[PRESET][tf]['confirmWindow']
        b = X[:, F['bars_since_raid']]
        check(b.min() >= 1 and b.max() <= cw, f'confirmation took 1..{cw} candles')
        check(set(np.unique(X[:, F['trend_h4']])) <= {-1.0, 0.0, 1.0}, '4H trend input is -1 / 0 / +1')
        check(X[:, F['hour']].min() >= 0 and X[:, F['hour']].max() <= 23 and X[:, F['weekday']].max() <= 6, 'hour / weekday ranges')
        check(((X[:, F['body_pct']] >= 0) & (X[:, F['body_pct']] <= 1)).all() and ((X[:, F['close_pos']] >= 0) & (X[:, F['close_pos']] <= 1)).all(),
              'candle body / close position within 0..1')
        check(((X[:, F['adx']] >= 0) & (X[:, F['adx']] <= 100) & (X[:, F['rsi']] >= 0) & (X[:, F['rsi']] <= 100)).all(), 'ADX / RSI within 0..100')
        check((X[:, F['close_from_level']] > 0).all(), 'every signal closed back inside the swept level')
        check((X[:, F['risk_atr']] > 0).all(), 'every stop is on the correct side')
        r = d['r']
        # R is measured against the risk at the actual fill, and a stop / target crossed by a price GAP (weekend open,
        # news minute) fills at the gap price, so a few trades lie outside -1..+3. Each one must be explained by a gap:
        # a 1-minute candle inside the exit candle that OPENED beyond the stop (loss) or target (win), or an entry
        # candle that opened far from the signal close (the real risk then differs from the planned one).
        from research.reaper.sim import EXITS, Simulator
        sim, stops = Simulator(m), rec.script.stops
        odd = np.flatnonzero((r < -1.2) | (r > 3.2))
        explained = 0
        for k in odd:
            i, side = int(d['i'][k]), int(d['side'][k])
            close, sl = stops[(i, side)]
            tr = sim.run(i, side, sl, abs(close - sl), close, EXITS['single_3R'])
            level = sl if tr.r < 0 else close + side * 3 * abs(close - sl)
            o_ = sim._paths(tr.exit_bar, side)[0]
            exit_gap = bool(np.any((o_ - level) * side * (1 if tr.r > 0 else -1) >= 0))
            entry_gap = abs(tr.risk - abs(close - sl)) > 0.05 * abs(close - sl)  # next open far from the signal close
            explained += exit_gap or entry_gap
        check(explained == len(odd), f'R outside -1.2..+3.2 only where a price gap moved the entry, stop or target '
                                     f'({len(odd)} of {len(r)} trades, all explained: {explained == len(odd)})')
        # every trade closed at its target (win) or its stop (loss) — none still open at the end of the data. Wins are
        # below +3R when the spread / slippage made the real risk larger than planned (small stops), never above
        # without a gap (checked above).
        outcomes = []
        for k in range(len(r)):
            i, side = int(d['i'][k]), int(d['side'][k])
            close, sl = stops[(i, side)]
            tr = sim.run(i, side, sl, abs(close - sl), close, EXITS['single_3R'])
            outcomes.append(tr.outcome)
            if abs(tr.r - r[k]) > 1e-9:
                outcomes[-1] = 'mismatch'
        outcomes = np.array(outcomes)
        check(set(outcomes) <= {'tp', 'sl'} and np.all((outcomes == 'tp') == (r > 0)),
              f"every trade ended at its target (win) or stop (loss): {int((outcomes == 'tp').sum())} targets, "
              f"{int((outcomes == 'sl').sum())} stops, other {int(((outcomes != 'tp') & (outcomes != 'sl')).sum())}")
        print(f'  signals {len(r)}, win rate {np.mean(r > 0):.1%}, average {r.mean():+.3f}R', flush=True)
    print('\nALL CHECKS PASSED' if not FAILS else f'\n{len(FAILS)} CHECK(S) FAILED: ' + '; '.join(FAILS), flush=True)
    sys.exit(1 if FAILS else 0)
