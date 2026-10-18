# This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)
# https://creativecommons.org/licenses/by-nc-sa/4.0/
# © LuxAlgo
# Python port of "Liquidity Swings [LuxAlgo]" (Pine v5), line for line.
from __future__ import annotations

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta
from ..engine.pine import new_color
from ..engine.script import Context, Script
from ..market.timeframes import get_timeframe
from .pinelib import NAN, Pivot, finite, fmt_volume

PINE_TF = {'1': 'M1', '5': 'M5', '15': 'M15', '30': 'M30', '60': 'H1', '240': 'H4', 'D': 'D'}
RED, TEAL = '#F23645FF', '#089981FF'  # Pine v5 color.red / color.teal

INPUTS = [
    InputSpec('length', 'int', 'Pivot Lookback', 14),
    InputSpec('area', 'string', 'Swing Area', 'Wick Extremity', options=('Wick Extremity', 'Full Range')),
    InputSpec('intraPrecision', 'bool', 'Intrabar Precision', False, inline='intrabar'),
    InputSpec('intrabarTf', 'timeframe', '', '1', inline='intrabar'),
    InputSpec('filterOptions', 'string', 'Filter Areas By', 'Count', options=('Count', 'Volume'), inline='filter'),
    InputSpec('filterValue', 'float', '', 0.0, inline='filter'),
    InputSpec('showTop', 'bool', 'Swing High', True, inline='top', group='Style'),
    InputSpec('topCss', 'color', '', RED, inline='top', group='Style'),
    InputSpec('topAreaCss', 'color', 'Area', new_color(RED, 50), inline='top', group='Style'),
    InputSpec('showBtm', 'bool', 'Swing Low', True, inline='btm', group='Style'),
    InputSpec('btmCss', 'color', '', TEAL, inline='btm', group='Style'),
    InputSpec('btmAreaCss', 'color', 'Area', new_color(TEAL, 50), inline='btm', group='Style'),
    InputSpec('labelSize', 'string', 'Labels Size', 'Tiny', options=('Tiny', 'Small', 'Normal'), group='Style'),
]

META = ScriptMeta(
    id='liquidity_swings_luxalgo', name='Liquidity Swings [LuxAlgo]', short_name='Liquidity Swings [LuxAlgo]',
    category='Custom',
    description='Swing high / low liquidity areas with the number of candles (or volume) that traded into them, '
                'their levels until price closes through, and volume labels. Port of the Pine v5 script by LuxAlgo '
                '(CC BY-NC-SA 4.0).',
    max_lines=500, max_labels=500, max_boxes=500, label_layout=False, inputs=INPUTS,
    drawing_groups=[DrawingGroup('areas', 'Swing areas'), DrawingGroup('levels', 'Swing levels'),
                    DrawingGroup('labels', 'Volume labels')],
)

LABEL_SIZE = {'Tiny': 'tiny', 'Small': 'small', 'Normal': 'normal'}
NA_COLOR = '#00000000'


class Side:
    """State of one side (pivot highs or pivot lows): the `var`s of the main block and of the get_counts /
    set_zone / set_level / set_label call sites, with the previous-bar values their `[1]` references read."""

    def __init__(self, high: bool, length: int):
        self.high = high
        self.pivot = Pivot(length, length, high)
        self.top = self.btm = NAN
        self.crossed: bool | None = None  # na until the first pivot
        self.x1 = 0
        self.bx = None  # the moving swing-area box (created on the first bar, like `var box`)
        self.bx_color = NA_COLOR
        self.count, self.vol = 0, 0.0
        self.zone_bx = None
        self.zone_prev = NAN  # target[1] at set_zone
        self.lvl = None
        self.level_prev = NAN  # target[1] at set_level
        self.level_prev_crossed: bool | None = None  # crossed[1] at set_level
        self.lbl = None
        self.label_prev = NAN  # target[1] at set_label


