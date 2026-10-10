# Python port of Pine ta primitives used by SMC Master. MPL-2.0.
from __future__ import annotations
import math
from collections import deque
from ...engine.ta import ATR, RMA

NAN = math.nan

def div(a, b):
    return a / b if b != 0 and math.isfinite(b) else NAN

class EMA:
    """Pine reference EMA seeds from the first non-na source, then ignores na."""
    def __init__(self, length): self.alpha=2/(length+1); self.value=NAN
    def update(self, x):
        if math.isfinite(x): self.value=x if not math.isfinite(self.value) else self.alpha*x+(1-self.alpha)*self.value
        return self.value

class Mean:
    def __init__(self, length): self.length=length; self.values=deque(maxlen=length); self.total=0.0
    def update(self, x):
        if math.isfinite(x):
            if len(self.values)==self.length: self.total-=self.values[0]
            self.values.append(x); self.total+=x
        return self.total/self.length if len(self.values)==self.length else NAN

class RSI:
    def __init__(self, length): self.up=RMA(length); self.down=RMA(length); self.prev=NAN
    def update(self, x):
        change=x-self.prev; self.prev=x
        u=self.up.update(max(change,0) if math.isfinite(change) else NAN)
        d=self.down.update(max(-change,0) if math.isfinite(change) else NAN)
        return 100.0 if math.isfinite(u) and d==0 else 0.0 if u==0 and math.isfinite(d) else 100-100/(1+div(u,d))

class TSI:
    def __init__(self, short, long, signal):
        self.a=EMA(long); self.b=EMA(short); self.c=EMA(long); self.d=EMA(short); self.signal=EMA(signal); self.prev=NAN
    def update(self,x):
        change=x-self.prev; self.prev=x
        tsi=100*div(self.b.update(self.a.update(change)),self.d.update(self.c.update(abs(change))))
        return tsi,self.signal.update(tsi)

class DMI:
    def __init__(self,length,smooth):
        self.tr=ATR(length); self.plus=RMA(length); self.minus=RMA(length); self.adx=RMA(smooth); self.h=NAN; self.l=NAN
    def update(self,h,l,c):
        up=h-self.h; dn=self.l-l; self.h=h; self.l=l
        plus=up if up>dn and up>0 else 0 if math.isfinite(up) else NAN
        minus=dn if dn>up and dn>0 else 0 if math.isfinite(dn) else NAN
        tr=self.tr.update(h,l,c); p=100*div(self.plus.update(plus),tr); m=100*div(self.minus.update(minus),tr)
        total=p+m; adx=100*self.adx.update(div(abs(p-m),1 if total==0 else total))
        return p,m,adx

class SAR:
    """Pine reference Parabolic SAR (initializes on bar 1)."""
    def __init__(self,start,inc,maximum):
        self.start=start; self.inc=inc; self.maximum=maximum; self.sar=NAN; self.extreme=NAN; self.acc=NAN; self.below=False; self.hist=deque(maxlen=2)
    def update(self,h,l,c):
        first=False
        if len(self.hist)==1 and not math.isfinite(self.sar):
            ph,pl,pc=self.hist[-1]; self.below=c>pc; self.extreme=h if self.below else l; self.sar=pl if self.below else ph; self.acc=self.start; first=True
        if self.hist and math.isfinite(self.sar):
            self.sar+=self.acc*(self.extreme-self.sar)
            if self.below and self.sar>l:
                first=True; self.below=False; self.sar=max(h,self.extreme); self.extreme=l; self.acc=self.start
            elif not self.below and self.sar<h:
                first=True; self.below=True; self.sar=min(l,self.extreme); self.extreme=h; self.acc=self.start
            if not first:
                if self.below and h>self.extreme: self.extreme=h; self.acc=min(self.acc+self.inc,self.maximum)
                elif not self.below and l<self.extreme: self.extreme=l; self.acc=min(self.acc+self.inc,self.maximum)
            for ph,pl,_ in self.hist: self.sar=min(self.sar,pl) if self.below else max(self.sar,ph)
        self.hist.append((h,l,c)); return self.sar

def pivot(values, left, right, high=True):
    if len(values)<left+right+1: return None
    a=values[-left-right-1:]; x=a[left]
    # Pine selects the last occurrence of equal extrema (ties on right disqualify).
    ok=all(x>=v for v in a[:left]) and all(x>v for v in a[left+1:]) if high else all(x<=v for v in a[:left]) and all(x<v for v in a[left+1:])
    return x if ok and math.isfinite(x) else None
