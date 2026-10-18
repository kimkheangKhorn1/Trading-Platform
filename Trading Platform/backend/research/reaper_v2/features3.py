"""Round-3 inputs: everything in features.py plus the rest of the project's indicators.

Added here (all known at the signal close):
  3EMA [three_ema.py] with other lengths (the indicator ships 50/100/200, never tuned): stacked, close vs slow EMA
  Liquidity Swings [LuxAlgo]: candles that traded into the swept pivot's wick area (Sig2.area)
  Volume Supply and Demand [TradingIQ]: sweep inside one of its live demand / supply zones (context3)
  SMC Master: bear / bull trap, pivot breakouts, S/R reactions, TSI curls, EQH/EQL, order block breakouts, swing and
              internal BOS / CHoCH separately, each of the 9 dashboard indicators (context3)
  Triple Confluence: Cardwell RSI cross, adaptive-RSI cross, structure break in the last bars (features.py recorder)
  Footprint IQ: where the signal candle's volume peak (POC from its 1-minute candles) sits in its range
  ADX neighbours (20 / 30) of the round-2 level, so a threshold is never judged alone
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from app.engine.ta import ema
from research.reaper.data import TF_SECONDS, market
from research.reaper_v2.context3 import DASH, record3
from research.reaper_v2.features import bars, candidates

EMA_SETS = ((8, 21, 55), (9, 21, 50), (10, 30, 60), (20, 50, 100), (50, 100, 200))


@lru_cache(maxsize=None)
def ema_of(tf, n):
    return ema(market(tf).bid.close, n)


@lru_cache(maxsize=None)
def bars3(tf):
    F = dict(bars(tf))
    F.update(record3(tf))
    # Footprint-style POC of each candle: the 1-minute candle with the most volume inside it, its mid price,
    # as a fraction of the candle's range (0 = at the low, 1 = at the high)
    b, m1 = market(tf).bid, market(tf).m1_bid
    lo = np.searchsorted(m1.time, b.time); hi = np.searchsorted(m1.time, b.time + TF_SECONDS[tf])
    poc = np.full(len(b), np.nan)
    vol, mid = m1.volume, (m1.high + m1.low) / 2
    for k in np.flatnonzero(hi > lo):
        j = lo[k] + int(np.argmax(vol[lo[k]:hi[k]]))
        rng = b.high[k] - b.low[k]
        if rng > 0:
            poc[k] = (mid[j] - b.low[k]) / rng
    F['poc'] = poc
    for n in sorted({n for t in EMA_SETS for n in t}):
        F[f'e{n}'] = ema_of(tf, n)
    return F


def candidates3():
    C = dict(candidates())

    def add(name, fn):
        C[name] = fn

    def recent(key_bull, key_bear, back):
        return lambda F, s, d, i: F[key_bull if d > 0 else key_bear][max(0, s.raid_bar - back):i + 1].any()

    # 3EMA lengths
    for a, b_, c_ in EMA_SETS:
        add(f'3EMA {a}/{b_}/{c_} stacked with trade',
            lambda F, s, d, i, a=a, b_=b_, c_=c_: (F[f'e{a}'][i] - F[f'e{b_}'][i]) * d > 0 and (F[f'e{b_}'][i] - F[f'e{c_}'][i]) * d > 0)
        add(f'3EMA {a}/{b_}/{c_}: close on trade side of the slow EMA', lambda F, s, d, i, c_=c_: (F['c'][i] - F[f'e{c_}'][i]) * d > 0)
    # Liquidity Swings
    add('LuxAlgo swing area traded into 1+ times', lambda F, s, d, i: s.area >= 1)
    add('LuxAlgo swing area traded into 3+ times', lambda F, s, d, i: s.area >= 3)
    add('LuxAlgo swing area traded into 0-2 times (fresh pool)', lambda F, s, d, i: s.area <= 2)
    # Volume Supply and Demand
    add('sweep inside a Volume S/D zone', lambda F, s, d, i: F['vsd_demand' if d > 0 else 'vsd_supply'][s.raid_bar:i + 1].any())
    # SMC events
    add('SMC bear trap / bull trap (raid..signal)', recent('false_bd', 'false_bu', 0))
    add('SMC reaction from pivot support / resistance (raid..signal)', recent('sup_react', 'res_react', 0))
    add('SMC TSI curl in direction (raid..signal)', recent('tsi_bull', 'tsi_bear', 0))
    add('SMC order block broken in direction (raid..signal)', recent('ob_break_bull', 'ob_break_bear', 0))
    add('SMC internal CHoCH in direction (5 bars)', recent('ichoch_bull', 'ichoch_bear', 5))
    add('SMC swing BOS / CHoCH in direction (last 50 bars)', lambda F, s, d, i: (F['sbos_bull' if d > 0 else 'sbos_bear'][max(0, i - 50):i + 1].any() or F['schoch_bull' if d > 0 else 'schoch_bear'][max(0, i - 50):i + 1].any()))
    add('no SMC pivot breakdown / breakout against (10 bars)', lambda F, s, d, i: not F['pivot_bd' if d > 0 else 'pivot_bo'][max(0, i - 10):i + 1].any())
    add('SMC EQL / EQH printed in the 50 bars before the raid', lambda F, s, d, i: F['eql' if d > 0 else 'eqh'][max(0, s.raid_bar - 50):s.raid_bar + 1].any())
    for k in DASH:
        add(f'SMC dashboard {k.upper()} with trade', lambda F, s, d, i, k=k: F['dash_' + k][i] == d)
    # Triple Confluence components
    add('Triple Confluence Cardwell RSI cross in direction (5 bars)', lambda F, s, d, i: F['tc_b1' if d > 0 else 'tc_s1'][i] <= 5)
    add('Triple Confluence adaptive-RSI cross in direction (5 bars)', lambda F, s, d, i: F['tc_b2' if d > 0 else 'tc_s2'][i] <= 5)
    add('Triple Confluence structure break in direction (10 bars)', lambda F, s, d, i: F['tc_b3' if d > 0 else 'tc_s3'][i] <= 10)
    # Footprint
    add('Footprint POC of signal candle in its far third (buy: low, sell: high)', lambda F, s, d, i: (F['poc'][i] <= 1 / 3) if d > 0 else (F['poc'][i] >= 2 / 3))
    add('Footprint POC of raid candle beyond the pool (absorbed)', lambda F, s, d, i: (F['poc'][s.raid_bar] <= 0.5) if d > 0 else (F['poc'][s.raid_bar] >= 0.5))
    # ADX neighbours
    add('ADX >= 20', lambda F, s, d, i: F['adx'][i] >= 20)
    add('ADX >= 30', lambda F, s, d, i: F['adx'][i] >= 30)
    return C
