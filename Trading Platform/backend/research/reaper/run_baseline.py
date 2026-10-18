"""Baseline: the original Liquidity Reaper rules (ATR x1.5 stop, single exit at TP3 = 3R) on every timeframe."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.reaper.data import TFS, TRAIN, VALID, TEST, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import StopRule, in_window, one_at_a_time, standalone, summarize

OUT = Path(__file__).with_name('out'); OUT.mkdir(exist_ok=True)
res = {}
for tf in TFS:
    t0 = time.time()
    m = market(tf)
    sigs = generate(m, GenParams())
    sim = Simulator(m)
    trades = standalone(sim, sigs, StopRule('atr', 1.5), EXITS['single_3R'])
    rows = one_at_a_time(sigs, trades, lambda s: True, m.first)
    times = m.bid.time
    res[tf] = {name: summarize(in_window(rows, times, w)) for name, w in
               (('train', TRAIN), ('valid', VALID), ('test', TEST), ('all', (TRAIN[0], TEST[1])))}
    a = res[tf]['all']
    print(f"{tf:4} {time.time()-t0:5.1f}s signals {len(sigs):6} trades {a['trades']:5} win {a['win_rate']:.1%} "
          f"netR {a['net_r']:8.1f} avgR {a['avg_r']:+.3f} PF {a['pf']:.2f} maxDD {a['max_dd_r']:.1f}R", flush=True)
(OUT / 'baseline.json').write_text(json.dumps(res, indent=1))
