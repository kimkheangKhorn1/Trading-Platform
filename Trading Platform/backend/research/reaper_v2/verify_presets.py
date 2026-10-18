"""Backtest again from the INDICATOR itself: run REAPER [JOAT] with 'Use Best Settings For The Chart Timeframe' on
real data, take its RAID labels and drawn stops, check they equal the research signals, then trade them with the
same simulator (one at a time, bid/ask, 1-minute stop/target order) and report every half-year and 2026."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.scripts.liquidity_reaper import TF_PRESETS, LiquidityReaper
from research.reaper.data import BID, market, store, ts
from research.reaper_v2.v3 import Cfg, rows, window

from research.reaper_v2.v4_tf import ROUND
NAMES = {'original': 'ORIGINAL', 'round 1': 'ROUND1', 'round 2': 'ROUND2', 'round 3': 'ROUND3'}
PICKS = {'Best in 2026': dict(M5='round 1', M15='round 2', M30='round 2', H1='round 1', H4='round 2'),
         'Most consistent 2024-2026': dict(M5='original', M15='round 3', M30='round 2', H1='round 2', H4='round 2')}
HALVES = [('2024 H1', '2024-01-01', '2024-07-01'), ('2024 H2', '2024-07-01', '2025-01-01'), ('2025 H1', '2025-01-01', '2025-07-01'),
          ('2025 H2', '2025-07-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]

if __name__ == '__main__':
    import app.scripts.liquidity_reaper as LR
    from research.reaper_v2.v3 import keep_of, sigs
    st = store(); feed = st.get_feed(BID)
    for mode, pick in PICKS.items():
        print(f"\n===== {mode} =====", flush=True)
        tot = {h: 0.0 for h, _, _ in HALVES}
        for tf, name in pick.items():
            assert LR.TF_PRESETS[mode][tf] is getattr(LR, NAMES[name])
            c, b = ROUND[name], market(tf).bid
            r = ScriptRunner(LiquidityReaper, {'tfPresetMode': mode}, timeframe=tf, alignment=feed.alignment,
                             precision=feed.precision, htf_provider=StoreHtfProvider(st, feed))
            r.process(b)
            labels = sorted((int(d['x']), 1 if d['text'].startswith('RAID BUY') else -1) for d in r.output()['drawings']
                            if d.get('group') == 'signals' and d['kind'] == 'label')
            research = sorted((int(b.time[s.i]), s.side) for s, k in zip(sigs(tf, c.gen), keep_of(tf, c)) if k)
            same = labels == [x for x in research if x[0] >= labels[0][0]]
            cells = [window(rows(tf, c), tf, (ts(a), ts(z))) for _, a, z in HALVES]
            for (h, _, _), x in zip(HALVES, cells):
                tot[h] += x['net_r']
            t26 = cells[-1]
            print(f"{tf} ({name}): indicator signals = research: {same} | " + ' | '.join(f"{h}: {x['net_r']:+.1f}R" for (h, _, _), x in zip(HALVES, cells)), flush=True)
            print(f"   2026: {t26['trades']} trades, {t26['wins']} wins / {t26['losses']} losses, win {t26['win_rate']:.0%}, "
                  f"net {t26['net_r']:+.1f}R, PF {t26['pf']:.2f}, max drawdown {t26['max_dd_r']:.1f}R", flush=True)
        print('5m-4H total: ' + ' | '.join(f"{h}: {v:+.1f}R" for h, v in tot.items()) + f" | all {sum(tot.values()):+.1f}R", flush=True)
