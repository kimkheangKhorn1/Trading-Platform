# MPL-2.0. Settings extracted from the preserved Pine source by tools/extract_pine_inputs.py.
import json
from pathlib import Path
from ...engine.inputs import DrawingGroup, InputSpec, PlotSpec, ScriptMeta, ShapeSpec

INPUTS=[InputSpec(**{**s, 'options':tuple(s['options']) if s.get('options') is not None else None}) for s in json.loads(Path(__file__).with_name('inputs.json').read_text())]
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
META=ScriptMeta(id='smc_ict_scalping_master',name='SMC & ICT & Indicators + Scalping Master',short_name='SMC & ICT & Indicators + Scalping Master',category='Custom',
    description='Python port of the supplied Pine indicator; separate structure, OB, Demand/Supply, pivot S/R, FVG, dashboard, BULB and ATR systems. Indicator only: no trade orders.',
    inputs=INPUTS,
    plots=[PlotSpec('sma','Simple Moving Average','#2962FFFF'),PlotSpec('upper','High Envelope','#FF5252FF'),PlotSpec('lower','Low Envelope','#4CAF50FF')],
    shapes=[ShapeSpec(s,t,sh,loc,DEFAULTS[col]) for s,t,sh,loc,col in SHAPES],
    drawing_groups=[DrawingGroup(k,t) for k,t in [('internal_structure','Internal BOS / CHoCH'),('swing_structure','Swing BOS / CHoCH'),('swing_points','HH / LH / HL / LL'),('strong_weak','Strong / Weak High / Low'),('internal_ob','SMC Internal Order Blocks'),('swing_ob','SMC Swing Order Blocks'),('liquidity','EQH / EQL liquidity levels'),('fvg','Fair Value Gaps'),('mtf','Previous D / W / M levels'),('premium_discount','Premium / Equilibrium / Discount'),('demand_supply','Demand / Supply'),('pivot_sr','Pivot Support / Resistance'),('patterns','Candle patterns'),('bulb','BULB reversal extremes')]])
