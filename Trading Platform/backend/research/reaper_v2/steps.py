"""REAPER [JOAT]: fix one weakness at a time -> tune on TRAIN -> keep only if VALID improves -> report 2026.

Protocol (fixed before running):
* Trades exactly as the user's backtest: one position at a time, signal on the close, next-open entry on real
  Exness bid/ask, stop / target decided by 1-minute candles (stop first if both in one minute).
* One setting for every timeframe. Score = mean TRAIN (2024-01 -> 2025-06) average R per trade over 5m, 15m, 30m
  and 1H. 1m and 4H are reported, not tuned on.
* Trade count: allowed only if TRAIN trades per trading day on each of 5m/15m/30m/1H are within +-20% of the
  user's 2026 rate (2832 / 622 / 227 / 114 / 49 / 13 trades on 1m..4H). Pivot length, volume filter and minimum
  wick are re-tuned at every step so a filter cannot win by simply trading less.
* A step is kept only if its best TRAIN config also raises the VALID (2025-07 -> 2025-12) mean average R.
* 2026 (the user's window) is reported for every step and never used to decide.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.ta import ema
from research.reaper.data import TEST, TRAIN, VALID, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Exit, Simulator
from research.reaper.study import in_window, one_at_a_time, summarize
from research.reaper.tune import STOPS, trades_for
from research.reaper_v2.context import record

OUT = Path(__file__).with_name('out')
WINDOWS = dict(train=TRAIN, valid=VALID, test=TEST)
TUNE_TFS = ('M5', 'M15', 'M30', 'H1')
ALL_TFS = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4')
TARGET = dict(M1=2832, M5=622, M15=227, M30=114, H1=49, H4=13)
BAND = 0.20
COUNT_GRID = [dict(pivot_len=p, use_vol=v, min_wick_pct=w) for p in (5, 8, 13) for v in (True, False) for w in (10.0, 25.0, 40.0)]
EXITS.update({'be_3R': Exit('be_3R', ((1.0, 0.5), (3.0, 0.5)), be=True), 'be_2R': Exit('be_2R', ((1.0, 0.5), (2.0, 0.5)), be=True)})


@dataclass(frozen=True)
class Cfg:
    pivot_len: int = 8
    use_vol: bool = True
    min_wick_pct: float = 25.0
    confirm: str = 'close_back'  # 'close_back' (original) | 'mss' (close beyond the raid candle)
    stop: str = 'atr1.5'  # 'atr1.5' (original) | 'sweep+0.2' | 'sweep+0.5' (beyond the swept wick)
    entry: str = 'market'  # 'market' (original) | 'retest' (limit at the swept level, 5 bars)
    plan: str = 'single_3R'  # original closes at TP3
    trend: str = 'none'  # none | h4 (H4 EMA50/200) | ema3 (chart 50/100/200, the 3EMA indicator) | smc (internal structure)
    loc: str = 'none'  # none | zone (sweep at an SMC order block / fresh D/S / pivot S/R zone) | no_zone
    skip_late: bool = False  # skip 17:00-24:00 UTC
    spread_guard: bool = False  # skip when the spread is over 10% of the risk

    def short(self):
        return (f"pivot {self.pivot_len} vol={'on' if self.use_vol else 'off'} wick>={self.min_wick_pct:g}% {self.confirm} "
                f"stop={self.stop} entry={self.entry} {self.plan} trend={self.trend} loc={self.loc}"
                f"{' skip17-24' if self.skip_late else ''}{' spread-guard' if self.spread_guard else ''}")


@lru_cache(maxsize=None)
def sim(tf):
    return Simulator(market(tf))


@lru_cache(maxsize=None)
def sigs(tf, pivot_len, use_vol, min_wick_pct, confirm):
    return generate(market(tf), GenParams(pivot_len=pivot_len, use_vol=use_vol, min_wick_pct=min_wick_pct, confirm=confirm))


@lru_cache(maxsize=None)
def trades(tf, pivot_len, use_vol, min_wick_pct, confirm, stop, entry, plan):
    return trades_for(sim(tf), sigs(tf, pivot_len, use_vol, min_wick_pct, confirm), stop, entry, plan)


@lru_cache(maxsize=None)
def ctx(tf):
    return record(tf)


@lru_cache(maxsize=None)
def ema3(tf):
    c = market(tf).bid.close
    return ema(c, 50), ema(c, 100), ema(c, 200)


@lru_cache(maxsize=None)
def trading_days(tf, w):
    t = market(tf).bid.time
    a, b = WINDOWS[w]
    d = np.unique(t[(t >= a) & (t < b)] // 86400)
    return int(np.count_nonzero((d + 3) % 7 < 5))


def keep_fn(tf, c: Cfg):
    k = ctx(tf)
    e50, e100, e200 = ema3(tf)
    rule = STOPS[c.stop]

    def keep(s):
        if c.skip_late and s.hour >= 17:
            return False
        if c.spread_guard:
            ref = s.close if c.entry == 'market' else s.level
            stop = ref - s.side * rule.mult * s.atr if rule.mode == 'atr' else s.extreme - s.side * rule.buffer * s.atr
            if s.spread > 0.10 * abs(ref - stop):
                return False
        if c.trend == 'h4' and s.side * s.htf_trend <= 0:
            return False
        if c.trend == 'ema3' and not ((e50[s.i] > e100[s.i] > e200[s.i]) if s.side == 1 else (e50[s.i] < e100[s.i] < e200[s.i])):
            return False
        if c.trend == 'smc' and k['itrend'][s.i] != s.side:
            return False
        if c.loc != 'none':
            span = slice(s.raid_bar, s.i + 1)
            fields = ('bull_ob', 'demand', 'pivot_sup') if s.side == 1 else ('bear_ob', 'supply', 'pivot_res')
            at = any(k[f][span].any() for f in fields)
            if at != (c.loc == 'zone'):
                return False
        return True
    return keep


@lru_cache(maxsize=None)
def rows(tf, c: Cfg):
    g = (tf, c.pivot_len, c.use_vol, c.min_wick_pct, c.confirm)
    return one_at_a_time(sigs(*g), trades(*g, c.stop, c.entry, c.plan), keep_fn(tf, c), market(tf).first)


def stats(tf, c, w):
    return summarize(in_window(rows(tf, c), market(tf).bid.time, WINDOWS[w]))


def rate_ok(c):
    for tf in TUNE_TFS:
        n = stats(tf, c, 'train')['trades']
        target = TARGET[tf] / trading_days(tf, 'test') * trading_days(tf, 'train')
        if abs(n / target - 1) > BAND:
            return False
    return True


def score(c, w):
    return float(np.mean([stats(tf, c, w)['avg_r'] for tf in TUNE_TFS]))


def search(prev, grid):
    found = []
    for combo in itertools.product(*grid.values()):
        for cg in COUNT_GRID:
            c = replace(prev, **cg, **dict(zip(grid, combo)))
            if rate_ok(c):
                found.append((score(c, 'train'), c))
    found.sort(key=lambda x: -x[0])
    return found


STEPS = [
    ('1. Stop inside the swept wick: stop beyond the sweep', dict(stop=['atr1.5', 'sweep+0.2', 'sweep+0.5'])),
    ('2. "Close back" is not a reversal: market-structure-shift confirmation', dict(confirm=['close_back', 'mss'])),
    ('3. Entry chases the close: limit on the retest of the swept level', dict(entry=['market', 'retest'])),
    ('4. No trend context: H4 trend / 3EMA 50-100-200 / SMC internal structure', dict(trend=['none', 'h4', 'ema3', 'smc'])),
    ('5. No location: sweep at an SMC order block / D/S / pivot zone', dict(loc=['none', 'zone', 'no_zone'])),
    ('6. Late session: skip 17:00-24:00 UTC', dict(skip_late=[False, True])),
    ('7. Spread eats small stops: spread guard', dict(spread_guard=[False, True])),
    ('8. Winners turn into losers: exits', dict(plan=['single_1R', 'single_1.5R', 'single_2R', 'single_3R', 'be_2R', 'be_3R', 'scaled_123'])),
]


def report(c):
    return {tf: {w: stats(tf, c, w) for w in ('train', 'valid', 'test')} for tf in ALL_TFS}


def lines(rep):
    out = []
    for tf in ALL_TFS:
        d = rep[tf]['test']
        out.append(f"    {tf:3s} 2026 trades={d['trades']:4d} (user {TARGET[tf]:4d}) wins={d['wins']:4d} losses={d['losses']:4d} win={d['win_rate']:.0%} "
                   f"netR={d['net_r']:+7.1f} avgR={d['avg_r']:+.3f} PF={d['pf']:.2f} | VALID avgR={rep[tf]['valid']['avg_r']:+.3f} | TRAIN avgR={rep[tf]['train']['avg_r']:+.3f}")
    return '\n'.join(out)


if __name__ == '__main__':
    log = []
    cur = Cfg()
    cur_valid = score(cur, 'valid')
    rep = report(cur)
    print(f"0. Original REAPER [JOAT]: {cur.short()}\n   TRAIN mean {score(cur, 'train'):+.3f}  VALID mean {cur_valid:+.3f}  count ok: {rate_ok(cur)}\n{lines(rep)}", flush=True)
    log.append(dict(step='0. Original', cfg=cur.__dict__, kept=True, train=score(cur, 'train'), valid=cur_valid, report=rep))
    for name, grid in STEPS:
        t0 = time.time()
        found = search(cur, grid)
        if not found:
            print(f"\n{name}: no config keeps the trade count", flush=True)
            log.append(dict(step=name, kept=False, reason='no config in the count band'))
            continue
        tr, best = found[0]
        va = score(best, 'valid')
        kept = va > cur_valid and best != cur
        rep = report(best)
        print(f"\n{name}  ({len(found)} configs keep the count, {time.time() - t0:.0f}s)\n   best on TRAIN: {best.short()}\n"
              f"   TRAIN mean {tr:+.3f}  VALID mean {va:+.3f} (current {cur_valid:+.3f}) -> {'KEPT' if kept else 'REJECTED'}\n{lines(rep)}", flush=True)
        log.append(dict(step=name, cfg=best.__dict__, kept=kept, train=tr, valid=va, report=rep,
                        top5=[dict(train=s, cfg=c.__dict__) for s, c in found[:5]]))
        if kept:
            cur, cur_valid = best, va
    print(f"\nFINAL: {cur.short()}", flush=True)
    (OUT / 'steps.json').write_text(json.dumps(dict(final=cur.__dict__, log=log), indent=1, default=str))
