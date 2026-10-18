"""Per-bar state of the full SMC indicator (used as Reaper filters), recorded once per timeframe (cached in out/ctx_<tf>.npz).

Runs the real SmcMaster on the research candles and stores, for every bar: internal / swing structure trend,
premium-discount position, which zones the candle touched (order blocks, fresh demand/supply, pivot S/R),
candle patterns and structure breaks printed on it, and how many of the 9 dashboard indicators say buy / sell.
Previous D/W/M levels are off (they need higher-timeframe requests and are not used here).
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.runner import ScriptRunner
from app.scripts.smc_master import SmcMaster
from research.reaper.data import market

OUT = Path(__file__).with_name('out')
FIELDS = ('itrend', 'strend', 'pd', 'bull_ob', 'bear_ob', 'demand', 'supply', 'pivot_sup', 'pivot_res',
          'pat_bull', 'pat_bear', 'bos_bull', 'bos_bear', 'choch_bull', 'choch_bear', 'dash_buy', 'dash_sell')


class Recorder(SmcMaster):
    def setup(self, ctx):
        super().setup(ctx)
        self.rec = {k: [] for k in FIELDS}
        self.bar_events = []

    def emit(self, ctx, title, group, **extra):
        if ctx.is_confirmed:
            self.bar_events.append((title, extra.get('direction')))

    def on_bar(self, ctx):
        self.bar_events = []
        super().on_bar(ctx)
        h, l, c = ctx.high, ctx.low, ctx.close
        touch = lambda zs: any(l <= z.top and h >= z.bottom for z in zs)
        r = self.rec
        r['itrend'].append(self.struct[0]['trend']); r['strend'].append(self.struct[1]['trend'])
        hi, lo = self.trail_high, self.trail_low
        r['pd'].append((c - lo) / (hi - lo) if math.isfinite(hi) and math.isfinite(lo) and hi > lo else math.nan)
        obs = self.obs[0] + self.obs[1]
        r['bull_ob'].append(touch([z for z in obs if z.side == 1])); r['bear_ob'].append(touch([z for z in obs if z.side == -1]))
        r['demand'].append(touch([z for z in self.ds[0] if z.state != 2])); r['supply'].append(touch([z for z in self.ds[1] if z.state != 2]))
        piv = self.pivots[0] + self.pivots[1]
        r['pivot_sup'].append(touch([z for z in piv if z.side == 1])); r['pivot_res'].append(touch([z for z in piv if z.side == -1]))
        ev = self.bar_events
        r['pat_bull'].append(any(d == 'bullish' for _, d in ev)); r['pat_bear'].append(any(d == 'bearish' for _, d in ev))
        for kind in ('BOS', 'CHoCH'):
            r[kind.lower() + '_bull'].append(any(t.endswith('Bullish ' + kind) for t, _ in ev))
            r[kind.lower() + '_bear'].append(any(t.endswith('Bearish ' + kind) for t, _ in ev))
        rows = self.tables[0]['rows'] if self.tables else []
        r['dash_buy'].append(sum(x['direction'] == 'buy' for x in rows)); r['dash_sell'].append(sum(x['direction'] == 'sell' for x in rows))

    def on_last_bar(self, ctx):
        pass

    def extra_output(self):
        return {}


PARAMS = dict(show_pdhl=False, show_pwhl=False, show_pmhl=False, fvg_tf='')


def record(tf: str) -> dict:
    path = OUT / f'ctx_{tf}.npz'
    b = market(tf).bid
    if path.exists():
        d = dict(np.load(path))
        if len(d['itrend']) == len(b):
            return d
    r = ScriptRunner(Recorder, PARAMS, timeframe=tf, alignment='ny17', precision=3)
    r.process(b)
    d = {k: np.asarray(v, dtype=float) for k, v in r.script.rec.items()}
    np.savez_compressed(path, **d)
    return d


if __name__ == '__main__':
    for tf in sys.argv[1:]:
        t = time.time(); d = record(tf)
        print(tf, len(d['itrend']), f'{time.time() - t:.0f}s', {k: round(float(np.nanmean(v)), 3) for k, v in d.items()}, flush=True)
