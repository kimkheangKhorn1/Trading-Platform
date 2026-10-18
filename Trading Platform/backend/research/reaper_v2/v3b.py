"""Round 3b: the round-3 search, started from the round-2 setting (4H trend + ADX >= 25 + stop beyond the sweep),
with one more acceptance check added because round 3 showed the score can mislead: the mean of per-timeframe
average R weighs 30 1H trades like 400 5m trades, so round 3 accepted filters that lost money in total.

Added check 5: total net R over 5m/15m/30m/1H on TRAIN+VALID must also improve. Everything else as v3.py.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper_v2.v3 import (ALL_TFS, OUT, PLACEBO_Q, TOP_K, TUNE_TFS, TV, WINDOWS, Cfg, bars3, breadth, changes,
                                   fmt, in_band, placebo_pct, repick, rows, score, stats, test_line, window)

START = Cfg(pivot=3, vol=False, wick=25.0, cw=5, feats=('4H trend with trade', 'ADX >= 25 (trending)'),
            plan=('sweep+0.2', 'market', 'single_3R'))


def total_tv(c):
    return sum(window(rows(tf, c), tf, TV)['net_r'] for tf in TUNE_TFS)


if __name__ == '__main__':
    for tf in ALL_TFS:
        bars3(tf)
    cur = START
    assert in_band(cur)
    cur_tr, cur_va, cur_tot = score(cur, 'train'), score(cur, 'valid'), total_tv(cur)
    print(f"START round-2 setting: TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f} total TRAIN+VALID {cur_tot:+.1f}R | 2026 {fmt(test_line(cur))}", flush=True)
    log = dict(start=dict(cfg=cur.__dict__), rounds=[])
    rnd = 0
    while True:
        rnd += 1
        res = []
        for name, c, is_filter in changes(cur):
            b = repick(c)
            if b is None:
                continue
            tr, c2 = b
            res.append(dict(name=name, cfg=c2, is_filter=is_filter, train=tr, valid=score(c2, 'valid'), test=test_line(c2)))
        ranked = sorted(res, key=lambda r: -r['train'])
        print(f"\n===== ROUND {rnd}: current TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f} total {cur_tot:+.1f}R =====", flush=True)
        for r in ranked[:25]:
            print(f"  {r['name'][:66]:66s} TRAIN {r['train']:+.3f} VALID {r['valid']:+.3f} | 2026 {fmt(r['test'])}", flush=True)
        accepted = None
        for r in ranked[:TOP_K]:
            why = []
            if r['train'] <= cur_tr: why.append('TRAIN not better')
            if r['valid'] <= cur_va: why.append('VALID not better')
            br = breadth(cur, r['cfg'])
            if br < 3: why.append(f'better on only {br}/4 timeframes')
            tot = total_tv(r['cfg'])
            if tot <= cur_tot: why.append(f'total net R {tot:+.1f} not above {cur_tot:+.1f}')
            pp = placebo_pct(cur, r['cfg']) if r['is_filter'] and not why else None
            if pp is not None and pp < PLACEBO_Q: why.append(f'beats only {pp:.0%} of random filters')
            print(f"  check '{r['name']}': breadth {br}/4, total {tot:+.1f}R" + (f", beats {pp:.0%} of random filters" if pp is not None else '')
                  + (' -> ACCEPTED' if not why else ' -> rejected (' + '; '.join(why) + ')'), flush=True)
            if not why:
                accepted = r
                break
        log['rounds'].append(dict(round=rnd, top=[dict(name=r['name'], train=r['train'], valid=r['valid'], test=r['test']) for r in ranked[:25]],
                                  accepted=accepted and accepted['name']))
        if not accepted:
            print(f"ROUND {rnd}: no change passes all checks -> done", flush=True)
            break
        cur, cur_tr, cur_va, cur_tot = accepted['cfg'], accepted['train'], accepted['valid'], total_tv(accepted['cfg'])
        print(f"ROUND {rnd}: ACCEPTED '{accepted['name']}'\n  now: {cur.short()}", flush=True)
    print(f"\nFINAL: {cur.short()}", flush=True)
    for tf in ALL_TFS:
        d, o = stats(tf, cur, 'test'), stats(tf, START, 'test')
        print(f"  {tf:3s} 2026 trades={d['trades']:4d} wins={d['wins']} losses={d['losses']} win={d['win_rate']:.0%} netR={d['net_r']:+.1f} "
              f"PF={d['pf']:.2f} maxDD={d['max_dd_r']:.1f}R | round-2 netR={o['net_r']:+.1f} ({o['trades']})", flush=True)
    log['final'] = dict(cfg=cur.__dict__)
    (OUT / 'v3b.json').write_text(json.dumps(log, indent=1, default=str))
