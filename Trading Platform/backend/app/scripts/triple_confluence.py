# This work is licensed under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
# https://creativecommons.org/licenses/by-nc-sa/4.0/
# © MarkitTick
# Python port of "Triple Confluence Navigator [MarkitTick]" (Pine v6), line for line.
from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..engine.inputs import DrawingGroup, InputSpec, ScriptMeta
from ..engine.pine import new_color
from ..engine.script import Context, Script
from ..engine.ta import ATR, RMA, Highest, Lowest
from .pinelib import GRAY, NAN, WHITE, Table, finite, fmt_pattern, fmt_price

G_RSI, G_MA, G_ARSI, G_ST, G_BOS = '⚙️ Cardwell RSI', '⚙️ Cardwell MA', '🌊 Adaptive RSI', '🌊 Supertrend', '🧱 Structure'
G_HTF, G_CHOP, G_TRADE, G_DASH, G_WH, G_COL = ('🕯️ HTF Filter', '🕯️ Choppiness', '📐 Trade Setup', '📊 Dash',
                                               '🔔 Alerts', '🌈 Colors')
PINE_TF = {'1': 'M1', '5': 'M5', '15': 'M15', '30': 'M30', '60': 'H1', '240': 'H4', 'D': 'D'}

INPUTS = [
    InputSpec('rsiLen', 'int', 'RSI Len', 14, minval=2, group=G_RSI),
    InputSpec('rmaFastLen', 'int', 'Fast Len', 9, minval=2, group=G_MA),
    InputSpec('rmaSlowLen', 'int', 'Slow Len', 45, minval=2, group=G_MA),
    InputSpec('maType', 'string', 'Filter Type', 'Kalman', options=('RMA', 'Kalman', 'LLAMA'), group=G_MA),
    InputSpec('arsiLen', 'int', 'ARSI Len', 14, minval=2, group=G_ARSI),
    InputSpec('arsiSigLen', 'int', 'Signal Len', 14, minval=1, group=G_ARSI),
    InputSpec('arsiFiltType', 'string', 'Filter Type', 'Kalman Filter', options=('RMA', 'Kalman Filter', 'LLAMA'),
              group=G_ARSI),
    InputSpec('kalmanProcN', 'float', 'Kalman Proc N', 0.01, minval=0.0001, step=0.001, group=G_ARSI),
    InputSpec('kalmanMeasN', 'float', 'Kalman Meas N', 0.10, minval=0.0001, step=0.01, group=G_ARSI),
    InputSpec('llamaAlphaMin', 'float', 'LLAMA Min α', 0.05, minval=0.01, maxval=0.99, step=0.01, group=G_ARSI),
    InputSpec('llamaAlphaMax', 'float', 'LLAMA Max α', 0.50, minval=0.01, maxval=0.99, step=0.01, group=G_ARSI),
    InputSpec('stAtrLen', 'int', 'ST ATR Len', 14, minval=1, group=G_ST),
    InputSpec('stFactor', 'float', 'ST Factor', 2.0, minval=0.1, step=0.1, group=G_ST),
    InputSpec('swingLen', 'int', 'Swing Len', 5, minval=3, maxval=50, group=G_BOS),
    InputSpec('atrLen', 'int', 'ATR Len', 14, minval=1, group=G_TRADE),
    InputSpec('atrMultSl', 'float', 'SL ATR Mult', 1.5, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('slStructBuf', 'float', 'SL Struct Buffer (ATR)', 0.25, minval=0.0, step=0.05, group=G_TRADE),
    InputSpec('rr1', 'float', 'TP1 R:R', 1.0, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('rr2', 'float', 'TP2 R:R', 2.0, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('rr3', 'float', 'TP3 R:R', 3.0, minval=0.1, step=0.1, group=G_TRADE),
    InputSpec('confluenceWindow', 'int', 'Confluence Window', 5, minval=1, maxval=50, group=G_TRADE),
    InputSpec('useHtfFilter', 'bool', 'Enable HTF Filter', False, group=G_HTF),
    InputSpec('htfTimeframe', 'timeframe', 'HTF TF', '240', group=G_HTF),
    InputSpec('useChopFilter', 'bool', 'Enable ADX Filter', False, group=G_CHOP),
    InputSpec('adxLen', 'int', 'ADX Len', 14, minval=1, group=G_CHOP),
    InputSpec('adxMinLevel', 'float', 'Min ADX', 20.0, minval=1.0, step=1.0, group=G_CHOP),
    InputSpec('showDash', 'bool', 'Show Dashboard', True, group=G_DASH),
    InputSpec('dashPos', 'string', 'Position', 'Top Right', options=('Top Right', 'Top Left', 'Bottom Right',
                                                                     'Bottom Left'), group=G_DASH),
    InputSpec('i_actionLong', 'string', '↑ Long Action', 'long', group=G_WH),
    InputSpec('i_actionShort', 'string', '↓ Short Action', 'short', group=G_WH),
    InputSpec('i_actionCloseLong', 'string', '✕ Close Long Action', 'closelong', group=G_WH),
    InputSpec('i_actionCloseShort', 'string', '✕ Close Short Action', 'closeshort', group=G_WH),
    InputSpec('colEntry', 'color', 'Entry', '#1E3A5CFF', group=G_COL),
    InputSpec('colSl', 'color', 'SL', '#7A1F1FFF', group=G_COL),
    InputSpec('colTp1', 'color', 'TP1', '#0E5C4AFF', group=G_COL),
    InputSpec('colTp2', 'color', 'TP2', '#0E6E52FF', group=G_COL),
    InputSpec('colTp3', 'color', 'TP3', '#12A382FF', group=G_COL),
    InputSpec('colBuyLbl', 'color', 'Buy Label', '#26A69AFF', group=G_COL),
    InputSpec('colSellLbl', 'color', 'Sell Label', '#EF5350FF', group=G_COL),
    InputSpec('zoneRiskColor', 'color', 'Risk Zone Fill', new_color('#7A1F1FFF', 55), group=G_COL),
    InputSpec('zoneRewardColor', 'color', 'Reward Zone Fill', new_color('#0E5C4AFF', 70), group=G_COL),
    InputSpec('dashBgColor', 'color', 'BG', new_color('#0A0F1AFF', 10), group=G_COL),
    InputSpec('dashHdrColor', 'color', 'Header BG', new_color('#14B5CBFF', 20), group=G_COL),
    InputSpec('dashLabelColor', 'color', 'Label Text', new_color('#FFFFFFFF', 30), group=G_COL),
    InputSpec('dashValueColor', 'color', 'Value Text', new_color('#FFFFFFFF', 0), group=G_COL),
]

META = ScriptMeta(
    id='triple_confluence_markittick', name='Triple Confluence Navigator [MarkitTick]',
    short_name='Triple Confluence Navigator [MarkitTick]', category='Custom',
    description='BUY / SELL when a Cardwell RSI cross, an adaptive-RSI supertrend cross and a structure break line up '
                'within a few bars, with entry / SL / TP levels and a dashboard. Port of the Pine v6 script by '
                'MarkitTick (CC BY-NC-SA 4.0).',
    max_lines=500, max_labels=500, label_layout=False, inputs=INPUTS,
    drawing_groups=[DrawingGroup('signals', 'BUY / SELL labels'), DrawingGroup('levels', 'Entry / SL / TP levels')],
)

GREEN, RED, AMBER = '#26A69AFF', '#EF5350FF', '#F9A825FF'


def pine_round(x: float) -> float:
    return math.floor(x + 0.5) if finite(x) else NAN


def nz(x: float, y: float) -> float:
    return y if not finite(x) else x


class Hist:
    """A float series with ``[n]`` history (na before the first value)."""

    def __init__(self, n: int):
        self.d: deque[float] = deque(maxlen=n + 1)

    def push(self, x: float) -> None:
        self.d.append(x)

    def __getitem__(self, k: int) -> float:
        return self.d[-1 - k] if k < len(self.d) else NAN


class RSI:
    """``ta.rsi``: RMA of gains and losses."""

    def __init__(self, n: int):
        self.up, self.dn, self.prev = RMA(n), RMA(n), NAN

    def update(self, x: float) -> float:
        ch = x - self.prev if finite(self.prev) else NAN
        self.prev = x
        u = self.up.update(max(ch, 0.0) if finite(ch) else NAN)
        d = self.dn.update(max(-ch, 0.0) if finite(ch) else NAN)
        if not (finite(u) and finite(d)):
            return NAN
        if d == 0:
            return 100.0
        if u == 0:
            return 0.0
        return 100 - 100 / (1 + u / d)


class ARSI:
    """``f_arsi``: adaptive RSI of the close."""

    def __init__(self, n: int):
        self.hi, self.lo, self.num, self.den = Highest(n), Lowest(n), RMA(n), RMA(n)
        self.pu = self.pl = self.pc = NAN

    def update(self, c: float) -> float:
        u, l = self.hi.update(c), self.lo.update(c)
        r = u - l
        d = c - self.pc if finite(self.pc) else NAN
        diff = r if (finite(u) and finite(self.pu) and u > self.pu) else \
            -r if (finite(l) and finite(self.pl) and l < self.pl) else d
        self.pu, self.pl, self.pc = u, l, c
        num = self.num.update(diff)
        den = self.den.update(abs(diff) if finite(diff) else NAN)
        return num / den * 50 + 50 if finite(den) and den != 0 else 50.0


class Kalman:
    """``f_kalman``"""

    def __init__(self, proc: float, meas: float):
        self.proc, self.meas, self.est, self.cov = proc, meas, NAN, 1.0

    def update(self, src: float) -> float:
        if not finite(self.est):
            self.est = src
        self.cov += self.proc
        gain = self.cov / (self.cov + self.meas)
        self.est = self.est + gain * (src - self.est)
        self.cov = (1 - gain) * self.cov
        return self.est


class Llama:
    """``f_llama``"""

    def __init__(self, n: int, amin: float, amax: float):
        self.n, self.amin, self.amax = n, amin, amax
        self.src = Hist(n)
        self.hi, self.lo = Highest(n), Lowest(n)
        self.out = NAN

    def update(self, src: float) -> float:
        if not finite(self.out):
            self.out = src
        self.src.push(src)
        slope = (src - self.src[self.n]) / self.n
        rng = self.hi.update(src) - self.lo.update(src)
        if finite(rng) and rng != 0:
            eff = min(abs(slope * self.n) / rng, 1.0) if finite(slope) else NAN
        else:
            eff = 0.0
        alpha = self.amin + eff * (self.amax - self.amin)
        self.out = self.out + alpha * (src - self.out)
        return self.out


class LinReg:
    """``ta.linreg(src, n, 0)``: the least-squares line's value on the current bar."""

    def __init__(self, n: int):
        self.n = n
        self.buf: deque[float] = deque(maxlen=n)

    def update(self, x: float) -> float:
        self.buf.append(x)
        n = self.n
        if len(self.buf) < n or not all(finite(v) for v in self.buf):
            return NAN
        xs = np.arange(n, dtype=float)
        ys = np.asarray(self.buf, dtype=float)
        sx, sy, sxx, sxy = xs.sum(), ys.sum(), (xs * xs).sum(), (xs * ys).sum()
        den = n * sxx - sx * sx
        if den == 0:
            return NAN
        slope = (n * sxy - sx * sy) / den
        intercept = (sy - slope * sx) / n
        return intercept + slope * (n - 1)


class DMI:
    """``ta.dmi(len, smooth)`` -> (+DI, -DI, ADX) with ``ta.tr`` (na on the first bar) as in Pine."""

    def __init__(self, n: int, smooth: int):
        self.tr, self.plus, self.minus, self.adx = RMA(n), RMA(n), RMA(n), RMA(smooth)
        self.ph = self.pl = self.pc = NAN
        self.last_p = self.last_m = NAN

    def update(self, h: float, l: float, c: float):
        up = h - self.ph if finite(self.ph) else NAN
        dn = self.pl - l if finite(self.pl) else NAN
        tr = max(h - l, abs(h - self.pc), abs(l - self.pc)) if finite(self.pc) else NAN
        self.ph, self.pl, self.pc = h, l, c
        plus = (up if up > dn and up > 0 else 0.0) if finite(up) else NAN
        minus = (dn if dn > up and dn > 0 else 0.0) if finite(dn) else NAN
        trur = self.tr.update(tr)
        sp, sm = self.plus.update(plus), self.minus.update(minus)
        ok = finite(trur) and trur != 0
        p = 100 * sp / trur if ok and finite(sp) else NAN
        m = 100 * sm / trur if ok and finite(sm) else NAN
        # fixnan(): keep the last valid +DI / -DI
        p = p if finite(p) else self.last_p
        m = m if finite(m) else self.last_m
        self.last_p, self.last_m = p, m
        s = p + m
        adx = 100 * self.adx.update(abs(p - m) / (1 if s == 0 else s)) if finite(s) else self.adx.update(NAN) * 100
        return p, m, adx


class TripleConfluence(Script):
    meta = META

    def required_warmup(self) -> int:
        return 2000

    def setup(self, ctx: Context) -> None:
        p = self.p
        self.rsi = RSI(p['rsiLen'])
        self.kfFastX, self.kfFastP, self.kfSlowX, self.kfSlowP = NAN, 1.0, NAN, 1.0
        self.rmaFast_rma, self.rmaSlow_rma = RMA(p['rmaFastLen']), RMA(p['rmaSlowLen'])
        self.lrFast, self.lrSlow = LinReg(p['rmaFastLen']), LinReg(p['rmaSlowLen'])
        self.rmaFast, self.rmaSlow = Hist(2), Hist(2)
        self.atr = ATR(p['atrLen'])
        self.atrHist = Hist(1)
        self.dmi = DMI(p['adxLen'], p['adxLen'])
        self.adxHist = Hist(1)
        self.arsiF = ARSI(p['arsiLen'])
        self.sigRma = RMA(p['arsiSigLen'])
        self.sigKalman = Kalman(p['kalmanProcN'], p['kalmanMeasN'])
        self.sigLlama = Llama(p['arsiSigLen'], p['llamaAlphaMin'], p['llamaAlphaMax'])
        self.arsiAtr = RMA(p['stAtrLen'])
        self.arsiHist = Hist(2)
        self.merged = Hist(2)
        self.supertrend, self.trendUp = NAN, True
        n = p['swingLen']
        self.highs, self.lows = Hist(2 * n), Hist(2 * n)
        self.lastSwingHigh = self.lastSwingLow = NAN
        self.lastSwingHighBar = self.lastSwingLowBar = None
        self.lastProcessedHighBar = self.lastProcessedLowBar = None
        self.trend3 = 0
        self.since = dict(b1=9999, s1=9999, b2=9999, s2=9999, b3=9999, s3=9999)
        self.prevBullConf = self.prevBearConf = False
        self.tradeActive, self.tradeIsLong = False, True
        self.trk = dict(sl=NAN, tp1=NAN, tp2=NAN, tp3=NAN, entry=NAN)
        self.levels: dict[str, object] = {}
        self.trkStartBar = None
        self.closeHist = Hist(1)
        self.state: dict = {}
        self.events: list[dict] = []
        self.htf = None
        if p['useHtfFilter']:
            tf = PINE_TF.get(p['htfTimeframe'], ctx.timeframe.code) if p['htfTimeframe'] else ctx.timeframe.code
            rl, fl, sl, al = p['rsiLen'], p['rmaFastLen'], p['rmaSlowLen'], p['arsiLen']

            def run(make):  # a fresh streaming chain over the higher-timeframe closes
                def expr(b):
                    step = make()
                    return np.array([step(x) for x in np.asarray(b.close, dtype=float).tolist()], dtype=float)
                return expr

            def rma_of_rsi(length):
                def make():
                    rsi, rma = RSI(rl), RMA(length)
                    return lambda x: rma.update(rsi.update(x))
                return run(make)

            rsi_only = run(lambda: RSI(rl).update)
            arsi_only = run(lambda: ARSI(al).update)

            req = lambda expr: ctx.request_security(tf, expr, lookahead=True, offset=1)
            self.htf = dict(fast=req(rma_of_rsi(fl)), slow=req(rma_of_rsi(sl)), arsi=req(arsi_only),
                            rsi=req(rsi_only))

    def on_bar(self, ctx: Context) -> None:
        p = self.p
        i = ctx.bar_index
        h, l, c = ctx.high, ctx.low, ctx.close
        confirmed = ctx.is_confirmed

        rsiValue = self.rsi.update(c)
        # Kalman fast / slow on the RSI (state updates every bar, also while the RSI is still na)
        kfQ, kfR = 0.001, 0.01
        kfFastK = self.kfFastP / (self.kfFastP + kfR)
        self.kfFastX = nz(self.kfFastX, rsiValue) + kfFastK * (rsiValue - nz(self.kfFastX, rsiValue))
        self.kfFastP = (1.0 - kfFastK) * self.kfFastP + kfQ
        kfSlowK = self.kfSlowP / (self.kfSlowP + kfR * (p['rmaSlowLen'] / p['rmaFastLen']))
        self.kfSlowX = nz(self.kfSlowX, rsiValue) + kfSlowK * (rsiValue - nz(self.kfSlowX, rsiValue))
        self.kfSlowP = (1.0 - kfSlowK) * self.kfSlowP + kfQ
        lrF, lrS = self.lrFast.update(rsiValue), self.lrSlow.update(rsiValue)
        rmF, rmS = self.rmaFast_rma.update(rsiValue), self.rmaSlow_rma.update(rsiValue)
        mt = p['maType']
        self.rmaFast.push(self.kfFastX if mt == 'Kalman' else lrF if mt == 'LLAMA' else rmF)
        self.rmaSlow.push(self.kfSlowX if mt == 'Kalman' else lrS if mt == 'LLAMA' else rmS)
        f, s = self.rmaFast, self.rmaSlow
        rawBullCross = f[1] > s[1] and f[2] <= s[2]
        rawBearCross = f[1] < s[1] and f[2] >= s[2]
        atrValue = self.atr.update(h, l, c)
        atrValue1 = nz(self.atrHist[0], atrValue)
        self.atrHist.push(atrValue)

        hv = {k: ctx.security(v) for k, v in self.htf.items()} if self.htf else {}
        htfBullBias = hv.get('fast', NAN) > hv.get('slow', NAN)
        htfBearBias = hv.get('fast', NAN) < hv.get('slow', NAN)
        useHtf = p['useHtfFilter']
        bullCross1 = rawBullCross and (not useHtf or htfBullBias)
        bearCross1 = rawBearCross and (not useHtf or htfBearBias)
        _, _, adxValue = self.dmi.update(h, l, c)
        chopOk = not p['useChopFilter'] or self.adxHist[0] >= p['adxMinLevel']
        self.adxHist.push(adxValue)
        bullSignal1 = bullCross1 and chopOk
        bearSignal1 = bearCross1 and chopOk

        arsi = self.arsiF.update(c)
        ft = p['arsiFiltType']
        sr, sk, sll = self.sigRma.update(arsi), self.sigKalman.update(arsi), self.sigLlama.update(arsi)
        arsiSignal = sr if ft == 'RMA' else sk if ft == 'Kalman Filter' else sll
        a1 = self.arsiHist[0]
        arsiAtr = self.arsiAtr.update(abs(arsi - a1) if finite(a1) else NAN)
        upperBand = arsi + p['stFactor'] * arsiAtr
        lowerBand = arsi - p['stFactor'] * arsiAtr
        prevST, prevUp = self.supertrend, self.trendUp
        if not finite(prevST):
            self.supertrend, self.trendUp = upperBand, True
        else:
            self.trendUp = True if arsi > prevST else False if arsi < prevST else prevUp
            self.supertrend = max(lowerBand, prevST) if self.trendUp else min(upperBand, prevST)
        rawMerged = arsiSignal if not finite(self.supertrend) else (arsiSignal + self.supertrend) / 2
        self.arsiHist.push(arsi)
        self.merged.push(rawMerged)
        A, M = self.arsiHist, self.merged
        ok = all(finite(x) for x in (A[1], A[2], M[1], M[2]))
        rawBullCross2 = ok and A[1] > M[1] and A[2] <= M[2]
        rawBearCross2 = ok and A[1] < M[1] and A[2] >= M[2]
        bullSignal2 = rawBullCross2 and (not useHtf or hv.get('arsi', NAN) >= 50.0)
        bearSignal2 = rawBearCross2 and (not useHtf or hv.get('arsi', NAN) < 50.0)
        htfOkBull3 = not useHtf or hv.get('rsi', NAN) > 50.0
        htfOkBear3 = not useHtf or hv.get('rsi', NAN) < 50.0

        # structure: custom pivots (only on confirmed bars)
        n = p['swingLen']
        self.highs.push(h)
        self.lows.push(l)
        H, Lw = self.highs, self.lows

        def pivot(arr, high):
            cnd = arr[n]
            ok = confirmed
            for k in range(n):
                if (arr[k] >= cnd) if high else (arr[k] <= cnd):
                    ok = False
            for k in range(n + 1, 2 * n + 1):
                if (arr[k] > cnd) if high else (arr[k] < cnd):
                    ok = False
            return cnd if ok else NAN

        ph, pl = pivot(H, True), pivot(Lw, False)
        if finite(ph):
            self.lastSwingHigh, self.lastSwingHighBar = ph, i - n
        if finite(pl):
            self.lastSwingLow, self.lastSwingLowBar = pl, i - n
        breakUp = (confirmed and finite(self.lastSwingHigh) and c > self.lastSwingHigh and htfOkBull3
                   and (self.lastProcessedHighBar is None or self.lastSwingHighBar != self.lastProcessedHighBar))
        breakDown = (confirmed and finite(self.lastSwingLow) and c < self.lastSwingLow and htfOkBear3
                     and (self.lastProcessedLowBar is None or self.lastSwingLowBar != self.lastProcessedLowBar))
        if breakUp:
            self.lastProcessedHighBar, self.trend3 = self.lastSwingHighBar, 1
        if breakDown:
            self.lastProcessedLowBar, self.trend3 = self.lastSwingLowBar, -1
        bullSignal3, bearSignal3 = breakUp, breakDown

        S = self.since
        for k, sig in (('b1', bullSignal1), ('s1', bearSignal1), ('b2', bullSignal2), ('s2', bearSignal2),
                       ('b3', bullSignal3), ('s3', bearSignal3)):
            S[k] = 0 if sig else S[k] + 1
        w = p['confluenceWindow']
        bullConf = S['b1'] <= w and S['b2'] <= w and S['b3'] <= w
        bearConf = S['s1'] <= w and S['s2'] <= w and S['s3'] <= w
        longSignal = bullConf and not self.prevBullConf and confirmed
        shortSignal = bearConf and not self.prevBearConf and confirmed
        self.prevBullConf, self.prevBearConf = bullConf, bearConf

        prevClose = self.closeHist[0]
        self.closeHist.push(c)
        for is_long, sig in ((True, longSignal), (False, shortSignal)):
            if sig:
                self._open_trade(ctx, is_long, prevClose, atrValue1)
        if self.levels:
            x2 = min(i, self.trkStartBar + 50)
            t2 = ctx.time_at(x2)
            for k, obj in self.levels.items():
                if k.endswith('Lbl'):
                    obj.x = t2
                elif k.endswith('Fill'):
                    obj.right = t2
                else:
                    obj.x2 = t2

        for is_long, sig in ((True, longSignal), (False, shortSignal)):
            if sig and confirmed:
                self.events.append(dict(time=ctx.time, title='BUY Signal' if is_long else 'SELL Signal'))
                del self.events[:-500]
        if longSignal:
            ctx.label(x=ctx.time, y=l, text='BUY', yloc='belowbar', style='label_up', color=p['colBuyLbl'],
                      text_color=WHITE, size='small', group='signals')
        if shortSignal:
            ctx.label(x=ctx.time, y=h, text='SELL', yloc='abovebar', style='label_down', color=p['colSellLbl'],
                      text_color=WHITE, size='small', group='signals')

        self.state = dict(b1=bullSignal1, s1=bearSignal1, b2=bullSignal2, s2=bearSignal2, b3=bullSignal3,
                          s3=bearSignal3, rsi=rsiValue, adx=adxValue, htfBull=htfBullBias, close=c)

    def _open_trade(self, ctx: Context, is_long: bool, entry: float, atr1: float) -> None:
        p = self.p
        for obj in self.levels.values():
            ctx.delete(obj)
        self.levels = {}
        capDist = atr1 * p['atrMultSl']
        if is_long:
            struct = entry - (self.lastSwingLow - atr1 * p['slStructBuf']) if finite(self.lastSwingLow) else NAN
        else:
            struct = (self.lastSwingHigh + atr1 * p['slStructBuf']) - entry if finite(self.lastSwingHigh) else NAN
        risk = capDist if not finite(struct) or struct <= 0 else min(struct, capDist)
        d = 1 if is_long else -1
        sl = entry - d * risk
        tp1, tp2, tp3 = (entry + d * risk * p[k] for k in ('rr1', 'rr2', 'rr3'))
        t = ctx.time
        lv = self.levels

        def line(y, col):
            return ctx.line(x1=t, y1=y, x2=t, y2=y, color=col, width=1, style='dashed', group='levels')

        lv['slLine'] = line(sl, p['colSl'])
        lv['entryLine'] = line(entry, p['colEntry'])
        lv['tp1Line'] = line(tp1, p['colTp1'])
        lv['tp2Line'] = line(tp2, p['colTp2'])
        lv['tp3Line'] = line(tp3, p['colTp3'])
        # linefill(slLine, entryLine) / linefill(entryLine, tp3Line): boxes over the same bars as the lines
        lv['riskFill'] = ctx.box(left=t, right=t, top=max(sl, entry), bottom=min(sl, entry), border_width=0,
                                 border_color='#00000000', bg_color=p['zoneRiskColor'], group='levels')
        lv['rewardFill'] = ctx.box(left=t, right=t, top=max(tp3, entry), bottom=min(tp3, entry), border_width=0,
                                   border_color='#00000000', bg_color=p['zoneRewardColor'], group='levels')
        prec = ctx.precision
        for key, y, text, col in (('slLbl', sl, '✕ SL  ', p['colSl']), ('entryLbl', entry, '▶ Entry  ', p['colEntry']),
                                  ('tp1Lbl', tp1, '● TP1  ', p['colTp1']), ('tp2Lbl', tp2, '★ TP2  ', p['colTp2']),
                                  ('tp3Lbl', tp3, '◆ TP3  ', p['colTp3'])):
            lv[key] = ctx.label(x=t, y=y, text=text + fmt_price(y, prec), style='label_left', color=col,
                                text_color=WHITE, size='small', group='levels')
        self.tradeActive, self.tradeIsLong = True, is_long
        self.trk = dict(sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, entry=entry)
        self.trkStartBar = ctx.bar_index

    def on_last_bar(self, ctx: Context) -> None:
        self.table = None
        p = self.p
        if not p['showDash'] or not self.state:
            return
        st = self.state
        pos = {'Top Left': 'top_left', 'Bottom Right': 'bottom_right', 'Bottom Left': 'bottom_left'}.get(
            p['dashPos'], 'top_right')
        edge = new_color('#14B5CBFF', 60)
        t = Table('triple_confluence', pos, 2, 9, border_width=1, border_color=edge, frame_width=1, frame_color=edge)
        lblBg, valBg = new_color('#0A0F1AFF', 0), new_color('#0D1117FF', 0)
        t.cell(0, 0, 'Triple Confluence', text_color=p['dashValueColor'], bgcolor=p['dashHdrColor'],
               text_halign='left', text_size='normal')
        t.cell(1, 0, '', bgcolor=p['dashHdrColor'], text_size='normal')

        def row(r, label, value, col):
            t.cell(0, r, label, text_color=p['dashLabelColor'], bgcolor=lblBg, text_halign='left', text_size='small')
            t.cell(1, r, value, text_color=col, bgcolor=valBg, text_halign='right', text_size='small')

        def sig(b, s):
            return ('▲ Bull', GREEN) if b else ('▼ Bear', RED) if s else ('● Wait', AMBER)

        def block_bar(raw, dom):
            pct = min(max(raw / dom * 100.0, 0.0), 100.0) if finite(raw) else NAN
            n = pine_round(pct / 10.0) if finite(pct) else NAN
            bar = ''.join('█' if finite(n) and k <= n else '░' for k in range(1, 11))
            return bar + '  ' + fmt_pattern(pine_round(pct), '##0') + '%'

        def bar_color(pct):
            return '#26A69AFF' if finite(pct) and pct >= 0.66 else '#F9A825FF' if finite(pct) and pct >= 0.33 \
                else '#EF5350FF'

        row(1, 'Cardwell', *sig(st['b1'], st['s1']))
        row(2, 'Adaptive ARSI', *sig(st['b2'], st['s2']))
        tr = self.trend3
        sv = sig(st['b3'], st['s3']) if st['b3'] or st['s3'] else ('▲ Bull', GREEN) if tr == 1 else \
            ('▼ Bear', RED) if tr == -1 else ('● Wait', AMBER)
        row(3, 'Structure', *sv)
        row(4, 'RSI', block_bar(st['rsi'], 100.0), bar_color(st['rsi'] / 100.0))
        row(5, 'ADX', block_bar(st['adx'], 50.0), bar_color(st['adx'] / 50.0))
        useHtf = p['useHtfFilter']
        row(6, 'HTF Bias', ('📈 Bull' if st['htfBull'] else '📉 Bear') if useHtf else 'Off',
            (GREEN if st['htfBull'] else RED) if useHtf else GRAY)
        row(7, 'Trade', ('▲ Long' if self.tradeIsLong else '▼ Short') if self.tradeActive else '—',
            (GREEN if self.tradeIsLong else RED) if self.tradeActive else GRAY)
        risk = abs(self.trk['entry'] - self.trk['sl'])
        cur = NAN
        if self.tradeActive and finite(risk) and risk > 0:
            cur = (st['close'] - self.trk['entry']) / risk if self.tradeIsLong else (self.trk['entry'] - st['close']) / risk
        row(8, 'Live R', '—' if not finite(cur) else ('+' if cur >= 0 else '') + fmt_pattern(cur, '#.##') + 'R',
            GRAY if not finite(cur) else GREEN if cur >= 0 else RED)
        self.table = t

    def extra_output(self) -> dict:
        t = getattr(self, 'table', None)
        js = t.to_json() if t else None
        return dict(tables=[js] if js else [])
