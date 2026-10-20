"""Writes research/reaper_ml/colab_all_models_2018_2025.ipynb: data audit + every model so far (boosted trees, random
forest, CatBoost, 6-input scorecard, a small neural network), each used to skip or half-size weak signals, on
2018-01-01 -> 2026-01-01 data with a yearly walk-forward test (2020 ... 2025)."""
from __future__ import annotations

import json
from pathlib import Path

MD, CODE = 'markdown', 'code'
CSV = 'reaper_ml_dataset_2018-01-01_2026-01-01.csv'
CELLS = [
(MD, rf"""# REAPER [JOAT] — all ML models, 2018 → 2025 data

**Data.** `{CSV}`: every signal of the real indicator ("Most consistent 2024-2026" preset) on Exness XAUUSD
**2018-01-01 → 2026-01-01**, 5m–4H, with 23 inputs known at the signal candle's close and the trade result (win = 3R
target before the stop, real bid/ask). Verified by `check_dataset.py` (same signals as the chart, no lookahead, trades
identical to the independent backtest).

**Step 1 — data audit** (does every input mean something?), measured on 2018–2019, the first training window.

**Step 2 — 10 models × 2 uses = 20 versions (fixed before running).**
* **Boosted trees** (HistGradientBoosting, depth 3), **random forest** (300 trees, depth 6), **CatBoost** (depth 4),
  **scorecard** (logistic regression on 6 inputs chosen inside each training window), **neural network** (2 hidden
  layers 32→16, L2 penalty, early stopping) — the deep-learning representative for 23 table inputs.
* Each trained on **all timeframes** or **15m–4H only**; used to **skip** the 30 % lowest-rated signals or trade them at
  **half size** (trade count unchanged).
* **Walk-forward by year**: test 2020 (train 2018–2019), test 2021 (train 2018–2020) … test 2025 (train 2018–2024).
  Six test years, each judged by a model that never saw it. Trades replayed one at a time per timeframe.

**Accepted only if** (15m–4H, the six test years together): more R than without ML, better in at least **4 of 6**
years, and better than 90 % of random versions (random 30 % skipped / at half size)."""),
(CODE, r"""# 1. Setup
!pip -q install catboost
import json, math, os, io
import numpy as np, pandas as pd, matplotlib.pyplot as plt
import sklearn, catboost
from catboost import CatBoostClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
print('catboost', catboost.__version__, '| scikit-learn', sklearn.__version__)"""),
(MD, rf"""## 2. Load the dataset
Drop `{CSV}` (from `backend/research/reaper_ml/out/`) into the Files panel, or run and choose it."""),
(CODE, rf"""from google.colab import files
path = '/content/{CSV}'
if os.path.exists(path):
    df = pd.read_csv(path)
else:
    up = files.upload(); df = pd.read_csv(io.BytesIO(up[next(iter(up))]))
FEATURES = list(df.columns[df.columns.get_loc('wick_pct'):df.columns.get_loc('tf_minutes') + 1])
df['date'] = pd.to_datetime(df['time'], unit='s', utc=True)
df = df.sort_values(['time', 'tf']).reset_index(drop=True)
ts = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp())
assert len(FEATURES) == 23 and df[FEATURES].notna().all().all()
assert df.time.min() >= ts('2018-01-01') and df.time.max() < ts('2026-01-01')
TF_ORDER, MAIN = ['M5', 'M15', 'M30', 'H1', 'H4'], ['M15', 'M30', 'H1', 'H4']
print(f'{{len(df)}} signals {{df.date.min():%Y-%m-%d}} -> {{df.date.max():%Y-%m-%d}}')
display(df.assign(year=df.date.dt.year).pivot_table(index='year', columns='tf', values='r', aggfunc='size')[TF_ORDER])"""),
(MD, r"""## 3. Data audit
### 3a. Label: per timeframe and year (R of every signal traded on its own)"""),
(CODE, r"""lab = df.groupby('tf').agg(signals=('r', 'size'), win_rate=('win', 'mean'), avg_R=('r', 'mean'),
                          wins_R=('r', lambda r: r[r > 0].mean()), losses_R=('r', lambda r: r[r <= 0].mean())).loc[TF_ORDER]
display(lab.style.format({'win_rate': '{:.1%}', 'avg_R': '{:+.3f}', 'wins_R': '{:+.2f}', 'losses_R': '{:+.2f}'}))
yr = df.assign(year=df.date.dt.year).pivot_table(index='year', columns='tf', values='r', aggfunc='sum')[TF_ORDER]
display(yr.style.format('{:+.1f}').background_gradient(cmap='RdYlGn', vmin=-80, vmax=80))"""),
(MD, r"""### 3b. Inputs that cannot carry information on a timeframe, and duplicates"""),
(CODE, r"""top = {tf: {f: df[df.tf == tf][f].round(6).value_counts(normalize=True).iloc[0] for f in FEATURES} for tf in TF_ORDER}
const = {tf: [f'{f} ({top[tf][f]:.0%})' for f in FEATURES if top[tf][f] >= 0.95] for tf in TF_ORDER}
display(pd.DataFrame({tf: [', '.join(v) or '—'] for tf, v in const.items()}, index=['(almost) always the same value']).T)
corr = df[FEATURES].corr().abs()
print('Almost identical inputs (|corr| > 0.8):', [(a, b, round(corr.loc[a, b], 2)) for i, a in enumerate(FEATURES) for b in FEATURES[i + 1:] if corr.loc[a, b] > 0.8] or 'none')"""),
(MD, r"""### 3c. Which inputs separate winners from losers — 2018–2019, 15m–4H (AUC 0.50 = no information; 90 % bootstrap range)"""),
(CODE, r"""tr0 = df[(df.time < ts('2020-01-01')) & df.tf.isin(MAIN)]
rng = np.random.default_rng(0); rows = []
for f in FEATURES:
    if tr0[f].nunique() < 3:
        rows.append(dict(input=f, AUC=np.nan, low=np.nan, high=np.nan)); continue
    bs = []
    for _ in range(300):
        s = tr0.iloc[rng.integers(0, len(tr0), len(tr0))]
        if s.win.nunique() == 2: bs.append(roc_auc_score(s.win, s[f]))
    rows.append(dict(input=f, AUC=roc_auc_score(tr0.win, tr0[f]), low=np.quantile(bs, 0.05), high=np.quantile(bs, 0.95)))
uni = pd.DataFrame(rows).set_index('input'); uni['real signal'] = (uni.low > 0.5) | (uni.high < 0.5)
display(uni.sort_values('AUC', key=lambda a: -abs(a - 0.5)).style.format({'AUC': '{:.3f}', 'low': '{:.3f}', 'high': '{:.3f}'}))
print(f"{int(uni['real signal'].sum())} of {len(uni)} inputs beyond chance in 2018-2019 ({len(tr0)} signals)")"""),
(MD, r"""### 3d. Drift: did inputs change after 2018–2019? (PSI: < 0.1 stable, > 0.25 changed)"""),
(CODE, r"""def psi(a, b, bins=10):
    e = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    if len(e) < 3: return np.nan
    pa = np.histogram(a, e)[0] / len(a) + 1e-6; pb = np.histogram(np.clip(b, e[0], e[-1]), e)[0] / len(b) + 1e-6
    return float(np.sum((pa - pb) * np.log(pa / pb)))
later = df[(df.time >= ts('2020-01-01')) & df.tf.isin(MAIN)]
drift = pd.Series({f: psi(tr0[f].values, later[f].values) for f in FEATURES}, name='PSI').sort_values(ascending=False)
display(drift.to_frame().style.format('{:.3f}').background_gradient(cmap='OrRd'))
print('Changed inputs (PSI > 0.25):', list(drift[drift > 0.25].index) or 'none')"""),
(MD, r"""## 4. Walk-forward test (20 versions, 6 test years) — takes several minutes"""),
(CODE, r"""SKIP, N_SCORE, N_PLACEBO = 0.30, 6, 200
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

def pick_inputs(tr):
    cand = sorted(((abs(roc_auc_score(tr.win, tr[f]) - 0.5), f) for f in FEATURES if tr[f].nunique() >= 3), reverse=True)
    chosen = []
    for _, f in cand:
        if all(abs(np.corrcoef(tr[f], tr[g])[0, 1]) <= 0.8 for g in chosen): chosen.append(f)
        if len(chosen) == N_SCORE: break
    return chosen

class Sk:
    def __init__(self, make): self.make = make
    def fit(self, tr):
        self.m = self.make().fit(tr[FEATURES].values, tr.win.values); return self
    def score(self, te): return self.m.predict_proba(te[FEATURES].values)[:, 1]

class Scaled(Sk):  # standardised inputs (neural network)
    def fit(self, tr):
        X = tr[FEATURES].values; self.mean, self.std = X.mean(0), X.std(0) + 1e-12
        self.m = self.make().fit((X - self.mean) / self.std, tr.win.values); return self
    def score(self, te): return self.m.predict_proba((te[FEATURES].values - self.mean) / self.std)[:, 1]

class Scorecard:
    def fit(self, tr):
        self.cols = pick_inputs(tr); X = tr[self.cols].values
        self.mean, self.std = X.mean(0), X.std(0) + 1e-12
        self.m = LogisticRegression(C=1.0, max_iter=2000).fit((X - self.mean) / self.std, tr.win.values); return self
    def score(self, te): return self.m.predict_proba((te[self.cols].values - self.mean) / self.std)[:, 1]

MODELS = {
    'boosted trees': lambda: Sk(lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=40, l2_regularization=1.0, random_state=0)),
    'random forest': lambda: Sk(lambda: RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=40, max_features='sqrt', random_state=0, n_jobs=-1)),
    'CatBoost': lambda: Sk(lambda: CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05, l2_leaf_reg=5, random_seed=0, verbose=0, allow_writing_files=False)),
    'scorecard (6 inputs)': lambda: Scorecard(),
    'neural network': lambda: Scaled(lambda: MLPClassifier(hidden_layer_sizes=(32, 16), alpha=1e-2, early_stopping=True, max_iter=500, random_state=0)),
}

def oof_threshold(make, tr):
    s = [make().fit(tr.iloc[a]).score(tr.iloc[b]) for a, b in TimeSeriesSplit(n_splits=4).split(tr)]
    return float(np.quantile(np.concatenate(s), SKIP))

def replay(part, weight):
    out = []
    for tf, g in part.assign(w=weight).groupby('tf'):
        free = -1
        for row in g.sort_values('bar').itertuples():
            if row.w > 0 and row.bar >= free:
                out.append((tf, row.r * row.w, row.w)); free = row.exit_bar
    return pd.DataFrame(out, columns=['tf', 'R', 'w'])

res, placebo = [], {}
for yi, Y in enumerate(YEARS):
    te = df[(df.time >= ts(f'{Y}-01-01')) & (df.time < ts(f'{Y + 1}-01-01'))]
    base = replay(te, np.ones(len(te)))
    rng = np.random.default_rng(1000 + yi)
    rnd = [rng.random(len(te)) < SKIP for _ in range(N_PLACEBO)]
    plac = {use: np.array([(lambda rp: rp[rp.tf.isin(MAIN)].R.sum())(replay(te, np.where(r, 0.0 if use == 'skip' else 0.5, 1.0))) for r in rnd])
            for use in ('skip', 'half size')}
    for mname, make in MODELS.items():
        for train_set in ('all timeframes', '15m-4H only'):
            tr = df[(df.time < ts(f'{Y}-01-01')) & df.tf.isin(TF_ORDER if train_set == 'all timeframes' else MAIN)]
            model = make().fit(tr); thr = oof_threshold(make, tr); low = model.score(te) < thr
            for use in ('skip', 'half size'):
                cfg = f'{mname} / {train_set} / {use}'
                placebo[cfg] = placebo.get(cfg, 0) + plac[use]
                ml = replay(te, np.where(low, 0.0 if use == 'skip' else 0.5, 1.0))
                for tf in TF_ORDER:
                    B, M = base[base.tf == tf], ml[ml.tf == tf]
                    res.append(dict(config=cfg, year=Y, tf=tf, no_ML_trades=len(B), no_ML_R=B.R.sum(), ML_trades=len(M),
                                    ML_half=int((M.w == 0.5).sum()), ML_R=M.R.sum()))
    print(f'{Y} done', flush=True)
res = pd.DataFrame(res)"""),
(CODE, r"""main = res[res.tf.isin(MAIN)].groupby(['config', 'year'], sort=False)[['no_ML_R', 'ML_R']].sum()
gain = (main.ML_R - main.no_ML_R).unstack('year')
display(gain.style.format('{:+.1f}').background_gradient(cmap='RdYlGn', vmin=-40, vmax=40).set_caption('ML gain in R per test year, 15m-4H (green = ML helped)'))
dec = []
for cfg, g in main.groupby(level=0, sort=False):
    b, m = g.no_ML_R.sum(), g.ML_R.sum(); better = int((g.ML_R > g.no_ML_R).sum()); beats = float(np.mean(m > placebo[cfg]))
    t = res[(res.config == cfg) & res.tf.isin(MAIN)]
    dec.append(dict(config=cfg, no_ML_R=b, ML_R=m, years_better=f'{better}/6', beats_random=beats,
                    trades_kept=t.ML_trades.sum() / t.no_ML_trades.sum(), accepted=bool(m > b and better >= 4 and beats >= 0.90)))
dec = pd.DataFrame(dec).set_index('config').sort_values('ML_R', ascending=False)
display(dec.style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}', 'beats_random': '{:.0%}', 'trades_kept': '{:.0%}'})
        .apply(lambda r: ['background-color:#1b5e20' if r.accepted else '' for _ in r], axis=1))
ok = dec[dec.accepted]
CHOSEN = ok.ML_R.idxmax() if len(ok) else None
print('CHOSEN:', CHOSEN or 'none — no version passes, nothing will be exported')"""),
(CODE, r"""show = CHOSEN or dec.ML_R.idxmax()
fig, ax = plt.subplots(1, 2, figsize=(14, 4))
for label, col in (('no_ML_R', 'gray'), ('ML_R', 'tab:blue')):
    s = res[(res.config == show) & res.tf.isin(MAIN)].groupby('year')[label].sum().cumsum()
    ax[0].plot(s.index, s.values, marker='o', color=col, label=label.replace('_R', '').replace('_', ' '))
ax[0].axhline(0, color='black', lw=0.5); ax[0].legend(); ax[0].set_title(f'Cumulative R 15m-4H — {show}')
ax[1].hist(placebo[show], bins=30, color='lightgray', label='random versions')
ax[1].axvline(dec.loc[show, 'ML_R'], color='tab:blue', label='ML'); ax[1].axvline(dec.loc[show, 'no_ML_R'], color='black', ls='--', label='no ML')
ax[1].legend(); ax[1].set_title('ML vs random versions'); plt.tight_layout(); plt.show()
display(res[res.config == show].groupby('tf', sort=False)[['no_ML_trades', 'no_ML_R', 'ML_trades', 'ML_half', 'ML_R']].sum().loc[TF_ORDER]
        .style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}'}).set_caption(show))"""),
(MD, r"""## 5. Export (only if a version passed)
Trained on all 2018 → 2025 signals of its training set; exported as JSON for the indicator, checked against the library,
downloaded. Put `reaper_ml_model.json` into `backend/app/scripts/`."""),
(CODE, r"""def export_spec(mname, mod, tr):
    if mname.startswith('scorecard'):
        idx = [FEATURES.index(c) for c in mod.cols]
        coef, mean, std = np.zeros(23), np.zeros(23), np.ones(23)
        coef[idx], mean[idx], std[idx] = mod.m.coef_[0], mod.mean, mod.std
        return dict(kind='logistic', fill=tr[FEATURES].median().tolist(), mean=mean.tolist(), std=std.tolist(), coef=coef.tolist(),
                    intercept=float(mod.m.intercept_[0]), inputs_used=mod.cols)
    m = mod.m
    if mname == 'CatBoost':
        m.save_model('cb.json', format='json'); cb = json.load(open('cb.json')); sc, bias = cb['scale_and_bias']
        return dict(kind='oblivious', scale=float(sc), bias=float(bias[0]), trees=[dict(feature=[s['float_feature_index'] for s in t['splits']],
                    border=[s['border'] for s in t['splits']], leaf_values=t['leaf_values']) for t in cb['oblivious_trees']])
    if mname == 'boosted trees':
        return dict(kind='trees', baseline=float(np.ravel(m._baseline_prediction)[0]), trees=[dict(feature=p.nodes['feature_idx'].tolist(),
                    threshold=p.nodes['num_threshold'].tolist(), left=p.nodes['left'].tolist(), right=p.nodes['right'].tolist(),
                    leaf=p.nodes['is_leaf'].astype(bool).tolist(), missing_left=p.nodes['missing_go_to_left'].astype(bool).tolist(),
                    value=p.nodes['value'].tolist()) for (p,) in m._predictors])
    if mname == 'random forest':
        return dict(kind='forest', fill=tr[FEATURES].median().tolist(), trees=[dict(feature=e.tree_.feature.tolist(), threshold=e.tree_.threshold.tolist(),
                    left=e.tree_.children_left.tolist(), right=e.tree_.children_right.tolist(),
                    value=(e.tree_.value[:, 0, 1] / e.tree_.value[:, 0, :].sum(1)).tolist()) for e in m.estimators_])
    return dict(kind='mlp', fill=tr[FEATURES].median().tolist(), mean=mod.mean.tolist(), std=mod.std.tolist(),
                weights=[w.tolist() for w in m.coefs_], biases=[b.tolist() for b in m.intercepts_])

if CHOSEN is None:
    print('No version passed: nothing exported. The indicator keeps working without ML.')
else:
    mname, train_set, use = CHOSEN.split(' / ')
    tfs = TF_ORDER if train_set == 'all timeframes' else MAIN
    tr = df[df.tf.isin(tfs)]
    mod = MODELS[mname]().fit(tr)
    spec = export_spec(mname, mod, tr)
    spec.update(features=FEATURES, threshold=oof_threshold(MODELS[mname], tr), skip_pct=SKIP * 100,
                mode='skip' if use == 'skip' else 'half_size', preset='Most consistent 2024-2026', timeframes=tfs, config=CHOSEN,
                trained_on='Exness XAUUSD 2018-01-01 -> 2026-01-01, REAPER Most consistent preset, 3R')
    json.dump(spec, open('reaper_ml_model.json', 'w'))
    files.download('reaper_ml_model.json')
    print('exported', CHOSEN, '| threshold', round(spec['threshold'], 4))"""),
]


def main():
    nb = dict(nbformat=4, nbformat_minor=5, metadata=dict(colab=dict(name='REAPER ML all models 2018-2025', provenance=[]),
                                                          kernelspec=dict(name='python3', display_name='Python 3'),
                                                          language_info=dict(name='python')),
              cells=[dict(cell_type=t, metadata={}, source=src.strip('\n').splitlines(keepends=True),
                          **(dict(outputs=[], execution_count=None) if t == CODE else {})) for t, src in CELLS])
    path = Path(__file__).with_name('colab_all_models_2018_2025.ipynb')
    path.write_text(json.dumps(nb, indent=1))
    return path


if __name__ == '__main__':
    print(main())
