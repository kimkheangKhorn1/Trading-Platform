import numpy as np, json
from summ import load, stats
m1t=np.load('m1t.npy'); m1=np.load('m1.npy'); spr=np.load('spr.npy')
H=60*24*40
def excursion(side, entry, sl, t0, horizon=H):
    """M1 path from the fill minute. Returns dict: mfe_R before SL, sl_idx(minutes), mfe after SL in 48h, mae."""
    a=int(np.searchsorted(m1t,t0)); b=min(len(m1t),a+horizon)
    o,h,l,c=m1[a:b,0],m1[a:b,1],m1[a:b,2],m1[a:b,3]; s=spr[a:b]
    risk=abs(entry-sl)
    if side==1:
        fav=h.copy(); adv=l.copy(); fav[0]=c[0]
        hit=np.nonzero(adv<=sl)[0]
        favR=(fav-entry)/risk
    else:
        fav=l+s; adv=h+s; fav[0]=c[0]+s[0]
        hit=np.nonzero(adv>=sl)[0]
        favR=(entry-fav)/risk
    k=int(hit[0]) if len(hit) else len(favR)
    pre=favR[:k] if k>0 else np.array([-0.0])
    mfe=float(pre.max()) if len(pre) else 0.0
    # first time reaching each level before SL
    post=favR[k:k+2880] if k<len(favR) else np.array([])
    return dict(mfe=mfe, sl_min=k if len(hit) else None, cum=np.maximum.accumulate(pre), post_mfe=float(post.max()) if len(post) else None)
if __name__=='__main__':
    import sys
    rows=load(sys.argv[1]); tr=[r for r in rows if 'R' in r]
    out=[]
    for t in tr:
        e=excursion(t['side'], t['entry'], t['sl'], t['entry_time'])
        tp1R=abs(t['tps'][0]-t['entry'])/t['risk']
        out.append(dict(id=t['id'], mfe=e['mfe'], sl_min=e['sl_min'], post=e['post_mfe'], tp1R=tp1R, win=t['win'], R=t['R'], risk=t['risk'], atr=t['atr']))
    json.dump(out, open(sys.argv[1]+'_path.json','w'))
    mfe=np.array([x['mfe'] for x in out])
    print('P(MFE>=x before SL):')
    for x in [0.1,0.2,0.3,0.4,0.5,0.6,0.75,1,1.25,1.5,2,2.5,3,4,5]:
        p=(mfe>=x).mean(); print(f'  {x:>4}R  {p*100:5.1f}%   random-walk {100/(1+x):5.1f}%   EV of fixed x-target {p*x-(1-p):+.3f}R')
    tp1=np.array([x['tp1R'] for x in out]); print('TP1 R quantiles', np.percentile(tp1,[10,25,50,75,90]).round(2))
    risk=np.array([x['risk'] for x in out]); atr=np.array([x['atr'] for x in out]); print('risk/ATR quantiles', np.percentile(risk/atr,[10,25,50,75,90]).round(2))
    los=[x for x in out if not x['win']]
    frac=np.array([x['mfe']/x['tp1R'] for x in los])
    print('losers', len(los), 'reached >=50% of TP1 dist', (frac>=.5).mean().round(3), '>=75%', (frac>=.75).mean().round(3), '>=90%', (frac>=.9).mean().round(3))
    print('losers MFE R quantiles', np.percentile([x['mfe'] for x in los],[25,50,75,90]).round(2))
    near=[x for x in los if x['mfe']/x['tp1R']>=.75]
    print('near-miss $ gap to TP1', sorted(round((x['tp1R']-x['mfe'])*x['risk'],2) for x in near))
