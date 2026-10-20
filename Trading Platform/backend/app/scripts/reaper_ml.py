"""Machine-learning filter for Liquidity Reaper [JOAT] — the part that runs inside the indicator.

HOW THE ML FITS IN (meta-labelling)
  REAPER still finds every raid with its own rules (pools, sweep, confirmation, filters). The model does not create
  signals; it only looks at each signal REAPER already produced and estimates the probability that the trade reaches
  its target (3R) before its stop. Signals the model rates in the lowest part of what it saw in training are skipped.
  So the ML can only remove trades, never add or reverse them.

THE THREE PIECES
  1. FeatureState — updated on EVERY bar by the indicator (EMAs, RSI, ADX/DMI, MACD, ATR average, ...). It keeps the
     few running values the features need, using only the current and past bars (no lookahead).
  2. signal_features() — called on the bar a signal confirms; turns that bar's state plus the sweep facts (wick, depth,
     pool age, room to the next pool, 1-minute delta, 4H trend...) into one row of numbers in FEATURES order.
     Direction-dependent values are multiplied by the signal side, so "with the trade" is always positive.
  3. Model — a trained model stored as JSON (trained in Google Colab: research/reaper_ml/colab_train.ipynb, which
     downloads reaper_ml_model.json into app/scripts/). Evaluated here with plain Python (gradient-boosted trees,
     random forest or logistic regression), so the platform needs no ML library at runtime.

THE SAME CODE BUILDS THE TRAINING DATA
  research/reaper_ml/dataset.py runs the real indicator and records signal_features() at every signal. Training and
  live use therefore see exactly the same numbers; there is no second implementation that could drift.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ..engine.ta import EMA, RMA, SMA
from .pinelib import NAN, finite
from .smc_master.technical import DMI, RSI

MODEL_PATH = Path(__file__).with_name('reaper_ml_model.json')

# One row per signal, in this order. "side" = +1 buy / -1 sell; "(x side)" = multiplied by side so positive = with
# the trade. ATR is ATR(14) of the chart timeframe at the signal bar.
FEATURES = (
    'wick_pct',          # sweep wick beyond the pool, % of the raid candle's range
    'depth_atr',         # how far the wick went past the pool level, in ATR
    'close_from_level',  # signal close distance back inside the level, in ATR
    'bars_since_raid',   # 1..confirmWindow: how long the confirmation took
    'vol_mult',          # signal candle volume / its 20-bar average
    'pool_age',          # bars between the pool's swing and the raid, log scale
    'room_atr',          # distance to the nearest opposite pool (next liquidity target), in ATR, capped at 20
    'body_pct',          # signal candle body / range
    'close_pos',         # where the signal candle closed in its range (1 = at the high for a buy / low for a sell)
    'range_atr',         # signal candle range / ATR
    'risk_atr',          # stop distance / ATR (depends on the stop setting)
    'trend_h4',          # 4H EMA50 vs EMA200 of the last completed 4H candle (x side): +1 with, -1 against, 0 unknown
    'slope50',           # EMA50 change over 10 bars / ATR (x side)
    'dist200',           # (close - EMA200) / ATR (x side)
    'adx',               # ADX(14)
    'di_diff',           # (+DI - -DI) / (+DI + -DI) (x side)
    'rsi',               # RSI(14), as 100-RSI for sells
    'macd_hist',         # MACD(12,26,9) histogram / ATR (x side)
    'atr_ratio',         # ATR / its 100-bar average (volatility regime)
    'delta',             # 1-minute order-flow delta of the signal candle (x side), 0 when unavailable
    'hour',              # UTC hour of the signal candle
    'weekday',           # 0 = Monday
    'tf_minutes',        # chart timeframe in minutes, log scale (one model serves all timeframes)
)


class FeatureState:
    """Running values for the features, updated once per bar with update(); read by signal_features()."""

    def __init__(self):
        self.e12, self.e26, self.sig = EMA(12), EMA(26), EMA(9)
        self.e50, self.e200 = EMA(50), EMA(200)
        self.e50_hist: list[float] = []  # last 11 EMA50 values for the 10-bar slope
        self.rsi = RSI(14)
        self.dmi = DMI(14, 14)
        self.atr_avg = SMA(100)
        self.vals = dict(rsi=NAN, pdi=NAN, mdi=NAN, adx=NAN, macd_hist=NAN, ema50=NAN, ema200=NAN, slope=NAN, atr_avg=NAN)

    def update(self, h: float, l: float, c: float, atr: float) -> None:
        v = self.vals
        v['rsi'] = self.rsi.update(c)
        v['pdi'], v['mdi'], v['adx'] = self.dmi.update(h, l, c)
        macd = self.e12.update(c) - self.e26.update(c)
        v['macd_hist'] = macd - self.sig.update(macd) if finite(macd) else NAN
        v['ema50'], v['ema200'] = self.e50.update(c), self.e200.update(c)
        self.e50_hist.append(v['ema50'])
        del self.e50_hist[:-11]
        v['slope'] = self.e50_hist[-1] - self.e50_hist[0] if len(self.e50_hist) == 11 else NAN
        v['atr_avg'] = self.atr_avg.update(atr)


def signal_features(st: FeatureState, *, side: int, o: float, h: float, l: float, c: float, atr: float, level: float,
                    extreme: float, raid_range: float, bars_since_raid: int, vol_mult: float, pool_age: int,
                    room: float, risk: float, trend_h4: int, delta: float, time: int, tf_seconds: int) -> list[float]:
    """The feature row of one signal (FEATURES order). Missing values are NaN; the model handles them."""
    v = st.vals
    a = atr if finite(atr) and atr > 0 else NAN
    rng = h - l
    div = lambda x, y: x / y if finite(x) and finite(y) and y != 0 else NAN
    di = div(v['pdi'] - v['mdi'], v['pdi'] + v['mdi'])
    return [
        div(abs(extreme - level), raid_range) * 100,
        div(abs(extreme - level), a),
        div((c - level) * side, a),
        float(bars_since_raid),
        vol_mult,
        math.log1p(max(pool_age, 0)),
        min(div(room, a), 20.0) if finite(room) else 20.0,
        div(abs(c - o), rng),
        div(c - l, rng) if side == 1 else div(h - c, rng),
        div(rng, a),
        div(risk, a),
        float(trend_h4 * side),
        div(v['slope'], a) * side if finite(v['slope']) else NAN,
        div(c - v['ema200'], a) * side if finite(v['ema200']) else NAN,
        v['adx'],
        di * side if finite(di) else NAN,
        v['rsi'] if side == 1 else (100 - v['rsi'] if finite(v['rsi']) else NAN),
        div(v['macd_hist'], a) * side if finite(v['macd_hist']) else NAN,
        div(atr, v['atr_avg']),
        delta * side if finite(delta) else 0.0,
        float((time // 3600) % 24),
        float(((time // 86400) + 3) % 7),
        math.log(tf_seconds / 60),
    ]


class Model:
    """A trained model from JSON. kind 'logistic': standardise, dot product, sigmoid. kind 'trees': sum of
    gradient-boosted regression trees (sklearn HistGradientBoosting layout) + baseline, sigmoid. kind 'forest': random
    forest — missing inputs replaced by the training median, then the average of the trees' leaf win-rates. kind
    'oblivious': CatBoost (symmetric trees: every level of a tree asks the same question; leaf index = the answers as
    bits, first split = lowest bit), raw = scale x sum of leaf values + bias, sigmoid.
    kind 'mlp': small neural network — standardise, hidden layers with ReLU, sigmoid output.
    ``mode``: 'skip' (default) drops signals below the threshold; 'half_size' keeps them and marks them half size.
    ``threshold``: signals scoring below it are skipped (chosen in training as the cut that skips the lowest
    ``skip_pct`` % of training signals)."""

    def __init__(self, spec: dict):
        if tuple(spec['features']) != FEATURES:
            raise ValueError('ML model was trained on different features; retrain it')
        self.spec = spec
        self.kind = spec['kind']
        self.threshold = float(spec['threshold'])

    @classmethod
    def load(cls, path: Path | None = None) -> 'Model | None':
        path = path or MODEL_PATH
        return cls(json.loads(path.read_text())) if path.exists() else None

    def prob(self, x: list[float]) -> float:
        s = self.spec
        if self.kind == 'mlp':
            h = [((v if finite(v) else f) - m) / sd for v, f, m, sd in zip(x, s['fill'], s['mean'], s['std'])]
            for layer, (W, b) in enumerate(zip(s['weights'], s['biases'])):
                h = [sum(hi * W[i][j] for i, hi in enumerate(h)) + b[j] for j in range(len(b))]
                if layer < len(s['weights']) - 1:
                    h = [max(0.0, v) for v in h]
            return 1.0 / (1.0 + math.exp(-h[0]))
        if self.kind == 'oblivious':
            tot = 0.0
            for tree in s['trees']:
                idx = 0
                for bit, (f, border) in enumerate(zip(tree['feature'], tree['border'])):
                    xv = x[f] if finite(x[f]) else -math.inf  # CatBoost default: missing = smaller than every border
                    if xv > border:
                        idx |= 1 << bit
                tot += tree['leaf_values'][idx]
            z = s['scale'] * tot + s['bias']
            return 1.0 / (1.0 + math.exp(-z))
        if self.kind == 'forest':
            xs = [s['fill'][k] if not finite(v) else v for k, v in enumerate(x)]
            tot = 0.0
            for tree in s['trees']:
                n = 0
                while tree['left'][n] != -1:
                    # sklearn decision trees compare float32 inputs; do the same so the split is identical
                    n = tree['left'][n] if float(np.float32(xs[tree['feature'][n]])) <= tree['threshold'][n] else tree['right'][n]
                tot += tree['value'][n]
            return tot / len(s['trees'])
        if self.kind == 'logistic':
            z = s['intercept']
            for xi, m, sd, w, fill in zip(x, s['mean'], s['std'], s['coef'], s['fill']):
                xi = fill if not finite(xi) else xi
                z += w * (xi - m) / sd
        else:
            z = s['baseline']
            for tree in s['trees']:
                n = 0
                while not tree['leaf'][n]:
                    xi = x[tree['feature'][n]]
                    if not finite(xi):
                        n = tree['left'][n] if tree['missing_left'][n] else tree['right'][n]
                    else:
                        n = tree['left'][n] if xi <= tree['threshold'][n] else tree['right'][n]
                z += tree['value'][n]
        return 1.0 / (1.0 + math.exp(-z))

    def keep(self, x: list[float]) -> tuple[bool, float]:
        p = self.prob(x)
        return p >= self.threshold, p


def probs(model: Model, X: np.ndarray) -> np.ndarray:
    """Vectorised helper for research: one probability per row."""
    return np.array([model.prob(list(r)) for r in X])
