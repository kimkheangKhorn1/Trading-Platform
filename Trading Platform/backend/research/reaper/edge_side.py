"""Forward move by side, against random entries in the same direction (train only): is it sweep skill or trend beta?"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from research.reaper.data import TRAIN, market
from research.reaper.events import GenParams, generate
from app.engine.ta import atr as atr_v

H = 20
for tf in ('M5', 'M15', 'M30', 'H1', 'H4'):
    m = market(tf); b, a = m.bid, m.ask; t = b.time
    at = atr_v(b.high, b.low, b.close, 14)
    idx = np.flatnonzero((t >= TRAIN[0]) & (t < TRAIN[1]))
    idx = idx[idx + H + 1 < len(b)]
    long_all = (b.close[idx + H] - a.open[idx + 1]) / at[idx]
    short_all = (b.open[idx + 1] - a.close[idx + H]) / at[idx]
    sigs = [s for s in generate(m, GenParams(use_vol=False)) if TRAIN[0] <= t[s.i] < TRAIN[1] and s.i + H + 1 < len(b)]
    out = f'{tf:4}'
    for side, base, name in ((1, long_all, 'BUY '), (-1, short_all, 'SELL')):
        r = np.array([((b.close[s.i + H] - a.open[s.i + 1]) if side == 1 else (b.open[s.i + 1] - a.close[s.i + H])) / s.atr
                      for s in sigs if s.side == side])
        diff = r.mean() - base.mean()
        se = r.std(ddof=1) / np.sqrt(len(r))
        out += f' | {name} n={len(r):4} signal {r.mean():+.3f} vs random {base.mean():+.3f} ATR -> skill {diff:+.3f} (t={diff/se:+.1f})'
    print(out)
