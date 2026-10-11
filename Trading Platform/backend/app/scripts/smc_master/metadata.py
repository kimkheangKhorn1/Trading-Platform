# MPL-2.0. Settings extracted from the preserved Pine source by tools/extract_pine_inputs.py.
import json
from pathlib import Path
from ...engine.inputs import DrawingGroup, InputSpec, PlotSpec, ScriptMeta, ShapeSpec, StrategySpec

# help.json (hand-written: plain explanation + example per settings row) is kept apart from the extracted inputs.json
HELP=json.loads(Path(__file__).with_name('help.json').read_text())
# Defaults that differ from the Pine source (inputs.json stays a faithful extraction). Each entry says why.
TUNED_DEFAULTS={
    # 'Moving' relocates the BULB label to later bars while RSI stays extreme: history then shows the exact turn,
    # which was not knowable at the time (repainting). 'Fixed' prints once, on the confirmed RSI threshold exit.
    'bulbMode':'Fixed (confirmed)',
    # Measured on Dukascopy XAUUSD 1H/4H: first return to a zone, then 1 ATR bounce before a close through it, versus
    # the same zone moved to arbitrary nearby prices. Chosen on 2025, confirmed on Jan-Sep 2026 (both timeframes):
    # pivot S/R zones 1 ATR wide (was 0.5): edge over random levels +5/+11 -> +19/+21 pts (2025), +10/+15 -> +19/+25 (2026).
    'mult':1.0,
    # demand/supply: 1.5 ATR departure (was 2.0): edge +5/+5 -> +12/+10 (2025), +2/+5 -> +11/+11 (2026), ~1.8x the zones.
    'ds_min_atr':1.5,
    # Reaction-from-S/R triangles and TSI curls scored ~0 against the same direction at random bars on 1H/4H 2025 and
    # 2026 and on 5m/15m Jan and Aug-Oct 2026 (e.g. 5m Aug-Oct: -0.7/+0.8 and -0.5/-3.3 pts over ~1,700 signals), at
    # about 20 per day on 5m. Off by default as noise; nothing else depends on them and they can be switched back on.
    'supPush':False,
    'resPush':False,
    'curl':False,
}
INPUTS=[InputSpec(**{**s, 'options':tuple(s['options']) if s.get('options') is not None else None,
                     'title':HELP.get(s['id'],{}).get('label',s['title']),
                     'help':HELP.get(s['id'],{}).get('about'), 'example':HELP.get(s['id'],{}).get('example')})
        for s in ({**s,'default':TUNED_DEFAULTS.get(s['id'],s['default']),
                   'replaced_defaults':(s['default'],) if s['id'] in TUNED_DEFAULTS else None}
                  for s in json.loads(Path(__file__).with_name('inputs.json').read_text()))]
SHAPES=[
    ('breakout','Pivot breakout','arrowup','belowbar','upCol'),
    ('breakdown','Pivot breakdown','arrowdown','abovebar','dnCol'),
    ('resistance_break','Resistance break','arrowup','belowbar','supCol'),
    ('support_break','Support break','arrowdown','abovebar','resCol'),
    ('false_breakdown','False breakdown / bear trap','arrowup','belowbar','fBull'),
    ('false_breakup','False breakup / bull trap','arrowdown','abovebar','fBear'),
    ('support_reaction','Reaction from pivot support','triangleup','belowbar','moveBullCol'),
    ('resistance_reaction','Reaction from pivot resistance','triangledown','abovebar','moveBearCol'),
    ('tsi_bull','Bullish TSI curl','triangleup','belowbar','curlBullCol'),
    ('tsi_bear','Bearish TSI curl','triangledown','abovebar','curlBearCol'),
]
DEFAULTS={x.id:x.default for x in INPUTS}
STRATEGY_ID='smc_master'
META=ScriptMeta(id='smc_ict_scalping_master',name='SMC & ICT & Indicators + Scalping Master',short_name='SMC & ICT & Indicators + Scalping Master',category='Custom',
    description='Python port of the supplied Pine indicator; separate structure, OB, Demand/Supply, pivot S/R, FVG, dashboard, BULB and ATR systems. Indicator and strategy: trade signals come from SmcMaster.strategy().',
    inputs=INPUTS,
    plots=[PlotSpec('sma','Simple Moving Average','#2962FFFF'),PlotSpec('upper','High Envelope','#FF5252FF'),PlotSpec('lower','Low Envelope','#4CAF50FF')],
    shapes=[ShapeSpec(s,t,sh,loc,DEFAULTS[col]) for s,t,sh,loc,col in SHAPES],
    # The script is both the chart indicator and the Strategy Tester strategy. Signals are emitted under this id.
    strategies=[StrategySpec(STRATEGY_ID,'SMC Master','Trades the signals emitted by SmcMaster.strategy() (none until its entry rules are added).')],
    drawing_groups=[DrawingGroup(k,t) for k,t in [('internal_structure','Internal BOS / CHoCH'),('swing_structure','Swing BOS / CHoCH'),('swing_points','HH / LH / HL / LL'),('strong_weak','Strong / Weak High / Low'),('internal_ob','SMC Internal Order Blocks'),('swing_ob','SMC Swing Order Blocks'),('liquidity','EQH / EQL liquidity levels'),('fvg','Fair Value Gaps'),('mtf','Previous D / W / M levels'),('premium_discount','Premium / Equilibrium / Discount'),('demand_supply','Demand / Supply'),('pivot_sr','Pivot Support / Resistance'),('patterns','Candle patterns'),('bulb','BULB reversal extremes')]])
