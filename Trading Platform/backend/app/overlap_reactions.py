"""Closed-candle reactions to observed D/S overlap locks, using the indicator's own formulas.

Pivot context is replaced by actual overlap contact. The original toggles, ATR/wick/body
thresholds and pattern priority apply. This is annotation only, with no strategy signals.
"""
from bisect import bisect_left

from .engine.ta import ATR
from .scripts.smc_master.patterns import pattern_formulas, PATTERN_PRIORITY, BEARISH_CODES
from .scripts.smc_master.technical import EMA, NAN


DIRECTIONAL_NAMES = {
    'BE': ('Bullish Engulfing','Bearish Engulfing'),
    'H': ('Hammer','Shooting Star'),
    'DD': ('Dragonfly Doji','Gravestone Doji'),
    'TB': ('Tweezer Bottom','Tweezer Top'),
    'P': ('Piercing Pattern','Dark Cloud Cover'),
    'HW': ('Bullish Harami','Bearish Harami'),
    'LLS': ('Long Lower Shadow','Long Upper Shadow'),
}

# Chart candles checked per lock: the first-touch candle plus the following ones (1m: 1 + 9, 5m: 1 + 2).
REACTION_WINDOW = {'M1': 10, 'M5': 3}


class PatternTape:
    """Closed chart candles with the indicator's candle-pattern matches, built oldest to newest.

    Matches only depend on earlier candles, so the tape is extended with new closed candles instead of being
    recomputed, and an earlier observation clock reads a prefix of it (``count``).
    """

    def __init__(self, params: dict, timeframe: str):
        self.params = params
        self.step = 60 if timeframe == 'M1' else 300
        self.rows: list[tuple] = []
        self.times: list[int] = []
        self.formulas: list[list] = []
        self.mean, self.atr = EMA(14), ATR(params['atrLen'])
        self.prev_avg = NAN
        self.whole_history = False  # True when nothing older exists before the first candle held

    def extend(self, bars, as_of: int) -> None:
        """Append the candles of ``bars`` after the last one held that are complete and closed by ``as_of``."""
        params, step = self.params, self.step
        last = self.times[-1] if self.times else None
        for i in range(len(bars)):
            t = int(bars.time[i])
            # Exclude both provisional candles and nominal closes after the observation clock.
            if not bars.complete[i] or t + step > as_of or (last is not None and t <= last):
                continue
            row = (t, float(bars.open[i]), float(bars.high[i]), float(bars.low[i]),
                   float(bars.close[i]), float(bars.volume[i]))
            _, o, h, l, c, _ = row
            avg = self.mean.update(abs(c-o))
            a = self.atr.update(h,l,c)
            prev = self.rows[-1] if self.rows else (0,NAN,NAN,NAN,NAN,NAN)
            found = pattern_formulas(params,row,prev,avg,self.prev_avg,a)
            self.formulas.append(found if params['atrMax']==0 or h-l<=params['atrMax']*a else [])
            self.rows.append(row)
            self.times.append(t)
            self.prev_avg = avg

    def count(self, as_of: int) -> int:
        """Number of held candles closed by ``as_of``."""
        return bisect_left(self.times, as_of - self.step + 1)


def overlap_reactions(bars, zones: list[dict], params: dict, timeframe: str, as_of: int) -> list[dict]:
    if timeframe not in ('M1', 'M5') or not zones:
        return []
    tape = PatternTape(params, timeframe)
    tape.extend(bars, as_of)
    return tape_reactions(tape, zones, timeframe, as_of)


def tape_reactions(tape: PatternTape, zones: list[dict], timeframe: str, as_of: int,
                   limit: int | None = 500) -> list[dict]:
    """Reactions at ``zones`` from the candles of ``tape`` closed by ``as_of`` (the newest ``limit``; None = all)."""
    if timeframe not in ('M1', 'M5') or not zones:
        return []
    params, step = tape.params, tape.step
    n = tape.count(as_of)
    rows, formulas, times = tape.rows[:n], tape.formulas, tape.times[:n]
    out = []
    for zone in zones:
        touch = zone['touched_at'] - 60  # first completed M1 contact, expressed as its open
        window_start = touch - touch % step
        # Exactly REACTION_WINDOW chart-candle slots, starting with the first-touch candle.
        # Later contacts never restart this window, even after replay/reload.
        start = max(bisect_left(times,zone['locked_at']), bisect_left(times,touch)-1, 0)
        for i in range(start,len(rows)):
            t,o,h,l,c,_ = rows[i]
            if t >= window_start + REACTION_WINDOW[timeframe]*step:
                break
            if t+step <= touch or t < zone['locked_at']:
                continue
            candidates = []
            for code,title,toggle,cb,cs,two,count,neutral in formulas[i]:
                if not params[toggle]:
                    continue
                first = i-count+1
                if first < 0 or rows[first][0] < zone['locked_at']:
                    continue
                # Two-candle patterns must use adjacent candles; missing market bars are not a pattern.
                if count==2 and t-rows[first][0] != step:
                    continue
                contact = any(r[0]+step>touch and r[3]<=zone['top'] and r[2]>=zone['bottom']
                              for r in rows[first:i+1])
                if not contact:
                    continue  # a gap over a band is consumption, not a candle touching it
                for bull,condition in ((True,cb),(False,cs)):
                    if code=='LLS' and bull:
                        continue  # Long Lower Shadow is context only, never overlap confirmation.
                    if not condition or (neutral and bull != (zone['side']=='demand')):
                        continue  # Neutral placement follows the zone; it is not a directional signal.
                    favorable = bull if zone['side']=='demand' else not bull
                    if neutral:
                        reaction = 'unconfirmed'
                    elif favorable and (c>zone['top'] if bull else c<zone['bottom']) and (c>o if bull else c<o):
                        reaction = 'rejection'
                    elif not favorable and (c>zone['top'] if bull else c<zone['bottom']) and (c>o if bull else c<o):
                        reaction = 'continuation'
                    else:
                        reaction = 'unconfirmed'
                    pattern_name = DIRECTIONAL_NAMES.get(code,(title,title))[0 if bull else 1]
                    candidates.append(dict(code=code if bull else BEARISH_CODES.get(code,code), name=pattern_name,
                        bullish=bull, neutral=neutral, reaction=reaction, priority=PATTERN_PRIORITY.index(code)))
            if not candidates:
                continue
            # One compact annotation per zone/candle, with every match available in its tooltip.
            selected = min(candidates,key=lambda m:(m['reaction']=='unconfirmed',m['priority']))
            matches = list(dict.fromkeys(m['name'] for m in candidates))
            out.append(dict(time=t, confirmed_at=t+step, price=l if selected['bullish'] else h, open=o, close=c,
                timeframe=timeframe, window_index=(t-window_start)//step+1, side=zone['side'], bottom=zone['bottom'], top=zone['top'],
                lock_id=zone['lock_id'], locked_at=zone['locked_at'], touched_at=zone['touched_at'],
                code=selected['code'], name=selected['name'], bullish=selected['bullish'], neutral=selected['neutral'],
                reaction=selected['reaction'], patterns=matches))
    # Stable ordering across restarts/replay and a bounded annotation payload.
    out.sort(key=lambda r:(r['time'],r['lock_id']))
    return out if limit is None else out[-limit:]
