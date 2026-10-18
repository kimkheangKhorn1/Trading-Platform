# This file is subject to the Mozilla Public License 2.0, https://mozilla.org/MPL/2.0/.
# Port of the user's SMC & ICT & Indicators + Scalping Master. Pine source remains authoritative.
from __future__ import annotations
import math
import copy
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import numpy as np
from ...engine.script import Script, Context
from ...engine.ta import ATR
from ...engine.pine import new_color
from ...market.timeframes import get_timeframe, bucket_start
from .metadata import META, SHAPES, STRATEGY_ID
from .technical import EMA, Mean, RSI, TSI, DMI, SAR, pivot, div, NAN
from .patterns import pattern_formulas, PATTERN_PRIORITY, BEARISH_CODES

PINE_TF={'1':'M1','5':'M5','15':'M15','30':'M30','60':'H1','240':'H4','D':'D'}
TRANSPARENT='#FFFFFF00'
# Label precedence when one candle matches several patterns: two-candle patterns, then the most specific single ones.
# White text needs a dark enough fill: the former bright green (#00E676) gave 1.7:1; these give 3.6-4.9:1.
DASH_BUY,DASH_SELL,DASH_WAIT='#089981FF','#F23645FF','#2962FFFF'

@dataclass
class Zone:
    top: float
    bottom: float
    origin: int
    born: int
    side: int
    kind: str
    state: int=0
    box: object=None
    pending_bar: int|None=None
    pending_dir: int=0
    pending_top: float=NAN
    pending_bottom: float=NAN
    near_time: int|None=None
    identity: int=0
    box_left: int|None=None  # where the current box starts: the pivot, or the last role-flip break (Pine)

