# This Pine Script™ code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
# © TradingIQ
# Python port of "Footprint IQ Pro [TradingIQ]" (Pine v6), line for line. Intrabar data comes from the feed's
# 1-minute candles (request.security_lower_tf "1"); 1-second and 1-tick granularity need data this platform does
# not store, so those options show a warning instead of a footprint.
from __future__ import annotations

import math

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta
from ..engine.pine import new_color, rgb
from ..engine.script import Context, Script
from ..engine.ta import ATR
from ..market.timeframes import bucket_start, get_timeframe
from .pinelib import (CHART_BG, CHART_FG, NAN, WHITE, Table, binary_search_leftmost, finite, fmt_percent, fmt_volume,
                      from_gradient)

M1, S1, T1 = '1-Minute', '1-Second', '1-Tick'
PINE_TF = {'1': 'M1', '5': 'M5', '15': 'M15', '30': 'M30', '60': 'H1', '240': 'H4', 'D': 'D'}

INPUTS = [
    InputSpec('gran', 'string', 'Granularity', M1, options=(M1, S1, T1)),
    InputSpec('atrMode', 'bool', 'Use ATR Levels (Not Recommended)', True),
    InputSpec('ticks', 'float', 'Tick Level Distance (0 = Auto)', 0.0),
    InputSpec('useCVD', 'bool', 'Use CVD Levels', False, group='CVD'),
    InputSpec('cvdRes', 'timeframe', 'CVD Reset TF', 'D', group='CVD'),
    InputSpec('showDeltaP', 'bool', 'Show Delta Percentage', True, group='Delta Boxes'),
    InputSpec('showTotalVolume', 'bool', 'Show Total Volume Per Level', True, group='Delta Boxes'),
    InputSpec('showBuyVol', 'bool', 'Show Buy Volume Per Level', True, group='Delta Boxes'),
    InputSpec('showSellVol', 'bool', 'Show Sell Volume Per Level', True, group='Delta Boxes'),
    InputSpec('useSymbols', 'bool', 'Footprint IQ Delta Symbols', True, group='Delta Boxes'),
    InputSpec('vaCumu', 'float', 'Value Area %', 70.0, minval=0, maxval=100),
    InputSpec('highestBuying', 'int', 'Highest +Delta Levels To Show', 0, minval=0, group='Delta Lines'),
    InputSpec('highestSelling', 'int', 'Highest -Delta To Show', 0, minval=0, group='Delta Lines'),
    InputSpec('belowAbove', 'bool', 'Show +Delta Levels Below Price And -Delta Levels Above Price', True,
              group='Delta Lines'),
    InputSpec('imbalanceP', 'float', 'Imbalance Percentage', 70.0, minval=0, maxval=100, group='Imbalances'),
    InputSpec('stacked', 'int', 'Stacked Imbalance Count', 3, minval=2, group='Imbalances'),
    InputSpec('upCol', 'color', '+ Color', rgb(128, 116, 255), group='Colors'),
    InputSpec('dnCol', 'color', '+ Color', rgb(255, 116, 116), group='Colors'),
    InputSpec('showPOC', 'bool', '', True, inline='POC', group='Colors'),
    InputSpec('pocCol', 'color', 'POC Color', rgb(255, 243, 116), inline='POC', group='Colors'),
    InputSpec('showadvancedMode', 'bool', 'Show Focus Mode', False, group='Focus Mode'),
    InputSpec('rows', 'int', 'Rows', 20, minval=5, maxval=150, group='Focus Mode'),
    InputSpec('upColAdv2', 'color', 'Up Color', '#96D3C8FF', inline='ADV UP', group='Focus Mode'),
    InputSpec('upColAdv', 'color', '', '#00FFFFFF', inline='ADV UP', group='Focus Mode'),
    InputSpec('dnColAdv2', 'color', 'Down Color', '#D39696FF', inline='ADV DN', group='Focus Mode'),
    InputSpec('dnColAdv', 'color', '', '#8F1B1BFF', inline='ADV DN', group='Focus Mode'),
    InputSpec('advCol', 'color', 'Text Color', WHITE, group='Focus Mode'),
]

