"""Which part of the final setting did the work (reported only; nothing is chosen here), plus a placebo:
the same filter strength with a random keep/skip instead of the H4 trend."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from research.reaper.data import market
from research.reaper.study import in_window, one_at_a_time, summarize
from research.reaper_v2.steps import ALL_TFS, WINDOWS, Cfg, sigs, stats, trades

FINAL = Cfg(pivot_len=5, use_vol=True, min_wick_pct=10.0, trend='h4')
CASES = {'original': Cfg(), 'original + H4 trend only': replace(Cfg(), trend='h4'),
         'final without H4 trend': replace(FINAL, trend='none'), 'final': FINAL}

if __name__ == '__main__':
    for name, c in CASES.items():
        print(f"{name:26s}", ' '.join(f"{tf}:{stats(tf, c, 'test')['net_r']:+7.1f}R/{stats(tf, c, 'test')['trades']}" for tf in ALL_TFS),
              '| VALID avgR', ' '.join(f"{stats(tf, c, 'valid')['avg_r']:+.3f}" for tf in ('M5', 'M15', 'M30', 'H1')), flush=True)
    # placebo: drop signals at random with the trend filter's own keep rate, 50 seeds
    base = replace(FINAL, trend='none')
    for tf in ('M5', 'M15', 'M30', 'H1'):
        g = (tf, base.pivot_len, base.use_vol, base.min_wick_pct, base.confirm)
        S, T = sigs(*g), trades(*g, base.stop, base.entry, base.plan)
        keep_rate = np.mean([s.side * s.htf_trend > 0 for s in S])
        real = {w: summarize(in_window(one_at_a_time(S, T, lambda s: s.side * s.htf_trend > 0, market(tf).first), market(tf).bid.time, WINDOWS[w]))['avg_r'] for w in ('valid', 'test')}
        pl = {w: [] for w in ('valid', 'test')}
        for seed in range(50):
            rng = np.random.default_rng(seed); mask = {id(s): rng.random() < keep_rate for s in S}
            rows = one_at_a_time(S, T, lambda s: mask[id(s)], market(tf).first)
            for w in pl:
                pl[w].append(summarize(in_window(rows, market(tf).bid.time, WINDOWS[w]))['avg_r'])
        print(f"{tf} H4-trend filter avgR valid {real['valid']:+.3f} test {real['test']:+.3f} | random filter (same keep rate {keep_rate:.0%}) "
              f"valid {np.mean(pl['valid']):+.3f} (beats {np.mean(np.array(pl['valid']) < real['valid']):.0%}) "
              f"test {np.mean(pl['test']):+.3f} (beats {np.mean(np.array(pl['test']) < real['test']):.0%})", flush=True)
