"""Order-block-only execution of the original structure and order block logic for overlap scans.

Same settings and history as the chart; only the systems order blocks depend on run (candles, ATRs,
market structure, order block creation and close-through removal). The chart's own display selection
(`displayed_order_blocks`) decides which blocks are eligible.
"""
from ...engine.ta import ATR
from .indicator import SmcMaster
from .technical import NAN


class OrderBlockSource(SmcMaster):
    def setup(self,ctx):
        p=self.p
        self.rows=[]; self.atr200=ATR(200); self.atr14=ATR(14); self.zone_atr=ATR(p['atrLen'])
        self.range_sum=0.; self.thresholds=[]; self.disp_atrs=[]; self.zone_atrs=[]
        self.struct=[dict(os=0,trend=0,top=NAN,bottom=NAN,tx=0,bx=0,top_cross=True,bottom_cross=True) for _ in range(2)]
        self.prev_levels=[(NAN,NAN),(NAN,NAN)]
        self.trail_high=NAN; self.trail_low=NAN; self.trail_hx=0; self.trail_lx=0
        self.obs=[[],[]]; self.latest={}; self.events=[]

    def emit(self,*args,**kwargs):
        pass  # Only order block geometry is consumed; source alerts are never published.

    def on_bar(self,ctx):
        self.structure_step(ctx)

    def extra_output(self):
        return {}
