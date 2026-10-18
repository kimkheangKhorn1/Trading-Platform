"""Round 4: the best logic PER TIMEFRAME (each timeframe may use different inputs).

For one timeframe, start from the round-2 setting and try every one-step change: add any of the 100+ inputs, remove
an input, change stop / entry / exit, toggle MSS. Swing strength / volume filter / min wick / window are re-picked so
TRAIN trades per day stay within +-20% of the original's on THIS timeframe.
A change is accepted only if: TRAIN avg R better, VALID avg R better, TRAIN+VALID net R better, and (filters) it beats
80% of random filters of the same strength on TRAIN+VALID. Top 5 by TRAIN are checked per round.
2026 (Jan 1 -> Oct 1) is printed, never used to decide.

Then the timeframe's final logic is chosen among {original, round 1, round 2, round 3, this per-timeframe search}
by TRAIN+VALID (2024-01 -> 2026-01) net R, and only after that is 2026 looked at.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper_v2.v3 import (COUNT_GRID, OUT, PLACEBO_N, PLACEBO_Q, TOP_K, TV, Cfg, bars3, changes, keep_of,
                                   one_at_a_time, rows, stats, target_train, window)

ROUND = {
    'original': Cfg(),
    'round 1': Cfg(pivot=5, vol=True, wick=10.0, cw=3, feats=('4H trend with trade',)),
    'round 2': Cfg(pivot=3, vol=False, wick=25.0, cw=5, feats=('4H trend with trade', 'ADX >= 25 (trending)'),
                   plan=('sweep+0.2', 'market', 'single_3R')),
    'round 3': Cfg(pivot=3, vol=False, wick=10.0, cw=5, feats=('engulfs the previous candle body', 'footprint delta with trade on signal candle')),
}


def in_band(tf, c):
    return abs(stats(tf, c, 'train')['trades'] / target_train(tf) - 1) <= 0.20


def repick(tf, c):
    ok = []
    for p, v, w, cw in COUNT_GRID:
        c2 = replace(c, pivot=p, vol=v, wick=w, cw=cw)
        if in_band(tf, c2):
            ok.append((stats(tf, c2, 'train')['avg_r'], c2))
    return max(ok, key=lambda x: x[0]) if ok else None


def tv_net(tf, c):
    return window(rows(tf, c), tf, TV)['net_r']


def placebo_pct(tf, cur, cand):
    real = window(rows(tf, cand), tf, TV)['avg_r']
    base = replace(cand, feats=cur.feats)
    k0, k1 = keep_of(tf, base), keep_of(tf, cand)
    rate = k1.sum() / max(k0.sum(), 1)
    beats = 0
    for seed in range(PLACEBO_N):
        rnd = np.random.default_rng(2000 + seed).random(len(k0)) < rate
        beats += real > window(one_at_a_time(tf, base.gen, base.plan, k0 & rnd), tf, TV)['avg_r']
    return beats / PLACEBO_N


def all_changes(cur):
    out = changes(cur)
    out += [(f'remove: {n}', replace(cur, feats=tuple(x for x in cur.feats if x != n)), False) for n in cur.feats]
    return out


def t26(tf, c):
    d = stats(tf, c, 'test')
    return f"{d['net_r']:+.1f}R/{d['trades']}"


def search(tf):
    cur = ROUND['round 2']
    if not in_band(tf, cur):
        b = repick(tf, cur)
        cur = b[1] if b else cur
    tr, va, tot = stats(tf, cur, 'train')['avg_r'], stats(tf, cur, 'valid')['avg_r'], tv_net(tf, cur)
    print(f"[{tf}] start round 2: TRAIN {tr:+.3f} VALID {va:+.3f} TRAIN+VALID {tot:+.1f}R | 2026 {t26(tf, cur)}", flush=True)
    rnd = 0
    while True:
        rnd += 1
        res = []
        for name, c, is_filter in all_changes(cur):
            b = repick(tf, c)
            if b:
                res.append(dict(name=name, cfg=b[1], is_filter=is_filter, train=b[0], valid=stats(tf, b[1], 'valid')['avg_r']))
        ranked = sorted(res, key=lambda r: -r['train'])
        acc = None
        for r in ranked[:TOP_K]:
            why = []
            if r['train'] <= tr: why.append('TRAIN not better')
            if r['valid'] <= va: why.append('VALID not better')
            tn = tv_net(tf, r['cfg'])
            if tn <= tot: why.append(f'TRAIN+VALID {tn:+.1f}R not above {tot:+.1f}R')
            pp = placebo_pct(tf, cur, r['cfg']) if r['is_filter'] and not why else None
            if pp is not None and pp < PLACEBO_Q: why.append(f'beats only {pp:.0%} of random filters')
            print(f"[{tf}] round {rnd} check '{r['name']}': TRAIN {r['train']:+.3f} VALID {r['valid']:+.3f} TRAIN+VALID {tn:+.1f}R"
                  + (f", beats {pp:.0%} random" if pp is not None else '') + f" | 2026 {t26(tf, r['cfg'])}"
                  + (' -> ACCEPTED' if not why else ' -> rejected (' + '; '.join(why) + ')'), flush=True)
            if not why:
                acc = r
                break
        if not acc:
            break
        cur, tr, va, tot = acc['cfg'], acc['train'], acc['valid'], tv_net(tf, acc['cfg'])
        print(f"[{tf}] ACCEPTED -> {cur.short()}", flush=True)
    return cur


if __name__ == '__main__':
    tf = sys.argv[1]
    t0 = time.time()
    bars3(tf)
    tuned = search(tf) if tf != 'M1' else None  # 1m: too slow to search; the fixed settings are compared
    options = dict(ROUND)
    if tuned is not None:
        options[f'{tf} search'] = tuned
    table = {}
    for name, c in options.items():
        band = in_band(tf, c)
        table[name] = dict(cfg=c.__dict__, in_band=band, train_valid=tv_net(tf, c), test=stats(tf, c, 'test'))
    eligible = {k: v for k, v in table.items() if v['in_band']}
    chosen = max(eligible, key=lambda k: eligible[k]['train_valid']) if eligible else 'original'
    print(f"\n[{tf}] CHOICE by 2024-2025 net R (trade count within the band):", flush=True)
    for name, v in table.items():
        t = v['test']
        print(f"[{tf}]   {'*' if name == chosen else ' '} {name:12s} count ok={v['in_band']!s:5s} 2024-25 {v['train_valid']:+7.1f}R | "
              f"2026 trades={t['trades']} wins={t['wins']} losses={t['losses']} netR={t['net_r']:+.1f} PF={t['pf']:.2f}", flush=True)
    print(f"[{tf}] CHOSEN: {chosen} -> {options[chosen].short()}  ({time.time() - t0:.0f}s)", flush=True)
    (OUT / f'v4_{tf}.json').write_text(json.dumps(dict(tf=tf, chosen=chosen, cfg=options[chosen].__dict__, table=table), indent=1, default=str))
