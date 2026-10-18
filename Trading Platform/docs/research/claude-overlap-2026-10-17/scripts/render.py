import json, sys, datetime as dt, numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from summ import load
OUT='/Users/kimkheangkhorn/Desktop/Trading Platform/docs/research/claude-overlap-2026-10-17/charts/'
m1t=np.load('m1t.npy'); m1=np.load('m1.npy'); h1=np.load('h1.npy')
h1t=h1[:,0].astype(np.int64)
sma=np.full(len(h1),np.nan); c=np.cumsum(np.concatenate([[0],h1[:,4]])); sma[46:]=(c[47:]-c[:-47])/47
def bars(t0,t1,step):
    a=np.searchsorted(m1t,t0); b=np.searchsorted(m1t,t1)
    t=m1t[a:b]; x=m1[a:b]
    if len(t)==0: return np.zeros((0,5))
    key=t-t%step; u,idx=np.unique(key,return_index=True); out=[]
    ends=list(idx[1:])+[len(t)]
    for k,(s,e) in enumerate(zip(idx,ends)):
        out.append((u[k],x[s,0],x[s:e,1].max(),x[s:e,2].min(),x[e-1,3]))
    return np.array(out)
def candles(ax,B,w):
    for t,o,h,l,c in B:
        col='#26a69a' if c>=o else '#ef5350'
        ax.plot([t,t],[l,h],color=col,lw=.6,zorder=2)
        ax.add_patch(Rectangle((t-w*.4,min(o,c)),w*.8,max(abs(c-o),1e-6),color=col,zorder=3))
def fmt(ts): return dt.datetime.fromtimestamp(ts,dt.UTC).strftime('%Y-%m-%d %H:%M')
def levels(ax,r,x0,x1):
    ax.hlines(r['limit'],x0,x1,colors='#2962ff',lw=1.3,label='limit')
    ax.hlines(r['sl'],x0,x1,colors='#d50000',lw=1.3)
    for k,tp in enumerate(r['tps']): ax.hlines(tp,x0,x1,colors='#00a000',lw=1.0+.4*k,linestyles='--')
