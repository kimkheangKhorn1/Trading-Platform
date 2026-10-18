"""research.reaper.events.generate plus pool facts at the signal (identical signals; checked by parity()).

Extra per signal, all known at the signal close:
  room   - distance from the close to the nearest live opposite pool (the next liquidity target), in ATR
  tests  - bars that came within 0.1 ATR of the swept level before the raid (how much liquidity rested there)
  equal  - other same-side pools within 0.15 ATR of the swept level when it was raided (equal highs / lows)
  area   - LuxAlgo Liquidity Swings style count: candles after the pivot that traded into the pivot candle's wick
           area (high pool: from its body top to its high; low pool: from its low to its body bottom) before the raid
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.engine.ta import ATR, SMA, ema
from app.scripts.pinelib import Pivot
from research.reaper.data import Market
from research.reaper.events import GenParams, Signal, _htf_trend, generate


@dataclass
class Sig2(Signal):
    room: float = math.inf
    tests: int = 0
    equal: int = 0
    area: int = 0


def generate2(m: Market, g: GenParams) -> list[Sig2]:
    b = m.bid
    n = len(b)
    o, h, l, c, v, t = b.open, b.high, b.low, b.close, b.volume, b.time
    e50, e200 = ema(c, 50), ema(c, 200)
    htf = _htf_trend(m)
    atr_s, vol_s = ATR(g.atr_len), SMA(g.vol_len)
    atrs = np.full(n, np.nan)
    ph_s, pl_s = Pivot(g.pivot_len, g.pivot_len, True), Pivot(g.pivot_len, g.pivot_len, False)
    mintick = 0.001
    high_pools: list[tuple[float, int]] = []
    low_pools: list[tuple[float, int]] = []
    pend = {1: None, -1: None}  # + [tests, equal] at the end
    ask_c = m.ask.close
    out: list[Sig2] = []
    L, cw = g.pivot_len, g.confirm_window
    hl, ll = h.tolist(), l.tolist()

    def area_for(born, i, high):
        top, btm = (h[born], max(o[born], c[born])) if high else (min(o[born], c[born]), l[born])
        if i <= born + 1:
            return 0
        return int(np.count_nonzero((l[born + 1:i] < top) & (h[born + 1:i] > btm)))

    def tests_for(lvl, born, i, a, high):
        j0 = born + L + 1
        if i <= j0 or not math.isfinite(a):
            return 0
        seg = h[j0:i] if high else l[j0:i]
        return int(np.count_nonzero(seg >= lvl - 0.1 * a)) if high else int(np.count_nonzero(seg <= lvl + 0.1 * a))

    for i in range(n):
        hi, lo, cl, op = hl[i], ll[i], float(c[i]), float(o[i])
        atr = atr_s.update(hi, lo, cl)
        atrs[i] = atr
        vma = vol_s.update(float(v[i]))
        vm = float(v[i]) / vma if math.isfinite(vma) and vma > 0 else 1.0
        vol_ok = not g.use_vol or vm >= g.vol_mult
        ph, pl = ph_s.update(hi), pl_s.update(lo)
        if math.isfinite(ph):
            high_pools.append((ph, i - L))
            if len(high_pools) > g.max_pools:
                high_pools.pop(0)
        if math.isfinite(pl):
            low_pools.append((pl, i - L))
            if len(low_pools) > g.max_pools:
                low_pools.pop(0)
        rng = max(hi - lo, mintick)
        swept = 0
        before = list(high_pools)
        for k in range(len(high_pools) - 1, -1, -1):
            lvl, born = high_pools[k]
            if hi > lvl:
                swept += 1
                eq = sum(1 for x, _ in before if x != lvl and math.isfinite(atr) and abs(x - lvl) <= 0.15 * atr)
                pend[-1] = [lvl, hi, i, rng, born, 0, hi, lo, tests_for(lvl, born, i, atr, True), eq, area_for(born, i, True)]
                del high_pools[k]
        if pend[-1] is not None and pend[-1][2] == i:
            pend[-1][5] = swept
        swept = 0
        before = list(low_pools)
        for k in range(len(low_pools) - 1, -1, -1):
            lvl, born = low_pools[k]
            if lo < lvl:
                swept += 1
                eq = sum(1 for x, _ in before if x != lvl and math.isfinite(atr) and abs(x - lvl) <= 0.15 * atr)
                pend[1] = [lvl, lo, i, rng, born, 0, hi, lo, tests_for(lvl, born, i, atr, False), eq, area_for(born, i, False)]
                del low_pools[k]
        if pend[1] is not None and pend[1][2] == i:
            pend[1][5] = swept
        if pend[-1] is not None:
            pend[-1][1] = max(pend[-1][1], hi)
        if pend[1] is not None:
            pend[1][1] = min(pend[1][1], lo)
        for side in (-1, 1):
            p = pend[side]
            if p is None:
                continue
            lvl, ext, bb, rr, born, sw, rhi, rlo, tests, eq, area = p
            elapsed = i - bb
            wick = ((ext - lvl) if side == -1 else (lvl - ext)) / rr * 100
            if 1 <= elapsed <= cw:
                back = (cl < lvl and cl < op) if side == -1 else (cl > lvl and cl > op)
                if g.confirm == 'mss':
                    back = back and ((cl < rlo) if side == -1 else (cl > rhi))
                if back and vol_ok and wick >= g.min_wick_pct:
                    hour = int((t[i] // 3600) % 24)
                    opp = [x for x, _ in (high_pools if side == 1 else low_pools) if (x > cl if side == 1 else x < cl)]
                    room = (min(opp) - cl if side == 1 else cl - max(opp)) / atr if opp and atr > 0 else math.inf
                    out.append(Sig2(i, side, lvl, ext, bb, bb - born, sw, atr, cl, op, vm, float(e50[i]),
                                    float(e200[i]), int(htf[i]), hour, float(ask_c[i] - cl), room, tests, eq, area))
                    pend[side] = None
            elif elapsed > cw:
                pend[side] = None
    return out


def parity(m: Market, g: GenParams) -> bool:
    a = [(s.i, s.side, s.level, s.extreme) for s in generate(m, g)]
    b = [(s.i, s.side, s.level, s.extreme) for s in generate2(m, g)]
    return a == b
