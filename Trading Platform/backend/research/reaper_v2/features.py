"""Per-bar technical inputs for REAPER filters, every value known at that bar's close (no lookahead).

Sources in this project: engine.ta (EMA/RMA/ATR/SMA, as the 3EMA indicator uses), SMC Master per-bar state
(context.py: structure, premium/discount, order blocks, D/S, pivot S/R, candle patterns, BOS/CHoCH, dashboard),
Triple Confluence Navigator (its own code, recorded: adaptive-RSI supertrend, structure trend, its three signals),
Footprint-style delta from the 1-minute candles inside each bar, and higher-timeframe trends / previous-day levels
from the last COMPLETED higher-timeframe candle.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from app.engine.runner import ScriptRunner
from app.engine.ta import atr as atr_v, ema, rma, sma
from app.scripts.triple_confluence import TripleConfluence
from research.reaper.data import TF_SECONDS, market
from research.reaper_v2.context import record

SEC = TF_SECONDS


class TCRecorder(TripleConfluence):
    def setup(self, ctx):
        super().setup(ctx)
        self.rec = {k: [] for k in ('tc_up', 'tc_trend3', 'tc_b1', 'tc_s1', 'tc_b2', 'tc_s2', 'tc_b3', 'tc_s3', 'tc_long', 'tc_short')}

    def on_bar(self, ctx):
        super().on_bar(ctx)
        r, S = self.rec, self.since
        r['tc_up'].append(1 if self.trendUp else -1); r['tc_trend3'].append(getattr(self, 'trend3', 0) or 0)
        for k in ('b1', 's1', 'b2', 's2', 'b3', 's3'):
            r['tc_' + k].append(S[k])
        new = [e['title'] for e in self.events[-2:] if e['time'] == ctx.time and ctx.is_confirmed]
        r['tc_long'].append('BUY Signal' in new); r['tc_short'].append('SELL Signal' in new)

    def on_last_bar(self, ctx):
        pass

    def extra_output(self):
        return {}


def _rsi(c, n):
    ch = np.r_[np.nan, np.diff(c)]
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0)), n)
    dn = rma(np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0)), n)
    with np.errstate(divide='ignore', invalid='ignore'):
        r = 100 - 100 / (1 + up / dn)
    r = np.where((dn == 0) & np.isfinite(up), 100.0, r)
    return np.where((up == 0) & np.isfinite(dn), 0.0, r)


def _htf_last_closed(tf, htf, values):
    """Value of the last ``htf`` candle that had closed by each chart candle's close."""
    hb = market(htf).bid
    k = np.searchsorted(hb.time + SEC[htf], market(tf).bid.time + SEC[tf], side='right') - 1
    out = np.full(len(k), np.nan)
    ok = k >= 0
    out[ok] = values[k[ok]]
    return out


