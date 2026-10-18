# Improved Liquidity Reaper (based on "Liquidity Reaper [JOAT]", MPL-2.0, © officialjackofalltrades).
# Weaknesses of the original found in backend/research/reaper (Exness XAUUSDm 2024-2026) and what this version adds:
#  - its 1.5 ATR stop sits inside the swept wick on ~45-60% of signals -> optional stop beyond the sweep extreme + buffer;
#  - a close back inside is not a reversal (price usually keeps going) -> optional market-structure-shift confirmation
#    (the close must also break the raid candle's opposite extreme);
#  - market entry at the confirmation close chases price -> optional limit entry on a retest of the swept level;
#  - sweeps against the higher-timeframe trend -> optional 4H EMA50/200 trend filter;
#  - spread is a large share of the risk on low timeframes / late session -> spread guard and 17-24 UTC filter;
#  - the trade plan only knew "TP3 or SL" -> single 1R / 2R / 3R exits or a scaled 1/2/3R plan with breakeven.
# "Tuned" takes the per-timeframe settings chosen on Jan 2024 - Jun 2025 data; the dashboard shows whether they also
# held on Jul - Dec 2025 (validation). The research report has the full monthly results, including the 2026 test.
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta, StrategySpec
from ..engine.pine import new_color, rgb
from ..engine.script import Context, Script
from ..engine.ta import ATR, SMA, ema
from .pinelib import NAN, TABLE_POSITIONS, WHITE, Pivot, Table, finite, fmt_pattern, fmt_price

STRATEGY_ID = 'reaper_pro'
CONFIRM = {'Close back (original)': 'close_back', 'Structure shift (MSS)': 'mss'}
STOPS = {'ATR x1.5 from entry (original)': ('atr', 1.5), 'Beyond sweep + 0.2 ATR': ('sweep', 0.2),
         'Beyond sweep + 0.5 ATR': ('sweep', 0.5)}
ENTRIES = {'Market at signal close (original)': 'market', 'Limit on retest of the swept level': 'retest'}
PLANS = {'1R': ((1.0, 1.0),), '2R': ((2.0, 1.0),), '3R (original)': ((3.0, 1.0),),
         'Scaled 1R / 2R / 3R + breakeven': ((1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3))}
RETEST_EXPIRY = 5

# Per-timeframe settings chosen by backend/research/reaper/tune.py on TRAIN (Jan 2024 - Jun 2025) only;
# 'validated' = the same settings were also profitable on VALID (Jul - Dec 2025); 'test' = their 2026 result (never
# used to choose). Written from research/reaper/out by the research scripts.
PRESETS: dict[str, dict] = json.loads(Path(__file__).with_name('liquidity_reaper_pro_presets.json').read_text())

G_P, G_L, G_F, G_T, G_V, G_D = ('Preset', 'Liquidity Detection', 'Filters', 'Trade Plan', 'Visuals', 'Dashboard')
INPUTS = [
    InputSpec('preset', 'string', 'Settings', 'Tuned for chart timeframe', options=('Tuned for chart timeframe', 'Custom'),
              group=G_P, tooltip='Tuned: the research settings for the chart timeframe (the dashboard says whether they '
                                 'passed validation). Custom: the settings below.'),
    InputSpec('pivotLen', 'int', 'Swing Strength (bars each side)', 8, minval=2, maxval=50, group=G_L),
    InputSpec('confirmWindow', 'int', 'Sweep Confirmation Window', 3, minval=1, maxval=20, group=G_L),
    InputSpec('maxPools', 'int', 'Max Live Pools Per Side', 12, minval=2, maxval=40, group=G_L),
    InputSpec('minWickPct', 'float', 'Min Sweep Wick (% of range)', 25.0, minval=0, maxval=90, step=5, group=G_L),
    InputSpec('confirm', 'string', 'Confirmation', 'Close back (original)', options=tuple(CONFIRM), group=G_L),
    InputSpec('useVolFilter', 'bool', 'Require Volume Expansion', True, group=G_F),
    InputSpec('volMultReq', 'float', 'Volume Multiplier', 1.1, minval=0.5, step=0.1, group=G_F),
    InputSpec('trendFilter', 'bool', 'Only With 4H Trend (EMA50 vs EMA200)', False, group=G_F),
    InputSpec('sessionFilter', 'bool', 'No Entries 17:00-24:00 UTC', False, group=G_F),
    InputSpec('spreadGuard', 'bool', 'Skip If Spread > 10% Of Risk', False, group=G_F),
    InputSpec('spread', 'float', 'Typical Spread (price units)', 0.26, minval=0, step=0.01, group=G_F,
              tooltip='The chart shows bid prices only; the guard uses this spread (Exness XAUUSDm median 2026: 0.26).'),
    InputSpec('stopMode', 'string', 'Stop Loss', 'ATR x1.5 from entry (original)', options=tuple(STOPS), group=G_T),
    InputSpec('entryMode', 'string', 'Entry', 'Market at signal close (original)', options=tuple(ENTRIES), group=G_T),
    InputSpec('plan', 'string', 'Take Profit', '3R (original)', options=tuple(PLANS), group=G_T),
    InputSpec('showRails', 'bool', 'Show Unswept Liquidity Rails', True, group=G_V),
    InputSpec('showZones', 'bool', 'Show Swept Zones', True, group=G_V),
    InputSpec('showSignalLabels', 'bool', 'Show Sweep Labels', True, group=G_V),
    InputSpec('showTargets', 'bool', 'Show TP/SL Levels', True, group=G_V),
    InputSpec('buyColor', 'color', 'Buy / Bull Liquidity Color', '#38BDF8FF', group=G_V),
    InputSpec('sellColor', 'color', 'Sell / Bear Liquidity Color', '#E11D48FF', group=G_V),
    InputSpec('showDash', 'bool', 'Show Dashboard', True, group=G_D),
    InputSpec('dashPos', 'string', 'Dashboard Position', 'Top Right', options=tuple(TABLE_POSITIONS)[:8], group=G_D),
]

