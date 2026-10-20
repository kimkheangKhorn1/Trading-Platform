"""Order-block-only execution of the original structure and order block logic, for OB trade signals.

Same settings and history as the chart; only the systems order blocks depend on run (candles, ATRs,
market structure, order block creation and close-through removal). Every order block is logged when it is
created and when it leaves the indicator's pools (closed through, or pushed out of the 100-block cap), so
the trade scan sees each block exactly while the indicator held it. Only confirmed bars create or remove
blocks, so a provisional (unfinished) candle never adds to the log.
"""
from ...engine.ta import ATR
from .indicator import SmcMaster
from .technical import NAN


class OrderBlockSource(SmcMaster):
    # The logs only hold immutable tuples: copy the lists, not every entry, on provisional-bar snapshots.
    _SCALAR_HISTORY = SmcMaster._SCALAR_HISTORY | {'ob_births', 'ob_ends'}

    def setup(self,ctx):
        p=self.p
        self.rows=[]; self.atr200=ATR(200); self.atr14=ATR(14); self.zone_atr=ATR(p['atrLen'])
        self.range_sum=0.; self.thresholds=[]; self.disp_atrs=[]; self.zone_atrs=[]
        self.struct=[dict(os=0,trend=0,top=NAN,bottom=NAN,tx=0,bx=0,top_cross=True,bottom_cross=True) for _ in range(2)]
        self.prev_levels=[(NAN,NAN),(NAN,NAN)]
        self.trail_high=NAN; self.trail_low=NAN; self.trail_hx=0; self.trail_lx=0
        self.obs=[[],[]]; self.latest={}; self.events=[]
        # (id, kind, side, top, bottom, origin, born): born = open time of the bar whose close created it
        self.ob_births=[]; self.ob_ends=[]  # (id, open time of the bar whose close removed it, 'broken' | 'cap')
        self.ob_live={}; self.next_ob=0  # id -> (top, bottom, side) of the blocks in the pools

    def emit(self,*args,**kwargs):
        pass  # Only order block geometry is consumed; source alerts are never published.

    def on_bar(self,ctx):
        self.structure_step(ctx)
        if not ctx.is_confirmed: return
        held=set()
        for pool in self.obs:
            for z in pool:
                if not z.identity:
                    self.next_ob+=1; z.identity=self.next_ob
                    self.ob_births.append((z.identity,z.kind,z.side,z.top,z.bottom,z.origin,ctx.time))
                    self.ob_live[z.identity]=(z.top,z.bottom,z.side)
                held.add(z.identity)
        for i in [i for i in self.ob_live if i not in held]:
            top,bottom,side=self.ob_live.pop(i)
            broken=ctx.close<bottom if side==1 else ctx.close>top
            self.ob_ends.append((i,ctx.time,'broken' if broken else 'cap'))

    def extra_output(self):
        return {}
