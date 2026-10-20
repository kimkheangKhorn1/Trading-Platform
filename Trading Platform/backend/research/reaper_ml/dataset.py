"""Step 1 of the REAPER ML: the training data.

For every timeframe, the REAL indicator (Liquidity Reaper [JOAT], "Most consistent 2024-2026" preset, ML off) runs on
Exness XAUUSD 2019-01-01 -> 2026-10-01 with ``collect`` on (candles from 2018-09 warm the indicators up). The user's test
period is 2024-01-01 -> 2026-10-01; the years before it are needed to TRAIN the model that is tested on it (training
only inside 2024-2026 left too few signals: no model passed), so it records the ML feature row of every signal it produces
(app/scripts/reaper_ml.signal_features — the same code the live filter uses). Each signal is then traded on its own
with the usual simulator (next-open entry on bid/ask + slippage, the indicator's stop, 3R target, 1-minute stop/target
order) to get its label: did it win (R > 0) and how many R.

Output: out/dataset_<tf>.npz with time, bar index, side, features X, trade R and the trade's exit bar (the exit bar is
needed later to replay "one trade at a time"), and out/reaper_ml_dataset.csv (all timeframes, one row per signal) for
the Colab notebook. research/reaper_ml/check_dataset.py proves the recorder is correct before training.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.scripts.liquidity_reaper import LiquidityReaper
from app.scripts.reaper_ml import FEATURES
from research.reaper.data import ASK, BID, Market, _align, _load, store, ts
from research.reaper.sim import EXITS, Simulator

# Period: default 2019-01-01 -> 2026-10-01; REAPER_ML_PERIOD="2018-01-01:2026-01-01" builds another period into files
# tagged with it (dataset_<tf>_2018-01-01_2026-01-01.npz, reaper_ml_dataset_2018-01-01_2026-01-01.csv).
import os
_PERIOD = os.environ.get('REAPER_ML_PERIOD')
START, END = (ts(x) for x in _PERIOD.split(':')) if _PERIOD else (ts('2019-01-01'), ts('2026-10-01'))
WARMUP_FROM = START - 122 * 86400  # ~4 months of candles before START warm the indicators up
SUFFIX = '_' + _PERIOD.replace(':', '_') if _PERIOD else ''


def dt_from(t: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d')
PRESET = 'Most consistent 2024-2026'
TFS = ('M5', 'M15', 'M30', 'H1', 'H4')
OUT = Path(__file__).with_name('out')


class Recorder(LiquidityReaper):
    """The indicator, recording features (collect=True) and the stop it draws for each signal."""
    collect = True

    def setup(self, ctx):
        super().setup(ctx)
        self.stops = {}

    def _targets(self, ctx, buySignal, sellSignal, atr_val, wick=float('nan')):
        super()._targets(ctx, buySignal, sellSignal, atr_val, wick)
        if (buySignal or sellSignal) and ctx.is_confirmed:
            self.stops[(ctx.bar_index, 1 if buySignal else -1)] = (ctx.close, self.sl_level)


def market_from(tf: str) -> Market:
    bid, ask = _align(_load(BID, tf, WARMUP_FROM), _load(ASK, tf, WARMUP_FROM))
    m1b, m1a = _align(_load(BID, 'M1', START - 86400), _load(ASK, 'M1', START - 86400))
    return Market(tf, bid, ask, m1b, m1a, int(np.searchsorted(bid.time, START)))


def build(tf: str) -> dict:
    st = store(); feed = st.get_feed(BID)
    m = market_from(tf)
    r = ScriptRunner(Recorder, {'tfPresetMode': PRESET}, timeframe=tf, alignment=feed.alignment, precision=feed.precision,
                     htf_provider=StoreHtfProvider(st, feed))
    r.process(m.bid)
    sim = Simulator(m)
    rows = []
    for i, side, x in r.script.signal_rows:
        if i < m.first or m.bid.time[i] >= END or (i, side) not in r.script.stops:
            continue
        close, sl = r.script.stops[(i, side)]
        risk = abs(close - sl)
        t = sim.run(i, side, sl, risk, close, EXITS['single_3R']) if risk > 0 else None
        if t is None:
            continue
        rows.append((int(m.bid.time[i]), i, side, x, float(t.r), int(t.exit_bar)))
    d = dict(time=np.array([x[0] for x in rows]), i=np.array([x[1] for x in rows]), side=np.array([x[2] for x in rows]),
             X=np.array([x[3] for x in rows], dtype=float), r=np.array([x[4] for x in rows]), exit=np.array([x[5] for x in rows]),
             features=np.array(FEATURES))
    np.savez_compressed(OUT / f'dataset_{tf}{SUFFIX}.npz', **d)
    return d


def write_csv(tfs=TFS) -> Path:
    """One CSV for Colab: tf, signal time (UTC seconds and text), side, the 23 inputs, R, win, bar and exit bar."""
    import csv
    import datetime as dt
    path = OUT / f'reaper_ml_dataset{SUFFIX}.csv'
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['tf', 'time', 'time_utc', 'side', *FEATURES, 'r', 'win', 'bar', 'exit_bar'])
        for tf in tfs:
            d = np.load(OUT / f'dataset_{tf}{SUFFIX}.npz')
            for k in range(len(d['r'])):
                t = int(d['time'][k])
                w.writerow([tf, t, dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M'), int(d['side'][k]),
                            *[repr(float(v)) for v in d['X'][k]], repr(float(d['r'][k])), int(d['r'][k] > 0),
                            int(d['i'][k]), int(d['exit'][k])])
    return path


if __name__ == '__main__':
    if sys.argv[1:] == ['csv']:
        print(write_csv())
        sys.exit(0)
    for tf in sys.argv[1:] or TFS:
        t0 = time.time()
        d = build(tf)
        print(f"{tf}: {len(d['r'])} signals {dt_from(START)}..{dt_from(END)}, win {np.mean(d['r'] > 0):.1%}, avg {d['r'].mean():+.3f}R, "
              f"{time.time() - t0:.0f}s", flush=True)
