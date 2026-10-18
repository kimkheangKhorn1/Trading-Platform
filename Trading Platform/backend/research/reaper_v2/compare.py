"""The candidate settings side by side, every half-year 2024 -> 2026 (5m-4H net R), on the user's window rules."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.reaper.data import ts
from research.reaper_v2.v3 import ALL_TFS, Cfg, rows, window

SETTINGS = {
    'original': Cfg(),
    'round 1 (4H trend)': Cfg(pivot=5, vol=True, wick=10.0, cw=3, feats=('4H trend with trade',)),
    'round 2 (4H trend + ADX + sweep stop)': Cfg(pivot=3, vol=False, wick=25.0, cw=5, feats=('4H trend with trade', 'ADX >= 25 (trending)'), plan=('sweep+0.2', 'market', 'single_3R')),
    'round 3 (engulfing + footprint delta)': Cfg(pivot=3, vol=False, wick=10.0, cw=5, feats=('engulfs the previous candle body', 'footprint delta with trade on signal candle')),
}
HALVES = [('2024 H1', '2024-01-01', '2024-07-01'), ('2024 H2', '2024-07-01', '2025-01-01'), ('2025 H1', '2025-01-01', '2025-07-01'),
          ('2025 H2', '2025-07-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]

if __name__ == '__main__':
    for name, c in SETTINGS.items():
        print(f'\n{name}')
        for tf in ALL_TFS:
            r = rows(tf, c)
            cells = [window(r, tf, (ts(a), ts(b))) for _, a, b in HALVES]
            print(f"  {tf:3s} " + ' | '.join(f"{h}: {x['net_r']:+6.1f}R/{x['trades']}" for (h, _, _), x in zip(HALVES, cells)), flush=True)
        tot = [sum(window(rows(tf, c), tf, (ts(a), ts(b)))['net_r'] for tf in ('M5', 'M15', 'M30', 'H1', 'H4')) for _, a, b in HALVES]
        print('  5m-4H sum ' + ' | '.join(f"{h}: {v:+6.1f}R" for (h, _, _), v in zip(HALVES, tot)) + f" | positive half-years {sum(v > 0 for v in tot)}/5", flush=True)
