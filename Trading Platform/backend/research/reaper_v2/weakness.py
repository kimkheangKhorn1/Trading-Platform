"""Where the original REAPER [JOAT] signals go wrong, measured on TRAIN + VALID (2024-01 -> 2026-01, before the
user's 2026 window). Original rules and trade plan: market entry next open, 1.5 ATR stop, 3R target, one at a time."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.ta import ema
from research.reaper.data import TRAIN, VALID, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import StopRule, one_at_a_time, standalone
from research.reaper_v2.context import record

OUT = Path(__file__).with_name('out')
A, B = TRAIN[0], VALID[1]


def grp(rows, f, labels):
    out = {}
    for lab, test in labels:
        r = [t.r for s, t, x in rows if test(f(s, x))]
        if r:
            out[lab] = dict(n=len(r), win=float(np.mean([v > 0 for v in r])), avg_r=float(np.mean(r)))
    return out


def run(tf):
    m = market(tf); b = m.bid; c, h, l, t = b.close, b.high, b.low, b.time
    sigs = generate(m, GenParams()); sim = Simulator(m)
    trades = standalone(sim, sigs, StopRule('atr', 1.5), EXITS['single_3R'])
    taken = one_at_a_time(sigs, trades, lambda s: True, m.first)
    k = record(tf) if tf != 'D' else None
    e50, e100, e200 = ema(c, 50), ema(c, 100), ema(c, 200)
    rows = []
    for s, tr in taken:
        if not A <= t[s.i] < B or s.i + 51 >= len(c):
            continue
        x = dict(fwd20=s.side * (c[s.i + 20] - c[s.i]) / s.atr, fwd50=s.side * (c[s.i + 50] - c[s.i]) / s.atr,
                 inside=(s.extreme - (s.close - s.side * 1.5 * s.atr)) * s.side < 0,
                 mss=(s.close > h[s.raid_bar]) if s.side == 1 else (s.close < l[s.raid_bar]),
                 ema3=(e50[s.i] > e100[s.i] > e200[s.i]) if s.side == 1 else (e50[s.i] < e100[s.i] < e200[s.i]),
                 best=((b.high[tr.entry_bar:tr.exit_bar + 1].max() - tr.entry) if s.side == 1 else (tr.entry - m.ask.low[tr.entry_bar:tr.exit_bar + 1].min())) / tr.risk)
        if k is not None:
            span = slice(s.raid_bar, s.i + 1); bull = s.side == 1
            x['smc_trend'] = k['itrend'][s.i] == s.side
            x['zone'] = any(k[f][span].any() for f in (('bull_ob', 'demand', 'pivot_sup') if bull else ('bear_ob', 'supply', 'pivot_res')))
        rows.append((s, tr, x))
    r = np.array([tr.r for _, tr, _ in rows])
    losers = [x for _, tr, x in rows if tr.r <= 0]
    res = dict(n=len(rows), win=float((r > 0).mean()), avg_r=float(r.mean()),
               right_way_20=float(np.mean([x['fwd20'] > 0 for _, _, x in rows])),
               right_way_50=float(np.mean([x['fwd50'] > 0 for _, _, x in rows])),
               fwd50_atr=float(np.mean([x['fwd50'] for _, _, x in rows])),
               stop_inside_wick=float(np.mean([x['inside'] for _, _, x in rows])),
               spread_r=float(np.median([tr.spread_r for _, tr, _ in rows])),
               losers_were_plus_1r=float(np.mean([x['best'] >= 1 for x in losers])),
               groups=dict(
                   stop=grp(rows, lambda s, x: x['inside'], [('stop inside wick', bool), ('stop beyond wick', lambda v: not v)]),
                   confirmation=grp(rows, lambda s, x: x['mss'], [('closed beyond raid candle (MSS)', bool), ('close back only', lambda v: not v)]),
                   h4_trend=grp(rows, lambda s, x: s.side * s.htf_trend, [('with H4 trend', lambda v: v > 0), ('against H4 trend', lambda v: v < 0)]),
                   ema3=grp(rows, lambda s, x: x['ema3'], [('3EMA stacked with trade', bool), ('not stacked', lambda v: not v)]),
                   session=grp(rows, lambda s, x: s.hour, [('Asia 0-7', lambda v: v < 7), ('London 7-12', lambda v: 7 <= v < 12), ('NY 12-17', lambda v: 12 <= v < 17), ('late 17-24', lambda v: v >= 17)]),
                   entry_chase=grp(rows, lambda s, x: (s.close - s.level) * s.side / s.atr, [('entry <0.5 ATR from level', lambda v: v < .5), ('entry >=0.5 ATR from level', lambda v: v >= .5)])))
    if k is not None:
        res['groups']['smc_trend'] = grp(rows, lambda s, x: x['smc_trend'], [('with SMC internal trend', bool), ('against', lambda v: not v)])
        res['groups']['zone'] = grp(rows, lambda s, x: x['zone'], [('sweep at OB / D/S / pivot zone', bool), ('no zone', lambda v: not v)])
    return res


if __name__ == '__main__':
    out = {}
    for tf in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4'):
        d = out[tf] = run(tf)
        print(f"\n== {tf} n={d['n']} win={d['win']:.0%} avgR={d['avg_r']:+.3f} | right way after 20/50 bars {d['right_way_20']:.1%}/{d['right_way_50']:.1%} "
              f"(fwd50 {d['fwd50_atr']:+.2f} ATR) | stop inside wick {d['stop_inside_wick']:.0%} | spread {d['spread_r']:.2f}R | losers +1R first {d['losers_were_plus_1r']:.0%}", flush=True)
        for g, v in d['groups'].items():
            print(f"   {g:12s}", ' | '.join(f"{a}: n={x['n']} win={x['win']:.0%} avg={x['avg_r']:+.3f}" for a, x in v.items()))
    (OUT / 'weakness.json').write_text(json.dumps(out, indent=1))
