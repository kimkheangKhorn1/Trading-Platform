"""Per-bar state recorded from the project's own indicators (their real code), cached in out/ctx3_<tf>.npz.

SMC Master (more than context.py): pivot S/R events (breakout / breakdown / resistance & support break / false
breakdown = bear trap / false breakup = bull trap / reactions), TSI curls, EQH / EQL, FVGs, order block breakouts,
internal and swing BOS / CHoCH separately, and each of the 9 dashboard directions.
Volume Supply and Demand [TradingIQ]: whether the candle traded inside one of its live demand / supply zones.
All values are what the indicator had at that bar's close.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.scripts.smc_master import SmcMaster
from app.scripts.volume_supply_demand import VolumeSupplyDemand
from research.reaper.data import BID, market, store

OUT = Path(__file__).with_name('out')
DASH = ('macd', 'stochastic', 'vortex', 'mom', 'rsi', 'psar', 'dmi', 'mfi', 'fisher')
EVENTS = {'False breakdown / bear trap': 'false_bd', 'False breakup / bull trap': 'false_bu', 'Pivot breakout': 'pivot_bo',
          'Pivot breakdown': 'pivot_bd', 'Resistance break': 'res_break', 'Support break': 'sup_break',
          'Reaction from pivot support': 'sup_react', 'Reaction from pivot resistance': 'res_react',
          'Bullish TSI curl': 'tsi_bull', 'Bearish TSI curl': 'tsi_bear', 'EQH': 'eqh', 'EQL': 'eql',
          'Bullish FVG': 'fvg_bull_evt', 'Bearish FVG': 'fvg_bear_evt',
          'Internal Bullish BOS': 'ibos_bull', 'Internal Bearish BOS': 'ibos_bear',
          'Internal Bullish CHoCH': 'ichoch_bull', 'Internal Bearish CHoCH': 'ichoch_bear',
          'Bullish BOS': 'sbos_bull', 'Bearish BOS': 'sbos_bear', 'Bullish CHoCH': 'schoch_bull', 'Bearish CHoCH': 'schoch_bear'}


class SmcRecorder(SmcMaster):
    def setup(self, ctx):
        super().setup(ctx)
        self.rec = {k: [] for k in list(EVENTS.values()) + ['ob_break_bull', 'ob_break_bear'] + ['dash_' + d for d in DASH]}
        self.bar_events = []

    def emit(self, ctx, title, group, **extra):
        if ctx.is_confirmed:
            self.bar_events.append(title)

    def on_bar(self, ctx):
        self.bar_events = []
        super().on_bar(ctx)
        ev = set(self.bar_events)
        for title, key in EVENTS.items():
            self.rec[key].append(title in ev)
        # 'Bullish OB Breakout' = a bullish order block broken (price closed below it) -> bearish information
        self.rec['ob_break_bull'].append(any(t.startswith('Bearish') and 'OB Breakout' in t for t in ev))
        self.rec['ob_break_bear'].append(any(t.startswith('Bullish') and 'OB Breakout' in t for t in ev))
        rows = {r['id']: r['direction'] for r in (self.tables[0]['rows'] if self.tables else [])}
        for d in DASH:
            v = rows.get(d)
            self.rec['dash_' + d].append(1 if v == 'buy' else -1 if v == 'sell' else 0)

    def on_last_bar(self, ctx):
        pass

    def extra_output(self):
        return {}


class VsdRecorder(VolumeSupplyDemand):
    def setup(self, ctx):
        super().setup(ctx)
        self.rec = dict(vsd_demand=[], vsd_supply=[])

    def on_bar(self, ctx):
        super().on_bar(ctx)
        h, l = ctx.high, ctx.low
        live = [(d.direction, d.sd.top, d.sd.bottom) for d in self.drawings if d.sd is not None]
        self.rec['vsd_demand'].append(any(not sup and l <= top and h >= bot for sup, top, bot in live))
        self.rec['vsd_supply'].append(any(sup and l <= top and h >= bot for sup, top, bot in live))

    def on_last_bar(self, ctx):
        pass

    def extra_output(self):
        return {}


# Display-only switches turned on so their events are recorded; no other system depends on them.
SMC_PARAMS = dict(show_pdhl=False, show_pwhl=False, show_pmhl=False, fvg_tf='', supPush=True, resPush=True, curl=True,
                  show_profile=False)


def record3(tf: str) -> dict:
    path = OUT / f'ctx3_{tf}.npz'
    b = market(tf).bid
    if path.exists():
        d = dict(np.load(path))
        if len(d['false_bd']) == len(b):
            return d
    st = store(); feed = st.get_feed(BID)
    r = ScriptRunner(SmcRecorder, {k: v for k, v in SMC_PARAMS.items() if k != 'show_profile'}, timeframe=tf,
                     alignment=feed.alignment, precision=3)
    r.process(b)
    d = {k: np.asarray(v, dtype=float) for k, v in r.script.rec.items()}
    v = ScriptRunner(VsdRecorder, dict(show_profile=False), timeframe=tf, alignment=feed.alignment, precision=feed.precision,
                     htf_provider=StoreHtfProvider(st, feed))
    v.process(b)
    d.update({k: np.asarray(x, dtype=float) for k, x in v.script.rec.items()})
    np.savez_compressed(path, **d)
    return d


if __name__ == '__main__':
    for tf in sys.argv[1:]:
        t = time.time(); d = record3(tf)
        print(tf, f'{time.time() - t:.0f}s', {k: int(np.nansum(np.abs(x))) for k, x in d.items()}, flush=True)
