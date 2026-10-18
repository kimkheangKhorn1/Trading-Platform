"""Weakness analysis of the original signals: which signal properties go with worse outcomes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from research.reaper.data import TFS, TRAIN, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import StopRule, standalone

def table(name, keyf, rows, buckets):
    print(f'  {name}:', end='')
    for label, test in buckets:
        sel = [r for s, r in rows if test(keyf(s))]
        if len(sel) >= 15:
            print(f'  {label} n={len(sel)} avgR={np.mean(sel):+.2f}', end='')
    print()

for tf in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4'):
    m = market(tf); sigs = generate(m, GenParams()); sim = Simulator(m)
    base = standalone(sim, sigs, StopRule('atr', 1.5), EXITS['single_3R'])
    alt = standalone(sim, sigs, StopRule('sweep', buffer=0.3), EXITS['single_3R'])
    t = m.bid.time
    keep = [(s, b, a) for s, b, a in zip(sigs, base, alt) if b and a and TRAIN[0] <= t[s.i] < TRAIN[1]]  # tuning data only
    rows = [(s, b.r) for s, b, a in keep]
    print(f'== {tf}: {len(rows)} train signals, original avgR {np.mean([r for _, r in rows]):+.3f}')
    inside = [(s, b.r) for s, b, a in keep if (s.extreme - (s.close - s.side * 1.5 * s.atr)) * s.side < 0]
    print(f'  ATR stop sits inside the swept wick (closer than the sweep extreme): {len(inside)}/{len(rows)} '
          f'= {len(inside)/len(rows):.0%}, their avgR {np.mean([r for _, r in inside]) if inside else 0:+.2f}')
    print(f'  same signals, stop beyond the sweep +0.3 ATR: avgR {np.mean([a.r for _, _, a in keep]):+.3f}')
    sp = np.array([b.spread_r for _, b, _ in keep])
    print(f'  spread cost per trade: median {np.median(sp):.2f}R  (p90 {np.percentile(sp, 90):.2f}R)')
    table('H4 trend', lambda s: s.side * s.htf_trend, rows, [('with', lambda x: x > 0), ('against', lambda x: x < 0)])
    table('TF EMA50/200', lambda s: s.side * np.sign(s.ema50 - s.ema200), rows,
          [('with', lambda x: x > 0), ('against', lambda x: x < 0)])
    table('entry distance from level (ATR)', lambda s: (s.close - s.level) * s.side / s.atr, rows,
          [('<0.3', lambda x: x < .3), ('0.3-0.7', lambda x: .3 <= x < .7), ('>=0.7', lambda x: x >= .7)])
    table('sweep depth (ATR)', lambda s: (s.extreme - s.level) * -s.side / s.atr, rows,
          [('<0.2', lambda x: x < .2), ('0.2-0.5', lambda x: .2 <= x < .5), ('>=0.5', lambda x: x >= .5)])
    table('pool age (bars)', lambda s: s.pool_age, rows,
          [('<20', lambda x: x < 20), ('20-60', lambda x: 20 <= x < 60), ('>=60', lambda x: x >= 60)])
    table('pools swept at once', lambda s: s.swept, rows, [('1', lambda x: x == 1), ('2+', lambda x: x >= 2)])
    table('volume x', lambda s: s.vol_mult, rows, [('<1.5', lambda x: x < 1.5), ('1.5-2.5', lambda x: 1.5 <= x < 2.5), ('>=2.5', lambda x: x >= 2.5)])
    table('session UTC', lambda s: s.hour, rows, [('Asia 0-7', lambda x: x < 7), ('London 7-12', lambda x: 7 <= x < 12),
          ('NY 12-17', lambda x: 12 <= x < 17), ('late 17-24', lambda x: x >= 17)])
