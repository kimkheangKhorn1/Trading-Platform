"""Frozen chart-review sample: Development period only (entries/approaches before 2023-01-01), seed 20261017."""
import json, random
from summ import load
DEV_END=1672531200
rows={r['id']:r for r in load('baseline')}
path={p['id']:p for p in json.load(open('baseline_path.json'))}
miss=json.load(open('baseline_missed.json'))
rng=random.Random(20261017)
tr=[r for r in rows.values() if 'R' in r and r['entry_time']<DEV_END]
win=[r['id'] for r in tr if r['win']]
near=[r['id'] for r in tr if not r['win'] and path[r['id']]['mfe']>=0.6*path[r['id']]['tp1R']]
los=[r['id'] for r in tr if not r['win'] and r['id'] not in near]
mis=[m['id'] for m in miss if m['placed']<DEV_END and 0<m['gapATR']<=0.25]
pick=lambda xs,n: sorted(rng.sample(xs,min(n,len(xs))))
S=dict(rule=dict(period='entry or order placed before 2023-01-01 UTC', seed=20261017,
  win='TP1 reached; random 15', near_tp_loss='no TP hit, MFE before SL >= 60% of entry-to-TP1 distance; random 10',
  loss='other losses; random 15', missed='unfilled order whose closest approach was within 0.25 ATR(1H) of the limit; random 10'),
  pools=dict(win=len(win), near=len(near), loss=len(los), missed=len(mis)),
  win=pick(win,15), near=pick(near,10), loss=pick(los,15), missed=pick(mis,10))
json.dump(S, open('sample.json','w'), indent=1); print(S['pools'], {k:len(S[k]) for k in ('win','near','loss','missed')})
