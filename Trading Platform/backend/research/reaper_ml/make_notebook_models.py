"""Writes research/reaper_ml/colab_models_2024_2026.ipynb: data audit ("does every input mean something?") and the
second round of models — CatBoost and a small scorecard, each used to SKIP weak signals or to trade them at HALF SIZE —
on the 2024-01-01 -> 2026-10-01 data only."""
from __future__ import annotations

import json
from pathlib import Path

MD, CODE = 'markdown', 'code'
CELLS = [
(MD, r"""# REAPER [JOAT] — ML round 2: data audit + CatBoost + scorecard (2024–2026 data only)

**Data.** `reaper_ml_dataset_2024_2026.csv`: every signal of the real indicator ("Most consistent 2024-2026" preset) on
Exness XAUUSD **2024-01-01 → 2026-10-01**, 5m–4H, with 23 inputs known at the signal candle's close and the trade
result. Checked by `check_dataset.py` (no lookahead, trades identical to the independent backtest).

**Step 1 — Is the data meaningful?** ("wrong input → wrong prediction") The audit below shows, before any model:
the label, inputs that are constant (carry no information) on a timeframe, duplicated inputs, which inputs actually
separate winners from losers in 2024 (the first training year), and which inputs changed meaning between 2024 and
2025–2026 (drift).

**Step 2 — Models (fixed before running, 8 versions).**
* **CatBoost** (built for small table data; depth 4, 300 trees, learning rate 0.05, strong L2).
* **Scorecard**: logistic regression on only **6 inputs**, chosen inside each training window by how well they separate
  winners from losers there (an input almost identical to one already chosen is skipped).
* Each trained on **all timeframes** or **15m–4H only**, and used two ways:
  **skip** the 30 % lowest-rated signals, or **half size** — trade them at half risk (trade count unchanged).
* Walk-forward inside 2024–2026: test 2025 H1 (train 2024), 2025 H2 (train 2024 → 2025-06), 2026 Jan–Sep (train
  2024 → 2025). Trades replayed one at a time per timeframe.

**Accepted only if** (15m–4H, test periods together): more R than without ML, better in at least 2 of 3 periods,
and better than 90 % of random versions (random 30 % skipped, or random 30 % at half size)."""),
(CODE, r"""# 1. Setup
!pip -q install catboost
import json, math, os, io
import numpy as np, pandas as pd, matplotlib.pyplot as plt
import sklearn, catboost
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
print('catboost', catboost.__version__, '| scikit-learn', sklearn.__version__)"""),
(MD, r"""## 2. Load the dataset
Drop `reaper_ml_dataset_2024_2026.csv` (from `backend/research/reaper_ml/out/`) into the Files panel, or run and choose it."""),
(CODE, r"""from google.colab import files
path = '/content/reaper_ml_dataset_2024_2026.csv'
if os.path.exists(path):
    df = pd.read_csv(path)
else:
    up = files.upload(); df = pd.read_csv(io.BytesIO(up[next(iter(up))]))
FEATURES = list(df.columns[df.columns.get_loc('wick_pct'):df.columns.get_loc('tf_minutes') + 1])
df['date'] = pd.to_datetime(df['time'], unit='s', utc=True)
df = df.sort_values(['time', 'tf']).reset_index(drop=True)
assert len(FEATURES) == 23 and df[FEATURES].notna().all().all()
assert df.time.min() >= int(pd.Timestamp('2024-01-01', tz='UTC').timestamp())
TF_ORDER, MAIN = ['M5', 'M15', 'M30', 'H1', 'H4'], ['M15', 'M30', 'H1', 'H4']
ts = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp())
print(f'{len(df)} signals {df.date.min():%Y-%m-%d} -> {df.date.max():%Y-%m-%d}')"""),
(MD, r"""## 3. Data audit — does every input mean something?
### 3a. The label (what the model learns to predict)
`win` = the trade reached its 3R target before its stop (real bid/ask, spread paid). At 3R, about 25 % wins is break-even."""),
(CODE, r"""lab = df.groupby('tf').agg(signals=('r', 'size'), win_rate=('win', 'mean'), avg_R=('r', 'mean'),
                          wins_R=('r', lambda r: r[r > 0].mean()), losses_R=('r', lambda r: r[r <= 0].mean())).loc[TF_ORDER]
display(lab.style.format({'win_rate': '{:.1%}', 'avg_R': '{:+.3f}', 'wins_R': '{:+.2f}', 'losses_R': '{:+.2f}'}))
print('Wins are ~+2.5..+3R (spread makes some a bit smaller), losses ~-1R: the label is a clean target-or-stop outcome.')"""),
(MD, r"""### 3b. Inputs that cannot carry information on a timeframe
Some REAPER filters fix an input: with the 4H trend filter on, `trend_h4` is always +1; with the ATR stop, `risk_atr` is
always 1.5; with the delta filter, `delta` is always positive; `tf_minutes` is one value per timeframe. A model cannot
learn anything from a constant, so these are listed, not trusted."""),
(CODE, r"""# share of the most common value (rounded): >= 95 % means the input is (almost) the same on every signal
top = {tf: {f: df[df.tf == tf][f].round(6).value_counts(normalize=True).iloc[0] for f in FEATURES} for tf in TF_ORDER}
const = {tf: [f'{f} ({top[tf][f]:.0%})' for f in FEATURES if top[tf][f] >= 0.95] for tf in TF_ORDER}
display(pd.DataFrame({tf: [', '.join(v) or '—'] for tf, v in const.items()}, index=['constant / near-constant inputs']).T)
corr = df[FEATURES].corr().abs()
dup = [(a, b, round(corr.loc[a, b], 2)) for i, a in enumerate(FEATURES) for b in FEATURES[i + 1:] if corr.loc[a, b] > 0.8]
print('Inputs that are almost the same (|correlation| > 0.8):', dup or 'none')"""),
(MD, r"""### 3c. Which inputs separate winners from losers — measured on 2024 only (the first training year)
AUC: 0.50 = no information, further from 0.50 = more. The 90 % range comes from 300 bootstrap resamples; if it crosses
0.50 the input may be noise. 15m–4H signals."""),
(CODE, r"""tr24 = df[(df.time < ts('2025-01-01')) & df.tf.isin(MAIN)]
rng = np.random.default_rng(0)
rows = []
for f in FEATURES:
    if tr24[f].nunique() < 3:
        rows.append(dict(input=f, AUC=np.nan, low=np.nan, high=np.nan)); continue
    auc = roc_auc_score(tr24.win, tr24[f])
    bs = []
    for _ in range(300):
        k = rng.integers(0, len(tr24), len(tr24)); s = tr24.iloc[k]
        if s.win.nunique() == 2: bs.append(roc_auc_score(s.win, s[f]))
    rows.append(dict(input=f, AUC=auc, low=np.quantile(bs, 0.05), high=np.quantile(bs, 0.95)))
uni = pd.DataFrame(rows).set_index('input')
uni['real signal'] = (uni.low > 0.5) | (uni.high < 0.5)
display(uni.sort_values('AUC', key=lambda a: -abs(a - 0.5)).style.format({'AUC': '{:.3f}', 'low': '{:.3f}', 'high': '{:.3f}'}))
print(f"{int(uni['real signal'].sum())} of {len(uni)} inputs separate winners from losers in 2024 beyond chance ({len(tr24)} signals).")"""),
(MD, r"""### 3d. Did inputs change meaning after 2024? (drift)
PSI compares each input's distribution in 2024 with 2025–2026 (15m–4H). Below 0.1 = stable, 0.1–0.25 = moderate,
above 0.25 = the market changed what this input looks like, so what a model learnt from 2024 may not transfer."""),
(CODE, r"""def psi(a, b, bins=10):
    edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3: return np.nan
    pa = np.histogram(a, edges)[0] / len(a) + 1e-6; pb = np.histogram(np.clip(b, edges[0], edges[-1]), edges)[0] / len(b) + 1e-6
    return float(np.sum((pa - pb) * np.log(pa / pb)))
later = df[(df.time >= ts('2025-01-01')) & df.tf.isin(MAIN)]
drift = pd.Series({f: psi(tr24[f].values, later[f].values) for f in FEATURES}, name='PSI').sort_values(ascending=False)
display(drift.to_frame().style.format('{:.3f}').background_gradient(cmap='OrRd'))
print('Drifting inputs (PSI > 0.25):', list(drift[drift > 0.25].index) or 'none')"""),
(MD, r"""## 4. Walk-forward test of the 8 versions"""),
(CODE, r"""SKIP, N_SCORE = 0.30, 6
PERIODS = [('2025 H1', '2025-01-01', '2025-07-01'), ('2025 H2', '2025-07-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]

def pick_inputs(tr):
    # scorecard: the N_SCORE inputs that best separate winners from losers in THIS training window,
    # skipping constants and anything almost identical (|corr| > 0.8) to an input already chosen
    cand = []
    for f in FEATURES:
        if tr[f].nunique() >= 3:
            cand.append((abs(roc_auc_score(tr.win, tr[f]) - 0.5), f))
    chosen = []
    for _, f in sorted(cand, reverse=True):
        if all(abs(np.corrcoef(tr[f], tr[g])[0, 1]) <= 0.8 for g in chosen):
            chosen.append(f)
        if len(chosen) == N_SCORE: break
    return chosen

class Scorecard:
    def fit(self, tr):
        self.cols = pick_inputs(tr)
        X = tr[self.cols].values
        self.mean, self.std = X.mean(0), X.std(0) + 1e-12
        self.m = LogisticRegression(C=1.0, max_iter=2000).fit((X - self.mean) / self.std, tr.win.values)
        return self
    def score(self, te):
        return self.m.predict_proba((te[self.cols].values - self.mean) / self.std)[:, 1]

class Cat:
    def fit(self, tr):
        self.m = CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05, l2_leaf_reg=5, random_seed=0,
                                    verbose=0, allow_writing_files=False).fit(tr[FEATURES].values, tr.win.values)
        return self
    def score(self, te):
        return self.m.predict_proba(te[FEATURES].values)[:, 1]

MODELS = {'CatBoost': Cat, 'scorecard (6 inputs)': Scorecard}

def oof_threshold(make, tr):
    s = []
    for a, b in TimeSeriesSplit(n_splits=4).split(tr):
        s.append(make().fit(tr.iloc[a]).score(tr.iloc[b]))
    return float(np.quantile(np.concatenate(s), SKIP))

def replay(part, weight):
    # one trade at a time per timeframe; weight 0 = skipped, 0.5 = half size, 1 = full
    out = []
    for tf, g in part.assign(w=weight).groupby('tf'):
        free = -1
        for row in g.sort_values('bar').itertuples():
            if row.w > 0 and row.bar >= free:
                out.append((tf, row.r * row.w, row.w)); free = row.exit_bar
    return pd.DataFrame(out, columns=['tf', 'R', 'w'])

res, placebo, picked = [], {}, {}
for mname, make in MODELS.items():
    for train_set in ('all timeframes', '15m-4H only'):
        for use in ('skip', 'half size'):
            cfg = f'{mname} / {train_set} / {use}'
            placebo[cfg] = np.zeros(300)
            for pi, (name, a, b) in enumerate(PERIODS):
                tr = df[(df.time < ts(a)) & df.tf.isin(TF_ORDER if train_set == 'all timeframes' else MAIN)]
                te = df[(df.time >= ts(a)) & (df.time < ts(b))]
                model = make().fit(tr); thr = oof_threshold(make, tr)
                if mname.startswith('scorecard'): picked[(cfg, name)] = model.cols
                low = model.score(te) < thr
                w = np.where(low, 0.0 if use == 'skip' else 0.5, 1.0)
                base, ml = replay(te, np.ones(len(te))), replay(te, w)
                for tf in TF_ORDER:
                    B, M = base[base.tf == tf], ml[ml.tf == tf]
                    res.append(dict(config=cfg, period=name, tf=tf, no_ML_trades=len(B), no_ML_R=B.R.sum(),
                                    ML_trades=len(M), ML_half=int((M.w == 0.5).sum()), ML_R=M.R.sum()))
                rng = np.random.default_rng(1000 + pi)
                for k in range(300):
                    rnd = rng.random(len(te)) < SKIP
                    rp = replay(te, np.where(rnd, 0.0 if use == 'skip' else 0.5, 1.0))
                    placebo[cfg][k] += rp[rp.tf.isin(MAIN)].R.sum()
res = pd.DataFrame(res)
print('done:', res.config.nunique(), 'versions')"""),
(CODE, r"""main = res[res.tf.isin(MAIN)].groupby(['config', 'period'], sort=False)[['no_ML_trades', 'no_ML_R', 'ML_trades', 'ML_half', 'ML_R']].sum()
main['ML gain R'] = main.ML_R - main.no_ML_R
display(main.style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}', 'ML gain R': '{:+.1f}'}).background_gradient(subset=['ML gain R'], cmap='RdYlGn', vmin=-40, vmax=40))
print('Inputs the scorecard chose in each training window:')
for (cfg, name), cols in picked.items():
    if cfg.endswith('/ skip'): print(f'  {cfg.split(" / ")[1]:15s} {name:13s} {cols}')"""),
(CODE, r"""dec = []
for cfg, g in main.groupby(level=0, sort=False):
    b, m = g.no_ML_R.sum(), g.ML_R.sum(); better = int((g.ML_R > g.no_ML_R).sum()); beats = float(np.mean(m > placebo[cfg]))
    dec.append(dict(config=cfg, no_ML_R=b, ML_R=m, periods_better=f'{better}/3', beats_random=beats,
                    trades_kept=g.ML_trades.sum() / g.no_ML_trades.sum(), accepted=bool(m > b and better >= 2 and beats >= 0.90)))
dec = pd.DataFrame(dec).set_index('config')
display(dec.style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}', 'beats_random': '{:.0%}', 'trades_kept': '{:.0%}'})
        .apply(lambda r: ['background-color:#1b5e20' if r.accepted else '' for _ in r], axis=1))
ok = dec[dec.accepted]
CHOSEN = ok.ML_R.idxmax() if len(ok) else None
print('CHOSEN:', CHOSEN or 'none — no version passes, nothing will be exported')"""),
(CODE, r"""show = CHOSEN or dec.ML_R.idxmax()
fig, ax = plt.subplots(1, 2, figsize=(14, 4))
for label, col in (('no_ML_R', 'gray'), ('ML_R', 'tab:blue')):
    s = res[(res.config == show) & res.tf.isin(MAIN)].groupby('period', sort=False)[label].sum().cumsum()
    ax[0].plot(s.index, s.values, marker='o', color=col, label=label.replace('_R', '').replace('_', ' '))
ax[0].axhline(0, color='black', lw=0.5); ax[0].legend(); ax[0].set_title(f'Cumulative R, 15m-4H — {show}')
ax[1].hist(placebo[show], bins=30, color='lightgray', label='random versions')
ax[1].axvline(dec.loc[show, 'ML_R'], color='tab:blue', label='ML'); ax[1].axvline(dec.loc[show, 'no_ML_R'], color='black', ls='--', label='no ML')
ax[1].legend(); ax[1].set_title('ML vs random versions'); plt.tight_layout(); plt.show()
display(res[res.config == show].groupby('tf', sort=False)[['no_ML_trades', 'no_ML_R', 'ML_trades', 'ML_half', 'ML_R']].sum().loc[TF_ORDER]
        .style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}'}).set_caption(show))"""),
(MD, r"""## 5. Export (only if a version passed)
Trained on all 2024 → 2026-09 signals of its training set, threshold from out-of-fold scores, exported as JSON for the
indicator, checked against the library to ~0 difference, then downloaded. Put `reaper_ml_model.json` into `backend/app/scripts/`."""),
(CODE, r"""def export_spec(mname, model, tr):
    if mname.startswith('scorecard'):
        idx = [FEATURES.index(c) for c in model.cols]
        coef, mean, std = np.zeros(len(FEATURES)), np.zeros(len(FEATURES)), np.ones(len(FEATURES))
        coef[idx], mean[idx], std[idx] = model.m.coef_[0], model.mean, model.std
        return dict(kind='logistic', features=FEATURES, fill=tr[FEATURES].median().tolist(), mean=mean.tolist(), std=std.tolist(),
                    coef=coef.tolist(), intercept=float(model.m.intercept_[0]), inputs_used=model.cols)
    model.m.save_model('cb.json', format='json'); cb = json.load(open('cb.json'))
    trees = [dict(feature=[s['float_feature_index'] for s in t['splits']], border=[s['border'] for s in t['splits']],
                  leaf_values=t['leaf_values']) for t in cb['oblivious_trees']]
    scale, bias = cb['scale_and_bias']
    return dict(kind='oblivious', features=FEATURES, trees=trees, scale=float(scale), bias=float(bias[0]))

def json_prob(spec, x):  # same evaluator as app/scripts/reaper_ml.py
    if spec['kind'] == 'logistic':
        z = spec['intercept'] + sum(w * (v - m) / s for v, m, s, w in zip(x, spec['mean'], spec['std'], spec['coef']))
    else:
        tot = 0.0
        for t in spec['trees']:
            idx = sum(1 << b for b, (f, br) in enumerate(zip(t['feature'], t['border'])) if x[f] > br)
            tot += t['leaf_values'][idx]
        z = spec['scale'] * tot + spec['bias']
    return 1 / (1 + math.exp(-z))

if CHOSEN is None:
    print('No version passed: nothing exported. The indicator keeps working without ML.')
else:
    mname, train_set, use = CHOSEN.split(' / ')
    tfs = TF_ORDER if train_set == 'all timeframes' else MAIN
    tr = df[df.tf.isin(tfs)]
    model = MODELS[mname]().fit(tr)
    spec = export_spec(mname, model, tr)
    spec.update(threshold=oof_threshold(MODELS[mname], tr), skip_pct=SKIP * 100, mode='skip' if use == 'skip' else 'half_size',
                preset='Most consistent 2024-2026', timeframes=tfs, config=CHOSEN,
                trained_on='Exness XAUUSD 2024-01-01 -> 2026-10-01, REAPER Most consistent preset, 3R')
    diff = max(abs(json_prob(spec, list(r)) - p) for r, p in zip(tr[FEATURES].values[:1500], model.score(tr.iloc[:1500])))
    print(f'JSON evaluator vs library: max difference {diff:.2e}'); assert diff < 1e-6
    json.dump(spec, open('reaper_ml_model.json', 'w'))
    files.download('reaper_ml_model.json')"""),
]


def main():
    nb = dict(nbformat=4, nbformat_minor=5, metadata=dict(colab=dict(name='REAPER ML round 2', provenance=[]),
                                                          kernelspec=dict(name='python3', display_name='Python 3'),
                                                          language_info=dict(name='python')),
              cells=[dict(cell_type=t, metadata={}, source=src.strip('\n').splitlines(keepends=True),
                          **(dict(outputs=[], execution_count=None) if t == CODE else {})) for t, src in CELLS])
    path = Path(__file__).with_name('colab_models_2024_2026.ipynb')
    path.write_text(json.dumps(nb, indent=1))
    return path


if __name__ == '__main__':
    print(main())