class SmcMaster(Script):
    meta=META

    # These lists contain only numbers or immutable OHLCV tuples. Copy the containers so
    # provisional appends roll back, but avoid recursively visiting every historical scalar.
    # All mutable zone state, drawing references and other fields still use the shared deepcopy memo.
    _SCALAR_HISTORY = frozenset(('rows', 'thresholds', 'disp_atrs', 'zone_atrs',
                                 'src_high', 'src_low', 'body_avgs', 'rsi_hist'))

    def __deepcopy__(self, memo):
        clone = type(self).__new__(type(self))
        memo[id(self)] = clone
        for name in self._SCALAR_HISTORY:
            if name in self.__dict__:
                history = self.__dict__[name]
                memo[id(history)] = history.copy()
        clone.__dict__.update((name, copy.deepcopy(value, memo)) for name, value in self.__dict__.items())
        return clone

    def required_warmup(self):
        p=self.p
        return max(1000,3*max(p['macdSlow'],p['fisherLength'],p['vortexLength'],p['mal'],p['atrl'],p['atrLen'],p['rsiLength'],p['rsiLength1'],p['lengthRSI']))

    def setup(self,ctx):
        p=self.p
        self.rows=[]; self.atr200=ATR(200); self.atr14=ATR(14); self.zone_atr=ATR(p['atrLen']); self.env_atr=ATR(p['atrl']); self.ma=Mean(p['mal'])
        self.range_sum=0.; self.thresholds=[]; self.disp_atrs=[]; self.zone_atrs=[]
        self.struct=[dict(os=0,trend=0,top=NAN,bottom=NAN,tx=0,bx=0,top_cross=True,bottom_cross=True) for _ in range(2)]
        self.prev_levels=[(NAN,NAN),(NAN,NAN)]
        self.trail_high=NAN; self.trail_low=NAN; self.trail_hx=0; self.trail_lx=0
        self.obs=[[],[]]; self.fvgs=[]; self.eq_prev=[None,None]; self.latest={}; self.events=[]; self.tables=[]; self.warnings=[]
        self.ds=[[],[]]; self.bases=[None,None]; self.last_direction=[None,None]; self.registered=[None,None]
        self.pivots=[[],[]]; self.retired=[]; self.pattern_boxes=[]; self.next_zone=0
        self.ha_open=NAN; self.ha_close=NAN; self.src_high=[]; self.src_low=[]
        self.pivot_history=[]; self.tsi=TSI(p['shortf'] if p['strat']=='Fast' else p['shorts'],p['longf'] if p['strat']=='Fast' else p['longs'],p['signalf'] if p['strat']=='Fast' else p['signals'])
        self.prev_tsi=NAN; self.body_mean=EMA(14); self.body_avgs=[]
        self.rsi=RSI(p['rsiLength']); self.bulb_rsi=RSI(p['rsiLength1']); self.stoch_rsi=RSI(p['lengthRSI']); self.rsi_hist=[]; self.kmean=Mean(p['smoothK']); self.dmean=Mean(p['smoothD'])
        self.macd_fast=EMA(p['macdFast']); self.macd_slow=EMA(p['macdSlow']); self.macd_signal=EMA(p['macdSignal']); self.dmi=DMI(p['dmiLength'],p['dmiSmooth']); self.sar=SAR(p['psarStart'],p['psarInc'],p['psarMax'])
        self.fish_value=0.; self.fish=0.; self.bulb_state=0; self.bulb_high=NAN; self.bulb_low=NAN; self.bulb_label=[None,None]; self.prev_bulb=NAN
        self.fvg_total=0.; self.fvg_count=0; self.fvg_prev_bucket=None; self.fvg_handles={}; self.htf={}
        selected=PINE_TF.get(p['fvg_tf'],ctx.timeframe.code); self.fvg_tf=get_timeframe(selected); self.fvg_htf=self.fvg_tf.seconds>ctx.timeframe.seconds
        self.fvg_lower=bool(p['fvg_tf'] and not self.fvg_htf and p['fvg_tf']!=ctx.timeframe.pine)
        if self.fvg_htf and p['show_fvg']:
            for key,field,offset in [('c','close',2),('o','open',2),('h','high',1),('l','low',1),('h2','high',3),('l2','low',3),('t','time',2)]:
                self.fvg_handles[key]=ctx.request_security(selected,lambda b,f=field:np.asarray(getattr(b,f),dtype=float),lookahead=True,offset=offset)
        # Previous completed reference candles. Missing data produces na, never invented levels.
        for tf,enabled in [('D',p['show_pdhl']),('W',p['show_pwhl']),('M',p['show_pmhl'])]:
            if not enabled: continue
            self.htf[tf]={field:ctx.request_security(tf,lambda b,f=field:np.asarray(getattr(b,f),dtype=float),lookahead=True,offset=1) for field in ('high','low','time')}

    def emit(self,ctx,title,group,**extra):
        if ctx.is_confirmed:
            self.events.append(dict(time=ctx.time,title=title,group=group,**extra))
            if len(self.events)>1000: del self.events[:-500]  # alertcondition log: recent events only

    def label(self,ctx,x,y,text,col,group,bull=False,size='small',bg=TRANSPARENT,tooltip='',yloc='price',latest=None):
        if latest and self.p['mode']=='Present':
            for obj in self.latest.get(latest,[]): ctx.delete(obj)
        obj=ctx.label(x=x,y=y,text=text,color=bg,text_color=col,style='label_up' if bull else 'label_down',size=size,group=group,tooltip=tooltip or text,yloc=yloc)
        if latest: self.latest[latest]=[obj]
        return obj

    def color(self,bull,internal=False):
        if self.p['style']=='Monochrome': return '#B2B5BEFF'
        return self.p[('swing_i' if internal else 'swing_')+('bull_css' if bull else 'bear_css')]

    def structure(self,ctx,a,length,internal):
        n=ctx.bar_index; p=self.p; s=self.struct[a]; rows=self.rows; prev_os=s['os']
        if n>=length:
            candidate=rows[n-length]
            upper=max(r[2] for r in rows[n-length+1:]); lower=min(r[3] for r in rows[n-length+1:])
            s['os']=0 if candidate[2]>upper else 1 if candidate[3]<lower else s['os']
            for high,changed in [(True,s['os']==0 and prev_os!=0),(False,s['os']==1 and prev_os!=1)]:
                if not changed or not ctx.is_confirmed: continue
                self.leg_extreme(ctx,a,s,not high,n-length,internal)
                key='top' if high else 'bottom'; xkey='tx' if high else 'bx'; y=candidate[2 if high else 3]; old=s[key]
                if not internal:
                    text=('H' if high else 'L') if not math.isfinite(old) else ('HH' if y>old else 'LH') if high else ('LL' if y<old else 'HL')
                    if p['show_swings']: self.label(ctx,candidate[0],y,text,self.color(not high),'swing_points',not high,latest=key+'_swing')
                    if high: self.trail_high=y; self.trail_hx=n-length
                    else: self.trail_low=y; self.trail_lx=n-length
                s[key]=y; s[xkey]=n-length; s[key+'_cross']=True
        if not internal:
            if not math.isfinite(self.trail_high) or ctx.high>=self.trail_high: self.trail_high=ctx.high; self.trail_hx=n
            if not math.isfinite(self.trail_low) or ctx.low<=self.trail_low: self.trail_low=ctx.low; self.trail_lx=n
        prev_close=rows[-2][4] if n else NAN
        oldtop,oldbottom=self.prev_levels[a]
        for bull in (True,False):
            key='top' if bull else 'bottom'; y=s[key]; old_y=oldtop if bull else oldbottom
            beyond=ctx.close>y if bull else ctx.close<y
            if internal:
                larger=self.struct[1][key]
                beyond=beyond and (not math.isfinite(larger) or larger!=y) and (not p['ifilter_confluence'] or (ctx.close-ctx.low>=ctx.high-ctx.close if bull else ctx.high-ctx.close>=ctx.close-ctx.low))
            else: beyond=beyond and (prev_close<=old_y if bull else prev_close>=old_y)
            if not(ctx.is_confirmed and math.isfinite(y) and s[key+'_cross'] and beyond): continue
            choch=s['trend']==(-1 if bull else 1); text='CHoCH' if choch else 'BOS'; group='internal_structure' if internal else 'swing_structure'; col=self.color(bull,internal)
            self.emit(ctx,('Internal ' if internal else '')+('Bullish ' if bull else 'Bearish ')+text,group,price=y)
            enabled=p['show_internals'] if internal else p['show_Structure']; selection=p[('show_i' if internal else 'show_')+('bull' if bull else 'bear')]
            if enabled and selection in ('All',text):
                lk=group+'_display'
                if p['mode']=='Present':
                    for obj in self.latest.get(lk,[]): ctx.delete(obj)
                line=ctx.line(x1=ctx.time_at(s['tx' if bull else 'bx']),y1=y,x2=ctx.time,y2=y,color=col,style='dashed' if internal else 'solid',group=group,tooltip=('Internal ' if internal else 'Swing ')+text)
                label=self.label(ctx,ctx.time_at((s['tx' if bull else 'bx']+n)//2),y,text,col,group,bull=not bull,size='tiny' if internal else 'small')
                self.latest[lk]=[line,label]
            if p['show_iob' if internal else 'show_ob']: self.order_block(ctx,a,s['tx' if bull else 'bx'],bull)
            s[key+'_cross']=False; s['trend']=1 if bull else -1
        self.prev_levels[a]=(s['top'],s['bottom'])

    def leg_extreme(self,ctx,a,s,high,loc,internal):
        """Before a new swing pivot at ``loc`` is stored, make the opposite level the true extreme of its leg.
        Swings alternate high/low, so when price breaks a swing high and keeps rising to a higher high before the next
        swing low confirms, that higher high was never stored: the next break above it printed no BOS, and a wick above
        the old high left the old, lower level as the bullish break line. The leg extreme is known only now (when the
        opposite pivot confirms), so the corrected level is drawn now and a break that already closed beyond it is not
        printed after the fact."""
        key='top' if high else 'bottom'; xkey='tx' if high else 'bx'; old=s[key]; start=s[xkey]
        if not math.isfinite(old) or start>=loc-1: return
        rows=self.rows; best=None
        for j in range(start+1,loc):
            v=rows[j][2 if high else 3]
            if (v>=best[0] if high else v<=best[0]) if best else (v>old if high else v<old): best=(v,j)
        if not best: return
        y,j=best; s[key]=y; s[xkey]=j
        s[key+'_cross']=not any((r[4]>y if high else r[4]<y) for r in rows[j+1:])
        if not internal:
            if self.p['show_swings']: self.label(ctx,rows[j][0],y,'HH' if high else 'LL',self.color(not high),'swing_points',not high,latest=key+'_swing')

    def order_block(self,ctx,a,loc,bull):
        # The whole leg is searched (Pine stopped at 499 bars back, TradingView's history-reference limit).
        p=self.p; n=ctx.bar_index; end=max(n-loc-1,1); end=min(end,n); found=None
        if p['ob_candle']=='Opposite Candle (ICT)':
            displacement=False; close_ext=None
            for off in range(end+1):
                r=self.rows[n-off]; _,o,h,l,c,_=r
                if off>0 and displacement and (c<o if bull else c>o):
                    valid=h-l<self.thresholds[n-off]*2 and (close_ext is None or (close_ext>=l if bull else close_ext<=h))
                    if valid: found=r
                    break
                body=c-o if bull else o-c
                if body>0 and body>=p['ob_disp_mult']*self.disp_atrs[n-off]: displacement=True
                close_ext=c if close_ext is None else min(close_ext,c) if bull else max(close_ext,c)
        else:
            candidates=[self.rows[n-off] for off in range(1,end+1) if self.rows[n-off][2]-self.rows[n-off][3]<self.thresholds[n-off]*2]
            if candidates: found=min(candidates,key=lambda r:r[3]) if bull else max(candidates,key=lambda r:r[2])
        if found:
            self.obs[a].insert(0,Zone(found[2],found[3],found[0],ctx.time,1 if bull else -1,'internal_ob' if a==0 else 'swing_ob')); del self.obs[a][100:]

    def equal_levels(self,ctx,atr):
        if not self.p['show_eq'] or not ctx.is_confirmed: return
        p=self.p; length=p['eq_len']; n=ctx.bar_index
        for a,high in enumerate((True,False)):
            value=pivot([r[2 if high else 3] for r in self.rows[-2*length-1:]],length,length,high)
            if value is None: continue
            old=self.eq_prev[a]; x=ctx.time_at(n-length)  # eq_prev = (time, price, bar index) of the previous pivot
            if old and math.isfinite(atr) and abs(value-old[1])<=max(atr*p['eq_threshold'],ctx.mintick*.5):
                text='EQH' if high else 'EQL'; col=self.color(not high)
                if p['mode']=='Present':
                    for o in self.latest.get(text,[]): ctx.delete(o)
                line=ctx.line(x1=old[0],y1=old[1],x2=x,y2=value,color=col,style='dotted',group='liquidity')
                # midpoint as a bar position: an averaged timestamp can fall between candles and not be placeable
                lab=self.label(ctx,ctx.time_at((old[2]+n-length)//2),value,text,col,'liquidity',not high,'tiny')
                self.latest[text]=[line,lab]; self.emit(ctx,text,'liquidity',price=value)
            self.eq_prev[a]=(x,value,n-length)

    def gaps(self,ctx):
        p=self.p
        if not p['show_fvg']: return
        changed=True
        if self.fvg_htf:
            bucket=bucket_start(ctx.time,self.fvg_tf,ctx._r.alignment); changed=bucket!=self.fvg_prev_bucket; self.fvg_prev_bucket=bucket
            v={k:ctx.security(h) for k,h in self.fvg_handles.items()}
        elif len(self.rows)>=3:
            r,m,l=self.rows[-1],self.rows[-2],self.rows[-3]; v=dict(c=m[4],o=m[1],h=r[2],l=r[3],h2=l[2],l2=l[3],t=m[0])
        else: v=dict(c=NAN,o=NAN,h=NAN,l=NAN,h2=NAN,l2=NAN,t=NAN)
        delta=100*div(v['c']-v['o'],v['o'])
        if changed: self.fvg_total+=abs(delta) if math.isfinite(delta) else 0; self.fvg_count+=1
        threshold=2*self.fvg_total/max(self.fvg_count,1) if p['fvg_auto'] else 0
        if ctx.is_confirmed and changed:
            for bull,cond in [(True,v['l']>v['h2'] and v['c']>v['h2'] and delta>threshold),(False,v['h']<v['l2'] and v['c']<v['l2'] and -delta>threshold)]:
                if cond:
                    self.fvgs.insert(0,Zone(v['l'] if bull else v['l2'],v['h2'] if bull else v['h'],int(v['t']),ctx.time,1 if bull else -1,'fvg'))
                    self.emit(ctx,'Bullish FVG' if bull else 'Bearish FVG','fvg')
        counts={1:0,-1:0}; kept=[]
        for z in self.fvgs:
            if ctx.low<=z.bottom if z.side==1 else ctx.high>=z.top: continue
            counts[z.side]+=1
            if counts[z.side]<=p['fvg_max_active']: kept.append(z)
        self.fvgs=kept

    def demand_supply(self,ctx,atr):
        p=self.p; n=ctx.bar_index; row=self.rows[-1]
        nearpc=100*div(atr,abs(ctx.close))*p['ds_near_atr'] if p['ds_mode']=='ATR' else p['ds_alerts_pc_near']
        lo,hi,cl,conf=ctx.low,ctx.high,ctx.close,ctx.is_confirmed
        for a,bull in enumerate((True,False)):
            for z in self.ds[a]:
                proximal=z.top if bull else z.bottom; hit=lo<=proximal if bull else hi>=proximal
                invalid=conf and (cl<z.bottom if bull else cl>z.top)
                if z.state==0 and hit:
                    z.state=2 if invalid else 1; z.near_time=None
                    if p['dsAlertDemand' if bull else 'dsAlertSupply']: self.emit(ctx,('Demand' if bull else 'Supply')+' Zone hit','demand_supply',origin=z.origin)
                if z.state!=2 and invalid: z.state=2; z.near_time=None
                near=lo<=proximal*(1+nearpc/100) if bull else hi>=proximal*(1-nearpc/100)
                if z.state==0 and near and conf and p['dsAlertDemand' if bull else 'dsAlertSupply']:
                    now=ctx.time+ctx.timeframe.seconds; cooldown=p['ds_alert_mins_no_repeat']*60
                    if z.near_time is None or now-z.near_time>=cooldown:
                        z.near_time=now; self.emit(ctx,('Demand' if bull else 'Supply')+' Near zone','demand_supply',origin=z.origin)
            if not ctx.is_confirmed: continue
            opposite=ctx.open>ctx.close if bull else ctx.open<ctx.close
            if opposite: self.last_direction[a]=n; continue
            last=self.last_direction[a]
            if last is None: continue
            run=n-last
            if run==1:
                prev=self.rows[-2]; tiny=abs(div(prev[4]-prev[1],prev[1]))*100<.02
                older=self.rows[-3] if n>=2 else None
                use_old=tiny and older is not None and (older[1]>older[4] if bull else older[1]<older[4])
                base=older if use_old else prev
                self.bases[a]=dict(o=base[1],c=base[4],wick=min(base[3],prev[3]) if bull else max(base[2],prev[2]),time=base[0],run_open=ctx.open,first_wick=ctx.low if bull else ctx.high,after=min(prev[4],ctx.close) if use_old and bull else max(prev[4],ctx.close) if use_old else ctx.close,direction=ctx.close>ctx.open if bull else ctx.close<ctx.open)
            elif self.bases[a]:
                b=self.bases[a]; b['after']=min(b['after'],ctx.close) if bull else max(b['after'],ctx.close); b['direction']=b['direction'] or (ctx.close>ctx.open if bull else ctx.close<ctx.open)
            b=self.bases[a]
            if not b or self.registered[a]==b['time']: continue
            departure=ctx.close-b['run_open'] if bull else b['run_open']-ctx.close
            min_depart=p['ds_min_atr']*atr if p['ds_mode']=='ATR' else abs(b['run_open'])*p['ds_min_pc_change']/100
            cap=p['ds_max_atr']*atr if p['ds_mode']=='ATR' else abs(b['o'])*p['ds_max_pc_zone']/100
            if not(b['direction'] and departure>0 and departure>=min_depart and cap>0): continue
            top=b['o'] if bull else max(b['wick'],b['first_wick']); bottom=min(b['wick'],b['first_wick']) if bull else b['o']
            if top-bottom>cap:
                large=abs(b['o']-b['c'])>cap
                if bull: top=min(b['o'],b['c']+cap/5) if large else b['o']; bottom=max(bottom,top-cap)
                else: bottom=max(b['o'],b['c']-cap/5) if large else b['o']; top=min(top,bottom+cap)
            top=math.floor(top/ctx.mintick+.5)*ctx.mintick; bottom=math.floor(bottom/ctx.mintick+.5)*ctx.mintick
            unbreached=b['after']>=bottom if bull else b['after']<=top
            if top>bottom and not unbreached: self.registered[a]=b['time']
            if top>bottom and unbreached and (ctx.close>top if bull else ctx.close<bottom):
                self.registered[a]=b['time']; z=Zone(top,bottom,b['time'],ctx.time,1 if bull else -1,'demand_supply'); self.ds[a].insert(0,z)
                if p['showDemandZone' if bull else 'showSupplyZone']:
                    z.box=ctx.box(left=z.origin-ctx.timeframe.seconds,right=z.origin+2*ctx.timeframe.seconds,top=top,bottom=bottom,border_color=p['demand_bord_col' if bull else 'supply_bord_col'],bg_color=p['demand_bg_col' if bull else 'supply_bg_col'],group='demand_supply')
                while len(self.ds[a])>p['ds_max_zones']:
                    remove=next((i for i in range(len(self.ds[a])-1,-1,-1) if self.ds[a][i].state!=0),len(self.ds[a])-1); z=self.ds[a].pop(remove)
                    if z.box: ctx.delete(z.box)

    def retire(self,ctx,z):
        if z.box:
            z.box._bull=z.side==1
            z.box.extend='none'; self.retired.append(z.box); z.box=None
            while len(self.retired)>50: ctx.delete(self.retired.pop(0))

    def pivot_system(self,ctx,atr):
        p=self.p; n=ctx.bar_index; o,h,l,c=ctx.open,ctx.high,ctx.low,ctx.close
        hc=(o+h+l+c)/4; ho=(o+c)/2 if not math.isfinite(self.ha_open) else (self.ha_open+self.ha_close)/2
        self.ha_open,self.ha_close=ho,hc
        self.src_high.append(max(ho,hc) if p['src']=='HA' else h if p['src']=='High/Low' else max(o,c)); self.src_low.append(min(ho,hc) if p['src']=='HA' else l if p['src']=='High/Low' else min(o,c))
        for a,high in enumerate((True,False)):
            y=pivot(self.src_high if high else self.src_low,p['left'],p['right'],high)
            if y is None or not ctx.is_confirmed or not p['dhighs' if high else 'dlows']: continue
            origin=n-p['right']; band=min(self.zone_atrs[origin]*p['mult'],self.rows[origin][4]*p['per']/100)/2
            if not math.isfinite(band): continue
            z=Zone(y+band,y-band,self.rows[origin][0],ctx.time,-1 if high else 1,'pivot_sr'); self.next_zone+=1; z.identity=self.next_zone
            self.pivots[a].insert(0,z)
            if len(self.pivots[a])>p['nPiv']: self.retire(ctx,self.pivots[a].pop())
            if p['alignZones']:
                for pool in self.pivots:
                    for older in pool:
                        if older is z: continue
                        t,b=older.top,older.bottom
                        if b<z.top<t or b<z.bottom<t or (z.top>t and z.bottom<b) or (z.bottom>b and z.top<t): z.top,z.bottom=t,b
        up=dn=0; falsedn=falseup=False
        for z in self.pivots[0]+self.pivots[1]:
            if z.born==ctx.time:
                if c>z.top: z.side=1
                elif c<z.bottom: z.side=-1
            elif ctx.is_confirmed:
                if z.pending_bar is not None and n-z.pending_bar>p['lookback']: z.pending_bar=None; z.pending_dir=0
                ub=z.side==-1 and c>z.top; db=z.side==1 and c<z.bottom
                if ub or db:
                    fd=ub and z.pending_dir==-1 and z.pending_bar is not None and n>z.pending_bar and c>z.pending_top
                    fu=db and z.pending_dir==1 and z.pending_bar is not None and n>z.pending_bar and c<z.pending_bottom
                    up+=int(ub); dn+=int(db); falsedn|=fd; falseup|=fu
                    if fd or fu: z.pending_bar=None; z.pending_dir=0
                    else: z.pending_bar=n; z.pending_dir=1 if ub else -1; z.pending_top=z.top; z.pending_bottom=z.bottom
                    # As in the Pine source: a break ends the box at this bar in its old role's color (history stays
                    # truthful) and the zone continues from this bar in a new box with the new role's color.
                    if z.box: z.box.right=ctx.time
                    self.retire(ctx,z); z.box_left=ctx.time
                    z.side=1 if ub else -1
            col=p['bullBorder' if z.side==1 else 'bearBorder']; bg=p['bullBgCol' if z.side==1 else 'bearBgCol']
            if not z.box: z.box=ctx.box(left=z.origin if z.box_left is None else z.box_left,right=ctx.time,top=z.top,bottom=z.bottom,border_color=col,bg_color=bg,extend='right' if p['extend'] else 'none',group='pivot_sr',tooltip=f'Pivot S/R #{z.identity}')
            z.box.right=ctx.time; z.box.border_color=col; z.box.bg_color=bg
        active=self.pivots[0]+self.pivots[1]; touched=[z for z in active if l<=z.top and h>=z.bottom]
        bull=any(z.side==1 for z in touched) and not any(z.side==-1 for z in touched); bear=any(z.side==-1 for z in touched) and not any(z.side==1 for z in touched)
        lows=sum(l>r[3] for r in self.rows[-p['reflect']-1:]); highs=sum(h>r[2] for r in self.rows[-p['reflect']-1:]); rank=min(p['offset'],p['reflect'])
        siglow=lows<=rank; sighigh=highs>=p['reflect']-rank
        closes=[r[4] for r in self.rows[-max(p['swing'],p['right']):]]; islow=c==min(closes[-p['swing']:]); ishigh=c==max(closes[-p['swing']:])
        prev=self.pivot_history[-1] if self.pivot_history else dict(bull=False,bear=False,siglow=False,sighigh=False,up=0,dn=0,islow=False,ishigh=False)
        recent=self.pivot_history[-p['lookback']:]+[dict(islow=islow,ishigh=ishigh)]
        fd=p['falseBull'] and falsedn and any(x['islow'] for x in recent); fu=p['falseBear'] and falseup and any(x['ishigh'] for x in recent)
        bo=p['detectBO'] and up>0 and c==max(closes[-p['right']:]) and active and all(z.side==1 for z in active) and not fd
        bd=p['detectBD'] and dn>0 and c==min(closes[-p['right']:]) and active and all(z.side==-1 for z in active) and not fu
        tsi,tsl=self.tsi.update(c)
        conditions={'false_breakdown':fd,'false_breakup':fu,'breakout':bo,'breakdown':bd,'resistance_break':p['breakUp'] and up>0 and not(bo or fd),'support_break':p['breakDn'] and dn>0 and not(bd or fu),'support_reaction':p['supPush'] and not up and not prev['up'] and c>o and bull,'resistance_reaction':p['resPush'] and not dn and not prev['dn'] and c<o and bear,'tsi_bull':p['curl'] and tsi>self.prev_tsi and tsi<tsl and bull,'tsi_bear':p['curl'] and tsi<self.prev_tsi and tsi>tsl and bear}
        if ctx.is_confirmed:
            for key,title,_,_,col in SHAPES:
                if conditions[key]: ctx.shape(key,color=p[col],tooltip=title); self.emit(ctx,title,'pivot_sr')
        self.prev_tsi=tsi
        bull2=not bear and not(bull and bear) and (siglow or prev['siglow']) and (bull or prev['bull']) and not(any(z.side==1 for z in touched) and any(z.side==-1 for z in touched))
        bear2=not bull and (sighigh or prev['sighigh']) and (bear or prev['bear']) and not(any(z.side==1 for z in touched) and any(z.side==-1 for z in touched))
        self.patterns(ctx,atr,siglow and bull,sighigh and bear,bull2,bear2)
        self.pivot_history.append(dict(bull=bull,bear=bear,siglow=siglow,sighigh=sighigh,up=up,dn=dn,islow=islow,ishigh=ishigh))
        if len(self.pivot_history)>300: self.pivot_history.pop(0)

    def patterns(self,ctx,atr,bull1,bear1,bull2,bear2):
        # Bjorgum/BjCandlePatterns/2 formulas, including the user's gravestone correction.
        p=self.p; h,l=ctx.high,ctx.low; body=abs(ctx.close-ctx.open); size=h-l
        avg=self.body_mean.update(body); self.body_avgs.append(avg)
        if p['repaint'] and not ctx.is_confirmed: return
        if p['atrMax']!=0 and not size<=p['atrMax']*atr: return
        prev=self.rows[-2] if len(self.rows)>1 else (0,NAN,NAN,NAN,NAN,NAN)
        formulas=pattern_formulas(p,self.rows[-1],prev,avg,self.body_avgs[-2] if len(self.body_avgs)>1 else NAN,atr)
        matches=[]
        for code,title,toggle,cb,cs,two,bars,neutral in formulas:
            if not p[toggle]: continue
            for bull,cond,context in [(True,cb,bull2 if two else bull1),(False,cs,bear2 if two else bear1)]:
                if cond and context:
                    matches.append((code,title,bull,neutral,bars)); self.emit(ctx,title,'patterns',direction='bullish' if bull else 'bearish')
        if not matches: return
        # One label per candle: a dragonfly doji or a hammer is also a long lower shadow, and printing every name stacked
        # labels on one candle. The most specific pattern is shown (two-candle patterns first); every match still alerts.
        code,title,bull,neutral,bars=min(matches,key=lambda m:PATTERN_PRIORITY.index(m[0]))
        also=[m[1] for m in matches if m[1]!=title]
        suffix='Neut' if neutral and code=='D' else 'Bull' if bull else 'Bear'; text=code if bull else BEARISH_CODES.get(code,code)
        tip=title+' near pivot '+('support' if bull else 'resistance')+(f" (also {', '.join(also)})" if also else '')
        if p['labels']:
            self.label(ctx,ctx.time,l if bull else h,text,p['text'+suffix+'Col'],'patterns',bull,bg=p['lab'+suffix+'Col'],tooltip=tip,yloc='belowbar' if bull else 'abovebar')
        if p['sBox'] and p['max']>0:
            sel=self.rows[-bars:]; bg=p['patNeutBg'] if neutral else p['pat'+suffix+'Bg']; border=p['patNeutBo'] if code=='D' else p['pat'+suffix+'Bo']
            box=ctx.box(left=ctx.time_at(ctx.bar_index-bars),right=ctx.time,top=max(r[2] for r in sel),bottom=min(r[3] for r in sel),border_color=border,bg_color=bg,group='patterns',tooltip=tip)
            self.pattern_boxes.insert(0,box)
            while len(self.pattern_boxes)>p['max']: ctx.delete(self.pattern_boxes.pop())

    def dashboard(self,ctx):
        p=self.p; c,h,l=ctx.close,ctx.high,ctx.low; rows=self.rows; n=ctx.bar_index
        rsi=self.rsi.update(c); stoch_rsi=self.stoch_rsi.update(c); self.rsi_hist.append(stoch_rsi)
        window=[v for v in self.rsi_hist[-p['lengthStoch']:] if math.isfinite(v)]
        stochastic=100*div(stoch_rsi-min(window),max(window)-min(window)) if window else NAN
        k=self.kmean.update(stochastic); d=self.dmean.update(k)
        macd=self.macd_fast.update(c)-self.macd_slow.update(c); macds=self.macd_signal.update(macd)
        dp,dm,adx=self.dmi.update(h,l,c); psar=self.sar.update(h,l,c); mom=c-rows[-1-p['momLength']][4] if n>=p['momLength'] else NAN
        vr=rows[-p['vortexLength']:]; vmp=vmm=tr=0.
        vortex_ready=n>=p['vortexLength']
        if vortex_ready:
            for j in range(n-p['vortexLength']+1,n+1):
                r=rows[j]; prev=rows[j-1]; vmp+=abs(r[2]-prev[3]); vmm+=abs(r[3]-prev[2]); tr+=max(r[2]-r[3],abs(r[2]-prev[4]),abs(r[3]-prev[4]))
        vip=div(vmp,tr) if vortex_ready else NAN; vim=div(vmm,tr) if vortex_ready else NAN
        # Standard Money Flow Index on the typical price (H+L+C)/3 (the Pine source used ta.mfi(close)).
        mfip=mfim=0.; mfi_ready=n>=p['mfiLength']-1
        vol=sum(r[5] for r in rows[-p['mfiLength']:])
        tp=lambda r:(r[2]+r[3]+r[4])/3
        for j in range(max(0,n-p['mfiLength']+1),n+1):
            r=rows[j]; change=tp(r)-tp(rows[j-1]) if j else NAN
            if not math.isfinite(r[5]): mfi_ready=False
            if change>0: mfip+=tp(r)*r[5]
            elif change<0: mfim+=tp(r)*r[5]
        mfi=(100 if mfim==0 and mfip>0 else 100-100/(1+div(mfip,mfim))) if mfi_ready and vol>0 else NAN
        mids=[(r[2]+r[3])/2 for r in rows[-p['fisherLength']:]]; hi=max(mids); lo=min(mids); fisher_ready=hi>lo
        if fisher_ready:
            v=.66*((h+l)/2-lo)/(hi-lo)-.33+.67*self.fish_value; self.fish_value=.999 if v>.99 else -.999 if v<-.99 else v
        prevfish=self.fish; self.fish=.5*math.log((1+self.fish_value)/(1-self.fish_value))+.5*self.fish
        def direction(a,b): return 'buy' if a>b else 'sell' if a<b else 'wait'
        def extreme(v,hi,lo): return 'OB' if v>=hi else 'OS' if v<=lo else ''
        values=[('macd','MACD',direction(macd,macds),'',macd),('stochastic','Stoch',direction(k,50 if p['stochDirectionMode']=='K vs 50' else d),extreme(k,80,20),k),('vortex','Vortex',direction(vip,vim),'',vip),('mom','Mom',direction(mom,0),'',mom),('rsi','RSI',direction(rsi,50),extreme(rsi,70,30),rsi),('psar','PSAR',direction(c,psar),'',psar),('dmi','DMI',direction(dp,dm) if adx>=p['adxThreshold'] else 'wait','',adx),('mfi','MFI',direction(mfi,50),extreme(mfi,80,20) if math.isfinite(mfi) else 'no volume',mfi),('fisher','Fisher',direction(self.fish,prevfish) if fisher_ready else 'wait','',self.fish)]
        # Pine writes rows in this exact order; a duplicate row order overwrites its previous cell.
        order=['mfi','fisher','dmi','mom','psar','rsi','macd','stochastic','vortex']; cells={}
        by_id={v[0]:v for v in values}
        for key in order:
            _,title,state,tag,val=by_id[key]
            cells[p[key+'Order']]=dict(id=key,title=title,direction=state,tag=tag,value=val if math.isfinite(val) else None,color=DASH_BUY if state=='buy' else DASH_SELL if state=='sell' else DASH_WAIT,visible=p[key+'On'])
        self.tables=[dict(id='directions',title='direction',position=p['itablePosition'],rows=[cells[i] for i in sorted(cells) if cells[i]['visible']])] if p['directions'] and p['infoDataTableOn'] else []
        if all(v[2]=='buy' for v in values): self.emit(ctx,'All Bullish Indicators Alert','dashboard')
        if all(v[2]=='sell' for v in values): self.emit(ctx,'All Bearish Indicators Alert','dashboard')

    def bulb(self,ctx):
        p=self.p; rsi=self.bulb_rsi.update(ctx.close); prev=self.prev_bulb; self.prev_bulb=rsi
        if not p['recommendation']: return
        ob=rsi>=p['rsiOverbought']; os=rsi<=p['rsiOvesold']; moving=p['bulbMode']=='Moving (original)'
        sell=self.bulb_state==2 and ob if moving else ctx.is_confirmed and rsi<p['rsiOverbought'] and prev>=p['rsiOverbought'] and (not p['bulbAlternate'] or self.bulb_state!=1)
        buy=self.bulb_state==1 and os if moving else ctx.is_confirmed and rsi>p['rsiOvesold'] and prev<=p['rsiOvesold'] and (not p['bulbAlternate'] or self.bulb_state!=2)
        for a,bull,cond in [(0,False,sell),(1,True,buy)]:
            if not cond: continue
            label=self.label(ctx,ctx.time,ctx.low if bull else ctx.high,'buy' if bull else 'sell','#FFFFFFFF','bulb',bull,'normal',DASH_BUY if bull else DASH_SELL,tooltip='BULB reversal extreme'+(' · Moving: label follows swing' if moving else ' · Fixed: confirmed RSI threshold exit'))
            self.bulb_label[a]=label
            if bull: self.bulb_low=ctx.low
            else: self.bulb_high=ctx.high
            if not moving: self.bulb_state=2 if bull else 1
            self.emit(ctx,'BULB Buy' if bull else 'BULB Sell','bulb',moving=moving)
        if moving:
            if ob: self.bulb_state=1
            if os: self.bulb_state=2
            if not math.isfinite(self.bulb_high): self.bulb_high=ctx.low
            if not math.isfinite(self.bulb_low): self.bulb_low=ctx.high
            if self.bulb_state==1 and ctx.high>=self.bulb_high:
                self.bulb_high=ctx.high
                if self.bulb_label[0]: self.bulb_label[0].x=ctx.time; self.bulb_label[0].y=ctx.high
            if self.bulb_state==2 and ctx.low<=self.bulb_low:
                self.bulb_low=ctx.low
                if self.bulb_label[1]: self.bulb_label[1].x=ctx.time; self.bulb_label[1].y=ctx.low

    def on_last_bar(self,ctx):
        # barstate.islast drawings; the engine runs this on a copy of the state, so nothing here persists.
        p=self.p; self.warnings=[]
        for tf,handles in self.htf.items():
            if not math.isfinite(ctx.security(handles['high'])): self.warnings.append(f'Previous {tf} levels unavailable: this feed needs completed {tf} candles.')
        if self.fvg_htf and p['show_fvg'] and not math.isfinite(ctx.security(self.fvg_handles['h2'])): self.warnings.append(f'FVG {self.fvg_tf.label}: insufficient completed higher-timeframe candles.')
        self.render(ctx,self.atr14.value)

    def displayed_order_blocks(self):
        """(internal, zone) for the order blocks the chart draws: within the distance filter, nearest first."""
        p=self.p; rows=self.rows; close=rows[-1][4]; hi=max(r[2] for r in rows[-120:]); lo=min(r[3] for r in rows[-120:]); pad=(hi-lo)*p['aoi_max_dist']
        def visible(top,bottom): return p['aoi_max_dist']==0 or top>=lo-pad and bottom<=hi+pad
        out=[]
        for a,internal in enumerate((True,False)):
            candidates=[z for z in self.obs[a] if visible(z.top,z.bottom)]
            candidates.sort(key=lambda z:max(z.bottom-close,close-z.top,0))
            out+=[(internal,z) for z in candidates[:p['iob_showlast' if internal else 'ob_showlast']]]
        return out

    def render(self,ctx,atr14):
        p=self.p; rows=self.rows; hi=max(r[2] for r in rows[-120:]); lo=min(r[3] for r in rows[-120:]); pad=(hi-lo)*p['aoi_max_dist']
        def visible(top,bottom): return p['aoi_max_dist']==0 or top>=lo-pad and bottom<=hi+pad
        line,label,box=ctx.line,ctx.label,ctx.box
        future=lambda bars:ctx.time_at(ctx.bar_index+bars)
        for internal,z in self.displayed_order_blocks():
            group='internal_ob' if internal else 'swing_ob'
            bull=z.side==1
            if p['style']=='Monochrome': css=new_color('#B2B5BEFF' if bull else '#5D606BFF',80); border=('#B2B5BEFF' if bull else '#5D606BFF') if not internal else css
            else: css=p[('i' if internal else '')+('bull_ob_css' if bull else 'bear_ob_css')]; border=css
            box(left=z.origin,right=z.origin,top=z.top,bottom=z.bottom,border_color=border,bg_color=css,extend='right',group=group,tooltip=('Internal ' if internal else 'Swing ')+'SMC Order Block')
        if p['show_hl_swings']:
            for high,y,x in [(True,self.trail_high,self.trail_hx),(False,self.trail_low,self.trail_lx)]:
                col=self.color(not high); strong=self.struct[1]['trend']<0 if high else self.struct[1]['trend']>0; text=('Strong ' if strong else 'Weak ')+('High' if high else 'Low')
                line(x1=ctx.time_at(x),y1=y,x2=future(20),y2=y,color=col,group='strong_weak')
                label(x=future(20),y=y,text=text,color=TRANSPARENT,text_color=col,style='label_down' if high else 'label_up',size='tiny',group='strong_weak')
        if p['show_sd']:
            upper,lower=self.trail_high,self.trail_low; left_i=max(self.struct[1]['tx'],self.struct[1]['bx']); left=ctx.time_at(left_i)
            mid=ctx.time_at((left_i+ctx.bar_index)//2)  # a bar time: an averaged timestamp can fall between candles
            for title,top,bottom,col in [('Premium',upper,.95*upper+.05*lower,p['premium_css']),('Equilibrium',.525*upper+.475*lower,.525*lower+.475*upper,p['eq_css']),('Discount',.95*lower+.05*upper,lower,p['discount_css'])]:
                box(left=left,right=ctx.time,top=top,bottom=bottom,border_color=TRANSPARENT,bg_color=new_color(col,80),group='premium_discount')
                label(x=ctx.time if title=='Equilibrium' else mid,y=(upper+lower)/2 if title=='Equilibrium' else upper if title=='Premium' else lower,text=title,color=TRANSPARENT,text_color=col,style='label_left' if title=='Equilibrium' else 'label_down' if title=='Premium' else 'label_up',size='small',group='premium_discount')
        for z in self.fvgs:
            if not visible(z.top,z.bottom): continue
            col=p['bull_fvg_css' if z.side==1 else 'bear_fvg_css']; right=future(p['fvg_extend']) if p['fvg_extend_active'] else z.born+p['fvg_extend']*ctx.timeframe.seconds; mid=(z.top+z.bottom)/2
            for top,bottom in [(z.top,mid),(mid,z.bottom)]: box(left=z.origin,right=right,top=top,bottom=bottom,border_color=col,bg_color=col,group='fvg',tooltip='Bullish FVG' if z.side==1 else 'Bearish FVG')
        if self.fvg_lower and p['show_fvg']:
            label(x=ctx.time,y=ctx.high,text=f'FVG {p["fvg_tf"]} below chart: showing {ctx.timeframe.pine}',color='#FF9800CC',text_color='#FFFFFFFF',style='label_left',size='small',group='fvg')
        for tf,handles in self.htf.items():
            prefix={'D':'pdhl','W':'pwhl','M':'pmhl'}[tf]; x=ctx.security(handles['time']); high=ctx.security(handles['high']); low=ctx.security(handles['low'])
            if not all(math.isfinite(v) for v in (x,high,low)): continue
            color=p[prefix+'_css']; style={'⎯⎯⎯':'solid','----':'dashed','····':'dotted'}[p[prefix+'_style']]; ext=future({'D':12,'W':24,'M':36}[tf])
            for ishigh,y in [(True,high),(False,low)]:
                if not visible(y,y): continue
                # Exact wick anchor: the last chart bar of the previous period that printed the level (the whole period
                # is searched; Pine looked back 1000 bars). Keep the period start if the history does not reach it.
                period_end=int(bucket_start(ctx.time,get_timeframe(tf,reference=True),ctx._r.alignment)); anchor=int(x)
                first=bisect_left(rows,x,key=lambda r:r[0]); last=bisect_left(rows,period_end,key=lambda r:r[0])
                for j in range(last-1,first-1,-1):
                    if abs(rows[j][2 if ishigh else 3]-y)<=ctx.mintick*.5: anchor=rows[j][0]; break
                text='P'+tf+('H' if ishigh else 'L'); tip=f'Previous {tf} '+('High' if ishigh else 'Low')+f': {y:.{ctx.precision}f}'
                line(x1=anchor,y1=y,x2=ext,y2=y,color=color,style=style,group='mtf',tooltip=tip)
                label(x=ext,y=y,text=text,color=color,text_color='#FFFFFFFF',style='label_left',size='small',group='mtf',tooltip=tip)
        self.render_demand_supply(ctx,atr14)
        # Align Zones snaps an overlapping new zone onto an older one's exact edges, so two zones can share a level.
        # Pine stacks their labels on one spot; the chart spreads overlapping labels apart, so draw one per price.
        labelled=set()
        for z in self.pivots[0]+self.pivots[1]:
            if z.box:
                z.box.bg_color=p['bullBgCol' if z.side==1 else 'bearBgCol'] if visible(z.top,z.bottom) else TRANSPARENT
                z.box.border_color=p['bullBorder' if z.side==1 else 'bearBorder'] if visible(z.top,z.bottom) else TRANSPARENT
            if p['lLab'] and visible(z.top,z.bottom):
                y=z.top if ctx.close>z.top else z.bottom if ctx.close<z.bottom else z.top if z.side==1 else z.bottom
                col=p['bullBorder' if z.side==1 else 'bearBorder']; bg=p['bullBgCol' if z.side==1 else 'bearBgCol']; text=f'{math.floor(y/ctx.mintick+.5)*ctx.mintick:.{ctx.precision}f}'
                if text in labelled: continue
                labelled.add(text)
                label(x=future(p['fut']),y=y,text=text,color=col,text_color='#111827FF',style='label_down' if ctx.close>z.top else 'label_up',group='pivot_sr',tooltip=f'Pivot S/R #{z.identity}')
                if not p['extend'] and p['fut']>0: line(x1=ctx.time,y1=y,x2=future(p['fut']),y2=y,color=col,group='pivot_sr')
        for obj in self.retired:
            # Restore colors based on stored role before each last-bar display filter.
            role=getattr(obj,'_bull',None)
            if role is None: obj._bull=obj.border_color==p['bullBorder']; role=obj._bull
            obj.border_color=p['bullBorder' if role else 'bearBorder'] if visible(obj.top,obj.bottom) else TRANSPARENT
            obj.bg_color=p['bullBgCol' if role else 'bearBgCol'] if visible(obj.top,obj.bottom) else TRANSPARENT

    def render_demand_supply(self,ctx,atr14):
        """Shared original D/S badge renderer, also used by the overlap-only calculation."""
        p=self.p; rows=self.rows; hi=max(r[2] for r in rows[-120:]); lo=min(r[3] for r in rows[-120:]); pad=(hi-lo)*p['aoi_max_dist']
        def visible(top,bottom): return p['aoi_max_dist']==0 or top>=lo-pad and bottom<=hi+pad
        line,label=ctx.line,ctx.label
        future=lambda bars:ctx.time_at(ctx.bar_index+bars)
        for a,bull in enumerate((True,False)):
            col=p['demand_bord_col' if bull else 'supply_bord_col']; bg=p['demand_bg_col' if bull else 'supply_bg_col']; show=p['showDemandZone' if bull else 'showSupplyZone']
            for z in self.ds[a]:
                if not show: continue
                title='Demand Zone' if bull else 'Supply Zone'; state=['Fresh','Tested','Invalidated'][z.state]
                date=datetime.fromtimestamp(z.origin,timezone.utc)+timedelta(hours=p['ds_hours_offset'])
                tip=f'{title} · {state}\nRange: {z.bottom:.{ctx.precision}f} – {z.top:.{ctx.precision}f}\nOrigin UTC{p["ds_hours_offset"]:+g}: {date:%Y/%m/%d %H:%M}'
                if not z.box: continue
                z.box.tooltip=tip; z.box.bg_color=bg if z.state==0 else TRANSPARENT; z.box.border_style=['solid','dotted','dashed'][z.state]; z.box.border_color='#787B8699' if z.state==2 else col
            active=sorted([z for z in self.ds[a] if z.state==0],key=lambda z:z.top if bull else z.bottom,reverse=bull)
            limit=100*div(atr14,abs(ctx.close))*p['ds_line_atr'] if p['ds_mode']=='ATR' else p['ds_line_pc_display']; stagger=100*div(atr14,abs(ctx.close)) if p['ds_mode']=='ATR' else .7
            previous=None; extra=0; drawn=0
            if show:
                for z in active:
                    y=z.top if bull else z.bottom
                    if drawn>=p['ds_line_max'] or abs(div(y-ctx.close,ctx.close))*100>limit or not visible(y,y): break
                    extra=0 if extra>0 else 25 if previous is not None and abs(div(y-previous,previous))*100<stagger else 0; previous=y; drawn+=1
                    line(x1=z.origin+2*ctx.timeframe.seconds,y1=y,x2=future(1),y2=y,color=col,group='demand_supply',tooltip=z.box.tooltip if z.box else '')
                    label(x=future(4+extra),y=y,text=f'{y:.{ctx.precision}f}',color=col,text_color='#111827FF',style='label_left',group='demand_supply',tooltip=z.box.tooltip if z.box else '')

    # Easy Mode keeps internal structure, order blocks, previous D/W/M levels, the SMA / envelope plots and the
    # structure-colored candles; the D/S + order block overlaps are separate chart layers and are always kept.
    EASY_MODE_HIDDEN=('swing_structure','swing_points','strong_weak','liquidity','fvg','premium_discount',
                       'demand_supply','pivot_sr','patterns','bulb')

    def display_filter(self):
        if not self.p.get('easy_mode'):
            return None
        return dict(groups=self.EASY_MODE_HIDDEN,shapes=[s[0] for s in SHAPES],tables=True)

    def extra_output(self):
        tables=[]
        for t in self.tables:
            rows=[[dict(text=' '+t['title'],bg_color='#9C27B0FF',text_color='#FFFFFFFF',tooltip='')]]
            for r in t['rows']:
                value='na' if r['value'] is None else f"{r['value']:.2f}"
                rows.append([dict(text=' '.join(x for x in (r['title'],r['direction'],r['tag']) if x),bg_color=r['color'],text_color='#FFFFFFFF',tooltip=f"{r['title']}: {value}")])
            tables.append(dict(id=t['id'],position=t['position'],rows=rows))
        return dict(tables=tables,warnings=list(self.warnings))

    def structure_step(self,ctx):
        """Candle history, ATRs, market structure and order blocks (creation and close-through removal)."""
        p=self.p; self.rows.append((ctx.time,ctx.open,ctx.high,ctx.low,ctx.close,ctx.volume))
        a200=self.atr200.update(ctx.high,ctx.low,ctx.close); a14=self.atr14.update(ctx.high,ctx.low,ctx.close); za=self.zone_atr.update(ctx.high,ctx.low,ctx.close)
        self.range_sum+=ctx.high-ctx.low; self.thresholds.append(a200 if p['ob_filter']=='Atr' else self.range_sum/(ctx.bar_index+1)); self.disp_atrs.append(a14); self.zone_atrs.append(za)
        self.structure(ctx,1,p['length'],False); self.structure(ctx,0,min(p['ilength'],max(p['length']-1,2)),True)
        if ctx.is_confirmed:
            for pool in self.obs:
                for z in list(pool):
                    if ctx.close<z.bottom if z.side==1 else ctx.close>z.top:
                        pool.remove(z); self.emit(ctx,('Bullish' if z.side==1 else 'Bearish')+' '+('Internal ' if z.kind=='internal_ob' else '')+'OB Breakout',z.kind)
        return a200,a14,za

    def on_bar(self,ctx:Context):
        p=self.p; a200,a14,za=self.structure_step(ctx)
        self.equal_levels(ctx,a200); self.gaps(ctx); self.demand_supply(ctx,a14); self.pivot_system(ctx,za); self.dashboard(ctx); self.bulb(ctx)
        m=self.ma.update(ctx.close); a=self.env_atr.update(ctx.high,ctx.low,ctx.close); width=m*div(a,ctx.close)*p['atrm']
        ctx.plot('sma',m); ctx.plot('upper',m+width); ctx.plot('lower',m-width)
        if p['show_trend']:
            bull=self.struct[0]['trend']==1; col=self.color(bull) if p['style']=='Colored' else '#B2B5BEFF' if bull else '#5D606BFF'; with_trend=ctx.close>=ctx.open if bull else ctx.close<=ctx.open
            ctx.plot_candle(col if with_trend else None,col,col); ctx.bar_color(col)
        self.strategy(ctx)

    def strategy(self,ctx:Context):
        """Trade signals for the Strategy Tester (strategy id STRATEGY_ID). Runs after every indicator system on
        each bar, so all of this bar's structure/zone state is available. Only confirmed bars produce orders, and
        ctx only exposes current and past bars, so signals cannot look ahead. Example (market buy, 1:2 risk/reward):

            if <your long condition>:
                sl=ctx.low-self.atr14.value*0.5; risk=ctx.close-sl
                ctx.signal(STRATEGY_ID,'long',stop_loss=sl,take_profits=[(ctx.close+2*risk,1.0)],comment='SMC long')

        Limit/stop entries: order_type='limit'|'stop', entry=price, expiry_bars=N. Partial exits: several
        take_profits [(price, fraction)], breakeven_after_tp1=True. See ctx.signal in engine/script.py.
        """