META = ScriptMeta(
    id='liquidity_reaper_pro', name='Liquidity Reaper Pro (improved)', short_name='REAPER PRO', category='Custom',
    description='Liquidity Reaper with the weaknesses found in research fixed as options (stop beyond the sweep, '
                'structure-shift confirmation, retest entry, 4H trend / session / spread filters, exit plans) and '
                'per-timeframe tuned settings. Also a Strategy Tester strategy.',
    max_boxes=500, max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS,
    strategies=[StrategySpec(STRATEGY_ID, 'Liquidity Reaper Pro', 'Trades the Reaper Pro signals with the chosen plan.')],
    drawing_groups=[DrawingGroup('rails', 'Liquidity rails'), DrawingGroup('zones', 'Swept zones'),
                    DrawingGroup('signals', 'Sweep labels'), DrawingGroup('targets', 'TP/SL levels')],
)

# research setting names -> input values
_R_CONFIRM = {v: k for k, v in CONFIRM.items()}
_R_STOP = {'atr1.5': 'ATR x1.5 from entry (original)', 'sweep+0.2': 'Beyond sweep + 0.2 ATR',
           'sweep+0.5': 'Beyond sweep + 0.5 ATR'}
_R_ENTRY = {'market': 'Market at signal close (original)', 'retest': 'Limit on retest of the swept level'}
_R_PLAN = {'single_1R': '1R', 'single_2R': '2R', 'single_3R': '3R (original)',
           'scaled_123': 'Scaled 1R / 2R / 3R + breakeven'}


def preset_inputs(cfg: dict) -> dict:
    return dict(pivotLen=cfg['pivot_len'], confirm=_R_CONFIRM[cfg['confirm']], useVolFilter=cfg['use_vol'],
                stopMode=_R_STOP[cfg['stop']], entryMode=_R_ENTRY[cfg['entry']], plan=_R_PLAN[cfg['plan']],
                trendFilter=cfg['trend_filter'], sessionFilter=cfg['session_filter'], spreadGuard=cfg['spread_guard'])