@lru_cache(maxsize=None)
def bars(tf):
    b = market(tf).bid
    o, h, l, c, v = b.open, b.high, b.low, b.close, b.volume
    F = dict(o=o, h=h, l=l, c=c)
    F['atr'] = atr_v(h, l, c, 14)
    F['atr100'] = sma(F['atr'], 100)
    F['rsi'] = _rsi(c, 14)
    for n in (20, 50, 100, 200):
        F[f'ema{n}'] = ema(c, n)
    F['slope50'] = (F['ema50'] - np.r_[np.full(10, np.nan), F['ema50'][:-10]]) / F['atr']
    # DMI / ADX (14)
    up, dn = np.r_[np.nan, np.diff(h)], np.r_[np.nan, -np.diff(l)]
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.fmax(h - l, np.fmax(np.abs(h - np.r_[np.nan, c[:-1]]), np.abs(l - np.r_[np.nan, c[:-1]])))
    trs, p_, m_ = rma(tr, 14), rma(pdm, 14), rma(mdm, 14)
    with np.errstate(divide='ignore', invalid='ignore'):
        pdi, mdi = 100 * p_ / trs, 100 * m_ / trs
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    F['adx'], F['pdi'], F['mdi'] = rma(dx, 14), pdi, mdi
    # stochastic %K(14) smoothed 3, MACD histogram (12, 26, 9)
    hh = np.array([h[max(0, i - 13):i + 1].max() for i in range(len(h))]); ll = np.array([l[max(0, i - 13):i + 1].min() for i in range(len(l))])
    with np.errstate(divide='ignore', invalid='ignore'):
        F['stoch'] = sma(100 * (c - ll) / (hh - ll), 3)
    macd = ema(c, 12) - ema(c, 26)
    F['macdh'] = macd - ema(macd, 9)
    # SMC ATR bands: SMA 47 +- 3 x ATR(89) scaled as in the indicator
    ma, a89 = sma(c, 47), atr_v(h, l, c, 89)
    F['band_up'], F['band_dn'] = ma + ma * a89 / c * 3, ma - ma * a89 / c * 3
    F['sma47'] = ma
    # fair value gap printed on this bar (3-candle imbalance)
    F['fvg_bull'] = np.r_[False, False, l[2:] > h[:-2]]; F['fvg_bear'] = np.r_[False, False, h[2:] < l[:-2]]
    # higher-timeframe trends (EMA50 vs EMA200) and previous completed day's high / low
    for htf in ('H1', 'H4'):
        hc = market(htf).bid.close
        F[f'trend_{htf}'] = _htf_last_closed(tf, htf, np.sign(ema(hc, 50) - ema(hc, 200)))
    d = market('D').bid
    F['pdh'], F['pdl'] = _htf_last_closed(tf, 'D', d.high), _htf_last_closed(tf, 'D', d.low)
    # footprint-style delta: 1-minute volume of up candles minus down candles inside each bar, / total
    m1 = market(tf).m1_bid
    lo = np.searchsorted(m1.time, b.time); hi = np.searchsorted(m1.time, b.time + SEC[tf])
    sv = np.where(m1.close > m1.open, m1.volume, np.where(m1.close < m1.open, -m1.volume, 0.0))
    cs, ct = np.r_[0, np.cumsum(sv)], np.r_[0, np.cumsum(m1.volume)]
    with np.errstate(divide='ignore', invalid='ignore'):
        F['delta'] = np.where(hi > lo, (cs[hi] - cs[lo]) / (ct[hi] - ct[lo]), np.nan)
    F['hour'] = (b.time // 3600) % 24
    F['dow'] = ((b.time // 86400) + 3) % 7  # 0 = Monday
    F.update(record(tf))
    r = ScriptRunner(TCRecorder, {}, timeframe=tf, alignment='ny17', precision=3)
    r.process(b)
    F.update({k: np.asarray(v, dtype=float) for k, v in r.script.rec.items()})
    return F


def candidates():
    """name -> f(F, s) -> bool. Written for a buy (side +1); ``d`` mirrors it for sells."""
    C = {}

    def add(name, fn):
        C[name] = fn

    # ---- trend / structure
    add('3EMA 50/100/200 stacked with trade', lambda F, s, d, i: (F['ema50'][i] - F['ema100'][i]) * d > 0 and (F['ema100'][i] - F['ema200'][i]) * d > 0)
    add('3EMA against trade (reversal)', lambda F, s, d, i: (F['ema50'][i] - F['ema100'][i]) * d < 0 and (F['ema100'][i] - F['ema200'][i]) * d < 0)
    add('close on trade side of EMA200', lambda F, s, d, i: (F['c'][i] - F['ema200'][i]) * d > 0)
    add('EMA50 rising with trade', lambda F, s, d, i: F['slope50'][i] * d > 0)
    add('EMA20 / EMA50 with trade', lambda F, s, d, i: (F['ema20'][i] - F['ema50'][i]) * d > 0)
    add('1H trend with trade', lambda F, s, d, i: F['trend_H1'][i] * d > 0)
    add('SMC internal structure with trade', lambda F, s, d, i: F['itrend'][i] == d)
    add('SMC swing structure with trade', lambda F, s, d, i: F['strend'][i] == d)
    add('SMC internal structure against (reversal)', lambda F, s, d, i: F['itrend'][i] == -d)
    add('Triple Confluence ARSI supertrend with trade', lambda F, s, d, i: F['tc_up'][i] == d)
    add('Triple Confluence structure trend with trade', lambda F, s, d, i: F['tc_trend3'][i] == d)
    add('Triple Confluence any signal in direction (10 bars)', lambda F, s, d, i: min(F['tc_b1' if d > 0 else 'tc_s1'][i], F['tc_b2' if d > 0 else 'tc_s2'][i], F['tc_b3' if d > 0 else 'tc_s3'][i]) <= 10)
    # ---- location
    add('premium/discount: buy in discount, sell in premium', lambda F, s, d, i: math.isfinite(F['pd'][i]) and (F['pd'][i] < 0.5 if d > 0 else F['pd'][i] > 0.5))
    add('sweep at SMC order block', lambda F, s, d, i: F['bull_ob' if d > 0 else 'bear_ob'][s.raid_bar:i + 1].any())
    add('sweep at SMC fresh demand / supply', lambda F, s, d, i: F['demand' if d > 0 else 'supply'][s.raid_bar:i + 1].any())
    add('sweep at SMC pivot S/R zone', lambda F, s, d, i: F['pivot_sup' if d > 0 else 'pivot_res'][s.raid_bar:i + 1].any())
    add('sweep outside SMC ATR band', lambda F, s, d, i: (s.extreme < F['band_dn'][s.raid_bar]) if d > 0 else (s.extreme > F['band_up'][s.raid_bar]))
    add('buy below / sell above the SMA47 band middle', lambda F, s, d, i: (F['c'][i] - F['sma47'][i]) * d < 0)
    add('swept previous day low / high (within 0.5 ATR)', lambda F, s, d, i: math.isfinite(F['pdl'][i]) and (abs(s.level - (F['pdl'][i] if d > 0 else F['pdh'][i])) <= 0.5 * s.atr or ((s.extreme < F['pdl'][i]) if d > 0 else (s.extreme > F['pdh'][i]))))
    # ---- the pool
    add('equal highs / lows swept (2+ pools)', lambda F, s, d, i: s.equal >= 1 or s.swept >= 2)
    add('pool tested before the raid (came within 0.1 ATR)', lambda F, s, d, i: s.tests >= 1)
    add('old pool (age 30+ bars)', lambda F, s, d, i: s.pool_age >= 30)
    add('room to next pool >= 2 ATR', lambda F, s, d, i: s.room >= 2)
    add('room to next pool >= 4 ATR', lambda F, s, d, i: s.room >= 4)
    # ---- candle structure / confirmation
    add('signal candle body >= 50% of range', lambda F, s, d, i: abs(F['c'][i] - F['o'][i]) >= 0.5 * (F['h'][i] - F['l'][i]))
    add('signal candle closes in its outer third', lambda F, s, d, i: (F['c'][i] - F['l'][i] >= 2 / 3 * (F['h'][i] - F['l'][i])) if d > 0 else (F['h'][i] - F['c'][i] >= 2 / 3 * (F['h'][i] - F['l'][i])))
    add('engulfs the previous candle body', lambda F, s, d, i: (F['c'][i] >= max(F['o'][i - 1], F['c'][i - 1]) and F['o'][i] <= min(F['o'][i - 1], F['c'][i - 1])) if d > 0 else (F['c'][i] <= min(F['o'][i - 1], F['c'][i - 1]) and F['o'][i] >= max(F['o'][i - 1], F['c'][i - 1])))
    add('displacement: body >= 0.7 ATR', lambda F, s, d, i: abs(F['c'][i] - F['o'][i]) >= 0.7 * s.atr)
    add('SMC candle pattern in direction (raid..signal)', lambda F, s, d, i: F['pat_bull' if d > 0 else 'pat_bear'][max(0, s.raid_bar - 1):i + 1].any())
    add('fair value gap in direction (raid..signal)', lambda F, s, d, i: F['fvg_bull' if d > 0 else 'fvg_bear'][s.raid_bar:i + 1].any())
    add('SMC CHoCH / BOS in direction (raid..signal+0)', lambda F, s, d, i: (F['choch_bull' if d > 0 else 'choch_bear'][s.raid_bar:i + 1].any() or F['bos_bull' if d > 0 else 'bos_bear'][s.raid_bar:i + 1].any()))
    add('confirmed on the next candle (not the raid candle)', lambda F, s, d, i: i - s.raid_bar >= 2)
    add('confirmed fast (1 candle after raid)', lambda F, s, d, i: i - s.raid_bar == 1)
    # ---- momentum / oscillators
    add('RSI(14) stretched at the raid (<40 buy / >60 sell)', lambda F, s, d, i: (F['rsi'][s.raid_bar] < 40) if d > 0 else (F['rsi'][s.raid_bar] > 60))
    add('RSI divergence vs the pool candle', lambda F, s, d, i: (F['rsi'][s.raid_bar] > F['rsi'][s.raid_bar - s.pool_age]) if d > 0 else (F['rsi'][s.raid_bar] < F['rsi'][s.raid_bar - s.pool_age]))
    add('stochastic was oversold / overbought at raid', lambda F, s, d, i: (F['stoch'][s.raid_bar] < 20) if d > 0 else (F['stoch'][s.raid_bar] > 80))
    add('MACD histogram turning with trade', lambda F, s, d, i: (F['macdh'][i] - F['macdh'][i - 1]) * d > 0)
    add('ADX < 25 (ranging)', lambda F, s, d, i: F['adx'][i] < 25)
    add('ADX >= 25 (trending)', lambda F, s, d, i: F['adx'][i] >= 25)
    add('DMI +DI/-DI with trade', lambda F, s, d, i: (F['pdi'][i] - F['mdi'][i]) * d > 0)
    add('SMC dashboard 5+ of 9 with trade', lambda F, s, d, i: F['dash_buy' if d > 0 else 'dash_sell'][i] >= 5)
    # ---- volume / order flow
    add('volume 1.5x+ on the raid or signal candle', lambda F, s, d, i: s.vol_mult >= 1.5)
    add('footprint delta with trade on signal candle', lambda F, s, d, i: F['delta'][i] * d > 0)
    add('footprint absorption: raid candle delta against trade', lambda F, s, d, i: F['delta'][s.raid_bar] * d < 0)
    # ---- volatility / time
    add('ATR above its 100-bar average', lambda F, s, d, i: F['atr'][i] > F['atr100'][i])
    add('ATR below its 100-bar average', lambda F, s, d, i: F['atr'][i] < F['atr100'][i])
    add('raid candle range <= 2 ATR (no news spike)', lambda F, s, d, i: F['h'][s.raid_bar] - F['l'][s.raid_bar] <= 2 * s.atr)
    add('London / New York hours (07-17 UTC)', lambda F, s, d, i: 7 <= F['hour'][i] < 17)
    add('kill zones (07-10, 12-15 UTC)', lambda F, s, d, i: 7 <= F['hour'][i] < 10 or 12 <= F['hour'][i] < 15)
    add('skip 17-24 UTC', lambda F, s, d, i: F['hour'][i] < 17)
    add('skip Monday before 07 UTC and Friday after 17 UTC', lambda F, s, d, i: not ((F['dow'][i] == 0 and F['hour'][i] < 7) or (F['dow'][i] == 4 and F['hour'][i] >= 17)))
    add('4H trend with trade', lambda F, s, d, i: s.side * s.htf_trend > 0)  # identical to the shipped filter
    return C
