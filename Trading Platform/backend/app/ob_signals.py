"""Order block (OB) trade signals on the 5m chart: N confirmation candles at an order block of a chosen timeframe.

Not overlaps: every order block of every selected source timeframe is traded on its own.

Signal: once an order block is known (the close of the source candle that created it), a 5m candle that touches it
(wick into the block) starts the watch. A *confirmation* candle closes in the reversal direction and beyond the
block's near edge: bullish (close > open) and above the top for a bullish OB, bearish and below the bottom for a
bearish OB. The first confirmation candle must touch the block or come right after a candle that did; the next ones
must follow without a break. ``confirmations`` such candles in a row give BUY / SELL at the close of the last one.
A candle in between that does not confirm restarts the count. A 5m close beyond the far edge breaks the block, and a
block the indicator removed on its own timeframe is gone from then on. Each order block gives at most one signal.

Trade: exactly the D/S overlap rule (``overlap_signals``): entry at the signal close, SL $3 beyond the confirmation
candles' extreme shadow (low for a buy, high for a sell), the $3 cut to keep the stop at most $15 and no trade when
the shadow alone is longer; TP 1R / 2R / 3R; the SL never moves; one trade at a time.
Chart candles only, no spread: a display record, not a backtest.
"""
from __future__ import annotations

import math
from bisect import bisect_left

import numpy as np

STEP = 300  # 5m
TF_LABELS = {'M1': '1m', 'M5': '5m', 'M15': '15m', 'M30': '30m', 'H1': '1H', 'H4': '4H'}


def ob_signal_candidates(rows, blocks: list[dict], confirmations: int, as_of: int,
                         floor: int | None = None) -> tuple[list[dict], list[dict]]:
    """``rows``: closed 5m (t, o, h, l, c, ...) rows, oldest first. ``blocks``: dicts with id, timeframe, kind, side
    (+1 bullish / -1 bearish), top, bottom, origin, known_at and ended_at (None while the indicator holds it).
    ``floor``: candles before this time are never scanned (keeps a replay's record independent of cached history).

    Returns (signals, live): signal candidates in the reaction format ``overlap_signals`` reads, and the blocks
    still waiting at ``as_of`` (known, not removed, not broken, not signalled)."""
    n = bisect_left([r[0] for r in rows], as_of - STEP + 1)
    if not n or not blocks:
        return [], []
    times = np.fromiter((r[0] for r in rows[:n]), dtype=np.int64, count=n)
    o = np.fromiter((r[1] for r in rows[:n]), dtype=float, count=n)
    h = np.fromiter((r[2] for r in rows[:n]), dtype=float, count=n)
    l = np.fromiter((r[3] for r in rows[:n]), dtype=float, count=n)
    c = np.fromiter((r[4] for r in rows[:n]), dtype=float, count=n)
    first = 0 if floor is None else int(np.searchsorted(times, floor, 'left'))
    signals, live = [], []
    for b in blocks:
        bull = b['side'] == 1
        top, bottom = b['top'], b['bottom']
        if b['known_at'] > as_of:
            continue
        start = max(first, int(np.searchsorted(times, b['known_at'], 'left')))  # candles opening after it is known
        # candles closed by its removal on its own timeframe; later ones no longer see it
        end = n if b['ended_at'] is None else int(np.searchsorted(times, b['ended_at'] - STEP, 'right'))
        touch = (l <= top) if bull else (h >= bottom)
        count, prev_touch, extreme, touched_at, i = 0, False, math.nan, None, start
        done = False
        while i < end:
            if not count and not prev_touch:  # nothing in progress: jump to the next candle touching the block
                k = np.flatnonzero(touch[i:end])
                if not len(k):
                    break
                i += int(k[0])
            t = int(times[i])
            touched_at = t if touched_at is None and touch[i] else touched_at
            confirm = (c[i] > o[i] and c[i] > top) if bull else (c[i] < o[i] and c[i] < bottom)
            if confirm and (count or touch[i] or prev_touch):
                shadow = l[i] if bull else h[i]  # the stop sits beyond the deepest shadow of the confirmations
                extreme = shadow if not count else min(extreme, shadow) if bull else max(extreme, shadow)
                count += 1
                if count >= confirmations:
                    signals.append(dict(
                        time=t, confirmed_at=t + STEP, timeframe='M5', reaction='rejection', neutral=False,
                        bullish=bull, side='demand' if bull else 'supply', bottom=bottom, top=top,
                        lock_id=f"ob:{b['timeframe']}:{b['id']}", locked_at=b['known_at'], touched_at=touched_at,
                        code='OB', name=f"{'Bullish' if bull else 'Bearish'} {b['kind']} OB {TF_LABELS[b['timeframe']]}",
                        patterns=[f"{confirmations} × 5m confirmation"], open=float(o[i]), close=float(c[i]),
                        price=float(extreme),
                        meta=dict(ob_timeframe=b['timeframe'], ob_kind=b['kind'], ob_origin=b['origin'],
                                  confirmations=confirmations)))
                    done = True
                    break
            else:
                count = 0
            if (c[i] < bottom) if bull else (c[i] > top):
                done = True  # closed through the block: broken
                break
            prev_touch = bool(touch[i])
            i += 1
        if not done and end == n:
            live.append({**b, 'touched_at': touched_at, 'count': count})
    signals.sort(key=lambda r: (r['time'], r['lock_id']))
    return signals, live


def nearest_live(live: list[dict], close: float, per_side: int = 2) -> list[dict]:
    """The ``per_side`` waiting blocks nearest to price per source timeframe and side."""
    out = []
    for tf in TF_LABELS:
        for side in (1, -1):
            group = [b for b in live if b['timeframe'] == tf and b['side'] == side]
            group.sort(key=lambda b: max(b['bottom'] - close, close - b['top'], 0))
            out += group[:per_side]
    return out
