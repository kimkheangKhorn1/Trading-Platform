import numpy as np, json, sys, collections
from summ import load
m1t=np.load('m1t.npy'); m1=np.load('m1.npy'); spr=np.load('spr.npy')
def approach(r):
    a=int(np.searchsorted(m1t,r['placed'])); b=int(np.searchsorted(m1t,r['end']))
    if b<=a: return None
    s=r['side']; L=r['limit']
    if s==1: p=m1[a:b,2]+spr[a:b]; gap=p-L
    else: p=m1[a:b,1]; gap=L-p
    k=int(np.argmin(gap)); return float(gap[k]), a+k
def after(r, k0, horizon=60*24*20):
    """From minute k0: which comes first, TP1 or SL (or the limit itself being filled later is ignored)."""
    s=r['side']; b=min(len(m1t),k0+horizon)
    h=m1[k0+1:b,1]; l=m1[k0+1:b,2]; sp=spr[k0+1:b]
    tp=r['tps'][0]; sl=r['sl']
    if s==1: thit=np.nonzero(h>=tp)[0]; shit=np.nonzero(l<=sl)[0]
    else: thit=np.nonzero(l+sp<=tp)[0]; shit=np.nonzero(h+sp>=sl)[0]
    ti=thit[0] if len(thit) else 1e18; si=shit[0] if len(shit) else 1e18
    return 'tp1' if ti<si else ('sl' if si<ti else 'none')
if __name__=='__main__':
    rows=load(sys.argv[1]); out=[]
    for r in rows:
        if r['fate'] not in ('withdrawn','invalidated'): continue
        x=approach(r)
        if x is None: continue
        gap,k=x; R0=abs(r['limit']-r['sl'])
        out.append(dict(id=r['id'], side=r['side'], gap=gap, gapR=gap/R0, gapATR=gap/r['atr'], k=k, fate=r['fate'],
            limit=r['limit'], placed=r['placed'], end=r['end'], res=after(r,k) if gap>0 else 'touched', life_h=(r['end']-r['placed'])/3600))
    json.dump(out, open(sys.argv[1]+'_missed.json','w'))
    g=np.array([o['gapATR'] for o in out])
    print('unfilled', len(out), 'touched-but-unfilled', (g<=0).sum())
    for lo,hi in [(0,.1),(.1,.25),(.25,.5),(.5,1),(1,2),(2,99)]:
        sel=[o for o in out if lo<o['gapATR']<=hi]
        c=collections.Counter(o['res'] for o in sel); n=len(sel)
        print(f'gap {lo}-{hi} ATR: n={n} tp1-first={c["tp1"]} sl-first={c["sl"]} none={c["none"]}  P(tp1)={c["tp1"]/max(1,c["tp1"]+c["sl"]):.2f}')
