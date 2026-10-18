# This Pine Script™ code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
# © TradingIQ
# Python port of "Volume Supply and Demand [TradingIQ]" (Pine v6), line for line. Lower-timeframe candles
# (chart timeframe / 5, at least 1 minute) are built from the feed's 1-minute candles.
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta
from ..engine.pine import new_color
from ..engine.script import Context, Script
from .pinelib import GRAY, NAN, color_t, finite, fmt_volume, from_gradient

INPUTS = [
    InputSpec('profile_type', 'string', 'Profile Type', 'Volume', options=('Volume', 'Time', 'Raw'), group='Profile',
              tooltip='Data source used to build the profile distribution.'),
    InputSpec('show_profile', 'bool', 'Show Profile Blocks', True, group='Profile',
              tooltip='Toggles the visibility of the profile histograms.'),
    InputSpec('show_vol_at_level', 'bool', 'Show Volume at Level', False, group='Profile',
              tooltip='Displays the numerical volume inside the profile blocks.'),
    InputSpec('show_poc', 'bool', 'Show POC Lines', False, group='POC',
              tooltip='Displays extending lines indicating the Point of Control for both bullish and bearish volume.'),
    InputSpec('line_width', 'int', 'POC Line Width', 2, minval=1, group='POC'),
    InputSpec('bullish_color', 'color', 'Bullish Color', '#37A3D267', group='POC',
              tooltip='Shared color for bullish profile blocks and the bullish POC line.'),
    InputSpec('bearish_color', 'color', 'Bearish Color', '#EDB45867', group='POC',
              tooltip='Shared color for bearish profile blocks and the bearish POC line.'),
    InputSpec('sd_style', 'string', 'S/D Zone Style', 'Wick', options=('Wick', 'Body', 'Candle'),
              group='Supply & Demand',
              tooltip='Calculation method for drawing the top and bottom constraints of the zones.'),
    InputSpec('enable_sd', 'bool', 'Enable S/D Boxes', True, group='Supply & Demand',
              tooltip='Toggles the background supply and demand zone boxes.'),
    InputSpec('max_age', 'int', 'Max Zone Age (Bars)', 5000, minval=1, group='Supply & Demand',
              tooltip='Maximum number of bars a supply or demand zone will remain on the chart before expiring.'),
    InputSpec('max_sd', 'int', 'Max Active Zones', 10, minval=1, group='Supply & Demand',
              tooltip='Maximum number of concurrent S/D zones allowed on the chart at once.'),
    InputSpec('enable_break', 'bool', 'Enable Hit Deletion', True, group='Supply & Demand'),
    InputSpec('sd_sup_col', 'color', 'Supply Color', '#37A3D267', group='Supply & Demand'),
    InputSpec('sd_dem_col', 'color', 'Demand Color', '#EDB45867', group='Supply & Demand'),
    InputSpec('sd_ext', 'bool', 'Extend Zones Right', False, group='Supply & Demand'),
]

META = ScriptMeta(
    id='volume_supply_demand_tradingiq', name='Volume Supply and Demand [TradingIQ]',
    short_name='Volume Supply and Demand [TradingIQ]', category='Custom',
    description='Supply / demand zones after steep runs of candles, each with a lower-timeframe volume profile '
                '(bullish / bearish blocks, value area, optional POC lines). Port of the Pine v6 script by TradingIQ.',
    max_boxes=500, max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS,
    drawing_groups=[DrawingGroup('zones', 'Supply / demand zones'), DrawingGroup('profile', 'Profile blocks'),
                    DrawingGroup('poc', 'POC lines')],
)

# hard-coded in the Pine source
SCALE, RATE, NUMBER_OF_BINS, PERIOD, FILTERING = 2, 5, 12, 50, 2.0
MIN_COUNT, MIN_ANGLE = 4, 69
HISTORY = 5000  # max_bars_back


def pine_round(x: float) -> int:
    return int(math.floor(x + 0.5))


@dataclass
class Drawing:
    start_idx: int
    direction: bool
    profile: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    sd: object = None


