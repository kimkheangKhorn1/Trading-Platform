"""Reproduce the user's exact legacy counts before changing any signal logic."""
from __future__ import annotations
import csv, hashlib, json, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.reaper.data import TFS, TEST, market
from research.reaper.events import GenParams, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import StopRule, in_window, one_at_a_time, standalone, summarize
OUT=Path(__file__).with_name('out')
EXPECTED=dict(zip(TFS,(2832,622,227,114,49,13,4)))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def run():
    result={'method':'Legacy reproduction, unchanged original event generator and simulator. Attribution by signal-bar open; continuous positions from 2024. Not a strict fold-isolated evaluation.', 'expected':EXPECTED,'timeframes':{},'source_sha256':{}}
    root=Path(__file__).resolve().parents[2]
    for rel in ('app/scripts/liquidity_reaper.py','research/reaper/events.py','research/reaper/sim.py','research/reaper/study.py','research/reaper/data.py'):
        result['source_sha256'][rel]=digest(root/rel)
    for tf in TFS:
        start=time.monotonic(); m=market(tf); sigs=generate(m,GenParams()); sim=Simulator(m)
        trades=standalone(sim,sigs,StopRule('atr',1.5),EXITS['single_3R'])
        rows=one_at_a_time(sigs,trades,lambda s:True,m.first)
        test=in_window(rows,m.bid.time,TEST)
        stats=summarize(test)
        result['timeframes'][tf]={'stats':stats,'expected':EXPECTED[tf],'matches':stats['trades']==EXPECTED[tf],
            'data_last_open':int(m.bid.time[-1]),'exits_at_or_after_cutoff':sum(int(m.bid.time[t.exit_bar])>=TEST[1] for s,t in test),
            'signal_count_2026':sum(TEST[0]<=int(m.bid.time[s.i])<TEST[1] for s in sigs)}
        with (OUT/f'baseline_{tf}_trades.csv').open('w') as f:
            w=csv.writer(f); w.writerow(['signal_open_utc_epoch','side','entry_open_utc_epoch','exit_bar_open_utc_epoch','entry','stop','risk','net_r','outcome'])
            w.writerows([int(m.bid.time[s.i]),s.side,int(m.bid.time[t.entry_bar]),int(m.bid.time[t.exit_bar]),t.entry,t.stop,t.risk,t.r,t.outcome] for s,t in test)
        print(tf,stats['trades'],'expected',EXPECTED[tf], 'netR',round(stats['net_r'],3),'seconds',round(time.monotonic()-start,1),flush=True)
        (OUT/'baseline_reproduced.json').write_text(json.dumps(result,indent=2))
        market.cache_clear()
    assert all(x['matches'] for x in result['timeframes'].values()), 'Baseline differs: investigate before tuning'
if __name__=='__main__': run()
