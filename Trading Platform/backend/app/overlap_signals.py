"""Trade signals from reversal candles at locked overlaps, followed like REAPER [JOAT]'s trade plan.

Signal: the first confirmed candle reaction of a lock on the 5m chart is a rejection (an enabled bullish
pattern at demand / bearish pattern at supply, closing beyond the band in the reversal direction).
A continuation (break through the band) as the first confirmed reaction means no signal for that lock.

Trade (one at a time, like REAPER [JOAT]):
- entry = close of the signal candle.
- SL = the signal candle's shadow end (low for a buy, high for a sell) moved a further $3 away (``buffer``),
  known at its close. The stop size may not exceed $15 (``max_risk``): the $3 shrinks to keep it at $15
  (shadow $14 -> +$1). A shadow already longer than $15 means no trade (status ``no_trade``).
- TPn = entry +/- n x the SL size (1R / 2R / 3R).
- The SL never moves. Each TP reached adds +1R: the trade ends at TP3 (+3R) or at the SL. SL before any TP:
  -1R (loss). SL after TPs: +1R / +2R, the TPs it reached (a win).
- Only candles after the entry candle count. When one 5m candle touches the SL and a TP not reached yet, its
  1-minute candles decide which came first (``minutes``); still both in one minute: the SL counts first.
- Signals while a trade is open are kept (labelled) but open nothing.
Chart candles only, no spread: a display record, not a backtest.
"""
from __future__ import annotations

SIGNAL_TIMEFRAMES = {'M5': 300}
DEFAULT_BUFFER = 3.0  # $ added beyond the signal candle's shadow (low / high), reduced to keep the stop <= max
DEFAULT_MAX_RISK = 15.0  # largest allowed SL size in $; above it the signal is not traded
DEFAULT_RR = (1.0, 2.0, 3.0)


def overlap_signals(candles, reactions: list[dict], timeframe: str, as_of: int, buffer: float = DEFAULT_BUFFER,
                    max_risk: float = DEFAULT_MAX_RISK, rr: tuple[float, float, float] = DEFAULT_RR,
                    minutes=None) -> list[dict]:
    """``candles``: chart ``Bars``, or closed (t, o, h, l, c, v) rows such as a ``PatternTape``'s.
    ``minutes(t)``: the (high, low) of the closed 1-minute candles inside the chart candle opening at ``t``, oldest
    first, used only when a chart candle touches both the SL and a new TP (None: that candle counts the SL)."""
    step = SIGNAL_TIMEFRAMES.get(timeframe)
    if step is None or not reactions or not buffer >= 0 or not max_risk > 0:
        return []
    # Closed chart candles up to the observation clock, same filter as the reactions.
    if hasattr(candles, 'complete'):
        rows = [(int(candles.time[i]), float(candles.high[i]), float(candles.low[i]))
                for i in range(len(candles)) if candles.complete[i] and int(candles.time[i]) + step <= as_of]
    else:
        rows = [(r[0], r[2], r[3]) for r in candles if r[0] + step <= as_of]
    index = {t: k for k, (t, _, _) in enumerate(rows)}
    first: dict[str, dict] = {}
    for r in sorted(reactions, key=lambda r: (r['time'], r['lock_id'])):
        if r['timeframe'] == timeframe and r['reaction'] != 'unconfirmed' and not r.get('neutral'):
            first.setdefault(r['lock_id'], r)
    signals = sorted((r for r in first.values() if r['reaction'] == 'rejection' and r['time'] in index),
                     key=lambda r: (r['time'], r['lock_id']))
    out: list[dict] = []
    busy_until = -1  # candle index that closed the open trade; a new trade may start on that candle (JOAT)
    for r in signals:
        k = index[r['time']]
        d = 1 if r['bullish'] else -1
        entry = float(r['close'])
        # shadow + buffer (r['price'] = the signal candle's low for a buy, high for a sell); the buffer shrinks so
        # the stop never exceeds max_risk, and a shadow longer than max_risk alone opens no trade
        shadow = round((entry - float(r['price'])) * d, 6)
        add = min(buffer, max_risk - shadow)
        risk = round(shadow + add, 6) if add >= 0 else round(shadow + buffer, 6)
        sl = round(entry - d * risk, 6)
        base = dict(id=f"{r['lock_id']}:{r['time']}", lock_id=r['lock_id'], side='long' if d == 1 else 'short',
                    time=r['time'], entry_time=r['confirmed_at'], entry=entry, risk=risk, initial_sl=sl,
                    zone_side=r['side'], bottom=r['bottom'], top=r['top'], code=r['code'], name=r['name'],
                    patterns=r['patterns'], price=r['price'], locked_at=r['locked_at'], **r.get('meta', {}))
        if busy_until is None or k < busy_until:
            out.append({**base, 'status': 'skipped'})
            continue
        if add < 0 or not risk > 0:
            out.append({**base, 'status': 'no_trade'})  # stop too wide: no trade, the slot stays free
            continue
        tps = [entry + d * risk * x for x in rr]
        trade = {**base, 'initial_sl': sl, 'tps': tps, 'status': 'open', 'sl': sl, 'tp_hit': 0,
                 'exit_time': None, 'exit_price': None, 'result_r': None, 'exit_reason': None}
        busy_until = None
        for j in range(k + 1, len(rows)):
            t, h, l = rows[j]
            sl_hit = l <= sl if d == 1 else h >= sl
            reached = sum(1 for tp in tps if (h >= tp if d == 1 else l <= tp))  # TPs this candle touched
            parts = [(h, l)]
            if sl_hit and reached > trade['tp_hit'] and minutes is not None:
                parts = minutes(t) or parts  # which came first inside this candle
            closed = False
            for ph, pl in parts:
                p_sl = pl <= sl if d == 1 else ph >= sl
                p_tp = sum(1 for tp in tps if (ph >= tp if d == 1 else pl <= tp))
                if p_tp > trade['tp_hit'] and not p_sl:
                    trade['tp_hit'] = p_tp
                if trade['tp_hit'] == len(tps) or p_sl:
                    closed = True
                    break
            if closed:
                n = trade['tp_hit']
                won = n == len(tps)
                trade.update(status='closed', exit_time=t + step, exit_price=tps[-1] if won else sl,
                             result_r=float(n * 1.0 if n else -1.0),
                             exit_reason=f'TP{len(tps)}' if won else f'SL after TP{n}' if n else 'SL')
                busy_until = j
                break
        out.append(trade)
    return out
