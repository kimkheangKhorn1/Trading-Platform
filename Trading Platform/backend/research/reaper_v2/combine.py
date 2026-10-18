"""Every round's logic on every timeframe, same window rules (2026 = Jan 1 -> Oct 1), plus the per-timeframe combos."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.reaper.data import ts
from research.reaper_v2.v3 import rows, window
from research.reaper_v2.v4_tf import ROUND, in_band

TFS = ('M5', 'M15', 'M30', 'H1', 'H4')
HALVES = [('2024 H1', '2024-01-01', '2024-07-01'), ('2024 H2', '2024-07-01', '2025-01-01'), ('2025 H1', '2025-01-01', '2025-07-01'),
          ('2025 H2', '2025-07-01', '2026-01-01'), ('2026', '2026-01-01', '2026-10-01')]

if __name__ == '__main__':
    R = {}
    for tf in TFS:
        for name, c in ROUND.items():
            cells = [window(rows(tf, c), tf, (ts(a), ts(b))) for _, a, b in HALVES]
            R[tf, name] = dict(net=[x['net_r'] for x in cells], n26=cells[-1]['trades'], w26=cells[-1]['wins'], l26=cells[-1]['losses'], ok=in_band(tf, c))
            r = R[tf, name]
            print(f"{tf:3s} {name:9s} count ok={r['ok']!s:5s} " + ' '.join(f"{h}:{v:+7.1f}" for (h, _, _), v in zip(HALVES, r['net']))
                  + f" | 2024-25 {sum(r['net'][:4]):+7.1f} | all {sum(r['net']):+7.1f} | 2026 {r['n26']} trades {r['w26']}W/{r['l26']}L", flush=True)
    def combo(pick):
        tot = [sum(R[tf, pick[tf]]['net'][k] for tf in TFS) for k in range(5)]
        return pick, tot
    best26 = {tf: max((n for n in ROUND if R[tf, n]['ok']), key=lambda n: R[tf, n]['net'][4]) for tf in TFS}
    bestall = {tf: max((n for n in ROUND if R[tf, n]['ok']), key=lambda n: sum(R[tf, n]['net'])) for tf in TFS}
    best2425 = {tf: max((n for n in ROUND if R[tf, n]['ok']), key=lambda n: sum(R[tf, n]['net'][:4])) for tf in TFS}
    for label, pick in (('best in 2026', best26), ('best over 2024-2026', bestall), ('best in 2024-2025 (current preset rule)', best2425),
                        ('current preset', dict(M5='original', M15='round 2', M30='round 2', H1='round 2', H4='round 2')),
                        ('round 2 for all', {tf: 'round 2' for tf in TFS})):
        p, tot = combo(pick)
        print(f"\n{label}: {p}\n   " + ' '.join(f"{h}:{v:+7.1f}" for (h, _, _), v in zip(HALVES, tot)) + f" | 2024-25 {sum(tot[:4]):+.1f} | all {sum(tot):+.1f}", flush=True)
