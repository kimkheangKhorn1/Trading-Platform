"""Does the sweep signal carry direction at all? Forward move after entry, in ATR, net of the spread (train only)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from research.reaper.data import TRAIN, market
from research.reaper.events import GenParams, generate

for tf in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4'):
    m = market(tf); b, a = m.bid, m.ask; t = b.time
    sigs = [s for s in generate(m, GenParams(use_vol=False)) if TRAIN[0] <= t[s.i] < TRAIN[1] and s.i + 41 < len(b)]
    line = f'{tf:4} n={len(sigs):5}'
    for hzn in (5, 10, 20, 40):
        # long: buy at ask open of i+1, value at bid close of i+hzn; short mirrored
        r = np.array([((b.close[s.i + hzn] - a.open[s.i + 1]) if s.side == 1 else (b.open[s.i + 1] - a.close[s.i + hzn])) / s.atr
                      for s in sigs])
        se = r.std(ddof=1) / np.sqrt(len(r))
        line += f' | {hzn:2} bars: {r.mean():+.3f} ATR (t={r.mean()/se:+.1f})'
    print(line)
