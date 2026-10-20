"""How a finished trade is scored, shared by the indicators that record trades and by the tuner.

A trade is a dict with ``open_time``, ``hits`` (TPs reached, 0-3) and ``outcome`` ('tp3' = all targets, 'sl' = stop).
R = the entry-to-stop distance.
"""
from __future__ import annotations

# How a finished trade is scored (R = entry-to-stop distance). 'rule' is the indicator's own rule.
OBJECTIVES = {
    'rule': '+1R for every TP reached, -1R at the stop',
    'split': 'real P&L, a third of the position closed at each TP (1R / 2R / 3R), the rest at the stop',
    'hold': 'real P&L, the whole position held to TP3 (+3R) or the stop (-1R)',
}


def trade_r(t: dict, how: str, rr: tuple = (1.0, 2.0, 3.0)) -> float:
    """Result of one finished trade in R; ``rr`` = the TP1..TP3 distances in R."""
    hits, win = t['hits'], t['outcome'] == 'tp3'
    if how == 'hold':
        return rr[2] if win else -1.0
    if how == 'split':
        banked = sum(rr[:hits]) / 3
        return banked if win else banked - (3 - hits) / 3
    return hits - (0 if win else 1)


def summarize(trades: list[dict], start: int, end: int, rr: tuple = (1.0, 2.0, 3.0)) -> dict:
    """Counts and scores of the trades opened in [start, end] that have finished."""
    ts = [t for t in trades if start <= t['open_time'] <= end]
    wins = sum(t['outcome'] == 'tp3' for t in ts)
    mid = start + (end - start) // 2
    out = dict(trades=len(ts), wins=wins, losses=len(ts) - wins,
               tp1=sum(t['hits'] >= 1 for t in ts), tp2=sum(t['hits'] >= 2 for t in ts), tp3=wins,
               win_rate=wins / len(ts) if ts else 0.0)
    for how in OBJECTIVES:
        out[how] = round(sum(trade_r(t, how, rr) for t in ts), 4)
        # same score in each half of the period: a winner that only works in one half is likely luck
        out[how + '_h1'] = round(sum(trade_r(t, how, rr) for t in ts if t['open_time'] < mid), 4)
        out[how + '_h2'] = round(sum(trade_r(t, how, rr) for t in ts if t['open_time'] >= mid), 4)
    return out
