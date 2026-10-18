"""One fixed recipe (the M1 selection) on every timeframe, no per-timeframe tuning."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.reaper.data import TEST, TRAIN, VALID, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import in_window, one_at_a_time, summarize
from research.reaper import tune as T

cfg = json.loads((T.OUT / 'tune_M1.json').read_text())['best']['config']
out = {}
for tf in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D'):
    m = market(tf); times = m.bid.time
    g = GenParams(pivot_len=cfg['pivot_len'], confirm=cfg['confirm'], use_vol=cfg['use_vol'])
    sigs = generate(m, g); sim = Simulator(m)
    tr = T.trades_for(sim, sigs, cfg['stop'], cfg['entry'], cfg['plan'])
    rows = one_at_a_time(sigs, tr, T.keep_fn(cfg['trend_filter'], cfg['session_filter'], cfg['spread_guard'], cfg['stop'],
                                             cfg['entry']), m.first)
    p = [summarize(in_window(rows, times, w)) for w in (TRAIN, VALID, TEST)]
    out[tf] = p
    print(f"{tf:4} train {p[0]['trades']:4} {p[0]['avg_r']:+.3f} | valid {p[1]['trades']:4} {p[1]['avg_r']:+.3f} | "
          f"test {p[2]['trades']:4} {p[2]['avg_r']:+.3f} net {p[2]['net_r']:+6.1f}R win {p[2]['win_rate']:.0%}", flush=True)
(T.OUT / 'recipe.json').write_text(json.dumps(dict(config=cfg, results=out)))
