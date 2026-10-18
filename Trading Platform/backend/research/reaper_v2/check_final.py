"""Checks on the round-2 setting (reported only): random-filter placebo for the ADX filter, and the out-of-sample
total (2025 H2 + 2026) next to the round-1 setting."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper.data import market
from research.reaper.study import summarize
from research.reaper_v2.forward import mask, sigs, stats, trades
from research.reaper_v2.steps import WINDOWS

GEN, FEATS, PLAN = (3, False, 25.0, 5), ('4H trend with trade', 'ADX >= 25 (trending)'), ('sweep+0.2', 'market', 'single_3R')
R1 = ((5, True, 10.0, 3), ('4H trend with trade',), ('atr1.5', 'market', 'single_3R'))


def one_at_a_time(tf, keep):
    S, T = sigs(tf, GEN), trades(tf, GEN, PLAN)
    first, free, out = market(tf).first, -1, []
    for k in np.flatnonzero(keep):
        s, t = S[k], T[k]
        if t is None or s.i < first or s.i < free:
            continue
        out.append((s, t)); free = t.exit_bar
    return out


def win(rows, tf, w):
    t = market(tf).bid.time; a, b = WINDOWS[w]
    return summarize([(s, x) for s, x in rows if a <= t[s.i] < b])


if __name__ == '__main__':
    for tf in ('M5', 'M15', 'M30', 'H1', 'H4'):
        trend, adx = mask(tf, GEN, FEATS[0]), mask(tf, GEN, FEATS[1])
        real = {w: win(one_at_a_time(tf, trend & adx), tf, w)['avg_r'] for w in ('valid', 'test')}
        rate = adx[trend].mean()
        pl = {w: [] for w in real}
        for seed in range(50):
            rnd = np.random.default_rng(seed).random(len(adx)) < rate
            rows = one_at_a_time(tf, trend & rnd)
            for w in pl:
                pl[w].append(win(rows, tf, w)['avg_r'])
        oos = sum(stats(tf, GEN, FEATS, PLAN, w)['net_r'] for w in ('valid', 'test'))
        oos1 = sum(stats(tf, *R1, w)['net_r'] for w in ('valid', 'test'))
        print(f"{tf}: ADX filter avgR valid {real['valid']:+.3f} test {real['test']:+.3f} | random filter (keeps {rate:.0%}) beaten "
              f"{np.mean(np.array(pl['valid']) < real['valid']):.0%} valid, {np.mean(np.array(pl['test']) < real['test']):.0%} 2026 "
              f"| out-of-sample net R (2025-H2 + 2026): round-2 {oos:+.1f} vs round-1 {oos1:+.1f}", flush=True)
