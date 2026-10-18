"""D/S-only execution of the original detector and renderer for overlap scans.

No substitute detection rules, reduced history or settings changes. Other independent
SMC systems cannot affect D/S state and need not execute to discover its fresh badges.
"""
from ...engine.ta import ATR
from .indicator import SmcMaster


class DemandSupplyBadgeSource(SmcMaster):
    def setup(self,ctx):
        self.rows=[]
        self.atr14=ATR(14)
        self.ds=[[],[]]
        self.bases=[None,None]
        self.last_direction=[None,None]
        self.registered=[None,None]

    def emit(self,*args,**kwargs):
        pass  # Only badge geometry is consumed; source alerts are never published.

    def on_bar(self,ctx):
        self.rows.append((ctx.time,ctx.open,ctx.high,ctx.low,ctx.close,ctx.volume))
        atr=self.atr14.update(ctx.high,ctx.low,ctx.close)
        self.demand_supply(ctx,atr)

    def on_last_bar(self,ctx):
        self.render_demand_supply(ctx,self.atr14.value)

    def extra_output(self):
        return {}
