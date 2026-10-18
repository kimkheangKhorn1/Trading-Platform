"""Stress tests for a tuned setting: fill realism, slippage, neighbouring settings, a fair limit-entry placebo."""
import json, sys
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
import research.reaper.sim as simmod
from app.engine.ta import atr as atr_v
from research.reaper.data import TEST, TRAIN, VALID, market
from research.reaper.events import GenParams, _htf_trend, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import in_window, one_at_a_time, summarize
from research.reaper import tune as T

tf = sys.argv[1] if len(sys.argv) > 1 else 'M1'
best = json.loads((T.OUT / f'tune_{tf}.json').read_text())['best']['config']
m = market(tf); times = m.bid.time

def run(cfg, through=0.0):
    g = GenParams(pivot_len=cfg['pivot_len'], confirm=cfg['confirm'], use_vol=cfg['use_vol'])
    sigs = generate(m, g); sim = Simulator(m)
    rule, plan = T.STOPS[cfg['stop']], EXITS[cfg['plan']]
    trades = []
    for s in sigs:
        ref = s.close if cfg['entry'] == 'market' else s.level
        stop = ref - s.side * rule.mult * s.atr if rule.mode == 'atr' else s.extreme - s.side * rule.buffer * s.atr
        rr = abs(ref - stop)
        if not (rr > 0 and (stop - ref) * s.side < 0):
            trades.append(None); continue
        trades.append(sim.run(s.i, s.side, stop, rr, s.close, plan) if cfg['entry'] == 'market'
                      else sim.run_limit(s.i, s.side, s.level, stop, plan, T.RETEST_EXPIRY, through))
    rows = one_at_a_time(sigs, trades, T.keep_fn(cfg['trend_filter'], cfg['session_filter'], cfg['spread_guard'],
                                                 cfg['stop'], cfg['entry']), m.first)
    return rows, sigs

def line(name, rows):
    p = [summarize(in_window(rows, times, w)) for w in (TRAIN, VALID, TEST)]
    print(f"{name:42} train {p[0]['trades']:4} {p[0]['avg_r']:+.3f} | valid {p[1]['trades']:4} {p[1]['avg_r']:+.3f} | "
          f"test {p[2]['trades']:4} {p[2]['avg_r']:+.3f} net {p[2]['net_r']:+6.1f}R", flush=True)
    return p

print(tf, best)
base_rows, base_sigs = run(best)
line('as tuned (touch fills, slippage 0.05)', base_rows)
if best['entry'] == 'retest':
    for th in (0.05, 0.10, 0.20):
        line(f'limit must trade through by {th:.2f}', run(best, th)[0])
simmod.SLIPPAGE = 0.10
line('slippage doubled to 0.10', run(best)[0]); simmod.SLIPPAGE = 0.05
for key, alts in (('pivot_len', (5, 8, 13)), ('confirm', ('close_back', 'mss')), ('stop', tuple(T.STOPS)),
                  ('plan', T.PLANS), ('entry', T.ENTRIES), ('trend_filter', (False, True)),
                  ('session_filter', (False, True)), ('spread_guard', (False, True)), ('use_vol', (False, True))):
    for a in alts:
        if a != best[key]:
            line(f'neighbour {key}={a}', run({**best, key: a})[0])
# fair placebo for limit entries: random bars, trend side, limit at the same pull-back distance, same stop/plan
if best['entry'] == 'retest':
    at = atr_v(m.bid.high, m.bid.low, m.bid.close, 14); htf = _htf_trend(m); hrs = (times // 3600) % 24
    test_rows = in_window(base_rows, times, TEST)
    d = np.array([(s.close - s.level) * s.side / s.atr for s, _ in test_rows])
    k = np.array([abs(s.level - (s.extreme - s.side * T.STOPS[best['stop']].buffer * s.atr)) / s.atr for s, _ in test_rows])
    idx = np.flatnonzero((times >= TEST[0]) & (times < TEST[1]) & np.isfinite(at) & (htf != 0))
    if best['session_filter']: idx = idx[hrs[idx] < 17]
    rng = np.random.default_rng(3); sim = Simulator(m); res = []
    for _ in range(100):
        taken, free = [], -1
        for i in np.sort(rng.choice(idx, len(test_rows) * 6, replace=False)):
            if i < free: continue
            side = int(htf[i]); q = rng.integers(len(d))
            lvl = m.bid.close[i] - side * d[q] * at[i]; stop = lvl - side * k[q] * at[i]
            if best['spread_guard'] and 0.26 > 0.10 * abs(lvl - stop): continue
            t = sim.run_limit(int(i), side, float(lvl), float(stop), EXITS[best['plan']], T.RETEST_EXPIRY)
            if t is None: continue
            taken.append(t.r); free = t.exit_bar
            if len(taken) == len(test_rows): break
        res.append(np.mean(taken))
    real = summarize(test_rows)['avg_r']
    print(f'fair placebo (random pull-back limits, trend side): mean {np.mean(res):+.3f}, 95th pct {np.percentile(res, 95):+.3f}; '
          f'strategy {real:+.3f} beats {np.mean(np.array(res) < real):.0%} of draws')
mo = {}
for s, t in in_window(base_rows, times, TEST):
    mo.setdefault(int(times[s.i]) // (86400 * 30), []).append(t.r)
