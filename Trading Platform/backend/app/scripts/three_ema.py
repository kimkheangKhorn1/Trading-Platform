# Python port of "3EMA" (Pine v3) by Victor Nascimento.
from __future__ import annotations

from ..engine.inputs import InputSpec, PlotSpec, ScriptMeta
from ..engine.script import Context, Script
from ..engine.ta import EMA

META = ScriptMeta(
    id='three_ema', name='3EMA', short_name='3EMA', category='Custom',
    description='Three exponential moving averages of the close (50 / 100 / 200 by default).',
    inputs=[InputSpec('EMA1', 'int', 'EMA1', 50, minval=1), InputSpec('EMA2', 'int', 'EMA2', 100, minval=1),
            InputSpec('EMA3', 'int', 'EMA3', 200, minval=1)],
    # Pine v3 named colors red / yellow / purple, linewidth 2
    plots=[PlotSpec('ema1', 'EMA1', '#FF5252FF', linewidth=2), PlotSpec('ema2', 'EMA2', '#FFEB3BFF', linewidth=2),
           PlotSpec('ema3', 'EMA3', '#9C27B0FF', linewidth=2)],
)


class ThreeEma(Script):
    meta = META

    def required_warmup(self) -> int:
        # ten lengths: the starting value's weight is below 1e-8 by then, whatever history TradingView loaded
        return 10 * max(self.p['EMA1'], self.p['EMA2'], self.p['EMA3'])

    def setup(self, ctx: Context) -> None:
        self.emas = [EMA(self.p[k]) for k in ('EMA1', 'EMA2', 'EMA3')]

    def on_bar(self, ctx: Context) -> None:
        for pid, ema in zip(('ema1', 'ema2', 'ema3'), self.emas):
            ctx.plot(pid, ema.update(ctx.close))