@dataclass
class Profile:
    sum_bins: list
    bullish_bins: list
    bearish_bins: list
    min: float
    bin_range: float
    bullish_mode: float = NAN
    bearish_mode: float = NAN


def epanechnikov(source: float, bandwidth: float) -> float:
    u = source / bandwidth
    return 0.75 * (1 - u * u) if abs(u) <= 1 else 0.0


def filter_matrix(bins: int, period: float) -> list[list[float]]:
    m = [[epanechnikov(col - row, period) for col in range(bins)] for row in range(bins)]
    return [[v / sum(r) for v in r] for r in m]


class VolumeSupplyDemand(Script):
    meta = META

    def required_warmup(self) -> int:
        # calc_bars_count = 20000: TradingView runs the script over the last 20,000 bars, and the zone angle uses a
        # cumulative average of the whole run, so the same depth of history is replayed here
        return 20000

    def setup(self, ctx: Context) -> None:
        tf_s = ctx.timeframe.seconds
        self.ltf_seconds = max(60, tf_s // RATE)
        self.ltf = ctx.request_lower_tf(self.ltf_seconds, lookback=PERIOD + 2)
        self.data: list[list[tuple]] = []  # newest first, at most PERIOD bars of intrabar candles
        self.ready = False
        self.smoothed = NAN  # ema() state
        self.cum = 0.0  # cumulative_ema() state
        self.count = 0
        self.green = self.red = 0
        self.supply_active = self.demand_active = False
        self.closes: list[float] = []
        self.hist = dict(ts=[], bs=[], td=[], bd=[])
        self.drawings: list[Drawing] = []
        self.filter_cache: tuple | None = None

    # ------------------------------------------------------------------ zones
    def _calculate_sd(self, ctx: Context):
        i = ctx.bar_index
        o, h, l, c = ctx.open, ctx.high, ctx.low, ctx.close
        length = int(min(i + 1, 200))
        alpha = 2 / (length + 1)
        src = ((h - l) + abs(o - c)) / 2
        self.smoothed = alpha * src + (1 - alpha) * (self.smoothed if finite(self.smoothed) else 0.0)
        atr = self.smoothed / 4
        if o < c:
            self.supply_active = False
            self.red = 0
            self.green += 1
        else:
            self.demand_active = False
            self.green = 0
            self.red += 1
        self.closes.append(c)
        del self.closes[:-HISTORY]
        self.count += 1
        k = 2.0 / (self.count + 1.0)
        self.cum = (1.0 - k) * self.cum + k * (h - l)

        def angle(end: int) -> float:
            if end >= len(self.closes):
                return NAN
            slope = (c - self.closes[-1 - end]) / self.cum
            return abs(math.degrees(math.atan(slope)))

        red_angle, green_angle = angle(self.red), angle(self.green)
        supply = demand = False
        supply_back = demand_back = None
        if self.red >= MIN_COUNT and finite(red_angle) and red_angle >= MIN_ANGLE and not self.supply_active \
                and ctx.is_confirmed:
            self.supply_active, supply, supply_back = True, True, self.red
        if self.green >= MIN_COUNT and finite(green_angle) and green_angle >= MIN_ANGLE and not self.demand_active \
                and ctx.is_confirmed:
            self.demand_active, demand, demand_back = True, True, self.green
        top, bot = max(o, c), min(o, c)
        style = self.p['sd_style']
        dem_wick, sup_wick = bot - l > atr, h - top > atr
        td = (bot if dem_wick else l + atr) if style == 'Wick' else top if style == 'Body' else h
        bd = l if style in ('Wick', 'Candle') else bot
        ts = h if style in ('Wick', 'Candle') else top
        bs = (top if sup_wick else h - atr) if style == 'Wick' else bot if style == 'Body' else l
        for key, val in (('ts', ts), ('bs', bs), ('td', td), ('bd', bd)):
            arr = self.hist[key]
            arr.append(val)
            del arr[:-HISTORY]

        def back(key, n):
            arr = self.hist[key]
            return arr[-1 - n] if n is not None and n < len(arr) else NAN

        sup = dict(top=back('ts', supply_back), bottom=back('bs', supply_back),
                   left=i - supply_back if supply_back is not None else None, flag=supply, width=supply_back or 0)
        dem = dict(top=back('td', demand_back), bottom=back('bd', demand_back),
                   left=i - demand_back if demand_back is not None else None, flag=demand, width=demand_back or 0)
        return sup, dem

    # ------------------------------------------------------------------ profile
    def _profile(self, data: list[list[tuple]], mintick: float) -> Profile | None:
        rows = data[:min(len(data), PERIOD)]
        candles = [cd for d in rows for cd in d[:min(len(d), PERIOD)]]
        if not candles:
            return None
        max_v = max(0.0, max(cd[0] for cd in candles))
        min_v = min(3.402823466e+38, min(cd[1] for cd in candles))
        full = max_v - min_v
        bins = int(min(max(1, int(full / mintick)), NUMBER_OF_BINS))
        rng = full / bins
        if not finite(rng) or rng <= 0:
            return None
        sum_b, bull_b, bear_b = [0.0] * bins, [0.0] * bins, [0.0] * bins

        def bin_number(price):
            return min(max(1, math.ceil((price - min_v) / rng)) - 1, bins - 1)

        for d in rows:
            for hh, ll, w, pol in d:
                top_bin, bottom_bin = bin_number(hh), bin_number(ll)
                hits = top_bin - bottom_bin + 1
                value = w / hits
                for k in range(hits):
                    b = int(bottom_bin) + k
                    sum_b[b] += value
                    if pol >= 0:
                        bull_b[b] += value
                    if pol <= 0:
                        bear_b[b] += value
        # filter_profile(2.0): Epanechnikov kernel smoothing across the bins
        if FILTERING > 1:
            if self.filter_cache is None or self.filter_cache[0] != bins:
                self.filter_cache = (bins, filter_matrix(bins, FILTERING))
            m = self.filter_cache[1]
            mult = lambda vec: [sum(m[r][k] * vec[k] for k in range(bins)) for r in range(bins)]
            sum_b, bull_b, bear_b = mult(sum_b), mult(bull_b), mult(bear_b)
        prof = Profile(sum_b, bull_b, bear_b, min_v, rng)
        prof.bullish_mode = min_v + (bull_b.index(max(bull_b)) + 0.5) * rng
        prof.bearish_mode = min_v + (bear_b.index(max(bear_b)) + 0.5) * rng
        return prof

    @staticmethod
    def _value_area(prof: Profile, percent: float = 70) -> tuple[int, int]:
        s = prof.sum_bins
        total = sum(s)
        area = max(s)
        poc = s.index(area)
        max_idx = len(s) - 1
        top = bottom = poc
        for _ in range(max_idx + 1):
            if top < max_idx:
                top += 1
                area += s[top]
            if bottom > 0:
                bottom -= 1
                area += s[bottom]
            if total != 0 and area / total * 100 >= percent:
                break
        return top + 1, bottom

    def _draw_profile(self, ctx: Context, dr: Drawing, prof: Profile, scale: int, offset: int) -> None:
        p = self.p
        i = ctx.bar_index
        left = i - offset
        mn, mx = min(prof.sum_bins), max(prof.sum_bins)
        rng_vol = max(mx - mn, 1e-10)
        va_top, va_bottom = self._value_area(prof, 70)
        for k, sum_vol in enumerate(prof.sum_bins):
            bottom = prof.min + prof.bin_range * k
            top = bottom + prof.bin_range
            bullish = prof.bullish_bins[k] >= prof.bearish_bins[k]
            norm = max(1, min(pine_round(1 + ((sum_vol - mn) * 10) / rng_vol), 10))
            blocks = '█' * norm
            padding = max(0, 10 - len(blocks))
            spaces = ' ' * padding if padding > 0 else (' ' if len(blocks) == 10 else '')
            if bullish:
                text = blocks + spaces + (fmt_volume(sum_vol) if p['show_vol_at_level'] else '')
                b_left, b_right, align = left, left + scale, 'left'
            else:
                text = spaces + blocks
                if p['show_vol_at_level']:
                    text = fmt_volume(sum_vol) + ' ' + text
                b_left, b_right, align = left - scale, left, 'right'
            base = p['bullish_color'] if bullish else p['bearish_color']
            col = from_gradient(norm, 0, 10, new_color(base, 90), new_color(base, 45))
            if k < va_bottom or k > va_top:
                col = new_color(GRAY, 60)
            dr.profile.append(ctx.box(left=ctx.time_at(b_left), top=top, right=ctx.time_at(b_right), bottom=bottom,
                                      text=text, text_halign=align, text_valign='center', border_color='#00000000',
                                      bg_color='#00000000', text_color=col, text_size='auto', group='profile'))

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: Context) -> None:
        p = self.p
        i = ctx.bar_index
        sup, dem = self._calculate_sd(ctx)

        # ltf_tick(): intrabar candles (high, low, weight, polarity), newest bar first
        ib = ctx.intrabars(self.ltf).bars
        w_mode = p['profile_type']
        candle = []
        for k in range(len(ib)):
            o, c = float(ib.open[k]), float(ib.close[k])
            w = float(ib.volume[k]) if w_mode == 'Volume' else float(self.ltf_seconds) if w_mode == 'Time' else 1.0
            candle.append((float(ib.high[k]), float(ib.low[k]), w, -1 if o > c else 0 if o == c else 1))
        if self.ready:
            self.data.insert(0, candle)
            del self.data[PERIOD:]
        else:
            self.ready = len(candle) > 0

        # drawings.clean(max_sd, max_age, enable_break)
        drs = self.drawings
        while len(drs) > p['max_sd']:
            self._delete(ctx, drs.pop(0))
        k = 0
        while k < len(drs):
            dr = drs[k]
            too_old = i - dr.start_idx >= p['max_age']
            broke = False
            if dr.sd is not None:
                broke = ctx.close > dr.sd.top if dr.direction else ctx.close < dr.sd.bottom
            broke = broke and p['enable_break']
            if (broke or too_old) and ctx.is_confirmed:
                self._delete(ctx, dr)
                drs.pop(k)
            if dr.sd is not None:
                dr.sd.right = ctx.time_at(i + 1)
            for ln in dr.lines:
                ln.x2 = ctx.time_at(i + 1)
            k += 1  # Pine's for…in keeps counting after a removal, so the next drawing is skipped this bar

        if not (sup['flag'] or dem['flag']):
            return
        width = sup['width'] if sup['flag'] else dem['width'] if dem['flag'] else 1
        data_width = min(width * 2 - 1, len(self.data))
        if data_width <= 0:
            return
        dr = Drawing(i, sup['flag'])
        prof = self._profile(self.data[:data_width], ctx.mintick)
        if prof is not None:
            if p['show_poc']:
                ext = 'right' if p['sd_ext'] else 'none'
                for price, col in ((prof.bullish_mode, p['bullish_color']), (prof.bearish_mode, p['bearish_color'])):
                    dr.lines.append(ctx.line(x1=ctx.time_at(i - width), y1=price, x2=ctx.time_at(i + SCALE), y2=price,
                                             extend=ext, color=col, width=p['line_width'], group='poc'))
            if p['show_profile']:
                self._draw_profile(ctx, dr, prof, int(width * SCALE), width)
        if p['enable_sd']:
            for z, col in ((sup, p['sd_sup_col']), (dem, p['sd_dem_col'])):
                if z['flag']:
                    dr.sd = ctx.box(left=ctx.time_at(z['left']), top=z['top'], right=ctx.time, bottom=z['bottom'],
                                    border_color=col, bg_color=new_color(col, (color_t(col) + 1) * 1.25),
                                    extend='right' if p['sd_ext'] else 'none', group='zones')
        drs.append(dr)

    @staticmethod
    def _delete(ctx: Context, dr: Drawing) -> None:
        for obj in dr.profile + dr.lines:
            ctx.delete(obj)
        dr.profile.clear()
        dr.lines.clear()
        ctx.delete(dr.sd)
