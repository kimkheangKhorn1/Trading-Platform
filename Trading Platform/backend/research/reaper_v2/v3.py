"""REAPER [JOAT] round 3: every input from every indicator in the project, one change at a time, with stricter
checks so a change is only kept when it is unlikely to be luck. Starts again from the ORIGINAL indicator, so the
round-1/2 choices (4H trend, ADX, stop beyond the sweep) must win their place again under these rules.

Protocol (fixed before running):
* Trades exactly as the user's backtest: one at a time, signal on the close, entry at the next open on Exness bid /
  ask (+0.05 slippage), stop / target decided by 1-minute candles, stop first if both in the same minute.
* Windows: TRAIN 2024-01-01 -> 2025-07-01 (choose), VALID 2025-07-01 -> 2026-01-01 (check),
  TEST 2026-01-01 -> 2026-10-01 (the user's window; reported only).
* One setting for all timeframes. Score = mean average R per trade over 5m / 15m / 30m / 1H. 1m and 4H reported.
* Trade count: for every candidate the signal settings (swing strength 3/5/8, volume filter on/off, min wick
  0/10/25 %, confirmation window 3/5) are re-picked so that TRAIN trades per trading day on 5m/15m/30m/1H stay
  within +-20% of the original's (the original gives the user's counts: 2832 / 622 / 227 / 114 / 49 / 13).
* A change is ACCEPTED only if all hold:
    1. TRAIN score better than the current setting's;
    2. VALID score better than the current setting's;
    3. breadth: average R on TRAIN+VALID improves on at least 3 of the 4 timeframes;
    4. not luck (filters only): its TRAIN+VALID score beats 80% of 30 random filters that drop the same share of
       signals on each timeframe.
  Each round tries every candidate, ranks them by TRAIN score and accepts the first of the top 5 that passes.
  No candidate passing = done.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper.data import market, ts
from research.reaper.events import GenParams
from research.reaper.study import summarize
from research.reaper.tune import trades_for
from research.reaper_v2.features3 import bars3, candidates3
from research.reaper_v2.gen2 import generate2
from research.reaper_v2.steps import sim

OUT = Path(__file__).with_name('out')
WINDOWS = dict(train=(ts('2024-01-01'), ts('2025-07-01')), valid=(ts('2025-07-01'), ts('2026-01-01')),
               test=(ts('2026-01-01'), ts('2026-10-01')))
TUNE_TFS = ('M5', 'M15', 'M30', 'H1')
ALL_TFS = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4')
USER = dict(M1=2832, M5=622, M15=227, M30=114, H1=49, H4=13)
BAND, TOP_K, PLACEBO_N, PLACEBO_Q = 0.20, 5, 30, 0.80
COUNT_GRID = [(p, v, w, cw) for p in (3, 5, 8) for v in (True, False) for w in (0.0, 10.0, 25.0) for cw in (3, 5)]
CANDS = candidates3()


@dataclass(frozen=True)
class Cfg:
    pivot: int = 8
    vol: bool = True
    wick: float = 25.0
    cw: int = 3
    confirm: str = 'close_back'
    feats: tuple = ()
    plan: tuple = ('atr1.5', 'market', 'single_3R')

    @property
    def gen(self):
        return (self.pivot, self.vol, self.wick, self.cw, self.confirm)

    def short(self):
        return (f"swing {self.pivot}, volume filter {'on' if self.vol else 'off'}, wick>={self.wick:g}%, window {self.cw}, "
                f"{self.confirm}, stop {self.plan[0]}, entry {self.plan[1]}, exit {self.plan[2]}, filters: {', '.join(self.feats) or 'none'}")


@lru_cache(maxsize=None)
def sigs(tf, gen):
    p, v, w, cw, conf = gen
    return generate2(market(tf), GenParams(pivot_len=p, use_vol=v, min_wick_pct=w, confirm_window=cw, confirm=conf))


@lru_cache(maxsize=None)
def trades(tf, gen, plan):
    return trades_for(sim(tf), sigs(tf, gen), *plan)


@lru_cache(maxsize=None)
def mask(tf, gen, name):
    F, f = bars3(tf), CANDS[name]
    out = np.zeros(len(sigs(tf, gen)), dtype=bool)
    for k, s in enumerate(sigs(tf, gen)):
        try:
            out[k] = bool(f(F, s, s.side, s.i))
        except (IndexError, ValueError):
            out[k] = False
    return out


def keep_of(tf, c):
    keep = np.ones(len(sigs(tf, c.gen)), dtype=bool)
    for name in c.feats:
        keep &= mask(tf, c.gen, name)
    return keep


def one_at_a_time(tf, gen, plan, keep):
    S, T = sigs(tf, gen), trades(tf, gen, plan)
    first, free, out = market(tf).first, -1, []
    for k in np.flatnonzero(keep):
        s, t = S[k], T[k]
        if t is None or s.i < first or s.i < free:
            continue
        out.append((s, t))
        free = t.exit_bar
    return out


@lru_cache(maxsize=None)
def rows(tf, c):
    return tuple(one_at_a_time(tf, c.gen, c.plan, keep_of(tf, c)))


def window(rws, tf, w):
    t = market(tf).bid.time
    a, b = WINDOWS[w] if isinstance(w, str) else w
    return summarize([(s, x) for s, x in rws if a <= t[s.i] < b])


TV = (WINDOWS['train'][0], WINDOWS['valid'][1])


def stats(tf, c, w):
    return window(rows(tf, c), tf, w)


@lru_cache(maxsize=None)
def trading_days(tf, w):
    t = market(tf).bid.time
    a, b = WINDOWS[w]
    d = np.unique(t[(t >= a) & (t < b)] // 86400)
    return int(np.count_nonzero((d + 3) % 7 < 5))


ORIGINAL = Cfg()


@lru_cache(maxsize=None)
def target_train(tf):
    """Original's trades per trading day in TEST (= the user's count) x trading days in TRAIN."""
    return stats(tf, ORIGINAL, 'test')['trades'] / trading_days(tf, 'test') * trading_days(tf, 'train')


def in_band(c):
    return all(abs(stats(tf, c, 'train')['trades'] / target_train(tf) - 1) <= BAND for tf in TUNE_TFS)


def score(c, w):
    return float(np.mean([stats(tf, c, w)['avg_r'] for tf in TUNE_TFS]))


def repick(c):
    """Best TRAIN score over the count grid that keeps the trade count; None if no setting can."""
    ok = []
    for p, v, w, cw in COUNT_GRID:
        c2 = replace(c, pivot=p, vol=v, wick=w, cw=cw)
        if in_band(c2):
            ok.append((score(c2, 'train'), c2))
    return max(ok, key=lambda x: x[0]) if ok else None


def placebo_pct(cur, cand):
    """Share of random filters (same per-timeframe keep rate, on the candidate's signal settings and the current
    filters) that the candidate beats on TRAIN+VALID."""
    real = float(np.mean([window(rows(tf, cand), tf, TV)['avg_r'] for tf in TUNE_TFS]))
    base = replace(cand, feats=cur.feats)
    rates = {}
    for tf in TUNE_TFS:
        k0, k1 = keep_of(tf, base), keep_of(tf, cand)
        rates[tf] = k1.sum() / max(k0.sum(), 1)
    beats = 0
    for seed in range(PLACEBO_N):
        vals = []
        for tf in TUNE_TFS:
            k0 = keep_of(tf, base)
            rnd = np.random.default_rng(1000 + seed).random(len(k0)) < rates[tf]
            vals.append(window(one_at_a_time(tf, base.gen, base.plan, k0 & rnd), tf, TV)['avg_r'])
        beats += real > float(np.mean(vals))
    return beats / PLACEBO_N


def changes(cur):
    """Every one-step change from the current setting: (name, new config before re-picking, is_filter)."""
    out = [(n, replace(cur, feats=cur.feats + (n,)), True) for n in CANDS if n not in cur.feats]
    for stop, entry, plan in itertools.product(('atr1.5', 'sweep+0.2', 'sweep+0.5'), ('market', 'retest'),
                                               ('single_1R', 'single_2R', 'single_3R', 'scaled_123')):
        if (stop, entry, plan) != cur.plan:
            out.append((f'trade plan: stop {stop}, entry {entry}, exit {plan}', replace(cur, plan=(stop, entry, plan)), False))
    out.append(('confirmation: ' + ('market structure shift' if cur.confirm == 'close_back' else 'close back'),
                replace(cur, confirm='mss' if cur.confirm == 'close_back' else 'close_back'), False))
    return out


def test_line(c):
    return {tf: dict(n=stats(tf, c, 'test')['trades'], net=round(stats(tf, c, 'test')['net_r'], 1)) for tf in TUNE_TFS + ('H4',)}


def fmt(t):
    return ' '.join(f"{tf}:{v['net']:+.1f}R/{v['n']}" for tf, v in t.items()) + f"  sum {sum(v['net'] for v in t.values()):+.1f}R"


def breadth(cur, cand):
    return sum(window(rows(tf, cand), tf, TV)['avg_r'] > window(rows(tf, cur), tf, TV)['avg_r'] for tf in TUNE_TFS)


if __name__ == '__main__':
    t0 = time.time()
    for tf in ALL_TFS:
        bars3(tf)
    print(f'inputs ready {time.time() - t0:.0f}s; original 2026 counts:',
          {tf: stats(tf, ORIGINAL, 'test')['trades'] for tf in ALL_TFS}, '(user, to 14 Oct:', USER, ')', flush=True)
    cur = ORIGINAL
    assert in_band(cur)
    cur_tr, cur_va = score(cur, 'train'), score(cur, 'valid')
    print(f"\nSTART original: TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f} | 2026 {fmt(test_line(cur))}", flush=True)
    log = dict(start=dict(cfg=cur.__dict__, train=cur_tr, valid=cur_va), rounds=[])
    rnd = 0
    while True:
        rnd += 1
        res = []
        for name, c, is_filter in changes(cur):
            b = repick(c)
            if b is None:
                res.append(dict(name=name, train=None))
                continue
            tr, c2 = b
            res.append(dict(name=name, cfg=c2, is_filter=is_filter, train=tr, valid=score(c2, 'valid'), test=test_line(c2)))
        ranked = sorted([r for r in res if r['train'] is not None], key=lambda r: -r['train'])
        print(f"\n===== ROUND {rnd}: current TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f} =====", flush=True)
        for r in ranked:
            print(f"  {r['name'][:66]:66s} TRAIN {r['train']:+.3f} VALID {r['valid']:+.3f} | 2026 {fmt(r['test'])}", flush=True)
        print(f"  ({sum(r['train'] is None for r in res)} changes cannot keep the trade count)", flush=True)
        accepted = None
        for r in ranked[:TOP_K]:
            why = []
            if r['train'] <= cur_tr: why.append('TRAIN not better')
            if r['valid'] <= cur_va: why.append('VALID not better')
            br = breadth(cur, r['cfg'])
            if br < 3: why.append(f'better on only {br}/4 timeframes')
            pp = placebo_pct(cur, r['cfg']) if r['is_filter'] and not why else None
            if pp is not None and pp < PLACEBO_Q: why.append(f'beats only {pp:.0%} of random filters')
            r['checks'] = dict(breadth=br, placebo=pp, failed=why)
            print(f"  check '{r['name']}': breadth {br}/4" + (f", beats {pp:.0%} of random filters" if pp is not None else '')
                  + (' -> ACCEPTED' if not why else ' -> rejected (' + '; '.join(why) + ')'), flush=True)
            if not why:
                accepted = r
                break
        log['rounds'].append(dict(round=rnd, results=[{k: (v.__dict__ if k == 'cfg' else v) for k, v in r.items()} for r in res],
                                  accepted=accepted and accepted['name']))
        if not accepted:
            print(f"ROUND {rnd}: no change passes all checks -> done", flush=True)
            break
        cur, cur_tr, cur_va = accepted['cfg'], accepted['train'], accepted['valid']
        print(f"ROUND {rnd}: ACCEPTED '{accepted['name']}'\n  now: {cur.short()}", flush=True)
    full = {tf: {w: stats(tf, cur, w) for w in WINDOWS} for tf in ALL_TFS}
    orig = {tf: {w: stats(tf, ORIGINAL, w) for w in WINDOWS} for tf in ALL_TFS}
    print(f"\nFINAL: {cur.short()}\n  TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f}", flush=True)
    for tf in ALL_TFS:
        d, o = full[tf]['test'], orig[tf]['test']
        print(f"  {tf:3s} 2026 trades={d['trades']:4d} (original {o['trades']}) wins={d['wins']} losses={d['losses']} win={d['win_rate']:.0%} "
              f"netR={d['net_r']:+.1f} PF={d['pf']:.2f} maxDD={d['max_dd_r']:.1f}R | original netR={o['net_r']:+.1f}", flush=True)
    log['final'] = dict(cfg=cur.__dict__, train=cur_tr, valid=cur_va, report=full, original=orig)
    (OUT / 'v3.json').write_text(json.dumps(log, indent=1, default=str))
