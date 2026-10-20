# This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
# © officialjackofalltrades
# Python port of "Liquidity Reaper [JOAT]" (Pine v6), line for line.
"""Liquidity Reaper [JOAT] — what each part does and which parts decide trades.

IDEA
  Stops rest just beyond obvious swing highs / lows ("liquidity pools"). When price spikes through a pool and closes
  back on the other side, the stops were taken ("raid") and price often reverses. The script marks those raids as
  RAID BUY (a low pool swept, close back above it) and RAID SELL (a high pool swept, close back below it).

SIGNAL PIPELINE (decides trades; every step runs once per bar in on_bar, in this order)
  1. Indicators   ATR(tp_atr_period) for the TP/SL distance, SMA(volLen) of volume for the volume pulse,
                  DMI/ADX(14) for the optional ADX filter.
  2. Pools        Pivot(pivotLen, pivotLen) finds a swing high / low ``pivotLen`` bars after it happened (it needs
                  that many bars on the right, so a pool is only known pivotLen bars late). Each becomes a Pool with a
                  level; at most ``maxPools`` per side live, the oldest is recycled.
  3. Raids        A bar whose high goes above a live high pool (low below a low pool) "raids" it: the pool is removed
                  and becomes the pending sweep for that side (if several pools are raided on one bar, the OLDEST one
                  is kept, as in Pine). The wick extreme of the pending sweep keeps following new highs / lows.
  4. Confirmation Within 1..``confirmWindow`` bars after the raid bar, a bar must close back inside the pool level
                  with a candle of the signal's colour (sell: close < level and close < open). It also needs the
                  volume filter (volume >= volMultReq x its SMA, if useVolFilter) and a sweep wick of at least
                  ``minWickPct`` % of the raid bar's range beyond the level. If the window passes without that, the
                  pending sweep is dropped. A confirmed sweep is the signal (buySignal / sellSignal).
  5. Filters      (added, not in Pine, all off by default) 4H EMA50/200 trend and ADX >= adxMin. They only drop
                  signals; the sweep is consumed either way.
  6. ML filter    (added, off by default; app/scripts/reaper_ml.py) a model trained on this indicator's past signals
                  scores each remaining signal from 23 inputs known at its close and skips those below its threshold.
  TRADING-RELEVANT INPUTS: pivotLen, confirmWindow, maxPools, minWickPct, useVolFilter, volLen, volMultReq,
  trendFilter, adxFilter, adxMin, and the TP/SL inputs below (they set the trade plan, not the signal).
  PRESET: tfPresetMode (added) replaces those inputs with the tested values for the chart timeframe (TF_PRESETS) in
  setup(), before any indicator object is built; timeframes without a tested preset keep the user's inputs.
  Added signal filters: engulfFilter (candle body engulfs the previous body), deltaFilter (1-minute order-flow delta).

TRADE PLAN (display of a position; the backtest in backend/research/reaper uses the same numbers)
  _targets(): on a signal, entry = signal close; risk = ATR x sl_atr_mult (or sl_percent of price, or — added
  option — the swept wick extreme + sl_sweep_buffer x ATR); SL = entry -/+ risk; TP1..TP3 = entry +/- risk x rr_tpN.
  The drawn position closes when price touches the SL or the furthest SHOWN target (TP3 = +3R by default). The stop
  steps up with the targets: once a candle touches TP1 the SL moves to TP1 (+1R locked), at TP2 to TP2 (+2R), so a
  trade scores -1R (SL before TP1), +1R (TP1), +2R (TP2) or +3R (TP3). The research backtest keeps the fixed SL.
  One trade at a time, like the backtest: a signal while a position is open keeps its label but opens nothing.
  REPLAY RECORD: in chart replay the dashboard adds wins / losses / net R of the trades closed since the bar where
  the replay was started (ctx.replay_start).

DISPLAY ONLY (never changes a signal)
  Liquidity rails (dotted lines at unswept pools), swept zones (box from the pool level to the wick, kept
  zoneLifeBars), RAID labels / triangles, TP/SL lines and fills, colours, and the Reaper dashboard (on_last_bar:
  pools above / below and their distance in ATR, volume pulse, raid counts, last raid, pending sweep, position, and
  in replay the win / loss record).

TIMING / NO LOOKAHEAD
  Everything uses the current and past bars only. Pools appear pivotLen bars after their swing; a signal is known at
  its bar's close; the 4H trend reads the last COMPLETED 4H candle; the 1-minute delta reads only the minutes inside
  the signal candle.

IMPROVED VERSION (how the added parts were chosen — full story in docs/research/reaper-v2-2026-10-18/PROCESS_NOTES.md)
  Exness XAUUSD, one trade at a time, next-open entry on bid/ask, 1-minute stop/target order. Each added rule was kept
  only if it improved 2024-01..2025-06 AND 2025-07..12 while keeping the trade count within +-20 %. The research
  code (backend/research/reaper, backend/research/reaper_v2) uses its own copy of this signal logic and was checked to
  produce exactly the same signals as this file. Long backtest 2022-01..2026-10 ("Most consistent", 15m-4H): +144R,
  but 2022 -30R and 2023 -64R; 5m and 1m lose. Original rules on 15m-4H: -292R.

FILE MAP
  TF_PRESETS ............ tested settings per timeframe (used by the "Settings Per Chart Timeframe" input)
  INPUTS / META ......... settings shown in the dialog, drawing groups, shapes
  Pool / PendingSweep ... the liquidity state the signals come from
  setup() ............... applies a preset, builds the indicators and registers 4H / 1-minute data requests
  on_bar() .............. the signal pipeline above, then zones, labels, TP/SL display, dashboard counters
  _delta() .............. 1-minute order-flow delta of the current candle (deltaFilter, ML input)
  _risk() / _ml_row() ... stop distance of a trade; the ML input row of a signal (see reaper_ml.py)
  _targets() ............ draws and tracks the position of the latest signal (display, not a broker)
  on_last_bar() ......... the Reaper dashboard table
"""
from __future__ import annotations

import math

import numpy as np
from dataclasses import dataclass

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta, ShapeSpec
from ..engine.pine import new_color, rgb
from ..engine.script import Context, Script
from ..engine.ta import ATR, SMA, ema
from .pinelib import NAN, TABLE_POSITIONS, WHITE, Pivot, Table, finite, fmt_pattern, fmt_price
from .smc_master.technical import DMI
from .reaper_ml import FeatureState, Model, signal_features

G_LIQ, G_VOL, G_SIG, G_RZ, G_VIS, G_TP, G_TPD, G_DASH = (
    'Liquidity Detection', 'Volume Filter', 'Signals', 'Rails and Zones', 'Visuals', 'TP/SL', 'TP/SL Display',
    'Reaper Dashboard')
