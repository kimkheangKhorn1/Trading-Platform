# This work is licensed under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
# https://creativecommons.org/licenses/by-nc-sa/4.0/
# © MarkitTick
# Python port of "Triple Confluence Navigator [MarkitTick]" (Pine v6), line for line.
#
# =====================================================================================================================
#  OVERVIEW - WHAT THIS INDICATOR DOES
# =====================================================================================================================
#
#  The indicator prints a BUY or SELL label only when THREE independent "engines" all agree on a direction within a
#  short window of bars (``confluenceWindow``, default 5). Each engine looks at the market in a different way:
#
#    Engine 1 - "Cardwell" RSI momentum   (signal b1 / s1)
#        A classic 14-period RSI is smoothed by a FAST line (len 9) and a SLOW line (len 45). When the fast line
#        crosses above the slow line, RSI momentum is turning up (b1); crossing below = turning down (s1).
#        Optional filters: HTF bias (higher-timeframe fast > slow) and ADX (trend strength >= 20).
#
#    Engine 2 - Adaptive RSI + Supertrend (signal b2 / s2)
#        An "adaptive RSI" (ARSI, 0..100, 50 = neutral) gets a signal line (RMA / Kalman / LLAMA) and a supertrend
#        that runs on the ARSI itself (not on price). The signal line and the supertrend are averaged into one
#        "merged" line. ARSI crossing above the merged line = b2, crossing below = s2.
#        Optional filter: HTF ARSI >= 50 for bulls / < 50 for bears.
#
#    Engine 3 - Market structure break    (signal b3 / s3)
#        Swing highs / lows are found with a symmetric pivot (``swingLen`` bars each side). A confirmed close
#        above the latest swing high = bullish break of structure (b3); a close below the latest swing low = bearish
#        break (s3). Every swing can only be "broken" once. Optional filter: HTF RSI > 50 / < 50.
#
#  Confluence: for every one of the six raw signals we count "bars since it last fired". A LONG fires on the first
#  confirmed bar where b1, b2 and b3 have ALL fired within the last ``confluenceWindow`` bars (in any order).
#  SHORT is the mirror with s1, s2, s3. Only the *rising edge* of that condition produces a label, so one cluster of
#  agreement gives exactly one BUY / SELL.
#
#  When a signal fires the indicator also draws a trade plan:
#      Entry = previous bar's close
#      SL    = beyond the last opposite swing (+ an ATR buffer), but never further than ``atrMultSl`` x ATR
#      TP1/2/3 = Entry +/- risk x rr1 / rr2 / rr3   (default 1R, 2R, 3R)
#  plus a dashboard table showing the state of each engine, RSI, ADX, HTF bias and the running R of the last trade.
#
#  Pipeline (one call to ``on_bar`` per bar):
#
#      close ──► RSI ──► fast/slow smoothing ──► cross? ──► HTF bias? ──► ADX ok? ──────────► b1 / s1 ─┐
#      close ──► ARSI ──► signal line + ARSI-supertrend ──► merged ──► ARSI x merged ──► HTF? ► b2 / s2 ─┼─► bars-since
#      high/low ──► pivots ──► last swing high/low ──► close breaks swing ──► HTF? ───────────► b3 / s3 ─┘   counters
#                                                                                                            │
#                                                     all 3 within window (rising edge, confirmed bar) ◄─────┘
#                                                                         │
#                          a trade is running? ──yes──► signal skipped (one trade at a time)
#                                                   │ no
#                                         BUY/SELL label + Entry/SL/TP lines & zones + alert event
#                                                   │
#                     each next bar: TP hit → +1R tag ... TP3 → trade ends (win) / SL → -1R, trade ends (loss)
#
#  File layout:
#      1. Inputs (settings panel), help, panes, tuning ranges .. INPUTS, HELP, PLOTS, META
#      2. Small helpers (Pine ``nz``, history buffer) .......... nz, Hist
#      3. Streaming indicator building blocks ................. RSI, ARSI, Kalman, Llama, LinReg, DMI
#      4. The script itself ................................... TripleConfluence
#           setup()        - create all state + optional HTF requests
#           on_bar()       - the three engines, confluence, signals, labels
#           _open_trade()  - Entry / SL / TP geometry and drawings
#           _update_trade() / _close_trade() - walk the trade bar by bar, score it, record it in self.closed
#           on_last_bar()  - dashboard panel data
#           extra_output() - hands the panel to the frontend
#      The tuner (app/tuning.py) runs this script with drawing = False and reads self.closed.
#
#  All building blocks are *streaming*: each has an ``update(x)`` that is fed one bar at a time and returns the
#  value for that bar - exactly like a Pine series evaluated bar by bar. NAN plays the role of Pine's ``na``.
# =====================================================================================================================
from __future__ import annotations

import dataclasses
from collections import deque

import numpy as np

from ..engine.inputs import DrawingGroup, InputSpec, PlotSpec, ScriptMeta
from ..engine.pine import new_color
from ..engine.script import Context, Script
from ..engine.ta import ATR, RMA, Highest, Lowest
from ..trade_score import OBJECTIVES, summarize
from .pinelib import NAN, WHITE, finite, fmt_price

# ---------------------------------------------------------------------------------------------------------------------
#  SETTINGS PANEL
#  Group names (shown as section headers in the indicator's settings dialog).
# ---------------------------------------------------------------------------------------------------------------------
G_RSI, G_MA, G_ARSI, G_ST, G_BOS = '⚙️ Cardwell RSI', '⚙️ Cardwell MA', '🌊 Adaptive RSI', '🌊 Supertrend', '🧱 Structure'
G_HTF, G_CHOP, G_TRADE, G_DASH, G_WH, G_COL = ('🕯️ HTF Filter', '🕯️ Choppiness', '📐 Trade Setup', '📊 Dash',
                                               '🔔 Alerts', '🌈 Colors')
# Maps the Pine timeframe strings used by the "HTF TF" input to this platform's timeframe codes.
PINE_TF = {'1': 'M1', '5': 'M5', '15': 'M15', '30': 'M30', '60': 'H1', '240': 'H4', 'D': 'D'}

