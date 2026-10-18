"""Tune the improved Liquidity Reaper per timeframe on TRAIN only, check it on VALID, score TEST once.

Selection rule (fixed before looking at any result): among settings with enough training trades, pick the one whose
WORSE training half has the highest average R (consistency over one lucky stretch). VALID decides go / no-go.
TEST (2026) is never used to choose anything."""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper.data import TEST, TRAIN, VALID, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import StopRule, in_window, one_at_a_time, summarize

OUT = Path(__file__).with_name('out')
OUT.mkdir(exist_ok=True)
MID = (TRAIN[0] + TRAIN[1]) // 2
MIN_TRAIN = {'M1': 60, 'M5': 60, 'M15': 60, 'M30': 60, 'H1': 30, 'H4': 15, 'D': 8}
RETEST_EXPIRY = 5  # bars a retest limit order stays open

GENS = [GenParams(pivot_len=p, confirm=c, use_vol=v) for p, c, v in
        itertools.product((5, 8, 13), ('close_back', 'mss'), (True, False))]
STOPS = {'atr1.5': StopRule('atr', 1.5), 'sweep+0.2': StopRule('sweep', buffer=0.2),
         'sweep+0.5': StopRule('sweep', buffer=0.5)}
ENTRIES = ('market', 'retest')
PLANS = ('single_1R', 'single_2R', 'single_3R', 'scaled_123')
FILTERS = list(itertools.product((False, True), (False, True), (False, True)))  # trend, session, spread guard


def trades_for(sim, sigs, stop_name, entry, plan_name):
    rule, plan, out = STOPS[stop_name], EXITS[plan_name], []
    for s in sigs:
        ref = s.close if entry == 'market' else s.level
        stop = ref - s.side * rule.mult * s.atr if rule.mode == 'atr' else s.extreme - s.side * rule.buffer * s.atr
        risk_ref = abs(ref - stop)
        if not (risk_ref > 0 and (stop - ref) * s.side < 0):
            out.append(None)
            continue
        if entry == 'market':
            out.append(sim.run(s.i, s.side, stop, risk_ref, s.close, plan))
        else:
            out.append(sim.run_limit(s.i, s.side, s.level, stop, plan, RETEST_EXPIRY))
    return out


def keep_fn(trend, session, spread_guard, stop_name, entry):
    rule = STOPS[stop_name]

    def keep(s):
        if trend and s.side * s.htf_trend <= 0:
            return False
        if session and s.hour >= 17:
            return False
        if spread_guard:
            ref = s.close if entry == 'market' else s.level
            stop = ref - s.side * rule.mult * s.atr if rule.mode == 'atr' else s.extreme - s.side * rule.buffer * s.atr
            if s.spread > 0.10 * abs(ref - stop):
                return False
        return True
    return keep


def config_name(g, stop, entry, plan, f):
    return dict(pivot_len=g.pivot_len, confirm=g.confirm, use_vol=g.use_vol, stop=stop, entry=entry, plan=plan,
                trend_filter=f[0], session_filter=f[1], spread_guard=f[2])


def tune(tf: str) -> dict:
    t0 = time.time()
    m = market(tf)
    sim = Simulator(m)
    times = m.bid.time
    results = []
    for g in GENS:
        sigs = generate(m, g)
        for stop, entry, plan in itertools.product(STOPS, ENTRIES, PLANS):
            trades = trades_for(sim, sigs, stop, entry, plan)
            for f in FILTERS:
                rows = one_at_a_time(sigs, trades, keep_fn(*f, stop, entry), m.first)
                tr = in_window(rows, times, TRAIN)
                h1 = summarize(in_window(rows, times, (TRAIN[0], MID)))
                h2 = summarize(in_window(rows, times, (MID, TRAIN[1])))
                results.append(dict(config=config_name(g, stop, entry, plan, f), train=summarize(tr),
                                    train_h1=h1, train_h2=h2, valid=summarize(in_window(rows, times, VALID)),
                                    test=summarize(in_window(rows, times, TEST)),
                                    score=min(h1['avg_r'], h2['avg_r']) if min(h1['trades'], h2['trades']) > 0 else -9))
        print(f'  {tf} {g} done {time.time() - t0:.0f}s', flush=True)
    eligible = [r for r in results if r['train']['trades'] >= MIN_TRAIN[tf]]
    best = max(eligible, key=lambda r: r['score']) if eligible else None
    out = dict(tf=tf, combos=len(results), eligible=len(eligible), best=best,
               passed_validation=bool(best and best['valid']['trades'] > 0 and best['valid']['avg_r'] > 0),
               all=results)
    (OUT / f'tune_{tf}.json').write_text(json.dumps(out))
    return out


if __name__ == '__main__':
    for tf in sys.argv[1:] or ('M5', 'M15', 'M30', 'H1', 'H4', 'D', 'M1'):
        r = tune(tf)
        b = r['best']
        if b:
            print(f"{tf}: best {b['config']} | train {b['train']['trades']} tr avgR {b['train']['avg_r']:+.3f} "
                  f"(halves {b['train_h1']['avg_r']:+.3f}/{b['train_h2']['avg_r']:+.3f}) | valid {b['valid']['trades']} tr "
                  f"avgR {b['valid']['avg_r']:+.3f} | passed {r['passed_validation']}", flush=True)
        else:
            print(f'{tf}: no setting with enough trades', flush=True)