def draw(r, kind, path=None, miss=None):
    s=r['side']; t_pl=r['placed']
    t_in=r.get('entry_time') or (miss['k_t'] if miss else t_pl)
    t_end=r.get('exit_time') or r['end'] or t_in
    fig,(a1,a2)=plt.subplots(1,2,figsize=(18,7.5),gridspec_kw=dict(width_ratios=[1.15,1]))
    L=max(t_pl-96*3600,0); R=min(max(t_end,t_in)+36*3600, t_pl+240*3600)
    i0,i1=np.searchsorted(h1t,L),np.searchsorted(h1t,R)
    B=h1[i0:i1]; candles(a1,B,3600)
    a1.plot(h1t[i0:i1],sma[i0:i1],color='#ff9800',lw=1,label='SMA47 1H')
    for tf,top,bot,origin,born in r['members']:
        inc = bot< r['core_top'] and top> r['core_bottom']
        a1.add_patch(Rectangle((origin,bot),max(t_in,t_pl)-origin,top-bot,fill=True,alpha=.10 if inc else .05,
            color='green' if s==1 else 'red',zorder=1))
        a1.text(max(origin,L),top,tf,fontsize=7,color='green' if s==1 else 'red',va='bottom',clip_on=True)
    a1.add_patch(Rectangle((t_pl,r['core_bottom']),max(t_in,t_pl)-t_pl+3600,r['core_top']-r['core_bottom'],fill=False,ec='blue',lw=1.5,zorder=4))
    for cb,ct,ub,ut,st,tfs in r['ahead']:
        a1.add_patch(Rectangle((t_pl,ub),R-t_pl,ut-ub,fill=True,alpha=.07,color='red' if s==1 else 'green',zorder=1))
        a1.add_patch(Rectangle((t_pl,cb),R-t_pl,ct-cb,fill=False,ec='red' if s==1 else 'green',lw=.8,ls=':',zorder=1))
        a1.text(R,ct,f' x{st} {tfs}',fontsize=7,va='center',color='red' if s==1 else 'green',clip_on=True)
    levels(a1,r,t_pl,t_end)
    a1.axvline(t_pl,color='gray',lw=.6,ls=':')
    if r.get('entry_time'): a1.plot(r['entry_time'],r['entry'],marker='^' if s==1 else 'v',ms=11,color='blue',zorder=6)
    for kd,ts,px in r.get('fills',[]): a1.plot(ts,px,marker='X',ms=9,color='#00a000' if kd.startswith('TP') else '#d50000',zorder=6)
    lo=min(B[:,3].min(),r['sl']); hi=max(B[:,2].max(),r['tps'][0] if s==1 else r['limit'])
    if s==-1: lo=min(lo, r['tps'][0]); hi=max(hi,r['sl'])
    pad=(hi-lo)*.05; a1.set_ylim(lo-pad,hi+pad); a1.set_xlim(L,R+ (R-L)*.08)
    ticks=np.linspace(L,R,6); a1.set_xticks(ticks); a1.set_xticklabels([fmt(x)[5:] for x in ticks],fontsize=7)
    a1.set_title('1H context  (blue box = entry core; dotted = opposite groups ahead at placement)',fontsize=9)
    # zoom
    if miss:
        z0,z1,step=miss['k_t']-24*3600, miss['k_t']+48*3600, 900
    else:
        z0=t_in-8*3600; z1=min(max(t_end,t_in), t_in+72*3600)+4*3600; step=300 if z1-z0<36*3600 else 900
    Z=bars(z0,z1,step); candles(a2,Z,step); levels(a2,r,z0,z1)
    if r.get('entry_time'): a2.plot(r['entry_time'],r['entry'],marker='^' if s==1 else 'v',ms=11,color='blue',zorder=6)
    for kd,ts,px in r.get('fills',[]):
        a2.plot(ts,px,marker='X',ms=9,color='#00a000' if kd.startswith('TP') else '#d50000',zorder=6); a2.annotate(kd,(ts,px),fontsize=8,annotation_clip=True)
    if miss: a2.plot(miss['k_t'],r['limit']+s*miss['gap'],marker='o',ms=9,mfc='none',mec='purple',zorder=6)
    for cb,ct,ub,ut,st,tfs in r['ahead'][:3]:
        a2.axhspan(cb,ct,color='red' if s==1 else 'green',alpha=.08)
    a2.axhspan(r['core_bottom'],r['core_top'],color='blue',alpha=.08)
    zl=min(Z[:,3].min(),r['sl']) if len(Z) else r['sl']; zh=max(Z[:,2].max(),r['tps'][0]) if len(Z) else r['tps'][0]
    if s==-1 and len(Z): zl=min(Z[:,3].min(),r['tps'][0]); zh=max(Z[:,2].max(),r['sl'])
    pz=(zh-zl)*.05; a2.set_ylim(zl-pz,zh+pz)
    ticks=np.linspace(z0,z1,6); a2.set_xticks(ticks); a2.set_xticklabels([fmt(x)[5:] for x in ticks],fontsize=7)
    a2.set_title(f'{"M5" if step==300 else "M15"} zoom (purple ring = closest approach of unfilled order)',fontsize=9)
    risk=abs(r['limit']-r['sl'])
    head=(f"#{r['id']} [{kind}] {'BUY' if s==1 else 'SELL'} x{r['strength']} {'/'.join(r['tfs'])}  placed {fmt(t_pl)}  limit {r['limit']:.2f} SL {r['sl']:.2f} "
          f"TPs {', '.join(f'{x:.2f}' for x in r['tps'])}  risk {risk:.2f} ({risk/r['atr']:.2f} ATR)  TP1 {abs(r['tps'][0]-r['limit'])/risk:.2f}R")
    if 'R' in r: head+=f"\nfilled {fmt(r['entry_time'])} at {r['entry']:.2f}; exits: "+'; '.join(f"{k} {fmt(ts)[5:]} @{px:.2f}" for k,ts,px in r['fills'])+f"   realized {r['R']:+.2f}R"
    if path: head+=f"   MFE before SL {path['mfe']:.2f}R; 48h after SL max {path['post'] if path['post'] is None else round(path['post'],2)}R"
    if miss: head+=f"\nnot filled ({r['fate']} {fmt(r['end'])}); closest {miss['gap']:.2f} ({miss['gapATR']:.2f} ATR) away at {fmt(miss['k_t'])}; afterwards {miss['res']} first"
    fig.suptitle(head,fontsize=9,ha='left',x=.01); fig.tight_layout(rect=(0,0,1,.92))
    fig.savefig(OUT+f"{kind}_{r['id']}.png",dpi=72); plt.close(fig)
if __name__=='__main__':
    import os; os.makedirs(OUT,exist_ok=True)
    rows={r['id']:r for r in load('baseline')}; S=json.load(open('sample.json'))
    path={p['id']:p for p in json.load(open('baseline_path.json'))}
    miss={m['id']:m for m in json.load(open('baseline_missed.json'))}
    for m in miss.values(): m['k_t']=int(m1t[m['k']])
    for kind in ('win','near','loss','missed'):
        for i in S[kind]: draw(rows[i],kind,path.get(i),miss.get(i) if kind=='missed' else None)
    print('ok')
