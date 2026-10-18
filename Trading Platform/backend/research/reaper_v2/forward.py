"""REAPER [JOAT] round 2: add one input at a time -> backtest -> keep it only if it improves.

Protocol (fixed before running):
* Start: the accepted round-1 setting (Swing Strength 5, volume filter on, Min Wick 10 %, 4H trend filter).
* Trades exactly as the user's backtest (one at a time, next-open entry on Exness bid/ask, 1.5 ATR stop, 3R target,
  1-minute candles decide stop vs target).
* Each round tries every remaining candidate input on top of the current setting. For each candidate the signal
  settings (swing strength 3/5/8, volume filter on/off, min wick 0/10/25 %, confirmation window 3/5) are re-picked so
  that TRAIN trades per day on 5m/15m/30m/1H stay within +-20% of the user's 2026 counts.
* Score = mean TRAIN (2024-01 -> 2025-06) average R over 5m/15m/30m/1H. The three best candidates of a round are
  checked in order; the first that improves BOTH the TRAIN score and the VALID (2025-07 -> 12) score is accepted.
  No candidate passing = stop.
* 2026 (the user's window) is printed for every candidate and never used to accept or reject.
* Last round: stop / entry / exit plans with the accepted inputs, same rule.
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper.data import market
from research.reaper.events import GenParams
from research.reaper.study import summarize
from research.reaper.tune import trades_for
from research.reaper_v2.features import bars, candidates
from research.reaper_v2.gen2 import generate2
from research.reaper_v2.steps import ALL_TFS, BAND, TARGET, TUNE_TFS, WINDOWS, sim, trading_days

OUT = Path(__file__).with_name('out')
GENS = [(p, v, w, cw) for p in (3, 5, 8) for v in (True, False) for w in (0.0, 10.0, 25.0) for cw in (3, 5)]
CANDS = candidates()
START_GEN, START_FEATS = (5, True, 10.0, 3), ('4H trend with trade',)
PLAN0 = ('atr1.5', 'market', 'single_3R')


@lru_cache(maxsize=None)
def sigs(tf, gen):
    p, v, w, cw = gen
    return generate2(market(tf), GenParams(pivot_len=p, use_vol=v, min_wick_pct=w, confirm_window=cw))


@lru_cache(maxsize=None)
def trades(tf, gen, plan):
    return trades_for(sim(tf), sigs(tf, gen), *plan)


@lru_cache(maxsize=None)
def mask(tf, gen, name):
    F, f = bars(tf), CANDS[name]
    out = np.zeros(len(sigs(tf, gen)), dtype=bool)
    for k, s in enumerate(sigs(tf, gen)):
        try:
            out[k] = bool(f(F, s, s.side, s.i))
        except (IndexError, ValueError):
            out[k] = False
    return out


@lru_cache(maxsize=None)
def rows(tf, gen, feats, plan):
    S, T = sigs(tf, gen), trades(tf, gen, plan)
    keep = np.ones(len(S), dtype=bool)
    for name in feats:
        keep &= mask(tf, gen, name)
    first, free, out = market(tf).first, -1, []
    for k in np.flatnonzero(keep):
        s, t = S[k], T[k]
        if t is None or s.i < first or s.i < free:
            continue
        out.append((s, t))
        free = t.exit_bar
    return tuple(out)


def stats(tf, gen, feats, plan, w):
    t = market(tf).bid.time
    a, b = WINDOWS[w]
    return summarize([(s, tr) for s, tr in rows(tf, gen, feats, plan) if a <= t[s.i] < b])


def in_band(gen, feats, plan):
    for tf in TUNE_TFS:
        n = stats(tf, gen, feats, plan, 'train')['trades']
        if abs(n / (TARGET[tf] / trading_days(tf, 'test') * trading_days(tf, 'train')) - 1) > BAND:
            return False
    return True


def score(gen, feats, plan, w):
    return float(np.mean([stats(tf, gen, feats, plan, w)['avg_r'] for tf in TUNE_TFS]))


def best_gen(feats, plan):
    ok = [(score(g, feats, plan, 'train'), g) for g in GENS if in_band(g, feats, plan)]
    return max(ok) if ok else None


def test_line(gen, feats, plan, tfs=TUNE_TFS + ('H4',)):
    return {tf: dict(n=stats(tf, gen, feats, plan, 'test')['trades'], net=round(stats(tf, gen, feats, plan, 'test')['net_r'], 1)) for tf in tfs}


def fmt(t):
    return ' '.join(f"{tf}:{v['net']:+.1f}R/{v['n']}" for tf, v in t.items()) + f"  sum {sum(v['net'] for v in t.values()):+.1f}R"


if __name__ == '__main__':
    t0 = time.time()
    for tf in ALL_TFS:
        bars(tf)
        print(f'features {tf} ready {time.time() - t0:.0f}s', flush=True)
    gen, feats, plan = START_GEN, START_FEATS, PLAN0
    cur_tr, cur_va = score(gen, feats, plan, 'train'), score(gen, feats, plan, 'valid')
    print(f"\nSTART {gen} {feats}: TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f} | 2026 {fmt(test_line(gen, feats, plan))}", flush=True)
    log = dict(start=dict(gen=gen, feats=feats, train=cur_tr, valid=cur_va), rounds=[])
    rnd = 0
    while True:
        rnd += 1
        res = []
        for name in CANDS:
            if name in feats:
                continue
            f2 = feats + (name,)
            b = best_gen(f2, plan)
            if b is None:
                res.append(dict(name=name, train=None))
                continue
            tr, g2 = b
            res.append(dict(name=name, gen=g2, train=tr, valid=score(g2, f2, plan, 'valid'), test=test_line(g2, f2, plan)))
        ranked = sorted([r for r in res if r['train'] is not None], key=lambda r: -r['train'])
        print(f"\n===== ROUND {rnd} (current TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f}) =====", flush=True)
        for r in ranked:
            print(f"  {r['name']:58s} TRAIN {r['train']:+.3f} VALID {r['valid']:+.3f} gen {r['gen']} | 2026 {fmt(r['test'])}", flush=True)
        for r in res:
            if r['train'] is None:
                print(f"  {r['name']:58s} cannot keep the trade count", flush=True)
        accepted = next((r for r in ranked[:3] if r['train'] > cur_tr and r['valid'] > cur_va), None)
        log['rounds'].append(dict(round=rnd, results=res, accepted=accepted and accepted['name']))
        if not accepted:
            print(f"ROUND {rnd}: nothing in the top 3 improves both TRAIN and VALID -> stop adding inputs", flush=True)
            break
        gen, feats = accepted['gen'], feats + (accepted['name'],)
        cur_tr, cur_va = accepted['train'], accepted['valid']
        print(f"ROUND {rnd}: ACCEPTED '{accepted['name']}' -> {gen} {feats}", flush=True)
    # exits / stops / entry with the accepted inputs
    res = []
    for stop, entry, pl in itertools.product(('atr1.5', 'sweep+0.2', 'sweep+0.5'), ('market', 'retest'),
                                             ('single_1R', 'single_2R', 'single_3R', 'scaled_123')):
        p2 = (stop, entry, pl)
        b = best_gen(feats, p2)
        if b:
            res.append(dict(plan=p2, gen=b[1], train=b[0], valid=score(b[1], feats, p2, 'valid'), test=test_line(b[1], feats, p2)))
    res.sort(key=lambda r: -r['train'])
    print(f"\n===== EXITS (current {plan}) =====", flush=True)
    for r in res:
        print(f"  {str(r['plan']):40s} TRAIN {r['train']:+.3f} VALID {r['valid']:+.3f} gen {r['gen']} | 2026 {fmt(r['test'])}", flush=True)
    acc = next((r for r in res[:3] if r['plan'] != plan and r['train'] > cur_tr and r['valid'] > cur_va), None)
    if acc:
        gen, plan, cur_tr, cur_va = acc['gen'], acc['plan'], acc['train'], acc['valid']
        print(f"EXITS: ACCEPTED {plan}", flush=True)
    else:
        print('EXITS: keep the original 1.5 ATR stop / 3R target', flush=True)
    full = {tf: {w: stats(tf, gen, feats, plan, w) for w in ('train', 'valid', 'test')} for tf in ALL_TFS}
    base = {tf: {w: stats(tf, START_GEN, START_FEATS, PLAN0, w) for w in ('train', 'valid', 'test')} for tf in ALL_TFS}
    print(f"\nFINAL gen={gen} feats={feats} plan={plan} TRAIN {cur_tr:+.3f} VALID {cur_va:+.3f}", flush=True)
    for tf in ALL_TFS:
        d, o = full[tf]['test'], base[tf]['test']
        print(f"  {tf:3s} 2026 trades={d['trades']:4d} (user {TARGET[tf]}) wins={d['wins']} losses={d['losses']} win={d['win_rate']:.0%} "
              f"netR={d['net_r']:+.1f} PF={d['pf']:.2f} maxDD={d['max_dd_r']:.1f}R | round-1 setting netR={o['net_r']:+.1f} ({o['trades']})", flush=True)
    log['final'] = dict(gen=gen, feats=feats, plan=plan, train=cur_tr, valid=cur_va, report=full, round1=base)
    (OUT / 'forward.json').write_text(json.dumps(log, indent=1, default=str))
