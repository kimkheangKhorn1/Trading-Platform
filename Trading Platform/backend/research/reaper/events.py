"""Liquidity Reaper sweep events, computed exactly like the Pine script (see app/scripts/liquidity_reaper.py), plus
the context of each signal that the weakness analysis and the improved rules need.

Everything about a signal is known at the close of its confirmation bar ``i``; nothing reads later bars."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.engine.ta import ATR, SMA, ema
from app.scripts.pinelib import Pivot

from .data import TF_SECONDS, Market, market


@dataclass(frozen=True)
class GenParams:
    pivot_len: int = 8
    confirm_window: int = 3
    max_pools: int = 12
    min_wick_pct: float = 25.0
    use_vol: bool = True
    vol_len: int = 20
    vol_mult: float = 1.1
    atr_len: int = 14
    # 'close_back' (original): a close back inside the pool with a candle of the signal's color.
    # 'mss': additionally the close must break the raid candle's opposite extreme (market structure shift).
    confirm: str = 'close_back'


@dataclass
class Signal:
    i: int  # confirmation bar (signal on its close)
    side: int  # +1 buy (sell-side liquidity swept), -1 sell
    level: float  # swept pool
    extreme: float  # sweep wick extreme up to the confirmation bar
    raid_bar: int
    pool_age: int  # bars between the pivot and the raid
    swept: int  # pools of this side taken out on the raid bar
    atr: float
    close: float
    open: float
    vol_mult: float
    ema50: float
    ema200: float
    htf_trend: int  # H4 EMA50 vs EMA200 on the last completed H4 bar: +1 up, -1 down, 0 unknown
    hour: int  # UTC hour of the confirmation bar's open
    spread: float = 0.0  # ask - bid at the confirmation close


def _htf_trend(m: Market) -> np.ndarray:
    """+1 / -1 / 0 per chart bar from the last H4 bar that had closed by the chart bar's close (no lookahead)."""
    h4 = market('H4').bid
    e50, e200 = ema(h4.close, 50), ema(h4.close, 200)
    trend = np.where(np.isfinite(e200), np.sign(e50 - e200), 0)
    h4_close = h4.time + 14400
    chart_close = m.bid.time + TF_SECONDS[m.tf]
    k = np.searchsorted(h4_close, chart_close, side='right') - 1
    out = np.zeros(len(m.bid), dtype=int)
    ok = k >= 0
    out[ok] = trend[k[ok]]
    return out


def generate(m: Market, g: GenParams) -> list[Signal]:
    b = m.bid
    n = len(b)
    o, h, l, c, v, t = b.open, b.high, b.low, b.close, b.volume, b.time
    e50, e200 = ema(c, 50), ema(c, 200)
    htf = _htf_trend(m)
    atr_s, vol_s = ATR(g.atr_len), SMA(g.vol_len)
    ph_s, pl_s = Pivot(g.pivot_len, g.pivot_len, True), Pivot(g.pivot_len, g.pivot_len, False)
    mintick = 0.001
    high_pools: list[tuple[float, int]] = []  # (level, born bar)
    low_pools: list[tuple[float, int]] = []
    pend = {1: None, -1: None}  # side -> [level, extreme, break_bar, raid_range, born, swept, raid_hi, raid_lo]
    ask_c = m.ask.close
    out: list[Signal] = []
    L, cw = g.pivot_len, g.confirm_window
    for i in range(n):
        hi, lo, cl, op = float(h[i]), float(l[i]), float(c[i]), float(o[i])
        atr = atr_s.update(hi, lo, cl)
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
        # raids: newest to oldest, the last (oldest) raided pool becomes the pending sweep, as in Pine
        swept = 0
        for k in range(len(high_pools) - 1, -1, -1):
            lvl, born = high_pools[k]
            if hi > lvl:
                swept += 1
                pend[-1] = [lvl, hi, i, rng, born, 0, hi, lo]
                del high_pools[k]
        if pend[-1] is not None and pend[-1][2] == i:
            pend[-1][5] = swept
        swept = 0
        for k in range(len(low_pools) - 1, -1, -1):
            lvl, born = low_pools[k]
            if lo < lvl:
                swept += 1
                pend[1] = [lvl, lo, i, rng, born, 0, hi, lo]
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
            lvl, ext, bb, rr, born, sw, rhi, rlo = p
            elapsed = i - bb
            wick = ((ext - lvl) if side == -1 else (lvl - ext)) / rr * 100
            if 1 <= elapsed <= cw:
                back = (cl < lvl and cl < op) if side == -1 else (cl > lvl and cl > op)
                if g.confirm == 'mss':
                    back = back and ((cl < rlo) if side == -1 else (cl > rhi))
                if back and vol_ok and wick >= g.min_wick_pct:
                    hour = int((t[i] // 3600) % 24)
                    out.append(Signal(i, side, lvl, ext, bb, bb - born, sw, atr, cl, op, vm, float(e50[i]),
                                      float(e200[i]), int(htf[i]), hour, float(ask_c[i] - cl)))
                    pend[side] = None
            elif elapsed > cw:
                pend[side] = None
    return out
