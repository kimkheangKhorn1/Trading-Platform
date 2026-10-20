"""Writes research/reaper_ml/colab_tuned_2018_2025.ipynb: nested walk-forward hyper-parameter tuning (Optuna) of
boosted trees, CatBoost, the scorecard and a neural network on 15m-4H signals, with 2026 kept as an untouched final test."""
from __future__ import annotations

import json
from pathlib import Path

MD, CODE = 'markdown', 'code'
CSV, HOLD = 'reaper_ml_dataset_2018-01-01_2026-01-01.csv', 'reaper_ml_holdout_2026.csv'
CELLS = [
(MD, rf"""# REAPER [JOAT] — tuned ML (nested walk-forward), 15m–4H

**Goal.** Let each model choose its own settings ("tuning") and how many weak signals to filter out, without overfitting.
Fewer trades are allowed (filtering bad trades), but at least **40 %** of the trades must remain in every year.

**Data.** `{CSV}` (2018 → 2025, tuning and testing) and `{HOLD}` (2026 Jan–Sep, **untouched until the very end**).
Real REAPER signals ("Most consistent" preset), 23 inputs at the signal close, result = 3R target or stop on real
bid/ask. **15m–4H only**, so a model cannot simply learn "skip 5m".

**How overfitting is prevented (fixed before running).**
1. **Nested walk-forward.** For each test year 2020 … 2025, the settings are tuned **only inside the earlier years**
   (inner yearly folds: train on the years before, check on the next one). The test year is then judged once.
2. **What is tuned** (30 Optuna trials per model per year): the model's knobs, the share of signals filtered out
   (20 / 30 / 40 / 50 %), and skip vs half size. The tuning score is the total R on the inner check years.
3. **Pass rules** (the 6 test years together): more R than without ML, better in at least 4 of 6 years, better than 90 %
   of random filters that remove the same share of trades each year, and ≥ 40 % of trades kept every year.
4. **Final exam: 2026 Jan–Sep**, never seen by any step: the passing model is tuned once on 2018–2025 and judged on 2026
   once. It is only recommended if it also beats "no ML" there."""),
(CODE, r"""# 1. Setup
!pip -q install catboost optuna
import json, math, os, io, warnings
import numpy as np, pandas as pd, matplotlib.pyplot as plt
import optuna, sklearn, catboost
from catboost import CatBoostClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import roc_auc_score
warnings.filterwarnings('ignore'); optuna.logging.set_verbosity(optuna.logging.WARNING)
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
print('optuna', optuna.__version__, '| catboost', catboost.__version__, '| scikit-learn', sklearn.__version__)"""),
(MD, rf"""## 2. Load the data
Drop `{CSV}` and `{HOLD}` into the Files panel (or run and choose them)."""),
(CODE, rf"""from google.colab import files
def load(name):
    p = '/content/' + name
    if os.path.exists(p): return pd.read_csv(p)
    print('choose', name); up = files.upload(); return pd.read_csv(io.BytesIO(up[next(iter(up))]))
ts = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp())
MAIN = ['M15', 'M30', 'H1', 'H4']
df, hold = load('{CSV}'), load('{HOLD}')
FEATURES = list(df.columns[df.columns.get_loc('wick_pct'):df.columns.get_loc('tf_minutes') + 1])
for d in (df, hold):
    d['year'] = pd.to_datetime(d['time'], unit='s', utc=True).dt.year
df = df[df.tf.isin(MAIN)].sort_values(['time', 'tf']).reset_index(drop=True)
hold = hold[hold.tf.isin(MAIN)].sort_values(['time', 'tf']).reset_index(drop=True)
assert df.time.max() < ts('2026-01-01') and hold.time.min() >= ts('2026-01-01')  # 2026 really is separate
assert df[FEATURES].notna().all().all() and hold[FEATURES].notna().all().all()
print(f'tuning/testing: {{len(df)}} signals 2018-2025 | final exam: {{len(hold)}} signals 2026 Jan-Sep (15m-4H)')
display(df.pivot_table(index='year', columns='tf', values='r', aggfunc='size')[MAIN])"""),
(MD, r"""## 3. Models and their tunable knobs"""),
(CODE, r"""def replay(part, weight):
    # one trade at a time per timeframe; weight 0 = filtered out, 0.5 = half size, 1 = full
    out = []
    for tf, g in part.assign(w=weight).groupby('tf'):
        free = -1
        for row in g.sort_values('bar').itertuples():
            if row.w > 0 and row.bar >= free:
                out.append((tf, row.r * row.w, row.w)); free = row.exit_bar
    return pd.DataFrame(out, columns=['tf', 'R', 'w'])

def pick_inputs(tr, k):
    cand = sorted(((abs(roc_auc_score(tr.win, tr[f]) - 0.5), f) for f in FEATURES if tr[f].nunique() >= 3), reverse=True)
    chosen = []
    for _, f in cand:
        if all(abs(np.corrcoef(tr[f], tr[g])[0, 1]) <= 0.8 for g in chosen): chosen.append(f)
        if len(chosen) == k: break
    return chosen

class Model:
    def __init__(self, kind, p): self.kind, self.p = kind, p
    def fit(self, tr):
        p, y = self.p, tr.win.values
        if self.kind == 'scorecard':
            self.cols = pick_inputs(tr, p['k'])
        else:
            self.cols = FEATURES
        X = tr[self.cols].values
        self.mean, self.std = X.mean(0), X.std(0) + 1e-12
        Z = (X - self.mean) / self.std
        if self.kind == 'boosted trees':
            self.m = HistGradientBoostingClassifier(max_depth=p['depth'], learning_rate=p['lr'], max_iter=p['trees'],
                                                    min_samples_leaf=p['leaf'], l2_regularization=p['l2'], random_state=0).fit(X, y)
        elif self.kind == 'CatBoost':
            self.m = CatBoostClassifier(iterations=p['trees'], depth=p['depth'], learning_rate=p['lr'], l2_leaf_reg=p['l2'],
                                        random_seed=0, verbose=0, allow_writing_files=False).fit(X, y)
        elif self.kind == 'scorecard':
            self.m = LogisticRegression(C=p['C'], max_iter=2000).fit(Z, y)
        else:
            self.m = MLPClassifier(hidden_layer_sizes=(p['h1'], p['h2']), alpha=p['alpha'], learning_rate_init=p['lr'],
                                   early_stopping=True, max_iter=400, random_state=0).fit(Z, y)
        return self
    def score(self, te):
        X = te[self.cols].values
        return self.m.predict_proba(X if self.kind in ('boosted trees', 'CatBoost') else (X - self.mean) / self.std)[:, 1]

def suggest(kind, t):
    if kind == 'boosted trees':
        return dict(depth=t.suggest_int('depth', 2, 5), lr=t.suggest_float('lr', 0.02, 0.2, log=True), trees=t.suggest_int('trees', 50, 300),
                    leaf=t.suggest_int('leaf', 20, 120), l2=t.suggest_float('l2', 0.0, 5.0))
    if kind == 'CatBoost':
        return dict(depth=t.suggest_int('depth', 2, 6), lr=t.suggest_float('lr', 0.02, 0.2, log=True), trees=t.suggest_int('trees', 100, 400),
                    l2=t.suggest_float('l2', 1.0, 10.0))
    if kind == 'scorecard':
        return dict(k=t.suggest_int('k', 3, 10), C=t.suggest_float('C', 0.01, 10.0, log=True))
    return dict(h1=t.suggest_categorical('h1', [8, 16, 32, 64]), h2=t.suggest_categorical('h2', [4, 8, 16]),
                alpha=t.suggest_float('alpha', 1e-4, 1.0, log=True), lr=t.suggest_float('lr', 1e-4, 1e-2, log=True))

KINDS = ['boosted trees', 'CatBoost', 'scorecard', 'neural network']
SKIPS, MIN_KEEP, TRIALS = [0.2, 0.3, 0.4, 0.5], 0.40, 30"""),
(MD, r"""## 4. Nested tuning + walk-forward test (several minutes per model)
For a test year Y, the tuner only sees years before Y. Inner folds: for each earlier year V (from the second year on),
train on the years before V, score V. The tuning score is the total R on those inner years (one trade at a time,
≥ 40 % kept)."""),
(CODE, r"""def tune(kind, train):
    years = sorted(train.year.unique())
    folds = [(train[train.year < v], train[train.year == v]) for v in years[1:]]
    def objective(t):
        p = suggest(kind, t); skip = t.suggest_categorical('skip', SKIPS); use = t.suggest_categorical('use', ['skip', 'half size'])
        total, scores = 0.0, []
        for tr, va in folds:
            m = Model(kind, p).fit(tr); s = m.score(va); scores.append(s)
            thr = np.quantile(s, skip)  # cut placed from this fold's own score distribution
            w = np.where(s < thr, 0.0 if use == 'skip' else 0.5, 1.0)
            base, ml = replay(va, np.ones(len(va))), replay(va, w)
            if len(ml) < MIN_KEEP * len(base): return -1e9
            total += ml.R.sum()
        t.set_user_attr('scores', np.concatenate(scores).tolist())
        return total
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(objective, n_trials=TRIALS)
    best = study.best_trial
    params = {k: v for k, v in best.params.items() if k not in ('skip', 'use')}
    thr = float(np.quantile(best.user_attrs['scores'], best.params['skip']))  # cut from inner out-of-sample scores
    return params, best.params['skip'], best.params['use'], thr, best.value

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
rows, chosen = [], []
for kind in KINDS:
    for yi, Y in enumerate(YEARS):
        train, te = df[df.year < Y], df[df.year == Y]
        params, skip, use, thr, inner = tune(kind, train)
        m = Model(kind, params).fit(train); s = m.score(te)
        w = np.where(s < thr, 0.0 if use == 'skip' else 0.5, 1.0)
        base, ml = replay(te, np.ones(len(te))), replay(te, w)
        rng = np.random.default_rng(1000 + yi)
        plac = [replay(te, np.where(rng.random(len(te)) < (w < 1).mean(), 0.0 if use == 'skip' else 0.5, 1.0)).R.sum() for _ in range(200)]
        rows.append(dict(model=kind, year=Y, no_ML_trades=len(base), no_ML_R=base.R.sum(), ML_trades=len(ml), ML_half=int((ml.w == 0.5).sum()),
                         ML_R=ml.R.sum(), placebo=plac, filtered=float((w < 1).mean()), use=use, kept=len(ml) / max(len(base), 1)))
        chosen.append(dict(model=kind, year=Y, use=use, filter_share=skip, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in params.items()}))
        print(f'{kind:15s} {Y}: no ML {base.R.sum():+6.1f}R/{len(base)} | ML {ml.R.sum():+6.1f}R/{len(ml)} ({use}, filter {skip:.0%})', flush=True)
res = pd.DataFrame(rows)
display(pd.DataFrame(chosen).set_index(['model', 'year']))"""),
(CODE, r"""gain = res.assign(gain=res.ML_R - res.no_ML_R).pivot(index='model', columns='year', values='gain').loc[KINDS]
display(gain.style.format('{:+.1f}').background_gradient(cmap='RdYlGn', vmin=-40, vmax=40).set_caption('ML gain in R per test year, 15m-4H'))
dec = []
for kind, g in res.groupby('model', sort=False):
    b, m = g.no_ML_R.sum(), g.ML_R.sum(); better = int((g.ML_R > g.no_ML_R).sum())
    beats = float(np.mean(m > np.sum(np.array(g.placebo.tolist()), axis=0)))
    dec.append(dict(model=kind, no_ML_R=b, ML_R=m, years_better=f'{better}/6', beats_random=beats,
                    trades_kept=g.ML_trades.sum() / g.no_ML_trades.sum(), min_kept=g.kept.min(),
                    accepted=bool(m > b and better >= 4 and beats >= 0.90 and g.kept.min() >= MIN_KEEP)))
dec = pd.DataFrame(dec).set_index('model').sort_values('ML_R', ascending=False)
display(dec.style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}', 'beats_random': '{:.0%}', 'trades_kept': '{:.0%}', 'min_kept': '{:.0%}'})
        .apply(lambda r: ['background-color:#1b5e20' if r.accepted else '' for _ in r], axis=1))
ok = dec[dec.accepted]; BEST = ok.ML_R.idxmax() if len(ok) else None
print('Passed the walk-forward test:', list(ok.index) or 'none', '| chosen for the final exam:', BEST)"""),
(MD, r"""## 5. Final exam — 2026 Jan–Sep (never used before this cell)
The chosen model is tuned once on all of 2018–2025, then judged on 2026 once."""),
(CODE, r"""cands = [BEST] if BEST else []
for kind in cands:
    params, skip, use, thr, _ = tune(kind, df)
    m = Model(kind, params).fit(df); s = m.score(hold)
    w = np.where(s < thr, 0.0 if use == 'skip' else 0.5, 1.0)
    base, ml = replay(hold, np.ones(len(hold))), replay(hold, w)
    rng = np.random.default_rng(7)
    plac = np.array([replay(hold, np.where(rng.random(len(hold)) < (w < 1).mean(), 0.0 if use == 'skip' else 0.5, 1.0)).R.sum() for _ in range(300)])
    t = pd.DataFrame({'no ML trades': base.groupby('tf').size(), 'no ML R': base.groupby('tf').R.sum(),
                      'ML trades': ml.groupby('tf').size(), 'ML R': ml.groupby('tf').R.sum()}).loc[MAIN].fillna(0)
    display(t.style.format({'no ML R': '{:+.1f}', 'ML R': '{:+.1f}'}).set_caption(f'2026 Jan-Sep — {kind} ({use}, filter {skip:.0%})'))
    passed = ml.R.sum() > base.R.sum() and ml.R.sum() > np.quantile(plac, 0.9)
    print(f'2026: no ML {base.R.sum():+.1f}R ({len(base)} trades) | ML {ml.R.sum():+.1f}R ({len(ml)} trades) | beats {np.mean(ml.R.sum() > plac):.0%} of random filters'
          f' -> {"PASSED the final exam" if passed else "FAILED the final exam"}')
    FINAL = dict(kind=kind, params=params, skip=skip, use=use, threshold=thr, passed=bool(passed))
if not cands:
    print('No model passed the walk-forward test, so there is nothing to take to the final exam.')"""),
]


def main():
    nb = dict(nbformat=4, nbformat_minor=5, metadata=dict(colab=dict(name='REAPER ML tuned', provenance=[]),
                                                          kernelspec=dict(name='python3', display_name='Python 3'),
                                                          language_info=dict(name='python')),
              cells=[dict(cell_type=t, metadata={}, source=src.strip('\n').splitlines(keepends=True),
                          **(dict(outputs=[], execution_count=None) if t == CODE else {})) for t, src in CELLS])
    path = Path(__file__).with_name('colab_tuned_2018_2025.ipynb')
    path.write_text(json.dumps(nb, indent=1))
    return path


if __name__ == '__main__':
    print(main())
