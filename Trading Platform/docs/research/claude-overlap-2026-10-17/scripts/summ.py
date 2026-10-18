import json, sys, math, collections, datetime as dt
import numpy as np
RUNS='/Users/kimkheangkhorn/Desktop/Trading Platform/docs/research/claude-overlap-2026-10-17/runs/'
def load(name): return json.load(open(RUNS+name+'.json'))['rows']
def yr(ts): return dt.datetime.utcfromtimestamp(ts).year
def stats(tr):
    if not tr: return dict(n=0)
    R=np.array([t['R'] for t in tr]); w=np.array([t['win'] for t in tr])
    eq=np.cumsum(R); dd=float(np.max(np.maximum.accumulate(np.concatenate([[0],eq]))[1:]-eq)) if len(eq) else 0
    pos=R[R>0].sum(); neg=-R[R<0].sum()
    return dict(n=len(tr), wr=round(w.mean()*100,1), R=round(R.sum(),1), avg=round(R.mean(),3),
        t=round(R.mean()/R.std(ddof=1)*math.sqrt(len(R)),2) if len(R)>2 else 0, pf=round(pos/neg,2) if neg else None, dd=round(dd,1),
        tp1=int(sum(t['tps_hit']>=1 for t in tr)), tp2=int(sum(t['tps_hit']>=2 for t in tr)), tp3=int(sum(t['tps_hit']>=3 for t in tr)))
if __name__=='__main__':
    rows=load(sys.argv[1]); tr=[r for r in rows if 'R' in r]
    print('fates', collections.Counter(r['fate'] for r in rows))
    print('ALL', stats(tr))
    dev=[t for t in tr if t['entry_time']<1672531200]; ho=[t for t in tr if t['entry_time']>=1672531200]
    print('DEV', stats(dev)); print('HOLD', stats(ho))
    for y in range(2015,2027): print(y, stats([t for t in tr if yr(t['entry_time'])==y]))
    print('ntps', collections.Counter(len(t['tps']) for t in tr), 'ambig', sum(t['ambiguous'] for t in tr))
