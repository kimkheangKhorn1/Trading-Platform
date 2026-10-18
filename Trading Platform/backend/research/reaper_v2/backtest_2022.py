"""Backtest of the shipped REAPER [JOAT] presets from 2022-01-01 to 2026-10-01, month by month.

The INDICATOR itself produces the signals (ScriptRunner + the stored Exness feed, 4H trend from the store, 1-minute
delta from the store); each signal's stop is the one the indicator draws. Trades as in every earlier test: one at a
time, entry at the next candle's open (buy at the ask, sell at the bid, +0.05 slippage), target 3R, stop / target
order from 1-minute candles (stop first if both in the same minute).
2022-2023 was never used to choose anything (tuning used 2024-2025 and the 2026 check).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.scripts.liquidity_reaper import LiquidityReaper
from research.reaper.data import ASK, BID, Market, _align, _load, store, ts
from research.reaper.sim import EXITS, Simulator

START, END = ts('2022-01-01'), ts('2026-10-01')
WARMUP_FROM = ts('2021-09-01')
TFS = ('M5', 'M15', 'M30', 'H1', 'H4')
MODES = ('Most consistent 2024-2026', 'Best in 2026', 'Off')
OUT = Path(__file__).with_name('out')


class SignalRecorder(LiquidityReaper):
    def setup(self, ctx):
        super().setup(ctx)
        self.taken = []

    def _targets(self, ctx, buySignal, sellSignal, atr_val, wick=float('nan')):
        super()._targets(ctx, buySignal, sellSignal, atr_val, wick)
        if (buySignal or sellSignal) and ctx.is_confirmed:
            self.taken.append((ctx.bar_index, 1 if buySignal else -1, ctx.close, self.sl_level))


def market_2022(tf):
    bid, ask = _align(_load(BID, tf, WARMUP_FROM), _load(ASK, tf, WARMUP_FROM))
    m1b, m1a = _align(_load(BID, 'M1', START - 86400), _load(ASK, 'M1', START - 86400))
    return Market(tf, bid, ask, m1b, m1a, int(np.searchsorted(bid.time, START)))


def trades_for(tf, m, mode):
    st = store(); feed = st.get_feed(BID)
    r = ScriptRunner(SignalRecorder, {'tfPresetMode': mode}, timeframe=tf, alignment=feed.alignment,
                     precision=feed.precision, htf_provider=StoreHtfProvider(st, feed))
    r.process(m.bid)
    sim, out, free = Simulator(m), [], -1
    for i, side, close, sl in r.script.taken:
        if i < m.first or i < free or m.bid.time[i] >= END:
            continue
        risk = abs(close - sl)
        if not risk > 0:
            continue
        t = sim.run(i, side, sl, risk, close, EXITS['single_3R'])
        if t is None:
            continue
        out.append(dict(time=int(m.bid.time[i]), side=side, r=float(t.r)))
        free = t.exit_bar
    return out


def month(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m')


def agg(trs):
    r = np.array([x['r'] for x in trs])
    n = len(r)
    return dict(trades=n, wins=int((r > 0).sum()), losses=int((r <= 0).sum()), win_rate=float((r > 0).mean()) if n else 0.0,
                net_r=float(r.sum()) if n else 0.0)


if __name__ == '__main__':
    months = [f'{y}-{m:02d}' for y in range(2022, 2027) for m in range(1, 13) if f'{y}-{m:02d}' < '2026-10']
    res = {}
    for tf in TFS:
        m = market_2022(tf)
        for mode in MODES:
            trs = trades_for(tf, m, mode)
            res[mode, tf] = trs
            a = agg(trs)
            print(f"{mode:26s} {tf:3s} trades {a['trades']:5d} win {a['win_rate']:.0%} net {a['net_r']:+.1f}R", flush=True)
    report = {}
    for mode in MODES:
        allt = [x for tf in TFS for x in res[mode, tf]]
        report[mode] = dict(
            monthly={mo: agg([x for x in allt if month(x['time']) == mo]) for mo in months},
            monthly_tf={tf: {mo: agg([x for x in res[mode, tf] if month(x['time']) == mo]) for mo in months} for tf in TFS},
            yearly={y: agg([x for x in allt if month(x['time']).startswith(y)]) for y in ('2022', '2023', '2024', '2025', '2026')},
            yearly_tf={tf: {y: agg([x for x in res[mode, tf] if month(x['time']).startswith(y)]) for y in ('2022', '2023', '2024', '2025', '2026')} for tf in TFS},
            total=agg(allt), total_tf={tf: agg(res[mode, tf]) for tf in TFS})
    (OUT / 'backtest_2022.json').write_text(json.dumps(report, indent=1))
    for mode in MODES:
        t = report[mode]['total']
        print(f"\n{mode}: {t['trades']} trades, win {t['win_rate']:.1%}, total {t['net_r']:+.1f}R | yearly "
              + ' '.join(f"{y}:{v['net_r']:+.1f}R" for y, v in report[mode]['yearly'].items()), flush=True)