# Every user-tunable setting. The three numbers in the chart legend ("14 9 45") are the first three inputs:
# RSI length, fast smoothing length and slow smoothing length of Engine 1.
INPUTS = [
    # --- Engine 1: Cardwell RSI -----------------------------------------------------------------------------------
    InputSpec('rsiLen', 'int', 'RSI Len', 14, minval=2, group=G_RSI),            # base RSI period
    InputSpec('rmaFastLen', 'int', 'Fast Len', 9, minval=2, group=G_MA),         # fast smoothing of the RSI
    InputSpec('rmaSlowLen', 'int', 'Slow Len', 45, minval=2, group=G_MA),        # slow smoothing of the RSI
    # How the RSI is smoothed into the fast/slow lines. NOTE: the 'LLAMA' option of THIS group actually uses a
    # linear-regression line (ta.linreg) - that is how the original Pine script is written, and the port keeps it.
    InputSpec('maType', 'string', 'Filter Type', 'Kalman', options=('RMA', 'Kalman', 'LLAMA'), group=G_MA),
    # --- Engine 2: Adaptive RSI -----------------------------------------------------------------------------------
    InputSpec('arsiLen', 'int', 'ARSI Len', 14, minval=2, group=G_ARSI),         # look-back of the adaptive RSI
    InputSpec('arsiSigLen', 'int', 'Signal Len', 14, minval=1, group=G_ARSI),    # length of the ARSI signal line
    # Which smoother builds the ARSI signal line (here 'LLAMA' really is the adaptive LLAMA filter below).
    InputSpec('arsiFiltType', 'string', 'Filter Type', 'Kalman Filter', options=('RMA', 'Kalman Filter', 'LLAMA'),
              group=G_ARSI),
    InputSpec('kalmanProcN', 'float', 'Kalman Proc N', 0.01, minval=0.0001, step=0.001, group=G_ARSI),  # process noise
    InputSpec('kalmanMeasN', 'float', 'Kalman Meas N', 0.10, minval=0.0001, step=0.01, group=G_ARSI),   # meas. noise
    InputSpec('llamaAlphaMin', 'float', 'LLAMA Min α', 0.05, minval=0.01, maxval=0.99, step=0.01, group=G_ARSI),
    InputSpec('llamaAlphaMax', 'float', 'LLAMA Max α', 0.50, minval=0.01, maxval=0.99, step=0.01, group=G_ARSI),
    # --- Engine 2: supertrend that runs on the ARSI ---------------------------------------------------------------
    InputSpec('stAtrLen', 'int', 'ST ATR Len', 14, minval=1, group=G_ST),        # smoothing of the ARSI's "ATR"
    InputSpec('stFactor', 'float', 'ST Factor', 2.0, minval=0.1, step=0.1, group=G_ST),  # band width multiplier
    # --- Engine 3: market structure -------------------------------------------------------------------------------
    InputSpec('swingLen', 'int', 'Swing Len', 5, minval=3, maxval=50, group=G_BOS),  # bars on each side of a pivot
    # --- Trade plan -----------------------------------------------------------------------------------------------
    # Entry of a new trade. 'Signal close' is a price you can actually get when the signal appears; the Pine original
    # uses the close BEFORE the signal bar, which makes results look much better than they can be traded.
    InputSpec('entryMode', 'string', 'Entry Price', 'Signal close', options=('Signal close', 'Previous close (original)'),
              group=G_TRADE),
    InputSpec('atrLen', 'int', 'ATR Len', 14, minval=1, group=G_TRADE),          # price ATR used for SL sizing
    InputSpec('atrMultSl', 'float', 'SL ATR Mult', 1.5, minval=0.1, step=0.1, group=G_TRADE),  # max SL distance (xATR)
    InputSpec('slStructBuf', 'float', 'SL Struct Buffer (ATR)', 0.25, minval=0.0, step=0.05, group=G_TRADE),  # SL pad
    InputSpec('rr1', 'float', 'TP1 R:R', 1.0, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('rr2', 'float', 'TP2 R:R', 2.0, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('rr3', 'float', 'TP3 R:R', 3.0, minval=0.1, step=0.1, group=G_TRADE),
    # Max number of bars allowed between the three engines' signals for them to count as "in confluence".
    InputSpec('confluenceWindow', 'int', 'Confluence Window', 5, minval=1, maxval=50, group=G_TRADE),
    # --- Optional filters -----------------------------------------------------------------------------------------
    InputSpec('useHtfFilter', 'bool', 'Enable HTF Filter', False, group=G_HTF),  # gate all 3 engines by a higher TF
    InputSpec('htfTimeframe', 'timeframe', 'HTF TF', '240', group=G_HTF),        # '240' = H4
    InputSpec('useChopFilter', 'bool', 'Enable ADX Filter', False, group=G_CHOP),  # gate Engine 1 by trend strength
    InputSpec('adxLen', 'int', 'ADX Len', 14, minval=1, group=G_CHOP),
    InputSpec('adxMinLevel', 'float', 'Min ADX', 20.0, minval=1.0, step=1.0, group=G_CHOP),
    # --- Dashboard ------------------------------------------------------------------------------------------------
    InputSpec('showDash', 'bool', 'Show Dashboard', True, group=G_DASH),
    InputSpec('dashPos', 'string', 'Position', 'Top Right', options=('Top Right', 'Top Left', 'Bottom Right',
                                                                     'Bottom Left'), group=G_DASH),
    # --- Webhook action words (kept so settings match the Pine original; this port does not send webhooks) -------
    InputSpec('i_actionLong', 'string', '↑ Long Action', 'long', group=G_WH),
    InputSpec('i_actionShort', 'string', '↓ Short Action', 'short', group=G_WH),
    InputSpec('i_actionCloseLong', 'string', '✕ Close Long Action', 'closelong', group=G_WH),
    InputSpec('i_actionCloseShort', 'string', '✕ Close Short Action', 'closeshort', group=G_WH),
    # --- Colours (purely visual, no effect on signals) ------------------------------------------------------------
    InputSpec('colEntry', 'color', 'Entry', '#1E3A5CFF', group=G_COL),
    InputSpec('colSl', 'color', 'SL', '#7A1F1FFF', group=G_COL),
    InputSpec('colTp1', 'color', 'TP1', '#0E5C4AFF', group=G_COL),
    InputSpec('colTp2', 'color', 'TP2', '#0E6E52FF', group=G_COL),
    InputSpec('colTp3', 'color', 'TP3', '#12A382FF', group=G_COL),
    InputSpec('colBuyLbl', 'color', 'Buy Label', '#26A69AFF', group=G_COL),
    InputSpec('colSellLbl', 'color', 'Sell Label', '#EF5350FF', group=G_COL),
    InputSpec('zoneRiskColor', 'color', 'Risk Zone Fill', new_color('#7A1F1FFF', 55), group=G_COL),
    InputSpec('zoneRewardColor', 'color', 'Reward Zone Fill', new_color('#0E5C4AFF', 70), group=G_COL),
    InputSpec('dashBgColor', 'color', 'BG', new_color('#0A0F1AFF', 10), group=G_COL),
    InputSpec('dashHdrColor', 'color', 'Header BG', new_color('#14B5CBFF', 20), group=G_COL),
    InputSpec('dashLabelColor', 'color', 'Label Text', new_color('#FFFFFFFF', 30), group=G_COL),
    InputSpec('dashValueColor', 'color', 'Value Text', new_color('#FFFFFFFF', 0), group=G_COL),
]

# Settings-dialog help card (ⓘ) for every input: what it is, then how the chart responds when it is raised / lowered.
HELP = {
    'rsiLen': ('Length of the base RSI (Engine 1). The RSI line in the "Cardwell RSI" pane.\n'
               'Higher → smoother, slower RSI that rarely reaches 0 / 100 → fewer fast/slow crosses.\n'
               'Lower → faster, jumpier RSI → more crosses, more noise.',
               '14 → 28: the RSI pane looks calmer and Engine 1 votes roughly half as often.'),
    'rmaFastLen': ('Fast line length (Engine 1).\n'
                   'Kalman (default): this line itself does not use it; it only sets how slow the Slow line is '
                   '(Slow Len ÷ Fast Len). Higher → smaller ratio → slow line follows the RSI more closely.\n'
                   'RMA / LLAMA: higher → smoother, later fast line → fewer crosses; '
                   'lower → hugs the RSI → more crosses.',
                   'RMA, 9 → 5: the teal line turns sooner and Cardwell votes appear more often.'),
    'rmaSlowLen': ('Slow line length (Engine 1) - the RSI\'s baseline.\n'
                   'Higher → flatter, slower orange line → fewer but bigger fast/slow crosses.\n'
                   'Lower → orange line stays close to the teal one → many small crosses.\n'
                   'Kalman: the slow line\'s lag scales with Slow Len ÷ Fast Len (45 ÷ 9 = 5).',
                   '45 → 90 (Kalman): ratio 10, the orange line barely moves and votes become rare.'),
    'maType': ('How the RSI is smoothed into the fast / slow lines.\n'
               'Kalman: smooth and quick (default). RMA: classic, most lag. '
               'LLAMA: here a straight-line (linear regression) fit - most responsive, overshoots at turns.',
               'Switch Kalman → RMA: crosses arrive a few bars later.'),
    'arsiLen': ('Look-back of the Adaptive RSI (Engine 2): a close at a new N-bar high/low pushes ARSI by the '
                'whole N-bar '
                'range.\nHigher → longer window, fewer "new highs/lows", smoother & slower ARSI.\n'
                'Lower → even small breakouts count → fast, noisy ARSI.',
                '14 → 7: the blue line reacts to every small breakout and Engine 2 votes more often.'),
    'arsiSigLen': ('Length of the ARSI signal line (RMA and LLAMA filters; Kalman ignores it).\n'
                   'Higher → smoother, slower purple line → merged line slower → fewer Engine 2 votes.\n'
                   'Lower → signal line hugs ARSI → more votes.',
                   'RMA, 14 → 28: the purple line flattens.'),
    'arsiFiltType': ('Smoother used for the ARSI signal line.\n'
                     'Kalman Filter: tuned by Proc N / Meas N (default). RMA: classic lag. '
                     'LLAMA: adaptive - fast in trends, nearly flat in chop.',
                     'Kalman → LLAMA: the purple line freezes in ranges and jumps in trends.'),
    'kalmanProcN': ('Kalman process noise for the ARSI signal line: how much the true value may change each bar.\n'
                    'Higher → follows ARSI faster (less smoothing). Lower → smoother, slower.',
                    '0.01 → 0.1: the purple line almost overlays the blue ARSI.'),
    'kalmanMeasN': ('Kalman measurement noise: how little each new ARSI reading is trusted.\n'
                    'Higher → smoother, slower signal line. Lower → follows ARSI faster.',
                    '0.10 → 1.0: the purple line becomes a slow, smooth curve.'),
    'llamaAlphaMin': ('LLAMA (ARSI signal) speed in choppy markets.\n'
                      'Higher → still moves in chop. Lower → nearly flat in chop.',
                      '0.05 → 0.2: the line keeps drifting even when ARSI goes sideways.'),
    'llamaAlphaMax': ('LLAMA (ARSI signal) speed in clean trends.\n'
                      'Higher → snaps to ARSI in trends. Lower → lags even in trends.',
                      '0.5 → 0.9: in a trend the line sits almost on the ARSI.'),
    'stAtrLen': ('Smoothing of the ARSI\'s "ATR" (average bar-to-bar ARSI change) that sizes the supertrend bands.\n'
                 'Higher → steadier band width. Lower → band width jumps with the latest ARSI moves.',
                 '14 → 5: after a sharp ARSI move the supertrend steps far away for a few bars.'),
    'stFactor': ('Supertrend band width on the ARSI (× its ATR).\n'
                 'Higher → supertrend sits further from ARSI → flips rarely → merged line slower → '
                 'fewer Engine 2 votes.\n'
                 'Lower → tight supertrend, flips often → more votes, more whipsaws.',
                 '2.0 → 4.0: the green/red line flips about half as often.'),
    'swingLen': ('Bars needed on EACH side of a swing high/low (Engine 3). '
                 'A swing is only known Swing Len bars later.\n'
                 'Higher → only major swings, found later, fewer and more meaningful BOS.\n'
                 'Lower → minor swings, found sooner → many BOS (more Engine 3 votes, more noise).',
                 '5 → 10: dotted swing lines sit at bigger turning points and BOS labels become rare.'),
    'entryMode': ('Price a new trade is measured from.\n'
                  'Signal close: the close of the signal candle - the price you can actually get when the signal '
                  'appears.\nPrevious close (original): the close BEFORE the signal candle, as in the Pine script. '
                  'That price is already gone, so results look much better than they can be traded.',
                  'M15 gold, last 60 days, defaults: real P&L +35R with the previous close, -22R from the signal '
                  'close.'),
    'atrLen': ('ATR length used to size the stop-loss.\nHigher → steadier ATR. Lower → ATR follows the latest candles.',
               '14 → 50: one big candle barely changes the SL distance.'),
    'atrMultSl': ('Maximum SL distance in ATRs (the cap).\n'
                  'Higher → stop can sit beyond a further swing → larger risk, TPs further away.\n'
                  'Lower → tighter stops, closer TPs (more likely to be stopped by noise).',
                  'ATR 4, 1.5 → cap $6. At 1.0 the cap is $4 and every level moves closer to entry.'),
    'slStructBuf': ('Extra room beyond the swing for the SL, in ATRs.\n'
                    'Higher → SL further past the swing (safer from wicks, bigger risk). '
                    'Lower → SL right at the swing.',
                    'ATR 4, swing low 2646.50: 0.25 → SL 2645.50; 0.5 → SL 2644.50 (unless the cap is hit).'),
    'rr1': ('TP1 distance in R (1R = entry-to-SL distance).\n'
            'Higher → further target, hit less often. Lower → closer, hit more often.',
            'Risk $4.50, 1.0 → TP1 $4.50 from entry; 1.5 → $6.75.'),
    'rr2': ('TP2 distance in R.\nHigher → further target, hit less often. Lower → closer, hit more often.',
            'Risk $4.50, 2.0 → TP2 $9 from entry.'),
    'rr3': ('TP3 distance in R. Also the far edge of the green reward zone.\n'
            'Higher → further target and bigger green box. Lower → closer.',
            'Risk $4.50, 3.0 → TP3 $13.50 from entry.'),
    'confluenceWindow': ('How close together (in bars) the three engine votes must be for a BUY / SELL.\n'
                         'Higher → votes may be far apart → more signals, looser agreement.\n'
                         'Lower → votes must almost coincide → fewer, stricter signals.',
                         '5 → 2: structure break, RSI cross and ARSI cross must all happen within 2 bars.'),
    'useHtfFilter': ('On: each engine\'s vote must also agree with the higher timeframe (last completed HTF candle).\n'
                     'Engine 1: HTF fast > slow RSI line. Engine 2: HTF ARSI ≥ 50. Engine 3: HTF RSI > 50 (mirror for '
                     'sells). Fewer signals, only with the bigger trend.', None),
    'htfTimeframe': ('Timeframe used by the HTF filter.\nHigher TF → slower bias, blocks more counter-trend signals. '
                     'Lower TF → bias flips more often.', 'M5 chart with H4: only buys while H4 momentum is up.'),
    'useChopFilter': ('On: Engine 1 votes only count when ADX (trend strength) of the previous bar is at least '
                      'Min ADX. '
                      'Removes votes in sideways markets.', None),
    'adxLen': ('ADX length.\nHigher → slower, steadier ADX. Lower → reacts faster to new trends (and to noise).', None),
    'adxMinLevel': ('Minimum ADX for Engine 1 votes (when the ADX filter is on).\n'
                    'Higher → only strong trends → fewer votes. Lower → allows weaker trends → more votes.',
                    '20 → 30: most range-bound Cardwell crosses are ignored.'),
    'showDash': ('Shows the status table. Display only - never changes signals.', None),
    'dashPos': ('Corner of the status table. Display only.', None),
    'i_actionLong': ('Webhook word of the original Pine script. Not used by this platform.', None),
    'i_actionShort': ('Webhook word of the original Pine script. Not used by this platform.', None),
    'i_actionCloseLong': ('Webhook word of the original Pine script. Not used by this platform.', None),
    'i_actionCloseShort': ('Webhook word of the original Pine script. Not used by this platform.', None),
}
INPUTS = [dataclasses.replace(s, help=HELP[s.id][0], example=HELP[s.id][1]) if s.id in HELP else s for s in INPUTS]

# ---------------------------------------------------------------------------------------------------------------------
#  OSCILLATOR PANES - each engine's inner lines, so the votes can be seen where they happen. Hovering a line shows
#  its tooltip: what it is and how the settings move it.
# ---------------------------------------------------------------------------------------------------------------------
PANE_CW, PANE_AR = 'Engine 1 · Cardwell RSI', 'Engine 2 · Adaptive RSI'
TIP_VOTE = 'The vote counts toward a {side} for the next {cw} bars (Confluence Window).'
PLOTS = [
    PlotSpec('rsi', 'RSI', '#9598A1B3', pane='separate', pane_group=PANE_CW,
             tooltip='Base RSI, 0-100. Above 50 = buyers stronger, below 50 = sellers stronger.\n'
                     'RSI Len ↑ → smoother, slower, rarely at extremes.  RSI Len ↓ → faster, jumpier.'),
    PlotSpec('fast', 'Fast line', '#26A69AFF', linewidth=2, pane='separate', pane_group=PANE_CW,
             tooltip='RSI smoothed by Filter Type. Turns first when momentum changes.\n'
                     'Crossing ABOVE the slow line = bull vote, BELOW = bear vote.\n'
                     'Kalman: fixed speed. RMA/LLAMA: Fast Len ↑ → smoother & later, Fast Len ↓ → hugs the RSI.'),
    PlotSpec('slow', 'Slow line', '#FF9800FF', linewidth=2, pane='separate', pane_group=PANE_CW,
             tooltip='Baseline of the RSI. The fast line crossing it is the Engine 1 vote.\n'
                     'Slow Len ↑ → flatter, slower → fewer, bigger crosses.  Slow Len ↓ → closer to the fast line → '
                     'many small crosses.\nKalman: lag grows with Slow Len ÷ Fast Len.'),
    PlotSpec('cwBull', 'Cardwell bull vote', '#26A69AFF', linewidth=3, style='circles', pane='separate',
             pane_group=PANE_CW,
             tooltip='Engine 1 BULL vote: fast crossed above slow on the previous closed bar.\n'
                     + TIP_VOTE.format(side='BUY', cw='5 (default)')
                     + '\nDropped when the HTF / ADX filters (if on) disagree.'),
    PlotSpec('cwBear', 'Cardwell bear vote', '#EF5350FF', linewidth=3, style='circles', pane='separate',
             pane_group=PANE_CW,
             tooltip='Engine 1 BEAR vote: fast crossed below slow on the previous closed bar.\n'
                     + TIP_VOTE.format(side='SELL', cw='5 (default)')
                     + '\nDropped when the HTF / ADX filters (if on) disagree.'),
    PlotSpec('arsi', 'Adaptive RSI', '#2962FFFF', linewidth=2, pane='separate', pane_group=PANE_AR,
             tooltip='Adaptive RSI, 0-100, 50 = neutral. A close at a new N-bar high/low counts the whole range, so '
                     'breakouts push it hard.\nARSI Len ↑ → smoother, slower.  ARSI Len ↓ → faster, noisier.'),
    PlotSpec('arsiSig', 'ARSI signal line', '#AB47BCFF', linestyle='dotted', pane='separate', pane_group=PANE_AR,
             tooltip='ARSI smoothed by the ARSI Filter Type. Half of the merged line.\n'
                     'Kalman: Proc N ↑ or Meas N ↓ → faster;  Proc N ↓ or Meas N ↑ → smoother.\n'
                     'RMA / LLAMA: Signal Len ↑ → slower.'),
    PlotSpec('st', 'ARSI supertrend', '#26A69AFF', pane='separate', pane_group=PANE_AR,
             tooltip='Trailing floor (green, ARSI rising) or ceiling (red, ARSI falling) on the ARSI. Flips when ARSI '
                     'crosses it. Half of the merged line.\n'
                     'ST Factor ↑ → further away, flips rarely.  ST Factor ↓ → tight, flips often.\n'
                     'ST ATR Len ↑ → steadier distance.'),
    PlotSpec('merged', 'Merged line', '#FFD54FFF', linewidth=2, pane='separate', pane_group=PANE_AR,
             tooltip='(signal line + supertrend) ÷ 2. ARSI crossing ABOVE it = Engine 2 bull vote, BELOW = bear vote.\n'
                     'Anything that slows the signal line or supertrend slows this line → fewer, later votes.'),
    PlotSpec('arBull', 'ARSI bull vote', '#26A69AFF', linewidth=3, style='circles', pane='separate',
             pane_group=PANE_AR,
             tooltip='Engine 2 BULL vote: ARSI crossed above the merged line on the previous closed bar.\n'
                     + TIP_VOTE.format(side='BUY', cw='5 (default)')
                     + '\nWith the HTF filter on, HTF ARSI must be ≥ 50.'),
    PlotSpec('arBear', 'ARSI bear vote', '#EF5350FF', linewidth=3, style='circles', pane='separate',
             pane_group=PANE_AR,
             tooltip='Engine 2 BEAR vote: ARSI crossed below the merged line on the previous closed bar.\n'
                     + TIP_VOTE.format(side='SELL', cw='5 (default)')
                     + '\nWith the HTF filter on, HTF ARSI must be < 50.'),
]

# Registration info for the platform: id, display name, drawing budgets and the two drawing groups the user can
# toggle independently in the UI ('signals' = BUY/SELL labels, 'levels' = the Entry/SL/TP trade plan).
META = ScriptMeta(
    id='triple_confluence_markittick', name='Triple Confluence Navigator [MarkitTick]',
    short_name='Triple Confluence Navigator [MarkitTick]', category='Custom',
    description='BUY / SELL when a Cardwell RSI cross, an adaptive-RSI supertrend cross and a structure break line up '
                'within a few bars, with entry / SL / TP levels and a dashboard. Port of the Pine v6 script by '
                'MarkitTick (CC BY-NC-SA 4.0).',
    max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS, plots=PLOTS,
    drawing_groups=[DrawingGroup('signals', 'BUY / SELL labels'), DrawingGroup('levels', 'Entry / SL / TP levels'),
                    DrawingGroup('structure', 'Swing levels / BOS (Engine 3)'),
                    DrawingGroup('results', 'Trade exit markers')],
    # swing lines, BOS labels and result tags have their own quota, so they never push out the signals or levels
    group_limits={'structure': {'line': 300, 'label': 300}, 'results': {'label': 500}},
    tuning=dict(
        score='+1R for every TP reached, -1R at the stop. One trade at a time: a trade ends at TP3 or at the stop.',
        note='TP R:R is not tuned: with TPs at 1R / 2R / 3R every TP is worth exactly +1R.',
        params=[
            dict(id='rsiLen', min=7, max=21, step=1, on=True),
            dict(id='rmaFastLen', min=3, max=15, step=1, on=True),
            dict(id='rmaSlowLen', min=20, max=80, step=5, on=True),
            dict(id='maType', options=['Kalman', 'RMA', 'LLAMA'], on=False),
            dict(id='arsiLen', min=7, max=28, step=1, on=True),
            dict(id='arsiSigLen', min=5, max=30, step=1, on=False),
            dict(id='arsiFiltType', options=['Kalman Filter', 'RMA', 'LLAMA'], on=False),
            dict(id='kalmanProcN', min=0.005, max=0.05, step=0.005, on=False),
            dict(id='kalmanMeasN', min=0.05, max=0.5, step=0.05, on=False),
            dict(id='stAtrLen', min=5, max=30, step=1, on=False),
            dict(id='stFactor', min=1.0, max=4.0, step=0.5, on=True),
            dict(id='swingLen', min=3, max=12, step=1, on=True),
            dict(id='confluenceWindow', min=2, max=10, step=1, on=True),
            dict(id='atrLen', min=7, max=28, step=7, on=False),
            dict(id='atrMultSl', min=1.0, max=3.0, step=0.25, on=True),
            dict(id='slStructBuf', min=0.0, max=0.5, step=0.05, on=False),
            dict(id='useChopFilter', options=[False, True], on=False),
            dict(id='adxMinLevel', min=15, max=35, step=5, on=False),
            dict(id='useHtfFilter', options=[False, True], on=False),
        ],
    ),
)

# Hover text of each dashboard row: what it shows and which settings change it.
TABLE_TIPS = {
    'Cardwell': 'Engine 1 vote on the last bar: Bull / Bear only on the bar the fast RSI line crosses the slow one, '
                'else Wait.\nMore votes: lower Slow Len, lower RSI Len.',
    'Adaptive ARSI': 'Engine 2 vote on the last bar: Bull / Bear only on the bar ARSI crosses the merged line, else '
                     'Wait.\nMore votes: lower ST Factor, lower ARSI Len.',
    'Structure': 'Engine 3: direction of the last break of structure (stays until the opposite break).\n'
                 'Swing Len lower → more frequent breaks.',
    'RSI': 'Current RSI (0-100). Green ≥ 66, amber ≥ 33, red below.\nRSI Len lower → swings to the extremes more.',
    'ADX': 'Trend strength, full bar = ADX 50. Below ~20 = choppy.\n'
           'With the ADX filter on, Engine 1 needs ADX ≥ Min ADX.',
    'HTF Bias': 'Higher-timeframe RSI fast vs slow line (last completed HTF candle). '
                '"Off" while the HTF filter is off.',
    'Trade': 'Direction of the last BUY / SELL. Stays until the next signal - SL / TP hits are not tracked.',
    'Live R': 'Last close vs the last entry, in R (1R = entry-to-SL). +1.00R = one risk unit in profit.\n'
              'Not a trade result: SL / TP hits are not tracked.',
}

# Dashboard text colours: bullish / bearish / waiting.
GREEN, RED, AMBER = '#26A69AFF', '#EF5350FF', '#F9A825FF'


# =====================================================================================================================
#  SMALL HELPERS (Pine built-ins that Python does not have)
# =====================================================================================================================

# Pine's nz(x, y): "if x is na use y instead". Used to seed the Kalman filters on their first valid value.
def nz(x: float, y: float) -> float:
    return y if not finite(x) else x


# A tiny ring buffer that lets the code read past values with Pine's ``series[k]`` syntax:
# h[0] = latest pushed value, h[1] = one bar ago, ... ; anything older than the buffer is NAN (Pine's na).
class Hist:
    """A float series with ``[n]`` history (na before the first value)."""

    def __init__(self, n: int):
        self.d: deque[float] = deque(maxlen=n + 1)  # keeps [0]..[n]

    def push(self, x: float) -> None:
        self.d.append(x)

    def __getitem__(self, k: int) -> float:
        return self.d[-1 - k] if k < len(self.d) else NAN


# =====================================================================================================================
#  STREAMING BUILDING BLOCKS
#  Each class keeps its own state and returns this bar's value from update(). They are the Python equivalents of the
#  Pine built-ins / user functions named in their docstrings.
# =====================================================================================================================

# ENGINE 1 INPUT - Wilder's RSI.
#   change = close - previous close
#   avgGain = RMA(max(change, 0)), avgLoss = RMA(max(-change, 0))
#   RSI = 100 - 100 / (1 + avgGain / avgLoss)            (0..100, 50 = balance between buyers and sellers)
# Edge cases follow Pine: no losses -> 100, no gains -> 0, not enough data -> NAN.
class RSI:
    """``ta.rsi``: RMA of gains and losses."""

    def __init__(self, n: int):
        self.up, self.dn, self.prev = RMA(n), RMA(n), NAN

    def update(self, x: float) -> float:
        ch = x - self.prev if finite(self.prev) else NAN  # bar-to-bar change of the source
        self.prev = x
        u = self.up.update(max(ch, 0.0) if finite(ch) else NAN)   # smoothed gains
        d = self.dn.update(max(-ch, 0.0) if finite(ch) else NAN)  # smoothed losses
        if not (finite(u) and finite(d)):
            return NAN
        if d == 0:
            return 100.0
        if u == 0:
            return 0.0
        return 100 - 100 / (1 + u / d)


# ENGINE 2 CORE - Adaptive RSI (ARSI), output 0..100 with 50 as the neutral midline.
# Instead of using the plain close-to-close change like a normal RSI, every bar contributes a "diff":
#   * if this bar made a NEW n-bar highest close  -> diff = +(highest - lowest)   (full range, strong bullish push)
#   * else if it made a NEW n-bar lowest close    -> diff = -(highest - lowest)   (full range, strong bearish push)
#   * otherwise                                   -> diff = close - previous close (normal momentum)
# ARSI = RMA(diff) / RMA(|diff|) * 50 + 50.
# So breakouts to new highs/lows are weighted much more heavily than ordinary bars, which makes the oscillator react
# quickly to range expansion. Before enough data exists it returns 50 (neutral).
class ARSI:
    """``f_arsi``: adaptive RSI of the close."""

    def __init__(self, n: int):
        self.hi, self.lo, self.num, self.den = Highest(n), Lowest(n), RMA(n), RMA(n)
        self.pu = self.pl = self.pc = NAN  # previous highest, previous lowest, previous close

    def update(self, c: float) -> float:
        u, l = self.hi.update(c), self.lo.update(c)  # n-bar highest / lowest close
        r = u - l                                     # current n-bar range
        d = c - self.pc if finite(self.pc) else NAN   # normal close-to-close change
        diff = r if (finite(u) and finite(self.pu) and u > self.pu) else \
            -r if (finite(l) and finite(self.pl) and l < self.pl) else d
        self.pu, self.pl, self.pc = u, l, c
        num = self.num.update(diff)                                # smoothed signed push
        den = self.den.update(abs(diff) if finite(diff) else NAN)  # smoothed absolute push
        return num / den * 50 + 50 if finite(den) and den != 0 else 50.0


# 1-D Kalman filter (ENGINE 2's default signal-line smoother).
# It tracks an estimate ``est`` with uncertainty ``cov``:
#   predict:  cov += proc                      (process noise: how much the true value may drift each bar)
#   gain   :  cov / (cov + meas)               (meas = measurement noise: how much we distrust each new reading)
#   correct:  est += gain * (src - est);  cov *= (1 - gain)
# Small proc / large meas -> smoother but slower line; large proc / small meas -> faster, noisier line.
class Kalman:
    """``f_kalman``"""

    def __init__(self, proc: float, meas: float):
        self.proc, self.meas, self.est, self.cov = proc, meas, NAN, 1.0

    def update(self, src: float) -> float:
        if not finite(self.est):
            self.est = src  # seed with the first value
        self.cov += self.proc
        gain = self.cov / (self.cov + self.meas)
        self.est = self.est + gain * (src - self.est)
        self.cov = (1 - gain) * self.cov
        return self.est


# LLAMA - an adaptive exponential moving average (optional ENGINE 2 signal-line smoother).
# Its smoothing factor alpha moves between amin and amax according to an "efficiency" ratio:
#   efficiency = |src - src[n]| / (n-bar range)   (1 = clean one-way move, 0 = pure chop)
#   alpha      = amin + efficiency * (amax - amin)
#   out       += alpha * (src - out)
# So the line follows quickly when the ARSI trends and barely moves when it chops sideways.
class Llama:
    """``f_llama``"""

    def __init__(self, n: int, amin: float, amax: float):
        self.n, self.amin, self.amax = n, amin, amax
        self.src = Hist(n)                         # to read src[n]
        self.hi, self.lo = Highest(n), Lowest(n)
        self.out = NAN

    def update(self, src: float) -> float:
        if not finite(self.out):
            self.out = src  # seed with the first value
        self.src.push(src)
        slope = (src - self.src[self.n]) / self.n           # average change per bar over n bars
        rng = self.hi.update(src) - self.lo.update(src)      # n-bar range
        if finite(rng) and rng != 0:
            eff = min(abs(slope * self.n) / rng, 1.0) if finite(slope) else NAN
        else:
            eff = 0.0
        alpha = self.amin + eff * (self.amax - self.amin)
        self.out = self.out + alpha * (src - self.out)
        return self.out


# Linear regression value on the current bar: fits a least-squares straight line through the last n values and
# returns that line's value at the newest bar. Used by ENGINE 1 when Filter Type = 'LLAMA' (see note on maType).
class LinReg:
    """``ta.linreg(src, n, 0)``: the least-squares line's value on the current bar."""

    def __init__(self, n: int):
        self.n = n
        self.buf: deque[float] = deque(maxlen=n)

    def update(self, x: float) -> float:
        self.buf.append(x)
        n = self.n
        if len(self.buf) < n or not all(finite(v) for v in self.buf):
            return NAN
        xs = np.arange(n, dtype=float)
        ys = np.asarray(self.buf, dtype=float)
        sx, sy, sxx, sxy = xs.sum(), ys.sum(), (xs * xs).sum(), (xs * ys).sum()
        den = n * sxx - sx * sx
        if den == 0:
            return NAN
        slope = (n * sxy - sx * sy) / den
        intercept = (sy - slope * sx) / n
        return intercept + slope * (n - 1)  # value of the fitted line at the newest point


# Directional Movement Index - provides the ADX used by the optional "choppiness" filter of ENGINE 1.
#   +DM = up-move if it is larger than the down-move and positive, else 0 (mirror for -DM)
#   +DI / -DI = 100 * RMA(+DM / -DM) / RMA(True Range)
#   ADX = 100 * RMA(|+DI - -DI| / (+DI + -DI))
# ADX measures trend STRENGTH (not direction): low ADX (< ~20) = choppy, sideways market.
class DMI:
    """``ta.dmi(len, smooth)`` -> (+DI, -DI, ADX) with ``ta.tr`` (na on the first bar) as in Pine."""

    def __init__(self, n: int, smooth: int):
        self.tr, self.plus, self.minus, self.adx = RMA(n), RMA(n), RMA(n), RMA(smooth)
        self.ph = self.pl = self.pc = NAN
        self.last_p = self.last_m = NAN

    def update(self, h: float, l: float, c: float):
        up = h - self.ph if finite(self.ph) else NAN   # how far the high moved up
        dn = self.pl - l if finite(self.pl) else NAN   # how far the low moved down
        tr = max(h - l, abs(h - self.pc), abs(l - self.pc)) if finite(self.pc) else NAN  # true range
        self.ph, self.pl, self.pc = h, l, c
        plus = (up if up > dn and up > 0 else 0.0) if finite(up) else NAN
        minus = (dn if dn > up and dn > 0 else 0.0) if finite(dn) else NAN
        trur = self.tr.update(tr)
        sp, sm = self.plus.update(plus), self.minus.update(minus)
        ok = finite(trur) and trur != 0
        p = 100 * sp / trur if ok and finite(sp) else NAN
        m = 100 * sm / trur if ok and finite(sm) else NAN
        # fixnan(): keep the last valid +DI / -DI
        p = p if finite(p) else self.last_p
        m = m if finite(m) else self.last_m
        self.last_p, self.last_m = p, m
        s = p + m
        adx = 100 * self.adx.update(abs(p - m) / (1 if s == 0 else s)) if finite(s) else self.adx.update(NAN) * 100
        return p, m, adx


# =====================================================================================================================
#  THE INDICATOR
# =====================================================================================================================
class TripleConfluence(Script):
    meta = META
    # False skips every drawing and plot (the tuner only needs the trades, and runs hundreds of times)
    drawing = True
    # Open time of the first bar that may open a trade (the tuner sets its period start, so no trade from the
    # warm-up bars runs into the period and blocks its first signals). None = any bar.
    trade_from: int | None = None

    # Bars of history to run before the visible range so the long-memory smoothers (RMA, Kalman, the 45-length
    # slow line, ADX ...) have converged and the first visible signals match TradingView.
    def required_warmup(self) -> int:
        return 2000

    # -----------------------------------------------------------------------------------------------------------------
    #  setup(): runs ONCE before the first bar. Creates every streaming calculator and every piece of state that
    #  Pine would declare with ``var`` (values that persist from bar to bar).
    # -----------------------------------------------------------------------------------------------------------------
    def setup(self, ctx: Context) -> None:
        p = self.p
        # ---- Engine 1 state: RSI and its fast / slow smoothing ------------------------------------------------------
        self.rsi = RSI(p['rsiLen'])
        # Kalman filter state for the fast and slow RSI lines: X = estimate, P = error covariance.
        self.kfFastX, self.kfFastP, self.kfSlowX, self.kfSlowP = NAN, 1.0, NAN, 1.0
        # The two alternative smoothers (all three are always computed; maType picks which one is used).
        self.rmaFast_rma, self.rmaSlow_rma = RMA(p['rmaFastLen']), RMA(p['rmaSlowLen'])
        self.lrFast, self.lrSlow = LinReg(p['rmaFastLen']), LinReg(p['rmaSlowLen'])
        # History of the chosen fast / slow lines so the cross can be checked on bars [1] vs [2].
        self.rmaFast, self.rmaSlow = Hist(2), Hist(2)
        # Price ATR (for SL sizing) and its 1-bar history (the trade plan uses the PREVIOUS bar's ATR).
        self.atr = ATR(p['atrLen'])
        self.atrHist = Hist(1)
        # ADX for the optional chop filter, with 1-bar history (the filter uses the PREVIOUS bar's ADX).
        self.dmi = DMI(p['adxLen'], p['adxLen'])
        self.adxHist = Hist(1)
        # ---- Engine 2 state: adaptive RSI, its signal line and its supertrend --------------------------------------
        self.arsiF = ARSI(p['arsiLen'])
        # Three candidate signal-line smoothers; arsiFiltType picks one each bar.
        self.sigRma = RMA(p['arsiSigLen'])
        self.sigKalman = Kalman(p['kalmanProcN'], p['kalmanMeasN'])
        self.sigLlama = Llama(p['arsiSigLen'], p['llamaAlphaMin'], p['llamaAlphaMax'])
        # "ATR" of the oscillator: RMA of |ARSI - ARSI[1]| - sizes the supertrend bands.
        self.arsiAtr = RMA(p['stAtrLen'])
        self.arsiHist = Hist(2)     # ARSI history ([1] and [2] are used for the cross)
        self.merged = Hist(2)       # merged-line history (same purpose)
        self.supertrend, self.trendUp = NAN, True  # supertrend level on the ARSI and its direction
        # ---- Engine 3 state: swing points / break of structure ---------------------------------------------------
        n = p['swingLen']
        # A pivot needs n bars left + the pivot bar + n bars right = 2n+1 highs/lows of history.
        self.highs, self.lows = Hist(2 * n), Hist(2 * n)
        self.lastSwingHigh = self.lastSwingLow = NAN               # price of the most recent swing high / low
        self.lastSwingHighBar = self.lastSwingLowBar = None        # bar index of those swings
        # The swing that was already broken, so the same swing cannot trigger a second break signal.
        self.lastProcessedHighBar = self.lastProcessedLowBar = None
        self.trend3 = 0  # structure trend for the dashboard: +1 after a bullish break, -1 after a bearish break
        # Dotted line of the swing high / low that is currently waiting to be broken (None when there is none).
        self.swingLine: dict[str, object] = {'high': None, 'low': None}
        # ---- Confluence state ---------------------------------------------------------------------------------------
        # "Bars since" each of the six raw signals last fired (9999 = never). b = bull, s = bear, 1/2/3 = engine.
        self.since = dict(b1=9999, s1=9999, b2=9999, s2=9999, b3=9999, s3=9999)
        # Previous bar's confluence state - signals fire only on the False -> True transition (rising edge).
        self.prevBullConf = self.prevBearConf = False
        # ---- Trade-plan state ---------------------------------------------------------------------------------------
        # A trade runs from its signal until TP3 or SL; signals while it runs are skipped (one trade at a time).
        self.tradeActive, self.tradeIsLong = False, True   # is a trade running / direction of the last one
        self.trk = dict(sl=NAN, tp1=NAN, tp2=NAN, tp3=NAN, entry=NAN)  # prices of the current trade plan
        self.hits = [False, False, False]                  # TP1..TP3 reached by the current trade
        self.trkOpenTime = None                            # signal bar time of the current trade
        # Finished trades: open/close time, side, TPs hit, outcome ('tp3' / 'sl') and result in R
        # (+1R for each TP reached, -1R when the stop is hit).
        self.closed: list[dict] = []
        self.skipped = 0                                   # signals ignored because a trade was running
        self.levels: dict[str, object] = {}                # drawing objects (lines, boxes, labels) of the plan
        self.trkStartBar = None                            # bar index where the current plan started
        self.closeHist = Hist(1)                           # close[1] - the entry price of a new plan
        self.state: dict = {}                              # last bar's values, read by the dashboard
        self.events: list[dict] = []                       # "BUY Signal" / "SELL Signal" alert events
        # ---- Optional higher-timeframe (HTF) filter -----------------------------------------------------------------
        # When enabled, four series are computed on the higher timeframe's closes and pulled onto this chart:
        #   fast / slow : RMA(fast) and RMA(slow) of the HTF RSI   -> Engine 1 bias (fast > slow = bullish)
        #   arsi        : HTF adaptive RSI                         -> Engine 2 bias (>= 50 = bullish)
        #   rsi         : HTF RSI                                  -> Engine 3 bias (> 50 = bullish)
        # Note: the HTF fast/slow lines always use RMA, whatever maType is set to for the chart timeframe.
        # 1-minute candles inside each chart bar: when one candle touches the stop AND a new target, they tell which
        # came first (the D chart would need years of 1-minute candles, so there the stop is assumed first).
        self.ltf = ctx.request_lower_tf(60) if 60 < ctx.timeframe.seconds <= 4 * 3600 else None
        self.htf = None
        if p['useHtfFilter']:
            tf = PINE_TF.get(p['htfTimeframe'], ctx.timeframe.code) if p['htfTimeframe'] else ctx.timeframe.code
            rl, fl, sl, al = p['rsiLen'], p['rmaFastLen'], p['rmaSlowLen'], p['arsiLen']

            def run(make):  # a fresh streaming chain over the higher-timeframe closes
                def expr(b):
                    step = make()
                    return np.array([step(x) for x in np.asarray(b.close, dtype=float).tolist()], dtype=float)
                return expr

            def rma_of_rsi(length):
                def make():
                    rsi, rma = RSI(rl), RMA(length)
                    return lambda x: rma.update(rsi.update(x))
                return run(make)

            rsi_only = run(lambda: RSI(rl).update)
            arsi_only = run(lambda: ARSI(al).update)

            # lookahead=True + offset=1 is the standard NON-REPAINTING HTF idiom: every chart bar sees the value of
            # the LAST COMPLETED higher-timeframe bar, never the still-forming one.
            req = lambda expr: ctx.request_security(tf, expr, lookahead=True, offset=1)
            self.htf = dict(fast=req(rma_of_rsi(fl)), slow=req(rma_of_rsi(sl)), arsi=req(arsi_only),
                            rsi=req(rsi_only))

    # -----------------------------------------------------------------------------------------------------------------
    #  on_bar(): runs once per bar, oldest to newest. This is where all three engines are computed and where the
    #  final BUY / SELL decision is made. (A still-forming live bar is processed provisionally and rolled back by
    #  the engine before the next update, so state never double-counts.)
    # -----------------------------------------------------------------------------------------------------------------
    def on_bar(self, ctx: Context) -> None:
        p = self.p
        i = ctx.bar_index
        h, l, c = ctx.high, ctx.low, ctx.close
        confirmed = ctx.is_confirmed  # True when the bar has closed; signals are only allowed on closed bars

        # =============================================================================================================
        #  ENGINE 1 - CARDWELL RSI (fast line vs slow line of the RSI)
        # =============================================================================================================
        rsiValue = self.rsi.update(c)
        # Kalman fast / slow on the RSI (state updates every bar, also while the RSI is still na)
        # kfQ = process noise, kfR = measurement noise. The SLOW filter multiplies kfR by slowLen / fastLen
        # (45 / 9 = 5x by default): it trusts each new RSI reading 5x less, so it reacts more slowly.
        kfQ, kfR = 0.001, 0.01
        kfFastK = self.kfFastP / (self.kfFastP + kfR)                                    # fast Kalman gain
        self.kfFastX = nz(self.kfFastX, rsiValue) + kfFastK * (rsiValue - nz(self.kfFastX, rsiValue))
        self.kfFastP = (1.0 - kfFastK) * self.kfFastP + kfQ
        kfSlowK = self.kfSlowP / (self.kfSlowP + kfR * (p['rmaSlowLen'] / p['rmaFastLen']))  # slow Kalman gain
        self.kfSlowX = nz(self.kfSlowX, rsiValue) + kfSlowK * (rsiValue - nz(self.kfSlowX, rsiValue))
        self.kfSlowP = (1.0 - kfSlowK) * self.kfSlowP + kfQ
        # The other two smoothers are always updated too, so switching maType never leaves them stale.
        lrF, lrS = self.lrFast.update(rsiValue), self.lrSlow.update(rsiValue)
        rmF, rmS = self.rmaFast_rma.update(rsiValue), self.rmaSlow_rma.update(rsiValue)
        # Pick the fast / slow line according to "Filter Type" ('LLAMA' -> linear regression, see the input note).
        mt = p['maType']
        self.rmaFast.push(self.kfFastX if mt == 'Kalman' else lrF if mt == 'LLAMA' else rmF)
        self.rmaSlow.push(self.kfSlowX if mt == 'Kalman' else lrS if mt == 'LLAMA' else rmS)
        f, s = self.rmaFast, self.rmaSlow
        # Cross detection on the PREVIOUS two bars ([1] vs [2]), not on the current bar: the cross is only accepted
        # once the bar it happened on has fully closed. This avoids repainting but means Engine 1 is 1 bar late.
        rawBullCross = f[1] > s[1] and f[2] <= s[2]   # fast crossed ABOVE slow -> RSI momentum turning up
        rawBearCross = f[1] < s[1] and f[2] >= s[2]   # fast crossed BELOW slow -> RSI momentum turning down
        # Price ATR. atrValue1 = ATR of the previous bar (falls back to the current one on the very first bar);
        # it sizes the stop-loss of a new trade plan.
        atrValue = self.atr.update(h, l, c)
        atrValue1 = nz(self.atrHist[0], atrValue)
        self.atrHist.push(atrValue)

        # ---- HTF values for this bar (empty dict when the HTF filter is off) ----------------------------------------
        hv = {k: ctx.security(v) for k, v in self.htf.items()} if self.htf else {}
        # HTF bias for Engine 1: HTF fast RSI line above / below the HTF slow line. (With no HTF data these
        # comparisons are against NAN and evaluate to False.)
        htfBullBias = hv.get('fast', NAN) > hv.get('slow', NAN)
        htfBearBias = hv.get('fast', NAN) < hv.get('slow', NAN)
        useHtf = p['useHtfFilter']
        # A cross only counts if the HTF filter is off OR the HTF bias agrees with it.
        bullCross1 = rawBullCross and (not useHtf or htfBullBias)
        bearCross1 = rawBearCross and (not useHtf or htfBearBias)
        # ---- ADX "choppiness" filter for Engine 1 -------------------------------------------------------------------
        # When enabled the PREVIOUS bar's ADX must be at least adxMinLevel (default 20), i.e. the market must be
        # trending enough. adxValue itself is also shown on the dashboard.
        _, _, adxValue = self.dmi.update(h, l, c)
        chopOk = not p['useChopFilter'] or self.adxHist[0] >= p['adxMinLevel']
        self.adxHist.push(adxValue)
        # >>> ENGINE 1 OUTPUT: b1 / s1 <<<
        bullSignal1 = bullCross1 and chopOk
        bearSignal1 = bearCross1 and chopOk

        # =============================================================================================================
        #  ENGINE 2 - ADAPTIVE RSI vs (signal line + ARSI supertrend)
        # =============================================================================================================
        arsi = self.arsiF.update(c)
        # Signal line of the ARSI - all three smoothers update every bar, arsiFiltType picks one.
        ft = p['arsiFiltType']
        sr, sk, sll = self.sigRma.update(arsi), self.sigKalman.update(arsi), self.sigLlama.update(arsi)
        arsiSignal = sr if ft == 'RMA' else sk if ft == 'Kalman Filter' else sll
        # ---- Supertrend computed on the ARSI oscillator (not on price) ---------------------------------------------
        # Its "ATR" is the smoothed bar-to-bar change of the ARSI. Bands = ARSI +/- stFactor x that ATR.
        a1 = self.arsiHist[0]  # ARSI of the previous bar (not pushed yet for this bar)
        arsiAtr = self.arsiAtr.update(abs(arsi - a1) if finite(a1) else NAN)
        upperBand = arsi + p['stFactor'] * arsiAtr
        lowerBand = arsi - p['stFactor'] * arsiAtr
        prevST, prevUp = self.supertrend, self.trendUp
        if not finite(prevST):
            # First valid bar: start the supertrend on the upper band in "up" mode (as the Pine script does).
            self.supertrend, self.trendUp = upperBand, True
        else:
            # Direction: ARSI above the previous supertrend = up, below = down, exactly equal = unchanged.
            self.trendUp = True if arsi > prevST else False if arsi < prevST else prevUp
            # Ratchet: in an up-trend the line can only RISE (follows the lower band upward); in a down-trend it can
            # only FALL (follows the upper band downward). This is the classic trailing-stop behaviour.
            self.supertrend = max(lowerBand, prevST) if self.trendUp else min(upperBand, prevST)
        # The "merged" line = average of the signal line and the supertrend. It blends a smooth average of the ARSI
        # with a volatility-trailing level, giving a line that is both responsive and resistant to whipsaws.
        rawMerged = arsiSignal if not finite(self.supertrend) else (arsiSignal + self.supertrend) / 2
        self.arsiHist.push(arsi)
        self.merged.push(rawMerged)
        A, M = self.arsiHist, self.merged
        # Same "previous closed bar" cross logic as Engine 1: ARSI vs merged line on [1] vs [2].
        ok = all(finite(x) for x in (A[1], A[2], M[1], M[2]))
        rawBullCross2 = ok and A[1] > M[1] and A[2] <= M[2]   # ARSI crossed ABOVE the merged line
        rawBearCross2 = ok and A[1] < M[1] and A[2] >= M[2]   # ARSI crossed BELOW the merged line
        # >>> ENGINE 2 OUTPUT: b2 / s2 <<<  (HTF filter: higher-timeframe ARSI on the bullish / bearish side of 50)
        bullSignal2 = rawBullCross2 and (not useHtf or hv.get('arsi', NAN) >= 50.0)
        bearSignal2 = rawBearCross2 and (not useHtf or hv.get('arsi', NAN) < 50.0)
        # HTF permission for Engine 3 (used below): higher-timeframe RSI above / below 50.
        htfOkBull3 = not useHtf or hv.get('rsi', NAN) > 50.0
        htfOkBear3 = not useHtf or hv.get('rsi', NAN) < 50.0

        # =============================================================================================================
        #  ENGINE 3 - MARKET STRUCTURE (swing pivots and break of structure)
        # =============================================================================================================
        # structure: custom pivots (only on confirmed bars)
        n = p['swingLen']
        self.highs.push(h)
        self.lows.push(l)
        H, Lw = self.highs, self.lows

        # Pivot test on the bar n bars ago (arr[n]) - the "candidate":
        #   * the n NEWER bars (arr[0]..arr[n-1]) must all be STRICTLY lower (high) / higher (low) than it;
        #   * the n OLDER bars (arr[n+1]..arr[2n]) must not exceed it (equal values are allowed on that side).
        # Because n bars to the right are needed, a swing is only known n bars AFTER it happened (default 5 bars).
        # Only evaluated on confirmed (closed) bars. Returns the pivot price or NAN.
        def pivot(arr, high):
            cnd = arr[n]
            ok = confirmed
            for k in range(n):
                if (arr[k] >= cnd) if high else (arr[k] <= cnd):
                    ok = False
            for k in range(n + 1, 2 * n + 1):
                if (arr[k] > cnd) if high else (arr[k] < cnd):
                    ok = False
            return cnd if ok else NAN

        ph, pl = pivot(H, True), pivot(Lw, False)
        # Remember the most recent swing high / low and the bar where it actually formed (i - n).
        if finite(ph):
            self.lastSwingHigh, self.lastSwingHighBar = ph, i - n
            if self.drawing:
                self._swing_line(ctx, True, ph, i - n)
        if finite(pl):
            self.lastSwingLow, self.lastSwingLowBar = pl, i - n
            if self.drawing:
                self._swing_line(ctx, False, pl, i - n)
        # Break of structure:
        #   bullish = a closed bar CLOSES above the latest swing high (a wick through is not enough),
        #             the HTF RSI permits it, and this particular swing high has not been broken before;
        #   bearish = mirror with the latest swing low.
        # If the HTF filter blocks a break, the swing stays "unprocessed" and can still trigger later.
        breakUp = (confirmed and finite(self.lastSwingHigh) and c > self.lastSwingHigh and htfOkBull3
                   and (self.lastProcessedHighBar is None or self.lastSwingHighBar != self.lastProcessedHighBar))
        breakDown = (confirmed and finite(self.lastSwingLow) and c < self.lastSwingLow and htfOkBear3
                     and (self.lastProcessedLowBar is None or self.lastSwingLowBar != self.lastProcessedLowBar))
        # Mark the swing as used and update the structure trend shown on the dashboard.
        if breakUp:
            self.lastProcessedHighBar, self.trend3 = self.lastSwingHighBar, 1
            if self.drawing:
                self._mark_bos(ctx, True, c)
        if breakDown:
            self.lastProcessedLowBar, self.trend3 = self.lastSwingLowBar, -1
            if self.drawing:
                self._mark_bos(ctx, False, c)
        # Swing lines still waiting for a break run up to the current bar.
        for obj in self.swingLine.values():
            if obj is not None:
                obj.x2 = ctx.time
        # >>> ENGINE 3 OUTPUT: b3 / s3 <<<
        bullSignal3, bearSignal3 = breakUp, breakDown

        # =============================================================================================================
        #  CONFLUENCE - combine the three engines into the final signal
        # =============================================================================================================
        # Pine's ta.barssince() for each raw signal: reset to 0 on the bar it fires, +1 on every other bar.
        S = self.since
        for k, sig in (('b1', bullSignal1), ('s1', bearSignal1), ('b2', bullSignal2), ('s2', bearSignal2),
                       ('b3', bullSignal3), ('s3', bearSignal3)):
            S[k] = 0 if sig else S[k] + 1
        # Confluence = all three engines of the same direction fired within the last w bars (order does not matter).
        # Example (w = 5): structure break 4 bars ago, RSI cross 2 bars ago, ARSI cross now -> bullConf is True.
        w = p['confluenceWindow']
        bullConf = S['b1'] <= w and S['b2'] <= w and S['b3'] <= w
        bearConf = S['s1'] <= w and S['s2'] <= w and S['s3'] <= w
        # >>> FINAL SIGNALS <<< only on the first bar the confluence becomes True (rising edge) and only on a closed
        # bar, so a cluster of agreement produces a single BUY / SELL and nothing repaints.
        longSignal = bullConf and not self.prevBullConf and confirmed
        shortSignal = bearConf and not self.prevBearConf and confirmed
        self.prevBullConf, self.prevBearConf = bullConf, bearConf

        # =============================================================================================================
        #  TRADE PLAN - draw Entry / SL / TP for a new signal and keep the current plan extended to the right
        # =============================================================================================================
        prevClose = self.closeHist[0]  # close[1]: the entry price of a new plan (the Pine original uses close[1])
        self.closeHist.push(c)
        # 1) Walk the running trade through this bar first: a trade that ends here frees the slot for a new signal.
        if self.tradeActive and i > self.trkStartBar:
            self._update_trade(ctx, h, l)
        # 2) Keep the running plan's lines, zones and tags reaching the current bar (they stop where the trade ends).
        if self.tradeActive and self.levels:
            self._extend_levels(ctx.time)
        # 3) One trade at a time: a signal only opens a trade when none is running (and inside the tuning period).
        if self.trade_from is not None and ctx.time < self.trade_from:
            longSignal = shortSignal = False
        if self.tradeActive:
            self.skipped += int(longSignal) + int(shortSignal)
            longSignal = shortSignal = False
        shortSignal = shortSignal and not longSignal  # both on one bar (practically never): take the long
        for is_long, sig in ((True, longSignal), (False, shortSignal)):
            if sig:
                self._open_trade(ctx, is_long, c if p['entryMode'] == 'Signal close' else prevClose, atrValue1)

        # =============================================================================================================
        #  OUTPUTS - alert events and the BUY / SELL labels on the chart
        # =============================================================================================================
        # Alert events (equivalent of Pine's alert() calls); only the last 500 are kept.
        for is_long, sig in ((True, longSignal), (False, shortSignal)):
            if sig and confirmed:
                self.events.append(dict(time=ctx.time, title='BUY Signal' if is_long else 'SELL Signal'))
                del self.events[:-500]
        # BUY label under the signal candle / SELL label above it (drawing group 'signals'). The tooltip lists when
        # each engine voted, so the confluence can be checked against the panes and the BOS line.
        def ago(n):
            return 'this bar' if n == 0 else f'{n} bar{"s" if n > 1 else ""} ago'

        def votes_tip(side, k):
            return (f'All 3 engines voted {side} within {w} bars:\n'
                    f'• Engine 1 Cardwell RSI cross: {ago(S[k + "1"])}\n'
                    f'• Engine 2 ARSI × merged line: {ago(S[k + "2"])}\n'
                    f'• Engine 3 structure break (BOS): {ago(S[k + "3"])}\n'
                    f'Confluence Window ({w}) ↑ → votes may be further apart → more, looser signals.\n'
                    f'Confluence Window ↓ → votes must nearly coincide → fewer, stricter signals.')

        if not self.drawing:
            return
        if longSignal:
            ctx.label(x=ctx.time, y=l, text='BUY', yloc='belowbar', style='label_up', color=p['colBuyLbl'],
                      text_color=WHITE, size='small', group='signals', tooltip_title='BUY signal',
                      tooltip=votes_tip('bullish', 'b'))
        if shortSignal:
            ctx.label(x=ctx.time, y=h, text='SELL', yloc='abovebar', style='label_down', color=p['colSellLbl'],
                      text_color=WHITE, size='small', group='signals', tooltip_title='SELL signal',
                      tooltip=votes_tip('bearish', 's'))

        # Oscillator panes: each engine's lines, plus a dot on the bar where its vote counted.
        ctx.plot('rsi', rsiValue)
        ctx.plot('fast', f[0])
        ctx.plot('slow', s[0])
        ctx.plot('cwBull', f[0] if bullSignal1 else NAN)
        ctx.plot('cwBear', f[0] if bearSignal1 else NAN)
        ctx.plot('arsi', arsi)
        ctx.plot('arsiSig', arsiSignal)
        ctx.plot('st', self.supertrend, GREEN if self.trendUp else RED)
        ctx.plot('merged', rawMerged)
        ctx.plot('arBull', arsi if bullSignal2 else NAN)
        ctx.plot('arBear', arsi if bearSignal2 else NAN)

        # Snapshot of this bar's values for the dashboard (on_last_bar reads the one from the newest bar).
        self.state = dict(b1=bullSignal1, s1=bearSignal1, b2=bullSignal2, s2=bearSignal2, b3=bullSignal3,
                          s3=bearSignal3, rsi=rsiValue, adx=adxValue, htfBull=htfBullBias, close=c)

    # -----------------------------------------------------------------------------------------------------------------
    #  Engine 3 drawings: a dotted line at the swing that is waiting to be broken, turned solid with a "BOS" tag when
    #  a close breaks it. A newer swing replaces an unbroken one (only the latest swing can be broken).
    # -----------------------------------------------------------------------------------------------------------------
    def _swing_line(self, ctx: Context, high: bool, price: float, bar: int) -> None:
        key = 'high' if high else 'low'
        ctx.delete(self.swingLine[key])
        n = self.p['swingLen']
        name, side, word = ('Swing high', 'above', 'bullish') if high else ('Swing low', 'below', 'bearish')
        self.swingLine[key] = ctx.line(
            x1=ctx.time_at(bar), y1=price, x2=ctx.time, y2=price, color=new_color(RED if high else GREEN, 40),
            width=1, style='dotted', group='structure', tooltip_title=name,
            tooltip=f'{name} {fmt_price(price, ctx.precision)}: '
                    f'{n} bars on each side are {"lower" if high else "higher"}.\n'
                    f'A candle CLOSE {side} this line = {word} BOS = Engine 3 vote.\n'
                    f'Swing Len ({n}) ↑ → only bigger swings, found later, fewer BOS.\n'
                    f'Swing Len ↓ → small swings, found sooner, more BOS (more noise).')

    def _mark_bos(self, ctx: Context, up: bool, close: float) -> None:
        key = 'high' if up else 'low'
        obj = self.swingLine[key]
        if obj is None:
            return
        self.swingLine[key] = None
        col = GREEN if up else RED
        name = 'Bullish BOS' if up else 'Bearish BOS'
        tip = (f'Break of structure: close {fmt_price(close, ctx.precision)} broke the swing '
               f'{"high" if up else "low"} {fmt_price(obj.y1, ctx.precision)}.\n'
               f'Engine 3 {"BULL" if up else "BEAR"} vote. ' + TIP_VOTE.format(side='BUY' if up else 'SELL',
                                                                               cw=self.p['confluenceWindow']) +
               '\nEach swing can be broken once. With the HTF filter on, HTF RSI must be '
               + ('above' if up else 'below') + ' 50.')
        obj.x2, obj.style, obj.width, obj.color = ctx.time, 'solid', 1, new_color(col, 20)
        obj.tooltip_title, obj.tooltip = name, tip
        swingBar = self.lastSwingHighBar if up else self.lastSwingLowBar  # the line always belongs to the last swing
        ctx.label(x=ctx.time_at((ctx.bar_index + swingBar) // 2), y=obj.y1, text='BOS',
                  style='label_down' if up else 'label_up', color='#00000000', text_color=col, size='tiny',
                  group='structure', tooltip_title=name, tooltip=tip)

    # -----------------------------------------------------------------------------------------------------------------
    #  _open_trade(): called on the bar a BUY / SELL fires. Computes the risk, SL and three TPs, and draws them.
    #  Only ONE plan is shown at a time - the previous plan's drawings are deleted first.
    #     entry = signal close (or the previous close, Entry Price setting),  atr1 = previous bar's ATR
    # -----------------------------------------------------------------------------------------------------------------
    def _open_trade(self, ctx: Context, is_long: bool, entry: float, atr1: float) -> None:
        p = self.p
        # ---- Risk (distance from entry to SL) -----------------------------------------------------------------------
        # capDist = the MAXIMUM stop distance allowed: atrMultSl x ATR (default 1.5 x ATR).
        capDist = atr1 * p['atrMultSl']
        # Structural stop distance: just beyond the last swing on the opposite side, plus a buffer of
        # slStructBuf x ATR (default 0.25 x ATR). Long -> below the last swing low; short -> above the last swing high.
        if is_long:
            struct = entry - (self.lastSwingLow - atr1 * p['slStructBuf']) if finite(self.lastSwingLow) else NAN
        else:
            struct = (self.lastSwingHigh + atr1 * p['slStructBuf']) - entry if finite(self.lastSwingHigh) else NAN
        # Use the structural distance when it is valid and tighter than the cap; if there is no swing yet or the swing
        # is on the wrong side of the entry (distance <= 0), fall back to the pure ATR cap.
        risk = capDist if not finite(struct) or struct <= 0 else min(struct, capDist)
        # ---- Price levels -------------------------------------------------------------------------------------------
        d = 1 if is_long else -1                  # +1 long / -1 short, so one formula serves both directions
        sl = entry - d * risk
        tp1, tp2, tp3 = (entry + d * risk * p[k] for k in ('rr1', 'rr2', 'rr3'))  # 1R / 2R / 3R by default
        if not (finite(entry) and finite(risk) and risk > 0):
            return  # no ATR yet (first bars): no trade, so nothing can block the following signals
        # ---- Start tracking the trade --------------------------------------------------------------------------------
        self.tradeActive, self.tradeIsLong = True, is_long
        self.trk = dict(sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, entry=entry)
        self.hits = [False, False, False]
        self.trkStartBar, self.trkOpenTime = ctx.bar_index, ctx.time
        if not self.drawing:
            return
        # Remove the previous trade's lines, zones and tags (its result labels stay).
        for obj in self.levels.values():
            ctx.delete(obj)
        self.levels = {}
        t = ctx.time
        lv = self.levels
        prec = ctx.precision
        fp = lambda v: fmt_price(v, prec)  # noqa: E731
        # ---- Hover texts: what each level is, how it was computed here, and which settings move it -----------------
        usedStruct = finite(struct) and 0 < struct <= capDist
        swing = self.lastSwingLow if is_long else self.lastSwingHigh
        how = (f'beyond the last swing {"low" if is_long else "high"} {fp(swing)} + {p["slStructBuf"]} × ATR'
               if usedStruct else f'at the cap {p["atrMultSl"]} × ATR (no usable swing, or it was further away)')
        tips = {
            'entry': ('Entry', f'Entry {fp(entry)} = ' + ('close of the signal candle.'
                                                           if p['entryMode'] == 'Signal close' else
                                                           'close of the candle BEFORE the signal (Pine original).')
                      + '\nSet by Entry Price.'),
            'sl': ('Stop loss (SL)',
                   f'SL {fp(sl)}, risk {fp(risk)} = 1R ({risk / atr1 if atr1 else NAN:.2f} × ATR), placed {how}.\n'
                   f'SL ATR Mult ({p["atrMultSl"]}) ↑ → wider stops allowed → bigger risk, TPs further.  ↓ → tighter.\n'
                   f'SL Struct Buffer ({p["slStructBuf"]}) ↑ → SL further past the swing.  ↓ → right at the swing.'),
        }
        for k, v in (('tp1', tp1), ('tp2', tp2), ('tp3', tp3)):
            rr = p['rr' + k[-1]]
            tips[k] = (f'Take profit {k[-1]} (TP{k[-1]})',
                       f'TP{k[-1]} {fp(v)} = entry {"+" if is_long else "-"} {rr} × risk ({rr}R, '
                       f'{fp(risk * rr)} away).\n'
                       f'TP{k[-1]} R:R ↑ → further target, hit less often.  ↓ → closer, hit more often.\n'
                       f'Also moves with the SL settings, because R = entry-to-SL distance.')

        # A dashed horizontal level line that starts (and for now ends) on the signal bar; on_bar extends it.
        def line(y, col, k):
            return ctx.line(x1=t, y1=y, x2=t, y2=y, color=col, width=1, style='dashed', group='levels',
                            tooltip_title=tips[k][0], tooltip=tips[k][1])

        lv['slLine'] = line(sl, p['colSl'], 'sl')
        lv['entryLine'] = line(entry, p['colEntry'], 'entry')
        lv['tp1Line'] = line(tp1, p['colTp1'], 'tp1')
        lv['tp2Line'] = line(tp2, p['colTp2'], 'tp2')
        lv['tp3Line'] = line(tp3, p['colTp3'], 'tp3')
        # linefill(slLine, entryLine) / linefill(entryLine, tp3Line): boxes over the same bars as the lines
        # Red zone = risk (entry to SL), green zone = reward (entry to TP3).
        lv['riskFill'] = ctx.box(left=t, right=t, top=max(sl, entry), bottom=min(sl, entry), border_width=0,
                                 border_color='#00000000', bg_color=p['zoneRiskColor'], group='levels',
                                 tooltip_title='Risk zone',
                                 tooltip=f'Entry → SL: what you lose if stopped = 1R ({fp(risk)}).\n'
                                         f'Grows with SL ATR Mult and SL Struct Buffer.')
        lv['rewardFill'] = ctx.box(left=t, right=t, top=max(tp3, entry), bottom=min(tp3, entry), border_width=0,
                                   border_color='#00000000', bg_color=p['zoneRewardColor'], group='levels',
                                   tooltip_title='Reward zone',
                                   tooltip=f'Entry → TP3: {p["rr3"]}R ({fp(risk * p["rr3"])}).\n'
                                           f'Grows with TP3 R:R and with the risk (SL settings).')
        # Price tags at the right end of each line, e.g. "● TP1  2650.25".
        for key, y, text, col, k in (('slLbl', sl, '✕ SL  ', p['colSl'], 'sl'),
                                     ('entryLbl', entry, '▶ Entry  ', p['colEntry'], 'entry'),
                                     ('tp1Lbl', tp1, '● TP1  ', p['colTp1'], 'tp1'),
                                     ('tp2Lbl', tp2, '★ TP2  ', p['colTp2'], 'tp2'),
                                     ('tp3Lbl', tp3, '◆ TP3  ', p['colTp3'], 'tp3')):
            lv[key] = ctx.label(x=t, y=y, text=text + fmt_price(y, prec), style='label_left', color=col,
                                text_color=WHITE, size='small', group='levels', tooltip_title=tips[k][0],
                                tooltip=tips[k][1])

    # -----------------------------------------------------------------------------------------------------------------
    #  _update_trade(): walks the running trade through one bar (from the bar after the signal).
    #  Scoring: +1R for every TP reached, -1R when the stop is hit. The trade ends at TP3 (win) or SL (loss).
    #  If one candle touches the stop AND a new TP, its 1-minute candles are walked in order to find which came
    #  first; only when a single 1-minute candle touches both (or none are available) is the stop assumed first.
    # -----------------------------------------------------------------------------------------------------------------
    def _update_trade(self, ctx: Context, h: float, l: float) -> None:
        if self.ltf is not None and self._sl_touched(h, l) and self._new_tp_touched(h, l):
            ib = ctx.intrabars(self.ltf)
            if len(ib):
                for hh, ll in zip(ib.bars.high.tolist(), ib.bars.low.tolist()):
                    if self._step(ctx, hh, ll):
                        return
                if self.tradeActive and self._sl_touched(h, l):
                    self._step(ctx, h, l)  # the 1-minute candles missed the stop the chart candle shows
                return
        self._step(ctx, h, l)

    def _sl_touched(self, h: float, l: float) -> bool:
        return (l <= self.trk['sl']) if self.tradeIsLong else (h >= self.trk['sl'])

    def _new_tp_touched(self, h: float, l: float) -> bool:
        return any(not self.hits[j] and ((h >= self.trk[key]) if self.tradeIsLong else (l <= self.trk[key]))
                   for j, key in enumerate(('tp1', 'tp2', 'tp3')))

    def _step(self, ctx: Context, h: float, l: float) -> bool:
        """One candle (or 1-minute candle) of the running trade; True when the trade ended."""
        k = self.trk
        if self._sl_touched(h, l):
            self._close_trade(ctx, 'sl', k['sl'])
            return True
        for j, key in enumerate(('tp1', 'tp2', 'tp3')):
            if not self.hits[j] and ((h >= k[key]) if self.tradeIsLong else (l <= k[key])):
                self.hits[j] = True
                tag = self.levels.get(f'{key}Lbl') if self.drawing else None
                if tag is not None:  # the running trade's own TP tag gets a tick - no extra badge on the chart
                    tag.text = f'✓ TP{j + 1}  {fmt_price(k[key], ctx.precision)}'
                    tag.tooltip = f'TP{j + 1} reached: +1R banked. The trade keeps running to TP3 or the stop.'
        if self.hits[2]:
            self._close_trade(ctx, 'tp3', k['tp3'])
            return True
        return False

    def _close_trade(self, ctx: Context, outcome: str, price: float) -> None:
        n = sum(self.hits)
        r = n - (1 if outcome == 'sl' else 0)
        k = self.trk
        self.closed.append(dict(open_time=self.trkOpenTime, close_time=ctx.time, long=self.tradeIsLong, hits=n,
                                outcome=outcome, r=r, entry=k['entry'], sl=k['sl'], tps=[k['tp1'], k['tp2'], k['tp3']]))
        self.tradeActive = False
        if not self.drawing:
            return
        self._extend_levels(ctx.time)  # the plan stops where the trade ended
        # one small marker where the trade ended, no text: the details are in its hover card
        side = 'Long' if self.tradeIsLong else 'Short'
        if outcome == 'tp3':
            self._exit_marker(ctx, price, 'diamond', GREEN, f'Win · {side} · {r:+d}R',
                              f'All three targets reached: +{r}R. The next signal can open a trade.')
        else:
            self._exit_marker(ctx, price, 'xcross', RED if r < 0 else AMBER, f'Loss · {side} · {r:+d}R',
                              'Stop hit: -1R' + (f' after {n} TP{"s" if n > 1 else ""} (+{n}R)' if n else '')
                              + f' = {r:+d}R. The next signal can open a trade.')

    def _exit_marker(self, ctx: Context, y: float, shape: str, col: str, title: str, tip: str) -> None:
        ctx.label(x=ctx.time, y=y, text='', style=shape, color=col, text_color=col, size='tiny', group='results',
                  tooltip_title=title, tooltip=tip)

    def _extend_levels(self, t2: int) -> None:
        for k, obj in self.levels.items():
            if k.endswith('Lbl'):
                obj.x = t2        # price tags move to the right end
            elif k.endswith('Fill'):
                obj.right = t2    # risk / reward zones widen
            else:
                obj.x2 = t2       # dashed level lines lengthen

    # -----------------------------------------------------------------------------------------------------------------
    #  on_last_bar(): runs once after the newest bar. Builds the dashboard panel (rendered by the chart's
    #  ConfluencePanel): the three engine votes, market gauges, the running trade and the recorded results.
    #  The dashboard is informational only - it does not influence any signal.
    # -----------------------------------------------------------------------------------------------------------------
    def on_last_bar(self, ctx: Context) -> None:
        self.panel = None
        p = self.p
        if not p['showDash'] or not self.state:
            return
        st, S, w = self.state, self.since, p['confluenceWindow']
        pos = {'Top Left': 'top_left', 'Bottom Right': 'bottom_right', 'Bottom Left': 'bottom_left'}.get(
            p['dashPos'], 'top_right')
        age = lambda n: n if n < 9999 else None  # noqa: E731 - bars since the vote, None = never
        engines = [
            dict(key='cardwell', name='Cardwell RSI', bull=age(S['b1']), bear=age(S['s1']), tip=TABLE_TIPS['Cardwell']),
            dict(key='arsi', name='Adaptive RSI', bull=age(S['b2']), bear=age(S['s2']), tip=TABLE_TIPS['Adaptive ARSI']),
            dict(key='structure', name='Structure', bull=age(S['b3']), bear=age(S['s3']), tip=TABLE_TIPS['Structure'],
                 trend=self.trend3),
        ]
        trade = None
        if self.tradeActive:
            k = self.trk
            risk = abs(k['entry'] - k['sl'])
            live = ((st['close'] - k['entry']) if self.tradeIsLong else (k['entry'] - st['close'])) / risk \
                if risk > 0 else NAN
            trade = dict(long=self.tradeIsLong, entry=k['entry'], sl=k['sl'], tps=[k['tp1'], k['tp2'], k['tp3']],
                         hits=list(self.hits), live_r=live if finite(live) else None, open_time=self.trkOpenTime,
                         bars=ctx.bar_index - self.trkStartBar)
        # results: every finished trade of the loaded history (in replay: since the replay started)
        since = ctx.replay_start
        rr = (p['rr1'], p['rr2'], p['rr3'])
        stats = summarize([t for t in self.closed if since is None or t['open_time'] >= since], 0, 2 ** 62, rr)
        stats.update(skipped=self.skipped, since=since, first=self.closed[0]['open_time'] if self.closed else None,
                     recent=[dict(long=t['long'], hits=t['hits'], outcome=t['outcome'], r=t['r'],
                                  time=t['close_time']) for t in self.closed[-12:]][::-1])
        htf = ('bull' if st['htfBull'] else 'bear') if p['useHtfFilter'] else None
        self.panel = dict(
            kind='triple_confluence', id='triple_confluence', position=pos, window=w, engines=engines,
            market=dict(rsi=st['rsi'] if finite(st['rsi']) else None, adx=st['adx'] if finite(st['adx']) else None,
                        adx_min=p['adxMinLevel'], adx_filter=p['useChopFilter'], htf=htf),
            trade=trade, stats=stats, precision=ctx.precision, entry_mode=p['entryMode'],
            tips=TABLE_TIPS, score=OBJECTIVES,
        )

    # Hands the dashboard panel to the frontend together with the normal drawings.
    def extra_output(self) -> dict:
        panel = getattr(self, 'panel', None)
        return dict(panels=[panel] if panel else [])