G_TREND = 'Trend Filter'

# ─── Tested settings per chart timeframe ───────────────────────────────────────────────────────────────────────────
# (docs/research/reaper-v2-2026-10-18/SUMMARY.md, "combining the rounds"). Each name below is one research round:
#   ORIGINAL - the Pine defaults (swing 8, window 3, wick 25 %, volume filter on, SL 1.5 ATR, TP3 3R)
#   ROUND1   - + 4H trend filter (swing 5, wick 10 % to keep the trade count)
#   ROUND2   - + ADX >= 25 and SL 0.2 ATR beyond the swept wick (swing 3, window 5, volume filter off)
#   ROUND3   - engulfing body + 1-minute delta with the trade (swing 3, window 5, wick 10 %, volume filter off)
# _BASE switches every added option off, so a preset never inherits a filter the user left on.
# Each timeframe uses the logic of whichever research round did best there; two ways of choosing are offered:
#   'Best in 2026'               - the highest net R on Jan-Oct 2026 per timeframe (highest 2026 total, but chosen on
#                                  2026 itself, and weaker over 2024-2025);
#   'Most consistent 2024-2026'  - the highest net R over all of 2024-2026 per timeframe (every half-year profitable).
# 1m lost money with every setting tested and the daily chart was not tested: there the user's inputs stay.
_BASE = dict(trendFilter=False, adxFilter=False, adxMin=25.0, engulfFilter=False, deltaFilter=False,
             sl_beyond_sweep=False, sl_sweep_buffer=0.2, use_atr_sl=True, sl_atr_mult=1.5, rr_tp3=3.0, volMultReq=1.1)
ORIGINAL = {**_BASE, 'pivotLen': 8, 'confirmWindow': 3, 'minWickPct': 25.0, 'useVolFilter': True}
ROUND1 = {**_BASE, 'pivotLen': 5, 'confirmWindow': 3, 'minWickPct': 10.0, 'useVolFilter': True, 'trendFilter': True}
ROUND2 = {**_BASE, 'pivotLen': 3, 'confirmWindow': 5, 'minWickPct': 25.0, 'useVolFilter': False, 'trendFilter': True,
          'adxFilter': True, 'sl_beyond_sweep': True}
ROUND3 = {**_BASE, 'pivotLen': 3, 'confirmWindow': 5, 'minWickPct': 10.0, 'useVolFilter': False, 'engulfFilter': True,
          'deltaFilter': True}
PRESET_BEST_2026, PRESET_CONSISTENT = 'Best in 2026', 'Most consistent 2024-2026'
TF_PRESETS = {
    PRESET_BEST_2026: {'M5': ROUND1, 'M15': ROUND2, 'M30': ROUND2, 'H1': ROUND1, 'H4': ROUND2},
    PRESET_CONSISTENT: {'M5': ORIGINAL, 'M15': ROUND3, 'M30': ROUND2, 'H1': ROUND2, 'H4': ROUND2},
}