class LiquidityReaperPro(Script):
    meta = META

    def required_warmup(self) -> int:
        return 3000

    def setup(self, ctx: Context) -> None:
        p = dict(self.p)
        self.preset = PRESETS.get(ctx.timeframe.code) if p['preset'] == 'Tuned for chart timeframe' else None
        if self.preset:
            p.update(preset_inputs(self.preset['config']))
        self.q = p  # effective settings
        self.atr = ATR(14)
        self.vol_sma = SMA(20)
        L = p['pivotLen']
        self.ph, self.pl = Pivot(L, L, True), Pivot(L, L, False)
        self.pools = {-1: [], 1: []}  # side of the signal a raid would give -> [(level, born, rail)]
        self.pend = {-1: None, 1: None}
        self.zones = []
        self.objs: dict = {}
        self.trade = None  # (side, entry, stop, targets, open_bar)
        self.stats = dict(buy=0, sell=0)
        self.last_signal = None
        h4 = lambda b, n: ema(np.asarray(b.close, dtype=float), n)
        self.h4 = (ctx.request_security('H4', lambda b: h4(b, 50)), ctx.request_security('H4', lambda b: h4(b, 200)))

    def _trend(self, ctx) -> int:
        e50, e200 = ctx.security(self.h4[0]), ctx.security(self.h4[1])
        return int(np.sign(e50 - e200)) if finite(e50) and finite(e200) else 0

    def on_bar(self, ctx: Context) -> None:
        q = self.q
        i, t = ctx.bar_index, ctx.time
        o, h, l, c, v = ctx.open, ctx.high, ctx.low, ctx.close, ctx.volume
        atr = self.atr.update(h, l, c)
        vma = self.vol_sma.update(v)
        vm = v / vma if finite(vma) and vma > 0 else 1.0
        vol_ok = not q['useVolFilter'] or vm >= q['volMultReq']
        L = q['pivotLen']
        for piv, side, col in ((self.ph.update(h), -1, q['sellColor']), (self.pl.update(l), 1, q['buyColor'])):
            if not finite(piv):
                continue
            rail = ctx.line(x1=ctx.time_at(i - L), y1=piv, x2=t, y2=piv, extend='right', color=new_color(col, 45),
                            style='dotted', group='rails') if q['showRails'] else None
            pools = self.pools[side]
            pools.append((piv, i - L, rail))
            if len(pools) > q['maxPools']:
                ctx.delete(pools.pop(0)[2])
        rng = max(h - l, ctx.mintick)
        for side, beyond in ((-1, lambda lvl: h > lvl), (1, lambda lvl: l < lvl)):
            pools = self.pools[side]
            for k in range(len(pools) - 1, -1, -1):
                lvl, born, rail = pools[k]
                if beyond(lvl):
                    if rail is not None:
                        rail.extend, rail.x2 = 'none', t
                    self.pend[side] = [lvl, h if side == -1 else l, i, rng, h, l]
                    del pools[k]
        if self.pend[-1] is not None:
            self.pend[-1][1] = max(self.pend[-1][1], h)
        if self.pend[1] is not None:
            self.pend[1][1] = min(self.pend[1][1], l)
        signals = []
        for side in (-1, 1):
            pd = self.pend[side]
            if pd is None:
                continue
            lvl, ext, bb, rr, rhi, rlo = pd
            elapsed = i - bb
            wick = ((ext - lvl) if side == -1 else (lvl - ext)) / rr * 100
            if 1 <= elapsed <= q['confirmWindow']:
                back = (c < lvl and c < o) if side == -1 else (c > lvl and c > o)
                if CONFIRM[q['confirm']] == 'mss':
                    back = back and ((c < rlo) if side == -1 else (c > rhi))
                if back and vol_ok and wick >= q['minWickPct']:
                    signals.append((side, lvl, ext, bb))
                    self.pend[side] = None
            elif elapsed > q['confirmWindow']:
                self.pend[side] = None
        self._manage_trade(ctx)
        for side, lvl, ext, bb in signals:
            self._signal(ctx, side, lvl, ext, bb, atr, vm)
        life = 120
        for k in range(len(self.zones) - 1, -1, -1):
            bx, born = self.zones[k]
            if i - born > life:
                ctx.delete(bx)
                del self.zones[k]
            else:
                bx.right = ctx.time_at(born + life)

    def _filters_ok(self, ctx, side, ref, stop) -> bool:
        q = self.q
        if q['trendFilter'] and side * self._trend(ctx) <= 0:
            return False
        if q['sessionFilter'] and (ctx.time // 3600) % 24 >= 17:
            return False
        if q['spreadGuard'] and q['spread'] > 0.10 * abs(ref - stop):
            return False
        return True

    def _signal(self, ctx, side, lvl, ext, bb, atr, vm):
        q = self.q
        i, c = ctx.bar_index, ctx.close
        col = q['buyColor'] if side == 1 else q['sellColor']
        mode, k = STOPS[q['stopMode']]
        entry_mode = ENTRIES[q['entryMode']]
        ref = c if entry_mode == 'market' else lvl
        stop = ref - side * k * atr if mode == 'atr' else ext - side * k * atr
        risk = abs(ref - stop)
        if not (risk > 0 and (stop - ref) * side < 0):
            return
        taken = self._filters_ok(ctx, side, ref, stop)
        if q['showZones']:
            top, bot = (lvl, ext) if side == 1 else (ext, lvl)
            bx = ctx.box(left=ctx.time_at(i - q['confirmWindow']), right=ctx.time_at(i + 120), top=top, bottom=bot,
                         border_color=new_color(col, 55), bg_color=new_color(col, 78), group='zones')
            self.zones.append((bx, i))
        if q['showSignalLabels']:
            text = ('RAID BUY' if side == 1 else 'RAID SELL') + f' · {fmt_pattern(vm, "#.#")}x' + ('' if taken else ' · filtered')
            ctx.label(x=ctx.time, y=ctx.low if side == 1 else ctx.high, text=text,
                      style='label_up' if side == 1 else 'label_down',
                      color=new_color(col, 8 if taken else 60), text_color=WHITE, size='small', group='signals')
        if not taken or self.trade is not None:
            return
        targets = [(ref + side * r * risk, f) for r, f in PLANS[q['plan']]]
        self.stats['buy' if side == 1 else 'sell'] += 1
        self.last_signal = (side, i)
        if ctx.is_confirmed:
            ctx.signal(STRATEGY_ID, 'long' if side == 1 else 'short',
                       order_type='market' if entry_mode == 'market' else 'limit',
                       entry=None if entry_mode == 'market' else lvl,
                       expiry_bars=None if entry_mode == 'market' else RETEST_EXPIRY,
                       stop_loss=stop, take_profits=targets, breakeven_after_tp1=len(targets) > 1,
                       comment='Reaper Pro ' + ('buy' if side == 1 else 'sell'))
        self.trade = dict(side=side, entry=ref, stop=stop, targets=targets, bar=i, filled=entry_mode == 'market',
                          expiry=i + RETEST_EXPIRY, hit=0)
        self._draw_trade(ctx)

    def _draw_trade(self, ctx):
        if not self.q['showTargets']:
            return
        for obj in self.objs.values():
            ctx.delete(obj)
        self.objs = {}
        tr, prec, t = self.trade, ctx.precision, ctx.time
        buy, sell = self.q['buyColor'], self.q['sellColor']
        main = buy if tr['side'] == 1 else sell
        mk = lambda y, col, **kw: ctx.line(x1=t, y1=y, x2=ctx.time_at(ctx.bar_index + 1), y2=y, color=col,
                                           group='targets', **kw)
        lab = lambda y, text, col, tc=WHITE: ctx.label(x=t, y=y, text=text, style='label_left', color=col, text_color=tc,
                                                       size='small', group='targets')
        kind = ('BUY ' if tr['side'] == 1 else 'SELL ') + ('' if tr['filled'] else 'LIMIT ')
        self.objs['entry_line'] = mk(tr['entry'], main, width=2)
        self.objs['entry_label'] = lab(tr['entry'], kind + fmt_price(tr['entry'], prec), main,
                                       rgb(6, 22, 32) if tr['side'] == 1 else WHITE)
        self.objs['sl_line'] = mk(tr['stop'], new_color(sell, 40), style='dashed')
        self.objs['sl_label'] = lab(tr['stop'], 'SL ' + fmt_price(tr['stop'], prec), new_color(sell, 40))
        for n, (px, _) in enumerate(tr['targets'], 1):
            name = f'TP{n}' if len(tr['targets']) > 1 else 'TP'
            self.objs[f'tp{n}_line'] = mk(px, new_color(buy, 40))
            self.objs[f'tp{n}_label'] = lab(px, f'{name} ' + fmt_price(px, prec), new_color(buy, 40))
        top = lambda a, b: (max(a, b), min(a, b))
        rt, rb = top(tr['entry'], tr['stop'])
        self.objs['risk_fill'] = ctx.box(left=t, right=ctx.time_at(ctx.bar_index + 1), top=rt, bottom=rb,
                                         border_width=0, border_color='#00000000', bg_color=new_color(sell, 76),
                                         group='targets')
        wt, wb = top(tr['entry'], tr['targets'][-1][0])
        self.objs['reward_fill'] = ctx.box(left=t, right=ctx.time_at(ctx.bar_index + 1), top=wt, bottom=wb,
                                           border_width=0, border_color='#00000000', bg_color=new_color(buy, 76),
                                           group='targets')

    def _manage_trade(self, ctx):
        """Chart-side picture of the open plan (the Strategy Tester simulates fills exactly; this only draws)."""
        tr = self.trade
        if tr is None or ctx.bar_index <= tr['bar']:
            return
        side, i, h, l = tr['side'], ctx.bar_index, ctx.high, ctx.low
        done = False
        if not tr['filled']:
            if (l <= tr['entry']) if side == 1 else (h >= tr['entry']):
                tr['filled'] = True
            elif i >= tr['expiry']:
                done = True
        if tr['filled'] and not done:
            if (l <= tr['stop']) if side == 1 else (h >= tr['stop']):
                done = True
            else:
                while tr['hit'] < len(tr['targets']) and ((h >= tr['targets'][tr['hit']][0]) if side == 1
                                                          else (l <= tr['targets'][tr['hit']][0])):
                    tr['hit'] += 1
                    if len(tr['targets']) > 1 and tr['hit'] == 1:
                        tr['stop'] = tr['entry']
                done = tr['hit'] >= len(tr['targets'])
        for k, obj in self.objs.items():
            if k.endswith('_label'):
                obj.x = ctx.time
            elif k.endswith('_fill'):
                obj.right = ctx.time if done else ctx.time_at(i + 1)
            else:
                obj.x2 = ctx.time if done else ctx.time_at(i + 1)
        if done:
            self.trade = None

    def on_last_bar(self, ctx: Context) -> None:
        self.table = None
        q = self.q
        if not q['showDash']:
            return
        buy, sell = q['buyColor'], q['sellColor']
        t = Table('reaper_pro', TABLE_POSITIONS[q['dashPos']], 2, 9, bg_color=new_color('#0B1220FF', 8),
                  border_width=1, border_color='#00000000', frame_width=2, frame_color=new_color(buy, 35))
        bg = new_color('#0E1830FF', 22)
        lbl = new_color(WHITE, 18)
        t.cell(0, 0, '❄ REAPER PRO', text_color=WHITE, bgcolor=new_color(buy, 35), text_size='small')
        t.cell(1, 0, f'{ctx.ticker} · {ctx.timeframe.pine}', text_color=WHITE, bgcolor=new_color(sell, 40),
               text_size='small')
        if self.p['preset'] == 'Tuned for chart timeframe':
            if self.preset:
                status = 'tuned · validated' if self.preset['validated'] else 'tuned · FAILED validation'
                status += f" · 2026 test {self.preset['test']['avg_r']:+.2f}R x {self.preset['test']['trades']}"
                scol = '#26A69AFF' if self.preset['validated'] else '#F23645FF'
            else:
                status, scol = 'no tuned preset · custom', new_color(WHITE, 30)
        else:
            status, scol = 'custom settings', new_color(WHITE, 30)
        rows = [('Settings', status, scol), ('Confirmation', q['confirm'].split(' (')[0], WHITE),
                ('Stop', q['stopMode'].split(' (')[0], WHITE), ('Entry', q['entryMode'].split(' (')[0], WHITE),
                ('Take profit', q['plan'], WHITE),
                ('Filters', ', '.join(n for n, on in (('4H trend', q['trendFilter']), ('session', q['sessionFilter']),
                                                       ('spread', q['spreadGuard']), ('volume', q['useVolFilter'])) if on)
                 or 'none', WHITE),
                ('Signals (B / S)', f"{self.stats['buy']} / {self.stats['sell']}", WHITE),
                ('Position', 'none' if self.trade is None else ('LONG' if self.trade['side'] == 1 else 'SHORT')
                 + ('' if self.trade['filled'] else ' (limit)'), buy if self.trade and self.trade['side'] == 1 else
                 sell if self.trade else new_color(WHITE, 40))]
        for r, (a, b, col) in enumerate(rows, 1):
            t.cell(0, r, a, text_color=lbl, bgcolor=bg, text_size='small', text_halign='left')
            t.cell(1, r, b, text_color=col, bgcolor=bg, text_size='small', text_halign='left')
        self.table = t

    def extra_output(self) -> dict:
        t = getattr(self, 'table', None)
        js = t.to_json() if t else None
        return dict(tables=[js] if js else [])

    def strategy(self, ctx):  # signals are emitted in _signal
        pass