class LiquiditySwings(Script):
    meta = META

    def required_warmup(self) -> int:
        return 1000

    def setup(self, ctx: Context) -> None:
        p = self.p
        L = p['length']
        self.sides = (Side(True, L), Side(False, L))
        self.ltf = None
        self.warnings: list[str] = []
        if p['intraPrecision']:
            code = PINE_TF.get(p['intrabarTf'], ctx.timeframe.code) if p['intrabarTf'] else ctx.timeframe.code
            secs = get_timeframe(code).seconds
            if secs <= ctx.timeframe.seconds and ctx.timeframe.seconds % secs == 0:
                # areas count candles from their pivot on; 500 bars before the first displayed bar get intrabars
                self.ltf = ctx.request_lower_tf(secs, lookback=500 + L)
            else:
                # TradingView stops the script with an error here; this port keeps running without intrabar volume
                self.warnings.append(f'Liquidity Swings: the intrabar timeframe ({p["intrabarTf"]}) must be lower than '
                                     'the chart timeframe and divide it; volume counts are 0 with Intrabar Precision.')

    def _target(self, side: Side) -> float:
        return side.count if self.p['filterOptions'] == 'Count' else side.vol

    def on_bar(self, ctx: Context) -> None:
        p = self.p
        if ctx.bar_index == 0:
            for side, area in zip(self.sides, (p['topAreaCss'], p['btmAreaCss'])):
                # box.new(na, na, na, na): created now (it is the script's oldest box), drawn nowhere until the first
                # pivot gives it coordinates, so it stays transparent until then
                side.bx = ctx.box(left=ctx.time, right=ctx.time, top=ctx.close, bottom=ctx.close,
                                  bg_color=NA_COLOR, border_color=NA_COLOR, group='areas')
                side.bx_color = new_color(area, 80)
        for side, show, css, area, style in (
                (self.sides[0], p['showTop'], p['topCss'], p['topAreaCss'], 'label_down'),
                (self.sides[1], p['showBtm'], p['btmCss'], p['btmAreaCss'], 'label_up')):
            self._side(ctx, side, show, css, area, style)

    def _side(self, ctx: Context, s: Side, show: bool, css: str, area_css: str, lbl_style: str) -> None:
        p = self.p
        n, L = ctx.bar_index, p['length']
        piv = s.pivot.update(ctx.high if s.high else ctx.low)
        cond = finite(piv)
        hi, lo = ctx.hist('high', L), ctx.hist('low', L)

        # get_counts(condition, top, btm), with this bar's top / btm before they are updated
        if cond:
            s.count, s.vol = 0, 0.0
        else:
            inside = finite(s.top) and finite(s.btm) and lo < s.top and hi > s.btm
            if p['intraPrecision']:
                if n > L and self.ltf is not None:
                    ib = ctx.intrabars(self.ltf, L).bars
                    for k in range(len(ib)):
                        if ib.low[k] < s.top and ib.high[k] > s.btm:
                            s.vol += float(ib.volume[k])
            else:
                s.vol += ctx.hist('volume', L) if inside else 0
            s.count += 1 if inside else 0

        # swing area and level
        if cond and show:
            o, c = ctx.hist('open', L), ctx.hist('close', L)
            if s.high:
                s.top = hi
                s.btm = max(c, o) if p['area'] == 'Wick Extremity' else lo
            else:
                s.top = min(c, o) if p['area'] == 'Wick Extremity' else hi
                s.btm = lo
            s.x1 = n - L
            s.crossed = False
            s.bx.left = s.bx.right = ctx.time_at(s.x1)
            s.bx.top, s.bx.bottom = s.top, s.btm
            s.bx.bg_color = s.bx_color
        else:
            edge = s.top if s.high else s.btm
            if finite(edge) and (ctx.close > edge if s.high else ctx.close < edge):
                s.crossed = True
            s.bx.right = ctx.time_at(s.x1 if s.crossed else n + 3)

        if not show:
            return
        target = self._target(s)
        fv = p['filterValue']
        level_value = s.top if s.high else s.btm

        # set_zone
        if target > fv and finite(s.zone_prev) and s.zone_prev <= fv:
            s.zone_bx = ctx.box(left=ctx.time_at(s.x1), top=s.top, right=ctx.time_at(s.x1 + s.count), bottom=s.btm,
                                border_color=NA_COLOR, bg_color=area_css, group='areas')
        if target > fv and s.zone_bx is not None:
            s.zone_bx.right = ctx.time_at(s.x1 + s.count)
        s.zone_prev = target

        # set_level
        prev_crossed = s.level_prev_crossed
        if cond:
            if finite(s.level_prev) and s.level_prev < fv:
                ctx.delete(s.lvl)
            elif prev_crossed is False and s.lvl is not None:
                s.lvl.x2 = ctx.time_at(n - L)
            s.lvl = ctx.line(x1=ctx.time_at(n - L), y1=level_value, x2=ctx.time, y2=level_value, color=NA_COLOR,
                             group='levels')
        if prev_crossed is False and s.lvl is not None:
            s.lvl.x2 = ctx.time_at(n + 3)
        if s.crossed and prev_crossed is False and s.lvl is not None:
            s.lvl.x2 = ctx.time
            s.lvl.style = 'dashed'
        if target > fv and s.lvl is not None:
            s.lvl.color = css
        s.level_prev = target
        s.level_prev_crossed = s.crossed

        # set_label (x / y: this bar's swing origin and level)
        if target > fv and finite(s.label_prev) and s.label_prev <= fv:
            s.lbl = ctx.label(x=ctx.time_at(s.x1), y=level_value, text=fmt_volume(s.vol), style=lbl_style,
                              size=LABEL_SIZE[p['labelSize']], color=NA_COLOR, text_color=css, group='labels')
        if target > fv and s.lbl is not None:
            s.lbl.text = fmt_volume(s.vol)
        s.label_prev = target

    def extra_output(self) -> dict:
        return dict(warnings=list(self.warnings))