INPUTS = [
    InputSpec('tfPresetMode', 'string', 'Settings Per Chart Timeframe', 'Off',
              options=('Off', PRESET_BEST_2026, PRESET_CONSISTENT), group='Preset',
              tooltip='Off: your inputs below. Otherwise the tested logic for the chart timeframe replaces the '
                      'Liquidity Detection, Volume, Filter and SL inputs (TP3 = 3R).\n\n'
                      'Best in 2026 (Jan-Oct net R, XAUUSD): 5m +30.6, 15m +40.5, 30m +3.1, 1H +17.7, 4H +5.9 = +97.7R, '
                      'but chosen on 2026 itself; 2024-2025 +122.6R.\n'
                      'Most consistent 2024-2026: 5m +26.9, 15m +39.8, 30m +3.1, 1H -4.4, 4H +5.9 = +71.4R in 2026; '
                      'every half-year since 2024 profitable, 2024-2025 +236.9R.\n'
                      '1m (loses with every setting) and the daily chart (untested) keep your inputs.'),
    InputSpec('pivotLen', 'int', 'Swing Strength (bars each side)', 8, minval=2, maxval=50, group=G_LIQ,
              tooltip='Bars to the left and right required to confirm a swing high/low as a liquidity pool.\n\n'
                      'Higher = fewer, more significant pools.'),
    InputSpec('confirmWindow', 'int', 'Sweep Confirmation Window', 3, minval=1, maxval=20, group=G_LIQ,
              tooltip='After price wicks through a pool, a confirming close back inside must occur within this many '
                      'bars for a valid sweep.'),
    InputSpec('maxPools', 'int', 'Max Live Pools Per Side', 12, minval=2, maxval=40, group=G_LIQ,
              tooltip='Oldest liquidity rails are recycled once this limit is reached — keeps the chart clean on all '
                      'timeframes.'),
    InputSpec('minWickPct', 'float', 'Min Sweep Wick (% of range)', 25.0, minval=0, maxval=90, step=5, group=G_LIQ,
              tooltip='The sweep candle must reject with a wick of at least this % of its range beyond the pool — '
                      'filters weak pokes.'),
    InputSpec('useVolFilter', 'bool', 'Require Volume Expansion', True, group=G_VOL,
              tooltip='Sweep signals require volume above its average × multiplier.'),
    InputSpec('volLen', 'int', 'Volume MA Length', 20, minval=1, group=G_VOL),
    InputSpec('volMultReq', 'float', 'Volume Multiplier', 1.1, minval=0.5, step=0.1, group=G_VOL),
    # ─── Added signal filters (not in the Pine source; all off = the original) ───
    # Chosen one at a time on Exness XAUUSD 2024-01..2025-06, each kept only if 2025-07..12 also improved, then run on
    # 2026; see docs/research/reaper-v2-2026-10-18/RESULTS.md. They only REMOVE signals, never add or flip one.
    InputSpec('trendFilter', 'bool', 'Only With 4H Trend (EMA50 vs EMA200)', False, group=G_TREND,
              tooltip='Buy raids only while the 4H EMA50 is above the 4H EMA200, sell raids only while it is below '
                      '(last completed 4H candle, no lookahead). Off = original signals.'),
    InputSpec('adxFilter', 'bool', 'Only When ADX Shows a Trend', False, group=G_TREND,
              tooltip='Raids only while ADX(14) is at or above the level below: a swept pool in a moving market '
                      'rather than a flat one. Off = original signals.'),
    InputSpec('adxMin', 'float', 'Minimum ADX', 25.0, minval=0, maxval=100, step=1, group=G_TREND),
    InputSpec('engulfFilter', 'bool', 'Signal Candle Must Engulf The Previous Body', False, group=G_TREND,
              tooltip='Buy: the signal candle opens at or below and closes at or above the previous candle\'s body. '
                      'Sell: the mirror. Off = original signals.'),
    InputSpec('deltaFilter', 'bool', 'Order Flow: 1-Minute Delta With The Trade', False, group=G_TREND,
              tooltip='Footprint-style delta of the signal candle from its 1-minute candles (volume of up minutes minus '
                      'down minutes) must be positive for a buy, negative for a sell. Needs 1-minute data.'),
    # ─── ML filter (added): a trained model rates each signal and skips the lowest-rated ones ───
    InputSpec('mlFilter', 'bool', 'ML Filter: Skip Low-Rated Raids', False, group='ML Filter',
              tooltip='A model trained on this indicator\'s own past signals (app/scripts/reaper_ml_model.json, see '
                      'app/scripts/reaper_ml.py) rates every raid and skips those below its threshold. It never adds or '
                      'reverses a signal. Trained on the "Most consistent 2024-2026" preset with a 3R target: with other '
                      'settings it is switched off with a warning. Labels show the setup score when on.'),
    # ─── Display settings (never change a signal) ───
    InputSpec('showSignalLabels', 'bool', 'Show Sweep Labels', True, group=G_SIG,
              tooltip='Labels show direction plus relative volume of the raid, e.g. "RAID BUY · 2.3x".'),
    InputSpec('labelSizeStr', 'string', 'Label Size', 'Small', options=('Tiny', 'Small', 'Normal'), group=G_SIG),
    InputSpec('showTriangles', 'bool', 'Show Triangle Signals', False, group=G_SIG),
    InputSpec('showRails', 'bool', 'Show Unswept Liquidity Rails', True, group=G_RZ),
    InputSpec('railStyleStr', 'string', 'Rail Style', 'Dotted', options=('Solid', 'Dashed', 'Dotted'), group=G_RZ),
    InputSpec('railTransp', 'int', 'Rail Transparency', 45, minval=0, maxval=100, group=G_RZ),
    InputSpec('showZones', 'bool', 'Show Swept Zones', True, group=G_RZ,
              tooltip='Paints a frost box across the raided range (pool level → sweep wick extreme).'),
    InputSpec('zoneTransp', 'int', 'Zone Transparency', 78, minval=0, maxval=100, group=G_RZ),
    InputSpec('zoneLifeBars', 'int', 'Zone Lifetime (bars)', 120, minval=10, maxval=500, group=G_RZ),
    InputSpec('buyColor', 'color', 'Buy / Bull Liquidity Color', '#38BDF8FF', group=G_VIS),
    InputSpec('sellColor', 'color', 'Sell / Bear Liquidity Color', '#E11D48FF', group=G_VIS),
    InputSpec('labelTextColor', 'color', 'Label Text Color', WHITE, group=G_VIS),
    # ─── Trade plan (where the SL / TPs of a signal sit; the backtests use the same rules) ───
    InputSpec('show_targets', 'bool', 'Show TP/SL Levels', True, group=G_TP),
    InputSpec('use_atr_sl', 'bool', 'SL = ATR × instead of %', True, group=G_TP),
    InputSpec('sl_beyond_sweep', 'bool', 'SL Beyond the Sweep Wick', False, group=G_TP,
              tooltip='Stop just past the swept wick extreme (+ the ATR buffer below) instead of ATR × / % from the '
                      'entry, so the stop is not inside the wick that made the signal. TPs stay R multiples of it.'),
    InputSpec('sl_sweep_buffer', 'float', 'Sweep SL Buffer (× ATR)', 0.2, minval=0, step=0.1, group=G_TP),
    InputSpec('tp_atr_period', 'int', 'ATR Period For TP/SL', 14, minval=1, group=G_TP),
    InputSpec('sl_atr_mult', 'float', 'ATR Multiplier For SL', 1.5, step=0.1, group=G_TP),
    InputSpec('sl_percent', 'float', 'SL % From Entry', 1.0, step=0.1, group=G_TP),
    InputSpec('rr_tp1', 'float', 'RR For TP1', 1.0, step=0.1, group=G_TP),
    InputSpec('rr_tp2', 'float', 'RR For TP2', 2.0, step=0.1, group=G_TP),
    InputSpec('rr_tp3', 'float', 'RR For TP3', 3.0, step=0.1, group=G_TP),
    InputSpec('show_sl_level', 'bool', 'Show SL Level', True, group=G_TPD),
    InputSpec('show_tp1_level', 'bool', 'Show TP1 Level', True, group=G_TPD),
    InputSpec('show_tp2_level', 'bool', 'Show TP2 Level', True, group=G_TPD),
    InputSpec('show_tp3_level', 'bool', 'Show TP3 Level', True, group=G_TPD),
    InputSpec('showDash', 'bool', 'Show Reaper Dashboard', True, group=G_DASH),
    InputSpec('dashPos', 'string', 'Dashboard Position', 'Top Right',
              options=('Top Left', 'Top Right', 'Bottom Left', 'Bottom Right', 'Top Center', 'Bottom Center',
                       'Middle Left', 'Middle Right'), group=G_DASH),
    InputSpec('dashSize', 'string', 'Dashboard Text Size', 'Normal', options=('Tiny', 'Small', 'Normal', 'Large'),
              group=G_DASH),
]

META = ScriptMeta(
    id='liquidity_reaper_joat', name='Liquidity Reaper [JOAT]', short_name='REAPER [JOAT]', category='Custom',
    description='Liquidity pools at swing highs/lows, confirmed sweeps (RAID BUY / SELL) with volume filter, swept '
                'zones, TP/SL levels and the Reaper dashboard. Port of the Pine v6 script by officialjackofalltrades.',
    max_boxes=500, max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS,
    shapes=[ShapeSpec('raid_buy', 'Raid Buy', 'triangleup', 'belowbar', '#38BDF8FF', size='tiny'),
            ShapeSpec('raid_sell', 'Raid Sell', 'triangledown', 'abovebar', '#E11D48FF', size='tiny')],
    drawing_groups=[DrawingGroup('rails', 'Liquidity rails'), DrawingGroup('zones', 'Swept zones'),
                    DrawingGroup('signals', 'Sweep labels'), DrawingGroup('targets', 'TP/SL levels')],
)

LABEL_SIZE = {'Tiny': 'tiny', 'Small': 'small', 'Normal': 'normal'}
DASH_SIZE = {'Tiny': 'tiny', 'Small': 'small', 'Normal': 'normal', 'Large': 'large'}
NA_COLOR = '#00000000'