META = ScriptMeta(
    id='footprint_iq_pro', name='Footprint IQ Pro [TradingIQ]', short_name='Footprint IQ Pro [TradingIQ]',
    category='Custom',
    description='Footprint per candle from 1-minute intrabars: delta, delta %, total / buy / sell volume per price '
                'level, POC, value area, stacked imbalances and Focus Mode. Port of the Pine v6 script by TradingIQ.',
    max_boxes=500, max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS, behind_chart=False,
    drawing_groups=[DrawingGroup('footprint', 'Footprint levels'), DrawingGroup('imbalance', 'Imbalances'),
                    DrawingGroup('value_area', 'Value area marker'), DrawingGroup('delta_lines', 'Highest delta lines'),
                    DrawingGroup('focus', 'Focus mode')],
)

TRANSPARENT = '#00000000'
BLACK = '#000000FF'  # box text_color default
YELLOW = rgb(255, 243, 116)


class FootprintIQ(Script):
    meta = META

    def required_warmup(self) -> int:
        return 2000

    def setup(self, ctx: Context) -> None:
        p = self.p
        # a day of bars before the first displayed one, so the daily level grid has been reset inside the window
        self.ltf = ctx.request_lower_tf(60, lookback=86400 // ctx.timeframe.seconds + 2) if p['gran'] == M1 else None
        self.atr = ATR(14)
        self.atr_val = self.atrN = NAN
        self.levels: list[float] = []
        self.delta: list[float] = []
        self.up: list[float] = []
        self.dn: list[float] = []
        self.total: list[float] = []
        self.hd_price: list[float] = []
        self.hd_delta: list[float] = []
        self.tickAmount = 0.0
        self.freezeATR = 0.0
        self.finalLevel = NAN
        self.prev_day = None
        self.prev_cvd = None
        self.last_ok = False
        self.last_open = NAN
        cvd = PINE_TF.get(p['cvdRes'], ctx.timeframe.code) if p['cvdRes'] else ctx.timeframe.code
        self.cvd_tf = get_timeframe(cvd)
        self.day_tf = get_timeframe('D')

    def _texts(self, values: list[str]) -> str:
        p = self.p
        labels = (['δ: ', '\nδ%: ', '\n⧎: ', '\n◭: ', '\n⧩: '] if p['useSymbols'] else
                  ['Delta: ', '\nDelta Percentage: ', '\nTotal Vol.: ', '\nBuy Vol.: ', '\nSell Vol.: '])
        add = [True, p['showDeltaP'], p['showTotalVolume'], p['showBuyVol'], p['showSellVol']]
        return ''.join(lab + v for lab, v, on in zip(labels, values, add) if on)

    def on_bar(self, ctx: Context) -> None:
        p = self.p
        i = ctx.bar_index
        o, h, l, c, v = ctx.open, ctx.high, ctx.low, ctx.close, ctx.volume
        self.atr_val = self.atr.update(h, l, c)
        self.atrN = self.atr_val / 4
        day = bucket_start(ctx.time, self.day_tf, ctx.alignment)
        new_day = day != self.prev_day
        self.prev_day = day
        cvd_key = bucket_start(ctx.time, self.cvd_tf, ctx.alignment)
        new_cvd = cvd_key != self.prev_cvd
        self.prev_cvd = cvd_key
        self.last_ok = False
        self.last_open = o

        bars = ctx.intrabars(self.ltf).bars if self.ltf is not None else None
        if bars is None or len(bars) == 0 or p['showadvancedMode']:
            return
        useCVD = p['useCVD']
        if not useCVD and self.delta:  # timeframe.change("") is true on every chart bar
            n = len(self.delta)
            self.delta, self.up, self.dn, self.total = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
        cond = (new_day and not useCVD) or (useCVD and new_cvd)
        if cond:
            self.freezeATR = self.atrN
            self.tickAmount = self.freezeATR if (p['ticks'] == 0) == p['atrMode'] else self.atr_val * ctx.mintick
            tA = self.tickAmount
            self.levels = [o - tA, o, o + tA]
            self.delta, self.up, self.dn, self.total = [0.0] * 3, [0.0] * 3, [0.0] * 3, [0.0] * 3
        tA = self.tickAmount
        if not self.levels or not finite(tA) or tA <= 0:
            return

        if not self.hd_delta:
            self.hd_price = [o - tA, o, o + tA]
            self.hd_delta = [0.0, 0.0, 0.0]

        last, first = self.levels[-1], self.levels[0]
        while h >= last:
            last += tA
            for arr, val in ((self.levels, last), (self.delta, 0.0), (self.up, 0.0), (self.dn, 0.0),
                             (self.total, 0.0)):
                arr.append(val)
            if len(self.levels) > 90000:
                for arr in (self.levels, self.delta, self.up, self.dn, self.total):
                    arr.pop(0)
        while l <= first:
            first -= tA
            for arr, val in ((self.levels, first), (self.delta, 0.0), (self.up, 0.0), (self.dn, 0.0),
                             (self.total, 0.0)):
                arr.insert(0, val)
            if len(self.levels) > 90000:
                for arr in (self.levels, self.delta, self.up, self.dn, self.total):
                    arr.pop()
        last, first = self.hd_price[-1], self.hd_price[0]
        while h >= last:
            last += tA
            self.hd_price.append(last)
            self.hd_delta.append(0.0)
            if len(self.hd_price) > 90000:
                self.hd_price.pop(0)
                self.hd_delta.pop(0)
        while l <= first:
            first -= tA
            self.hd_price.insert(0, first)
            self.hd_delta.insert(0, 0.0)
            if len(self.hd_price) > 90000:
                self.hd_price.pop()
                self.hd_delta.pop()

        levels = self.levels
        for k in range(len(bars)):
            vol = float(bars.volume[k])
            ih, il = float(bars.high[k]), float(bars.low[k])
            top = max(binary_search_leftmost(levels, ih), 0)
            bot = max(binary_search_leftmost(levels, il), 0)
            if float(bars.close[k]) < float(bars.open[k]):
                vol *= -1
            div = vol / (top - bot + 1)
            for x in range(bot, top + 1):
                self.delta[x] += div
                if vol > 0:
                    self.up[x] += div
                elif vol < 0:
                    self.dn[x] += div
                self.total[x] += abs(div)
            top = max(binary_search_leftmost(self.hd_price, ih), 0)
            bot = max(binary_search_leftmost(self.hd_price, il), 0)
            for x in range(bot, top + 1):
                self.hd_delta[x] += div

        start = binary_search_leftmost(levels, l)
        end = binary_search_leftmost(levels, h)
        size = len(levels)
        pos = [d for d in self.delta if d > 0]
        neg = [d for d in self.delta if d < 0]
        minGrad = min(min(pos) if pos else 0.0, abs(max(neg)) if neg else 0.0)
        maxGrad = max(max(pos) if pos else 0.0, abs(min(neg)) if neg else 0.0)
        upCol, dnCol = p['upCol'], p['dnCol']
        stack = 0
        imb: list = []
        boxes = []
        for k in range(start, end + 1):
            lvl = levels[k]
            nxt = levels[k + 1] if k + 1 != size else lvl + tA
            d = self.delta[k]
            self.finalLevel = nxt
            if d > 0:
                grad = from_gradient(d, minGrad, maxGrad, new_color(upCol, 70), new_color(upCol, 10))
            else:
                grad = from_gradient(d, -maxGrad, -minGrad, new_color(dnCol, 10), new_color(dnCol, 70))
            tot = self.total[k]
            dp = d / tot * 100 if tot != 0 else NAN
            if p['showPOC'] and (d == maxGrad or d == -maxGrad):
                grad = new_color(p['pocCol'], 40)
            text = self._texts([fmt_volume(d), fmt_percent(dp), fmt_volume(tot), fmt_volume(self.up[k]),
                                fmt_volume(self.dn[k])])
            boxes.append(ctx.box(left=ctx.time, top=lvl, right=ctx.time_at(i + 1), bottom=nxt,
                                 border_color=CHART_BG, border_width=4, bg_color=grad or TRANSPARENT, text=text,
                                 text_color=BLACK, text_size='auto', group='footprint'))
            if finite(dp) and abs(dp) >= p['imbalanceP']:
                stack += 1
                imb.append(ctx.label(x=ctx.time_at(i + 1), y=(lvl + nxt) / 2, text_color=CHART_FG,
                                     text='𝅉' if dp > 0 else '𝅏', size='normal', color=TRANSPARENT,
                                     style='label_center', tooltip=fmt_percent(dp) + ' Imbalance', group='imbalance'))
                if stack == p['stacked']:
                    for lab in imb:
                        lab.text_color = YELLOW
                elif stack > p['stacked']:
                    imb[-1].text_color = YELLOW
            else:
                stack = 0
                imb.clear()

        if not p['useCVD']:
            sliceVol = self.total[start:end + 1]
            sliceLevels = levels[start:end + 1]
            n = len(sliceVol)
            if n:
                poc = sliceVol.index(max(sliceVol))
                sumVol = sum(sliceVol)
                for x in range(n):
                    part = sliceVol[max(poc - x, 0):min(poc + x + 1, n)]
                    if sumVol != 0 and sum(part) / sumVol >= p['vaCumu'] / 100:
                        bottom = max(poc - x, 0)
                        ctx.label(x=ctx.time, y=sliceLevels[bottom], text='🞂', color=TRANSPARENT, size='small',
                                  style='label_center', text_color=WHITE, group='value_area')
                        break
            total_d = sum(self.delta)
            dp = total_d / v * 100 if v != 0 else NAN
            text = self._texts([fmt_volume(total_d), fmt_percent(dp), fmt_volume(v), fmt_volume(sum(self.up)),
                                fmt_volume(sum(self.dn))])
            ctx.box(left=ctx.time, top=self.finalLevel, right=ctx.time_at(i + 1), bottom=self.finalLevel + tA,
                    border_color=TRANSPARENT, bg_color=TRANSPARENT, text=text, text_color=BLACK, text_size='auto',
                    group='footprint')
        self.last_ok = True

    # ------------------------------------------------------------------ barstate.islast
    def on_last_bar(self, ctx: Context) -> None:
        p = self.p
        self.tables = []
        self.warnings = []
        if p['gran'] != M1:
            self.warnings.append(f'Footprint IQ Pro: {p["gran"]} granularity needs second / tick data, which this '
                                 'platform does not store. Choose 1-Minute.')
        if self.last_ok:
            self._delta_lines(ctx)
        if p['showadvancedMode']:
            self._focus(ctx)
        if ctx.timeframe.seconds == 60 and p['gran'] == M1:
            t = Table('footprint_warn', 'top_right', 99, 99, bg_color='#20222CFF', border_color='#363843FF',
                      frame_color='#363843FF', border_width=1, frame_width=1)
            t.cell(0, 0, "You're on the 1-minute chart and Granularity is also set to 1-minute.\nSet Granularity to "
                         "1-second or 1-tick for Footprint IQ Pro to work properly.\nIf you receive an error, a "
                         "TradingView Premium plan or higher is required for this feature.\n\nThe Granularity setting "
                         "is the first setting in the indicator options!", text_color=CHART_FG)
            self.tables.append(t)

    def _delta_lines(self, ctx: Context) -> None:
        p = self.p
        i = ctx.bar_index
        o = self.last_open
        srt = sorted(self.hd_delta)
        plus: dict[float, float] = {}
        neg: dict[float, float] = {}
        up_count = dn_count = 0  # counted per put, as in Pine (a repeated price overwrites but still counts)
        if p['highestBuying'] != 0:
            for d in reversed(srt):
                if d > 0:
                    if up_count < p['highestBuying']:
                        price = self.hd_price[self.hd_delta.index(d)]
                        if not p['belowAbove'] or price <= o:
                            plus[price] = d
                            up_count += 1
                    else:
                        break
                else:
                    break
        if p['highestSelling'] != 0:
            for d in srt:
                if d < 0:
                    if dn_count < p['highestSelling']:
                        price = self.hd_price[self.hd_delta.index(d)]
                        if not p['belowAbove'] or price >= o:
                            neg[price] = d
                            dn_count += 1
                    else:
                        break
                else:
                    break
        for levels, col, positive in ((plus, p['upCol'], True), (neg, p['dnCol'], False)):
            for lvl, d in levels.items():
                top, bot = lvl * 1.0008, lvl * .9998
                rng = (top - bot) / 20
                y = (top + bot + (top if positive else bot)) / 3
                ctx.label(x=ctx.time_at(i + 20), y=y, text=('+' if positive else '') + fmt_volume(d),
                          color=TRANSPARENT, style='label_left', size='small', text_color=col, group='delta_lines')
                ys = [bot + rng * x for x in range(20)]
                for x in range(1, 20):
                    g = from_gradient(x, 0, 10, CHART_BG, col) if x < 10 else from_gradient(x, 11, 20, col, CHART_BG)
                    ctx.box(left=ctx.time_at(i - 1), right=ctx.time_at(i + 1), top=ys[x], bottom=ys[x - 1],
                            extend='both', border_color=TRANSPARENT, border_width=0, bg_color=new_color(g, 80),
                            group='delta_lines')
                ctx.line(x1=ctx.time_at(i - 1), y1=ys[10], x2=ctx.time_at(i + 1), y2=ys[10], extend='both',
                         color=col, width=2, group='delta_lines')

    def _focus(self, ctx: Context) -> None:
        p = self.p
        if self.ltf is None:
            return
        ib = ctx.intrabars(self.ltf)
        bars = ib.bars
        if len(bars) == 0:
            return
        i = ctx.bar_index
        o, h, l, c = ctx.open, ctx.high, ctx.low, ctx.close
        rows = p['rows']
        rng = (h - l) / rows
        levels = [l + rng * k for k in range(rows)]
        delta, buy, sell, total = [0.0] * rows, [0.0] * rows, [0.0] * rows, [0.0] * rows
        prev = ib.prev_close
        for k in range(len(bars)):
            cl = float(bars.close[k])
            direction = (cl > prev) - (cl < prev) if finite(prev) else NAN
            prev = cl
            data = float(bars.volume[k]) * direction
            if not finite(data):
                continue
            top = max(binary_search_leftmost(levels, float(bars.high[k])), 0)
            bot = max(binary_search_leftmost(levels, float(bars.low[k])), 0)
            div = data / (abs(top - bot) + 1)
            for x in range(bot, top + 1):
                delta[x] += div
                total[x] += abs(div)
                if div > 0:
                    buy[x] += div
                elif div < 0:
                    sell[x] += abs(div)
        col = p['upColAdv'] if c >= o else p['dnColAdv']
        t = ctx.time_at
        ctx.box(left=t(i + 5), top=max(o, c), right=t(i + 15), bottom=min(o, c), bg_color=col, border_color=CHART_BG,
                group='focus')
        ctx.line(x1=t(i + 10), y1=max(o, c), x2=t(i + 10), y2=h, color=col, group='focus')
        ctx.line(x1=t(i + 10), y1=min(o, c), x2=t(i + 10), y2=l, color=col, group='focus')
        absd = [abs(d) for d in delta]
        minD, maxD = min(absd), max(absd)
        maxV, minV = max(total), min(total)
        for k in range(rows):
            lvl = levels[k]
            norm = round_half_up(1 + ((total[k] - minV) / (maxV - minV)) * 15) if maxV != minV else None
            if delta[k] > 0:
                bg = from_gradient(absd[k], minD, maxD, p['upColAdv2'], p['upColAdv'])
            else:
                bg = from_gradient(absd[k], minD, maxD, p['dnColAdv2'], p['dnColAdv'])
            if norm is not None:
                ctx.box(left=t(i + 15), top=lvl + rng, right=t(i + 15 + norm), bottom=lvl, border_color=CHART_BG,
                        bg_color=new_color(bg, 0) if bg else TRANSPARENT, group='focus')
            ctx.box(left=t(i + 30), top=lvl + rng, right=t(i + 35), bottom=lvl, border_color=TRANSPARENT,
                    bg_color=TRANSPARENT, text_color=p['advCol'], text=fmt_volume(sell[k]), text_size='auto',
                    group='focus')
            ctx.box(left=t(i + 35), top=lvl + rng, right=t(i + 40), bottom=lvl, border_color=TRANSPARENT,
                    bg_color=TRANSPARENT, text_color=p['advCol'], text=fmt_volume(buy[k]), text_size='auto',
                    group='focus')

    def extra_output(self) -> dict:
        tables = [js for js in (t.to_json() for t in getattr(self, 'tables', [])) if js]
        return dict(tables=tables, warnings=list(getattr(self, 'warnings', [])))


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))
