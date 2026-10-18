"""Trade simulation on real Exness bid / ask candles.

* A signal on the close of bar i enters at the open of bar i+1: buys at the ask, sells at the bid, plus slippage.
* Long exits trade on the bid, short exits on the ask (the spread is paid on the way in and out).
* Stop fills get adverse slippage; a bar opening beyond the stop fills at that (worse) open. Targets fill at their
  price (or at a better open on a gap), never with intrabar improvement.
* When a bar reaches both a stop and a target, the 1-minute candles inside it decide the order; if both happen in
  the same minute the stop is assumed first (the worse case).
* R is measured against the actual risk at the fill: |entry fill - stop|.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .data import TF_SECONDS, Market

SLIPPAGE = 0.05


@dataclass(frozen=True)
class Exit:
    """Take-profit plan: ``targets`` = ((R multiple, fraction), ...); ``be`` moves the stop to entry after TP1."""
    name: str
    targets: tuple
    be: bool = False


EXITS = {
    'single_1R': Exit('single_1R', ((1.0, 1.0),)),
    'single_1.5R': Exit('single_1.5R', ((1.5, 1.0),)),
    'single_2R': Exit('single_2R', ((2.0, 1.0),)),
    'single_3R': Exit('single_3R', ((3.0, 1.0),)),  # the original indicator: closes at TP3 or SL
    'scaled_123': Exit('scaled_123', ((1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)), be=True),
}


@dataclass
class Trade:
    signal_bar: int
    side: int
    entry_bar: int
    exit_bar: int
    entry: float
    stop: float
    risk: float
    r: float
    tp_hits: list = field(default_factory=list)  # per target: reached before the trade closed
    outcome: str = ''  # 'tp' (all targets), 'sl', 'be', 'partial+sl', 'partial+be', 'open'
    spread_r: float = 0.0  # spread paid at entry, in R


class Simulator:
    def __init__(self, m: Market):
        self.m = m
        b, a = m.bid, m.ask
        self.bo, self.bh, self.bl, self.bc = b.open, b.high, b.low, b.close
        self.ao, self.ah, self.al, self.ac = a.open, a.high, a.low, a.close
        self.n = len(b)
        sec = TF_SECONDS[m.tf]
        mt = m.m1_bid.time
        self.m1_lo = np.searchsorted(mt, b.time, side='left')
        self.m1_hi = np.searchsorted(mt, b.time + sec, side='left')
        self.through = 0.0  # set by run_limit for the fill-minute search
        self.m1 = (m.m1_bid.open, m.m1_bid.high, m.m1_bid.low, m.m1_ask.open, m.m1_ask.high, m.m1_ask.low)

    def _first_touch(self, j: int, side: int, stop: float, tp: float) -> int:
        """First bar >= j whose exit-side range reaches the stop or the next target (n if none)."""
        n, step = self.n, 512
        if side == 1:
            lo, hi = self.bl, self.bh
            while j < n:
                k = min(n, j + step)
                hit = (lo[j:k] <= stop) | (hi[j:k] >= tp)
                if hit.any():
                    return j + int(hit.argmax())
                j, step = k, step * 2
        else:
            lo, hi = self.al, self.ah
            while j < n:
                k = min(n, j + step)
                hit = (hi[j:k] >= stop) | (lo[j:k] <= tp)
                if hit.any():
                    return j + int(hit.argmax())
                j, step = k, step * 2
        return n

    def _paths(self, j: int, side: int):
        """(open, high, low) of the exit side for the 1-minute candles of bar j (the bar itself without M1 data)."""
        a, b = int(self.m1_lo[j]), int(self.m1_hi[j])
        bo, bh, bl, ao, ah, al = self.m1
        if b > a:
            return (bo[a:b], bh[a:b], bl[a:b]) if side == 1 else (ao[a:b], ah[a:b], al[a:b])
        return ((self.bo[j:j + 1], self.bh[j:j + 1], self.bl[j:j + 1]) if side == 1 else
                (self.ao[j:j + 1], self.ah[j:j + 1], self.al[j:j + 1]))

    def run_limit(self, i: int, side: int, price: float, stop: float, plan: Exit, expiry: int,
                  through: float = 0.0) -> Trade | None:
        """Limit entry at ``price`` (the swept level) during bars i+1 .. i+expiry; cancelled if the stop trades first.
        A buy limit fills when the ask reaches the price (sells: the bid); a gap through it fills at the better open.
        Targets are R multiples of the planned risk |price - stop|, measured from the limit price. ``through`` > 0
        only counts a fill when price trades that far beyond the limit (a touch alone may not fill a resting order)."""
        risk_ref = abs(price - stop)
        if not risk_ref > 0:
            return None
        self.through = through
        for j in range(i + 1, min(self.n, i + 1 + expiry)):
            if side == 1:
                if self.bl[j] <= stop and self.al[j] > price:  # stop side traded before any fill: cancel
                    return None
                if self.al[j] <= price - through:
                    fill = min(price, self.ao[j]) if self.ao[j] <= price else price
                    return self._manage(i, j, side, fill, stop, price, risk_ref, plan, entry_bar_open=False)
            else:
                if self.ah[j] >= stop and self.bh[j] < price:
                    return None
                if self.bh[j] >= price + through:
                    fill = max(price, self.bo[j]) if self.bo[j] >= price else price
                    return self._manage(i, j, side, fill, stop, price, risk_ref, plan, entry_bar_open=False)
        return None

    def run(self, i: int, side: int, stop: float, risk_ref: float, close_ref: float, plan: Exit) -> Trade | None:
        """Enter on bar i+1. Targets are placed at close_ref +- R * risk_ref (the plan known at the signal close)."""
        j0 = i + 1
        if j0 >= self.n:
            return None
        entry = (self.ao[j0] + SLIPPAGE) if side == 1 else (self.bo[j0] - SLIPPAGE)
        return self._manage(i, j0, side, entry, stop, close_ref, risk_ref, plan)

    def _manage(self, i: int, j0: int, side: int, entry: float, stop: float, close_ref: float, risk_ref: float,
                plan: Exit, entry_bar_open: bool = True) -> Trade | None:
        """Manage a position entered on bar j0 at ``entry`` (at that bar's open, or later inside it for a limit)."""
        risk = (entry - stop) * side
        if not risk > 0:
            return None
        spread_r = (self.ao[j0] - self.bo[j0]) / risk
        targets = [(close_ref + side * r * risk_ref, f) for r, f in plan.targets]
        hits = [False] * len(targets)
        cur_stop, left, k, r_sum = stop, 1.0, 0, 0.0
        j = j0
        moved_be = False
        while True:
            tp = targets[k][0] if k < len(targets) else (np.inf if side == 1 else -np.inf)
            j = self._first_touch(j, side, cur_stop, tp)
            if j >= self.n:  # still open at the end of the data: close at the last exit-side close
                last = self.bc[-1] if side == 1 else self.ac[-1]
                r_sum += left * (last - entry) * side / risk
                return Trade(i, side, j0, self.n - 1, entry, stop, risk, r_sum, hits, 'open', spread_r)
            op, hi, lo = self._paths(j, side)
            q0 = 0
            if j == j0 and not entry_bar_open:
                # limit fill inside bar j0: manage from the minute the entry side first reached the limit price
                eo, eh, el = self._paths(j0, -side)
                reach = (el <= entry - self.through) if side == 1 else (eh >= entry + self.through)
                q0 = int(reach.argmax()) if reach.any() else 0
            for q in range(q0, len(op)):
                # one minute: a gap at its open first, then the stop before the target when both are in range;
                # after a target fills inside the minute its open is in the past, so only ranges are checked
                filled_here = j == j0 and q == q0 and not entry_bar_open  # entered during this minute
                while True:
                    tp = targets[k][0] if k < len(targets) else None
                    if side == 1:
                        s_hit = lo[q] <= cur_stop
                        t_hit = tp is not None and hi[q] >= tp
                        s_gap = not filled_here and op[q] <= cur_stop
                        t_gap = not filled_here and tp is not None and op[q] >= tp
                    else:
                        s_hit = hi[q] >= cur_stop
                        t_hit = tp is not None and lo[q] <= tp
                        s_gap = not filled_here and op[q] >= cur_stop
                        t_gap = not filled_here and tp is not None and op[q] <= tp
                    if s_gap or (s_hit and not t_gap):
                        px = (op[q] if s_gap else cur_stop) - side * SLIPPAGE
                        r_sum += left * (px - entry) * side / risk
                        outcome = 'sl' if k == 0 else ('partial+be' if moved_be else 'partial+sl')
                        return Trade(i, side, j0, j, entry, stop, risk, r_sum, hits, outcome, spread_r)
                    if t_hit:
                        px = op[q] if t_gap else tp
                        f = targets[k][1]
                        r_sum += f * (px - entry) * side / risk
                        left -= f
                        hits[k] = True
                        k += 1
                        filled_here = True
                        if plan.be and k == 1:
                            cur_stop, moved_be = entry, True
                        if k >= len(targets) or left <= 1e-9:
                            return Trade(i, side, j0, j, entry, stop, risk, r_sum, hits, 'tp', spread_r)
                        continue
                    break
            j += 1