@dataclass
class Pool:
    """A live liquidity pool: an unswept swing high (sell-side stops above it) or swing low (buy-side stops below)."""
    rail: object  # the dotted line drawn at the level (display only; None when rails are hidden)
    lvl: float  # the swing high / low price
    born_bar: int  # bar index of the swing candle itself (pivotLen bars before it was confirmed)
    is_high: bool


@dataclass
class PendingSweep:
    """A pool that price has just wicked through, waiting up to confirmWindow bars for a close back inside."""
    lvl: float  # the raided pool's level
    wick_extreme: float  # highest high (high pool) / lowest low (low pool) since the raid — the sweep wick
    break_bar: int  # bar index of the raid candle
    raid_range: float  # high - low of the raid candle (the min-wick test is a % of it)
    is_high: bool
    born: int = 0  # bar index of the raided pool's swing candle (ML input: pool age)


@dataclass
class SweptZone:
    """The box painted over a confirmed sweep (display only), deleted after zoneLifeBars bars."""
    bx: object
    born_bar: int


class LiquidityReaper(Script):
    meta = META

    collect = False  # research only: record every signal's ML inputs (research/reaper_ml/dataset.py sets it)

    # Per-bar state: highPools / lowPools (live liquidity), pendHigh / pendLow (raided pool waiting for a confirming
    # close), zones (swept-zone boxes), trade_dir / sl_level / extreme_level (the drawn position) and the dashboard
    # counters. Only the pools and pending sweeps affect signals.
    def required_warmup(self) -> int:
        # pools, the open trade and zones carry over from earlier bars; 2000 bars settles them on every timeframe
        return 2000

    def setup(self, ctx: Context) -> None:
        """Runs once before the first bar: choose the effective settings, then build every running calculation."""
        p = self.p
        self.warnings: list[str] = []  # shown next to the indicator name in the chart legend
        # Preset: replace the trading inputs with the tested ones for this chart timeframe BEFORE anything below is
        # built from them (pivot lengths, data requests). Untested timeframes keep the user's inputs + a warning.
        if p['tfPresetMode'] != 'Off':
            preset = TF_PRESETS[p['tfPresetMode']].get(ctx.timeframe.code)
            if preset is not None:
                self.p = p = {**p, **preset}
            else:
                self.warnings.append(f'Reaper: no tested settings for {ctx.timeframe.label}'
                                     + (' (1m lost money with every setting tested)' if ctx.timeframe.code == 'M1' else '')
                                     + '; your inputs are used.')
        # Running calculations used by the signal (each updated once per bar in on_bar)
        self.atr = ATR(p['tp_atr_period'])  # stop distance and the sweep-stop buffer
        self.vol_sma = SMA(p['volLen'])  # volume average for the volume filter / pulse
        self.ph = Pivot(p['pivotLen'], p['pivotLen'], True)  # swing highs -> high pools
        self.pl = Pivot(p['pivotLen'], p['pivotLen'], False)  # swing lows -> low pools
        # Liquidity state (SIGNAL)
        self.highPools: list[Pool] = []
        self.lowPools: list[Pool] = []
        self.zones: list[SweptZone] = []
        self.pendHigh: PendingSweep | None = None
        self.pendLow: PendingSweep | None = None
        # TP/SL engine
        self.trade_dir = 0
        self.sl_level = NAN
        self.extreme_level = NAN
        self.entry_bar = None
        self.entry_time = 0
        self.entry_price = NAN
        self.risk = NAN
        self.step_tps: list[float] = []  # shown targets before the furthest one: the SL moves to each once touched
        self.closed: list[tuple[int, int, float]] = []  # every closed trade: (signal candle time, side, R)
        self.objs: dict[str, object] = {}
        self.fills: dict[str, object] = {}
        # dashboard
        self.totalBuyRaids = 0
        self.totalSellRaids = 0
        self.lastRaidBar = None
        self.lastRaidDir = 0
        self.atr_val = NAN  # latest ATR / volume multiple, kept for the dashboard
        self.volMult = 1.0
        # Added filters (SIGNAL). Data requests are registered only when their filter is on, so the original setting
        # needs no 4H or 1-minute data.
        self.h4 = None  # (EMA50, EMA200) of the 4H closes, read for the last completed 4H candle
        self.dmi = DMI(14, 14)  # ADX(14) for adxFilter (same code as the SMC indicator's dashboard)
        self.adx = NAN
        self.prev_oc = (NAN, NAN)  # previous candle's open / close (engulfing filter)
        # 1-minute candles inside each chart candle for the order-flow delta (not on the 1m chart itself)
        # ML filter: FeatureState tracks the model inputs on every bar; the model is loaded once. It is only used with
        # the settings it was trained on (the preset named in the model file, 3R target). ``collect`` (research only)
        # records the input row of every signal BEFORE the ML decision — this is how the training data is built.
        self.fs = FeatureState()
        self.ml = Model.load() if p['mlFilter'] else None
        if p['mlFilter'] and self.ml is None:
            self.warnings.append('Reaper ML filter: no trained model (app/scripts/reaper_ml_model.json); signals unfiltered.')
        if self.ml is not None and (p['tfPresetMode'] != self.ml.spec.get('preset') or p['rr_tp3'] != 3.0
                                    or ctx.timeframe.code not in self.ml.spec.get('timeframes', ())):
            self.warnings.append('Reaper ML filter: the model was trained on the "%s" preset (3R) for %s; it is off with '
                                 'these settings.' % (self.ml.spec.get('preset'), ', '.join(self.ml.spec.get('timeframes', ()))))
            self.ml = None
        need_ml = self.ml is not None or self.collect
        self.signal_rows: list[tuple] = []  # research: (bar index, side, input row) of every signal
        self.last_score = {1: NAN, -1: NAN}
        self.ltf = ctx.request_lower_tf(60) if (p['deltaFilter'] or need_ml) and ctx.timeframe.seconds > 60 else None
        if p['trendFilter'] or need_ml:
            h4 = lambda b, n: ema(np.asarray(b.close, dtype=float), n)
            self.h4 = (ctx.request_security('H4', lambda b: h4(b, 50)), ctx.request_security('H4', lambda b: h4(b, 200)))

    # ------------------------------------------------------------------ helpers
    def _line(self, ctx, x1, y1, x2, y2, group, **kw):
        return ctx.line(x1=ctx.time_at(x1), y1=y1, x2=ctx.time_at(x2), y2=y2, group=group, **kw)

    def _set_x2(self, ctx, obj, x2):
        if obj is not None:
            obj.x2 = ctx.time_at(x2)

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: Context) -> None:
        """One candle: update indicators, find pools / raids / confirmations (the signal), apply the added filters,
        then draw zones and labels, move the TP/SL display and update the dashboard counters."""
        p = self.p
        i = ctx.bar_index
        h, l, o, c, v = ctx.high, ctx.low, ctx.open, ctx.close, ctx.volume
        buyColor, sellColor = p['buyColor'], p['sellColor']

        # ─── Indicators ─── ATR, ADX, volume multiple (this candle's volume / its average)
        atr_val = self.atr.update(h, l, c)
        self.adx = self.dmi.update(h, l, c)[2]
        self.fs.update(h, l, c, atr_val)  # ML: running indicator values for the model inputs
        volMA = self.vol_sma.update(v)
        volMult = v / volMA if finite(volMA) and volMA > 0 else 1.0
        volOK = not p['useVolFilter'] or volMult >= p['volMultReq']
        self.atr_val, self.volMult = atr_val, volMult

        # A pivot is reported pivotLen bars after the swing candle (it needs pivotLen lower highs / higher lows on its
        # right), so ph / pl are the swing prices of bar i - pivotLen, or NaN
        ph = self.ph.update(h)
        pl = self.pl.update(l)
        L = p['pivotLen']
        style = {'Solid': 'solid', 'Dashed': 'dashed', 'Dotted': 'dotted'}[p['railStyleStr']]

        # ─── Register new pools ─── (SIGNAL) a confirmed pivot becomes a live pool; the rail is display only
        for lvl, pools, col, is_high in ((ph, self.highPools, sellColor, True), (pl, self.lowPools, buyColor, False)):
            if not finite(lvl):
                continue
            r = None
            if p['showRails']:
                r = self._line(ctx, i - L, lvl, i, lvl, 'rails', extend='right',
                               color=new_color(col, p['railTransp']), style=style, width=1)
            pools.append(Pool(r, lvl, i - L, is_high))
            if len(pools) > p['maxPools']:
                old = pools.pop(0)
                ctx.delete(old.rail)

        # ─── Detect raids (wick through a pool) ─── (SIGNAL) the raided pool becomes the pending sweep of its side.
        # Pools are scanned newest -> oldest, so when one candle takes several pools the OLDEST becomes pending
        # (Pine behaviour); every raided pool is removed and its rail stops at this candle.
        raid_range = max(h - l, ctx.mintick)
        for k in range(len(self.highPools) - 1, -1, -1):
            pool = self.highPools[k]
            if h > pool.lvl:
                if pool.rail is not None:
                    pool.rail.extend = 'none'
                    pool.rail.x2 = ctx.time
                self.pendHigh = PendingSweep(pool.lvl, h, i, raid_range, True, pool.born_bar)
                del self.highPools[k]
        for k in range(len(self.lowPools) - 1, -1, -1):
            pool = self.lowPools[k]
            if l < pool.lvl:
                if pool.rail is not None:
                    pool.rail.extend = 'none'
                    pool.rail.x2 = ctx.time
                self.pendLow = PendingSweep(pool.lvl, l, i, raid_range, False, pool.born_bar)
                del self.lowPools[k]

        # Track wick extremes while a raid is pending (SIGNAL: the min-wick test; TRADE: the beyond-sweep stop)
        if self.pendHigh is not None:
            self.pendHigh.wick_extreme = max(self.pendHigh.wick_extreme, h)
        if self.pendLow is not None:
            self.pendLow.wick_extreme = min(self.pendLow.wick_extreme, l)

        # ─── Confirm sweeps ─── (SIGNAL) close back inside within the window + colour + volume + wick size.
        # elapsed = bars since the raid candle; the raid candle itself (elapsed 0) can never confirm. A pending sweep
        # that is not confirmed within confirmWindow bars is dropped. Sell = high pool swept, buy = low pool swept.
        sellSignal = buySignal = False
        sellSweep = buySweep = None  # the confirmed PendingSweep of each side (ML inputs)
        sweepLvlSell = sweepWickSell = sweepLvlBuy = sweepWickBuy = NAN
        cw = p['confirmWindow']
        ph_ = self.pendHigh
        if ph_ is not None:
            elapsed = i - ph_.break_bar
            wickOK = (ph_.wick_extreme - ph_.lvl) / ph_.raid_range * 100 >= p['minWickPct']
            if 1 <= elapsed <= cw:
                if c < ph_.lvl and c < o and volOK and wickOK:
                    sellSignal = True
                    sweepLvlSell, sweepWickSell = ph_.lvl, ph_.wick_extreme
                    sellSweep = ph_
                    self.pendHigh = None
            elif elapsed > cw:
                self.pendHigh = None
        pl_ = self.pendLow
        if pl_ is not None:
            elapsed = i - pl_.break_bar
            wickOK = (pl_.lvl - pl_.wick_extreme) / pl_.raid_range * 100 >= p['minWickPct']
            if 1 <= elapsed <= cw:
                if c > pl_.lvl and c > o and volOK and wickOK:
                    buySignal = True
                    sweepLvlBuy, sweepWickBuy = pl_.lvl, pl_.wick_extreme
                    buySweep = pl_
                    self.pendLow = None
            elif elapsed > cw:
                self.pendLow = None

        # ─── Optional filters ─── (SIGNAL, added) the sweep is consumed as usual; the signal is dropped when the
        # last completed 4H candle's EMA50/200 trend is against it, or ADX(14) is below adxMin
        trend = 0  # 4H trend: +1 up, -1 down, 0 unknown (also an ML input)
        if self.h4 is not None and (buySignal or sellSignal):
            e50, e200 = ctx.security(self.h4[0]), ctx.security(self.h4[1])
            trend = int(np.sign(e50 - e200)) if finite(e50) and finite(e200) else 0
        if p['trendFilter'] and (buySignal or sellSignal):
            buySignal = buySignal and trend > 0
            sellSignal = sellSignal and trend < 0
        if p['adxFilter'] and not (finite(self.adx) and self.adx >= p['adxMin']):
            buySignal = sellSignal = False
        po, pc = self.prev_oc  # updated on every candle, whether or not there is a signal
        self.prev_oc = (o, c)
        if p['engulfFilter'] and (buySignal or sellSignal):
            lo_b, hi_b = min(po, pc), max(po, pc)
            buySignal = buySignal and c >= hi_b and o <= lo_b
            sellSignal = sellSignal and c <= lo_b and o >= hi_b
        delta = self._delta(ctx) if (buySignal or sellSignal) else NAN
        if p['deltaFilter'] and (buySignal or sellSignal):
            buySignal = buySignal and finite(delta) and delta > 0
            sellSignal = sellSignal and finite(delta) and delta < 0

        # ─── ML filter ─── (SIGNAL, added) build the input row of each remaining signal; with collect on, record it
        # (training data); with a model loaded, skip the signal when its score is below the model's threshold
        if (self.ml is not None or self.collect) and (buySignal or sellSignal):
            for side, on, sw in ((1, buySignal, buySweep), (-1, sellSignal, sellSweep)):
                if not on:
                    continue
                x = self._ml_row(ctx, side, sw, volMult, atr_val, trend, delta)
                if self.collect and ctx.is_confirmed:
                    self.signal_rows.append((i, side, x))
                if self.ml is not None:
                    keep, self.last_score[side] = self.ml.keep(x)
                    if not keep:
                        buySignal = buySignal and side != 1
                        sellSignal = sellSignal and side != -1

        # ─── Paint swept zones ─── (DISPLAY)
        life = p['zoneLifeBars']
        if sellSignal and p['showZones'] and finite(sweepLvlSell) and sweepWickSell > sweepLvlSell:
            b = ctx.box(left=ctx.time_at(i - cw), top=sweepWickSell, right=ctx.time_at(i + life), bottom=sweepLvlSell,
                        border_color=new_color(sellColor, 55), border_width=1,
                        bg_color=new_color(sellColor, p['zoneTransp']), group='zones')
            self.zones.append(SweptZone(b, i))
        if buySignal and p['showZones'] and finite(sweepLvlBuy) and sweepWickBuy < sweepLvlBuy:
            b = ctx.box(left=ctx.time_at(i - cw), top=sweepLvlBuy, right=ctx.time_at(i + life), bottom=sweepWickBuy,
                        border_color=new_color(buyColor, 55), border_width=1,
                        bg_color=new_color(buyColor, p['zoneTransp']), group='zones')
            self.zones.append(SweptZone(b, i))

        # Expire old zones
        for k in range(len(self.zones) - 1, -1, -1):
            z = self.zones[k]
            if i - z.born_bar > life:
                ctx.delete(z.bx)
                del self.zones[k]
            else:
                # the right edge is bar born+life; pin it to that bar's real time once it exists (weekend gaps)
                z.bx.right = ctx.time_at(z.born_bar + life)

        # ─── Signal labels ─── (DISPLAY) RAID BUY / SELL with the relative volume of the confirming bar
        size = LABEL_SIZE[p['labelSizeStr']]
        mult = fmt_pattern(volMult, '#.#')
        if buySignal and p['showSignalLabels']:
            ctx.label(x=ctx.time, y=l, text=f'RAID BUY · {mult}x' + self._score_text(1), style='label_up', color=new_color(buyColor, 8),
                      text_color=p['labelTextColor'], size=size, group='signals')
        if sellSignal and p['showSignalLabels']:
            ctx.label(x=ctx.time, y=h, text=f'RAID SELL · {mult}x' + self._score_text(-1), style='label_down', color=new_color(sellColor, 8),
                      text_color=p['labelTextColor'], size=size, group='signals')
        if p['showTriangles'] and buySignal:
            ctx.shape('raid_buy', color=p['buyColor'])
        if p['showTriangles'] and sellSignal:
            ctx.shape('raid_sell', color=p['sellColor'])

        # ─── Trade plan display ─── (TRADE PLAN) entry / SL / TPs of the newest signal and its SL / TP tracking
        self._targets(ctx, buySignal, sellSignal, atr_val, sweepWickBuy if buySignal else sweepWickSell)

        # ─── Dashboard counters ─── (DISPLAY)
        if buySignal:
            self.totalBuyRaids += 1
            self.lastRaidBar, self.lastRaidDir = i, 1
        if sellSignal:
            self.totalSellRaids += 1
            self.lastRaidBar, self.lastRaidDir = i, -1

    def _risk(self, entry: float, d: int, atr_val: float, wick: float) -> float:
        """Stop distance of a new trade: ATR x mult (or % of price), or just beyond the swept wick when enabled."""
        p = self.p
        risk = atr_val * p['sl_atr_mult'] if p['use_atr_sl'] else entry * (p['sl_percent'] / 100)
        if p['sl_beyond_sweep'] and finite(wick) and finite(atr_val):
            beyond = wick - d * p['sl_sweep_buffer'] * atr_val
            if (entry - beyond) * d > 0:  # a stop on the wrong side of the entry keeps the ATR / % stop
                risk = abs(entry - beyond)
        return risk

    def _ml_row(self, ctx: Context, side: int, sw: PendingSweep, vol_mult: float, atr_val: float, trend: int,
                delta: float) -> list[float]:
        """ML input row (reaper_ml.FEATURES) of a signal confirmed on this candle — known at its close."""
        c = ctx.close
        # nearest live opposite pool = the next liquidity target (pools raided on this candle are already removed)
        opp = [pl.lvl for pl in self.highPools if pl.lvl > c] if side == 1 else [pl.lvl for pl in self.lowPools if pl.lvl < c]
        room = (min(opp) - c if side == 1 else c - max(opp)) if opp else NAN
        return signal_features(self.fs, side=side, o=ctx.open, h=ctx.high, l=ctx.low, c=c, atr=atr_val, level=sw.lvl,
                               extreme=sw.wick_extreme, raid_range=sw.raid_range,
                               bars_since_raid=ctx.bar_index - sw.break_bar, vol_mult=vol_mult,
                               pool_age=sw.break_bar - sw.born, room=room,
                               risk=self._risk(c, side, atr_val, sw.wick_extreme), trend_h4=trend, delta=delta,
                               time=ctx.time, tf_seconds=ctx.timeframe.seconds)

    def _score_text(self, side: int) -> str:
        """Label suffix with the model's setup score (a ranking score, not a calibrated win probability)."""
        return f' · ML {self.last_score[side]:.2f}' if self.ml is not None and finite(self.last_score[side]) else ''

    def _delta(self, ctx: Context) -> float:
        """(volume of up 1-minute candles - volume of down ones) / total volume, inside the current candle."""
        if self.ltf is None:
            return NAN
        b = ctx.intrabars(self.ltf).bars
        if len(b) == 0:
            return NAN
        tot = float(np.sum(b.volume))
        signed = float(np.sum(np.where(b.close > b.open, b.volume, np.where(b.close < b.open, -b.volume, 0.0))))
        return signed / tot if tot > 0 else NAN

    # ------------------------------------------------------------------ TP/SL engine (TRADE PLAN display)
    def _targets(self, ctx: Context, buySignal: bool, sellSignal: bool, atr_val: float, wick: float = NAN) -> None:
        """Follow the position of the chart, one trade at a time (like the backtest), and draw it.

        1. An open position is extended to this candle and closed when the candle touches its SL or its furthest
           shown TP (only candles after the entry candle can close it). If one candle touches both, the SL counts
           (we cannot know which came first). A candle that touches a nearer shown TP moves the SL to that TP for
           the next candles (TP1 locks +1R, TP2 +2R). The closed trade is added to ``self.closed`` (the replay record).
        2. Only when flat, a signal opens a new position: entry = this candle's close, risk = ATR x sl_atr_mult (or
           sl_percent of the price; or the distance to the swept wick + sl_sweep_buffer x ATR when sl_beyond_sweep
           is on and that stop is on the correct side), SL = entry -/+ risk, TPn = entry +/- risk x rr_tpN.
           A signal on the candle that closed the previous trade can open the next one (as in the backtest).
           Signals while a trade is open keep their RAID label but open nothing.
        This is a chart display: it uses candle highs / lows without spread and enters at the signal close, so a
        result can differ from the research backtest, which enters at the next open on bid/ask with 1-minute order.
        """
        p = self.p
        i = ctx.bar_index
        buy, sell = p['buyColor'], p['sellColor']
        buyLight, sellLight = new_color(buy, 40), new_color(sell, 40)
        buyFill, sellFill = new_color(buy, 76), new_color(sell, 76)
        objs = self.objs
        prec = ctx.precision

        # 1. Position update + close on SL/TP hit
        if self.trade_dir != 0:
            for k, obj in objs.items():
                if k.endswith('_label'):
                    obj.x = ctx.time
                else:
                    self._set_x2(ctx, obj, i + 1)
            for f in self.fills.values():
                f.right = ctx.time_at(i + 1)
            d = self.trade_dir
            sl_hit = (ctx.low <= self.sl_level) if d == 1 else (ctx.high >= self.sl_level)
            tp_hit = finite(self.extreme_level) and ((ctx.high >= self.extreme_level) if d == 1
                                                     else (ctx.low <= self.extreme_level))
            if i > self.entry_bar and (sl_hit or tp_hit):
                for k, obj in objs.items():
                    if k.endswith('_line'):
                        obj.x2 = ctx.time
                for f in self.fills.values():
                    f.right = ctx.time
                # R of the trade: the SL in R (-1R, or the TP it was moved to); a win is the furthest shown TP
                r = (self.sl_level if sl_hit else self.extreme_level) - self.entry_price
                self.closed.append((self.entry_time, d, r * d / self.risk))
                self.trade_dir = 0
            elif i > self.entry_bar:
                # the SL steps up to each nearer TP this candle touched (it applies from the next candle)
                for tp in self.step_tps:
                    if ((ctx.high >= tp) if d == 1 else (ctx.low <= tp)) and (tp - self.sl_level) * d > 0:
                        self.sl_level = tp

        # 2. A new position, only when flat (and with a usable stop: ATR is unknown on the first candles)
        risk = self._risk(ctx.close, 1 if buySignal else -1, atr_val, wick) if (buySignal or sellSignal) else NAN
        if (buySignal or sellSignal) and self.trade_dir == 0 and finite(risk) and risk > 0:
            for obj in list(objs.values()) + list(self.fills.values()):
                ctx.delete(obj)
            objs.clear()
            self.fills.clear()
            self.trade_dir = d = 1 if buySignal else -1
            self.entry_bar, self.entry_time = i, ctx.time
            self.entry_price = entry = ctx.close
            self.risk = risk
            self.sl_level = entry - d * risk
            tps = [entry + d * risk * p[k] for k in ('rr_tp1', 'rr_tp2', 'rr_tp3')]
            show = [p['show_tp1_level'], p['show_tp2_level'], p['show_tp3_level']]
            self.extreme_level = tps[2] if show[2] else tps[1] if show[1] else tps[0] if show[0] else NAN
            self.step_tps = [tp for tp, on in zip(tps, show) if on and tp != self.extreme_level]
            if not p['show_targets']:
                return  # the position is still followed (dashboard, replay record), just not drawn

            def line(y, **kw):
                return self._line(ctx, i, y, i + 1, y, 'targets', extend='none', **kw)

            def label(y, text, color, text_color):
                return ctx.label(x=ctx.time, y=y, text=text, style='label_left', color=color, text_color=text_color,
                                 size='small', group='targets')

            main = buy if d == 1 else sell
            objs['entry_line'] = line(entry, color=main, width=2)
            objs['entry_label'] = label(entry, ('BUY ' if d == 1 else 'SELL ') + fmt_price(entry, prec), main,
                                        rgb(6, 22, 32) if d == 1 else WHITE)
            if p['show_sl_level']:
                objs['sl_line'] = line(self.sl_level, color=sellLight, width=1, style='dashed')
                objs['sl_label'] = label(self.sl_level, 'SL ' + fmt_price(self.sl_level, prec), sellLight, WHITE)
            for n, (tp, on) in enumerate(zip(tps, show), 1):
                if on:
                    objs[f'tp{n}_line'] = line(tp, color=buyLight, width=1, style='dotted' if n == 3 else 'solid')
                    objs[f'tp{n}_label'] = label(tp, f'TP{n} ' + fmt_price(tp, prec), buyLight, WHITE)
            # linefill between two lines with the same x range = a box spanning them (kept in step with the lines)
            if p['show_sl_level']:
                self.fills['risk'] = self._fill(ctx, entry, self.sl_level, sellFill)
            shown = [tp for tp, on in zip(tps, show) if on]
            far = max([entry, *shown]) if d == 1 else min([entry, *shown])
            if far != entry:
                objs['extreme_line'] = line(far, color=NA_COLOR)
                self.fills['reward'] = self._fill(ctx, entry, far, buyFill)

    def _fill(self, ctx, y1, y2, color):
        return ctx.box(left=ctx.time, right=ctx.time_at(ctx.bar_index + 1), top=max(y1, y2), bottom=min(y1, y2),
                       border_color=NA_COLOR, border_width=0, bg_color=color, group='targets')

    # ------------------------------------------------------------------ dashboard (DISPLAY, last bar only)
    def on_last_bar(self, ctx: Context) -> None:
        """The Reaper dashboard (last bar only; display): pools above / below and the nearest ones in ATR, volume
        pulse, raid counts, last raid, pending sweep and the drawn position. It reads the state; it changes nothing."""
        self.table = None
        p = self.p
        if not p['showDash']:
            return
        buy, sell = p['buyColor'], p['sellColor']
        size = DASH_SIZE[p['dashSize']]
        close = ctx.close
        # replay: three more rows with the record of the trades closed since the replay start bar
        replay = ctx.replay_start is not None
        t = Table('reaper', TABLE_POSITIONS[p['dashPos']], 3, 14 if replay else 11, bg_color=new_color('#0B1220FF', 8),
                  border_width=1, border_color=new_color('#000000FF', 100), frame_width=2,
                  frame_color=new_color(buy, 35))
        rowBg, rowBgAlt = new_color('#0E1830FF', 22), new_color('#0A1326FF', 22)
        lbl = new_color(WHITE, 18)
        purple = '#7A5AA8FF'

        above = [pl.lvl for pl in self.highPools if pl.lvl > close]
        below = [pl.lvl for pl in self.lowPools if pl.lvl < close]
        nearestAbove = min(above) if above else NAN
        nearestBelow = max(below) if below else NAN
        atr = self.atr_val

        def gauge(n):
            return ''.join('▮' if k <= min(n, 8) else '▯' for k in range(1, 9))

        def row(r, label, v1, c1, v2='', c2=None, bg=None, bg2=None):
            bg = bg or (rowBg if r % 2 == 0 else rowBgAlt)
            t.cell(0, r, label, text_color=lbl, bgcolor=bg, text_size=size)
            t.cell(1, r, v1, text_color=c1, bgcolor=bg, text_size=size)
            t.cell(2, r, v2, text_color=c2 or '#000000FF', bgcolor=bg2 or bg, text_size=size)

        t.cell(0, 0, '❄ LIQUIDITY REAPER', text_color=WHITE, bgcolor=new_color(buy, 35), text_size=size)
        t.cell(1, 0, '', bgcolor=new_color(purple, 40), text_size=size)
        t.cell(2, 0, f'{ctx.ticker} · {ctx.timeframe.pine}', text_color=WHITE,
               bgcolor=new_color(sell, 40), text_size=size)
        t.cell(0, 1, '', bgcolor=new_color(buy, 60), text_size='tiny')
        t.cell(1, 1, '', bgcolor=new_color(purple, 58), text_size='tiny')
        t.cell(2, 1, '', bgcolor=new_color(sell, 60), text_size='tiny')
        n_hi, n_lo = len(self.highPools), len(self.lowPools)
        row(2, 'Pools Above', f'{n_hi} / {p["maxPools"]}', new_color(sell, 10), gauge(n_hi), new_color(sell, 25))
        row(3, 'Pools Below', f'{n_lo} / {p["maxPools"]}', new_color(buy, 10), gauge(n_lo), new_color(buy, 25))
        distAbove = (nearestAbove - close) / atr if finite(nearestAbove) and finite(atr) and atr > 0 else NAN
        distBelow = (close - nearestBelow) / atr if finite(nearestBelow) and finite(atr) and atr > 0 else NAN
        prec = ctx.precision
        row(4, 'Nearest Above', fmt_price(nearestAbove, prec) if finite(nearestAbove) else '—', new_color(sell, 10),
            fmt_pattern(distAbove, '0.0') + 'x ATR away' if finite(distAbove) else '', new_color(WHITE, 35))
        row(5, 'Nearest Below', fmt_price(nearestBelow, prec) if finite(nearestBelow) else '—', new_color(buy, 10),
            fmt_pattern(distBelow, '0.0') + 'x ATR away' if finite(distBelow) else '', new_color(WHITE, 35))
        vm = self.volMult
        exp = vm >= p['volMultReq']
        row(6, 'Volume Pulse', fmt_pattern(vm, '#.##') + 'x', buy if exp else new_color(WHITE, 35),
            '✓ EXPANSION' if exp else '… quiet', WHITE if exp else new_color(WHITE, 45),
            bg2=new_color(buy, 55) if exp else rowBg)
        row(7, 'Raids (B / S)', f'{self.totalBuyRaids} / {self.totalSellRaids}', new_color(WHITE, 10))
        last = '—' if self.lastRaidBar is None else (
            ('RAID BUY' if self.lastRaidDir == 1 else 'RAID SELL') + f' · {ctx.bar_index - self.lastRaidBar} bars ago')
        row(8, 'Last Raid', last,
            buy if self.lastRaidDir == 1 else sell if self.lastRaidDir == -1 else new_color(WHITE, 40))
        pend = 'high-side raid live' if self.pendHigh else 'low-side raid live' if self.pendLow else 'none'
        row(9, 'Pending Sweep', pend, sell if self.pendHigh else buy if self.pendLow else new_color(WHITE, 45),
            f'{p["confirmWindow"]}-bar window', new_color(WHITE, 40))
        d = self.trade_dir
        pos = '● LONG ACTIVE' if d == 1 else '● SHORT ACTIVE' if d == -1 else '○ FLAT'
        posBg = new_color(buy, 40) if d == 1 else new_color(sell, 40) if d == -1 else rowBg
        t.cell(0, 10, 'Position', text_color=lbl, bgcolor=rowBg, text_size=size)
        t.cell(1, 10, pos, text_color=WHITE, bgcolor=posBg, text_size=size)
        locked = (self.sl_level - self.entry_price) * d / self.risk if d != 0 and finite(self.sl_level) else -1.0
        t.cell(2, 10, ('SL ' + fmt_price(self.sl_level, prec) + (f' · {locked:+.1f}R locked' if locked > 0 else ''))
               if d != 0 and finite(self.sl_level) else '',
               text_color=new_color(buy, 10) if locked > 0 else new_color(sell, 25), bgcolor=rowBg, text_size=size)
        if replay:
            # trades whose signal candle came after the replay start bar (the start bar was already on screen)
            rs = [r for when, _, r in self.closed if when > ctx.replay_start]
            wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
            net = sum(rs)
            grey = new_color(WHITE, 40)
            row(11, 'Replay Wins', str(len(wins)), new_color(buy, 10), f'{sum(wins):+.1f}R', new_color(buy, 10))
            row(12, 'Replay Losses', str(len(losses)), new_color(sell, 10), f'{sum(losses):+.1f}R', new_color(sell, 10))
            row(13, 'Replay Net R', f'{net:+.1f}R', buy if net > 0 else sell if net < 0 else grey,
                f'{len(wins) / len(rs):.0%} win · {len(rs)} trades' if rs else 'no closed trade yet', grey)
        self.table = t

    def extra_output(self) -> dict:
        t = getattr(self, 'table', None)
        js = t.to_json() if t else None
        return dict(tables=[js] if js else [], warnings=list(getattr(self, 'warnings', [])))
