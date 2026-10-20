"""Writes research/reaper_ml/colab_train.ipynb — the Google Colab notebook that trains the REAPER ML filter.

The notebook is generated from the cells below so it can be read and reviewed as plain Python here.
"""
from __future__ import annotations

import json
from pathlib import Path

MD, CODE = 'markdown', 'code'
CELLS = [
(MD, r"""# REAPER [JOAT] — ML filter training

**What this does.** REAPER finds liquidity raids with its own rules. This notebook trains a model that looks at each
REAPER signal and rates how likely it is to reach its **3R target before the stop**. In the indicator, the
lowest-rated signals are skipped. The model **never creates or reverses a signal**.

**Data.** `reaper_ml_dataset.csv`, made by the platform (`backend/research/reaper_ml/dataset.py`) from the real indicator
on Exness XAUUSD, **2019-01-01 → 2026-10-01**, "Most consistent 2024-2026" preset, timeframes 5m–4H. **The test period is
2024 → 2026-09** (the stable market you chose); the years before it are the training data the model learns from. One row per signal:
23 inputs known at the signal candle's close, and the trade result (R, win = target hit). The recorder was verified by
`check_dataset.py` (same signals as the chart, no lookahead, trades identical to the independent research backtest).

**How it is tested (fixed before running).**
* Walk-forward: the model for each test year is trained only on signals **before** it —
  test 2024 (train 2019–2023), test 2025 (train 2019–2024), test 2026 Jan–Sep (train 2019–2025).
  (Training only inside 2024–2026 was tried first: too few signals, no model passed.)
* Two models × two training sets: **Gradient-boosted trees** and **Random forest**; trained on **all timeframes** or
  **15m–4H only**.
* The model skips the **30 %** of signals it rates lowest (threshold from out-of-fold scores on the training data).
* Trades replayed **one at a time** per timeframe, like the indicator backtest.

**Accepted only if** (15m–4H, all test periods together): more R than without ML, better in at least 2 of the 3 test
periods, and better than 90 % of random filters that also skip 30 %.
"""),
(CODE, r"""# 1. Setup
import json, math, io
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.inspection import permutation_importance
pd.set_option('display.width', 200); pd.set_option('display.max_columns', 40)
print('scikit-learn', sklearn.__version__, '| numpy', np.__version__, '| pandas', pd.__version__)"""),
(MD, r"""## 2. Load the dataset
Drop `reaper_ml_dataset.csv` (from `backend/research/reaper_ml/out/`) into the Files panel on the left, or run the cell
and choose it."""),
(CODE, r"""import os
from google.colab import files
# The dataset: if reaper_ml_dataset.csv is already in the Files panel (/content), use it; otherwise ask for it.
path = '/content/reaper_ml_dataset.csv'
if os.path.exists(path):
    df = pd.read_csv(path)
else:
    uploaded = files.upload()
    df = pd.read_csv(io.BytesIO(uploaded[next(iter(uploaded))]))
FEATURES = list(df.columns[df.columns.get_loc('wick_pct'):df.columns.get_loc('tf_minutes') + 1])
df['date'] = pd.to_datetime(df['time'], unit='s', utc=True)
df = df.sort_values(['time', 'tf']).reset_index(drop=True)
assert len(FEATURES) == 23 and df[FEATURES].notna().all().all()
print(f'{len(df)} signals, {df.date.min():%Y-%m-%d} -> {df.date.max():%Y-%m-%d}, {len(FEATURES)} inputs')
df.head()"""),
(MD, r"""## 3. Look at the data
Signals and win rate per timeframe, and the R of all signals (each traded on its own) per month."""),
(CODE, r"""TF_ORDER = ['M5', 'M15', 'M30', 'H1', 'H4']
summary = df.groupby('tf').agg(signals=('r', 'size'), win_rate=('win', 'mean'), avg_R=('r', 'mean'), total_R=('r', 'sum')).loc[TF_ORDER]
display(summary.style.format({'win_rate': '{:.1%}', 'avg_R': '{:+.3f}', 'total_R': '{:+.1f}'}))
fig, ax = plt.subplots(1, 2, figsize=(14, 4))
m = df.assign(month=df.date.dt.strftime('%Y-%m')).groupby(['month', 'tf']).r.sum().unstack()[TF_ORDER]
m.plot.bar(stacked=True, ax=ax[0], width=0.9); ax[0].set_title('R per month, every signal on its own')
ax[0].set_xticks(range(0, len(m), 6)); ax[0].set_xticklabels(m.index[::6]); ax[0].set_xlabel('')
ax[0].tick_params(axis='x', labelsize=7)
df.groupby('tf').win.mean().loc[TF_ORDER].plot.bar(ax=ax[1], color='tab:green'); ax[1].axhline(0.25, color='gray', ls='--')
ax[1].set_title('Win rate per timeframe (break-even at 3R ≈ 25%)'); plt.tight_layout(); plt.show()"""),
(MD, r"""### The 23 inputs (all known at the signal candle's close; "× side" = positive means *with the trade*)
| Input | Meaning | | Input | Meaning |
|---|---|---|---|---|
| wick_pct | sweep wick beyond the pool, % of the raid candle | | trend_h4 | 4H EMA50 vs EMA200 (× side) |
| depth_atr | how far past the pool, in ATR | | slope50 | EMA50 10-bar change / ATR (× side) |
| close_from_level | close back inside the level, in ATR | | dist200 | distance from EMA200 / ATR (× side) |
| bars_since_raid | candles the confirmation took | | adx | ADX(14) |
| vol_mult | volume / 20-bar average | | di_diff | +DI vs −DI (× side) |
| pool_age | age of the swept swing (log bars) | | rsi | RSI(14), 100−RSI for sells |
| room_atr | distance to the next opposite pool, ATR | | macd_hist | MACD histogram / ATR (× side) |
| body_pct | candle body / range | | atr_ratio | ATR / its 100-bar average |
| close_pos | close position in the candle | | delta | 1-minute order flow (× side) |
| range_atr | candle range / ATR | | hour, weekday | time (UTC) |
| risk_atr | stop distance / ATR | | tf_minutes | timeframe (log minutes) |"""),
(CODE, r"""# Inputs of winning vs losing signals (median); a big difference = an input that may help
cmp = df.groupby('win')[FEATURES].median().T.rename(columns={0: 'losers', 1: 'winners'})
cmp['difference'] = cmp.winners - cmp.losers
display(cmp.style.format('{:+.3f}').background_gradient(subset=['difference'], cmap='RdYlGn'))"""),
(MD, r"""## 4. Walk-forward test"""),
(CODE, r"""SKIP = 0.30
PERIODS = [('2024', '2024-01-01', '2025-01-01'), ('2025', '2025-01-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]
MAIN = ['M15', 'M30', 'H1', 'H4']
ts = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp())

def make_model(kind):
    if kind == 'boosted trees':
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=40,
                                              l2_regularization=1.0, random_state=0)
    return RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=40, max_features='sqrt',
                                  random_state=0, n_jobs=-1)

def oof_threshold(kind, X, y):
    # 30th percentile of out-of-fold scores on the training rows (time-ordered folds: never trained on the future)
    scores = []
    for tr, va in TimeSeriesSplit(n_splits=4).split(X):
        scores.append(make_model(kind).fit(X[tr], y[tr]).predict_proba(X[va])[:, 1])
    return float(np.quantile(np.concatenate(scores), SKIP))

def replay(part, keep):
    # one trade at a time per timeframe: a signal is skipped while that timeframe's previous trade is open
    out = []
    for tf, g in part.assign(keep=keep).groupby('tf'):
        free = -1
        for row in g.sort_values('bar').itertuples():
            if row.keep and row.bar >= free:
                out.append((tf, row.r)); free = row.exit_bar
    return pd.DataFrame(out, columns=['tf', 'r'])

def stats(t):
    return dict(trades=len(t), wins=int((t.r > 0).sum()), win_rate=(t.r > 0).mean() if len(t) else 0.0, R=t.r.sum())

rows, curves, placebo = [], {}, {}
for kind in ('boosted trees', 'random forest'):
    for train_set in ('all timeframes', '15m-4H only'):
        cfg = f'{kind} / {train_set}'
        placebo[cfg] = np.zeros(300)
        for pi, (name, a, b) in enumerate(PERIODS):
            tr = df[(df.time < ts(a)) & (df.tf.isin(TF_ORDER if train_set == 'all timeframes' else MAIN))]
            te = df[(df.time >= ts(a)) & (df.time < ts(b))]
            model = make_model(kind).fit(tr[FEATURES].values, tr.win.values)
            thr = oof_threshold(kind, tr[FEATURES].values, tr.win.values)
            keep = model.predict_proba(te[FEATURES].values)[:, 1] >= thr
            base, ml = replay(te, np.ones(len(te), bool)), replay(te, keep)
            for tf in TF_ORDER:
                rows.append(dict(config=cfg, period=name, tf=tf, **{f'no ML {k}': v for k, v in stats(base[base.tf == tf]).items()},
                                 **{f'ML {k}': v for k, v in stats(ml[ml.tf == tf]).items()}))
            rng = np.random.default_rng(1000 + pi)  # same random filters for every configuration
            for k in range(300):
                rp = replay(te, rng.random(len(te)) >= SKIP)
                placebo[cfg][k] += rp[rp.tf.isin(MAIN)].r.sum()
res = pd.DataFrame(rows)
print('done:', len(res), 'rows')"""),
(CODE, r"""# Results, 15m-4H together, per test period
main = res[res.tf.isin(MAIN)].groupby(['config', 'period'], sort=False)[['no ML trades', 'no ML R', 'ML trades', 'ML R']].sum()
main['ML gain R'] = main['ML R'] - main['no ML R']
display(main.style.format({'no ML R': '{:+.1f}', 'ML R': '{:+.1f}', 'ML gain R': '{:+.1f}'}).background_gradient(subset=['ML gain R'], cmap='RdYlGn'))"""),
(CODE, r"""# Decision (rules fixed above)
dec = []
for cfg, g in main.groupby(level=0, sort=False):
    base_R, ml_R = g['no ML R'].sum(), g['ML R'].sum()
    better = int((g['ML R'] > g['no ML R']).sum())
    beats = float(np.mean(ml_R > placebo[cfg]))
    dec.append(dict(config=cfg, no_ML_R=base_R, ML_R=ml_R, periods_better=f'{better}/3', beats_random=beats,
                    trades_kept=g['ML trades'].sum() / g['no ML trades'].sum(),
                    accepted=bool(ml_R > base_R and better >= 2 and beats >= 0.90)))
dec = pd.DataFrame(dec).set_index('config')
display(dec.style.format({'no_ML_R': '{:+.1f}', 'ML_R': '{:+.1f}', 'beats_random': '{:.0%}', 'trades_kept': '{:.0%}'}))
ok = dec[dec.accepted]
CHOSEN = ok.ML_R.idxmax() if len(ok) else None
print('CHOSEN:', CHOSEN if CHOSEN else 'none — no model passes, nothing will be exported')"""),
(CODE, r"""# Per timeframe detail of the chosen (or best) configuration, and its cumulative R
show = CHOSEN or dec.ML_R.idxmax()
d = res[res.config == show].groupby('tf', sort=False)[['no ML trades', 'no ML R', 'ML trades', 'ML R']].sum().loc[TF_ORDER]
display(d.style.format({'no ML R': '{:+.1f}', 'ML R': '{:+.1f}'}).set_caption(show))
fig, ax = plt.subplots(figsize=(10, 4))
for label, col in (('no ML R', 'gray'), ('ML R', 'tab:blue')):
    s = res[(res.config == show) & res.tf.isin(MAIN)].groupby('period', sort=False)[label].sum().cumsum()
    ax.plot(s.index, s.values, marker='o', color=col, label=label.replace(' R', ''))
ax.axhline(0, color='black', lw=0.5); ax.set_title(f'Cumulative R, 15m-4H, test periods only — {show}')
ax.legend(); plt.show()
plt.figure(figsize=(8, 3)); plt.hist(placebo[show], bins=30, color='lightgray', label='random 30% skips')
plt.axvline(dec.loc[show, 'ML_R'], color='tab:blue', label='ML'); plt.axvline(dec.loc[show, 'no_ML_R'], color='black', ls='--', label='no ML')
plt.legend(); plt.title('ML vs random filters that skip the same share'); plt.show()"""),
(MD, r"""## 5. Final model and export
Trained on **all** 2019 → 2026-09 signals of the chosen training set, threshold from out-of-fold scores, exported as
JSON for the indicator (evaluated there with plain Python), checked against scikit-learn, then downloaded.
Put the downloaded `reaper_ml_model.json` into `backend/app/scripts/`."""),
(CODE, r"""def export(kind, model, X):
    spec = dict(features=FEATURES)
    if kind == 'boosted trees':
        trees = []
        for (pred,) in model._predictors:
            nd = pred.nodes
            trees.append(dict(feature=nd['feature_idx'].tolist(), threshold=nd['num_threshold'].tolist(), left=nd['left'].tolist(),
                              right=nd['right'].tolist(), leaf=nd['is_leaf'].astype(bool).tolist(),
                              missing_left=nd['missing_go_to_left'].astype(bool).tolist(), value=nd['value'].tolist()))
        spec.update(kind='trees', baseline=float(np.ravel(model._baseline_prediction)[0]), trees=trees)
    else:
        trees = []
        for est in model.estimators_:
            t = est.tree_
            val = t.value[:, 0, :]
            trees.append(dict(feature=t.feature.tolist(), threshold=t.threshold.tolist(), left=t.children_left.tolist(),
                              right=t.children_right.tolist(), value=(val[:, 1] / val.sum(1)).tolist()))
        spec.update(kind='forest', fill=np.nanmedian(X, 0).tolist(), trees=trees)
    return spec

def json_prob(spec, x):  # the same evaluator as app/scripts/reaper_ml.py (Model.prob)
    if spec['kind'] == 'forest':
        tot = 0.0
        for t in spec['trees']:
            n = 0
            while t['left'][n] != -1:
                n = t['left'][n] if float(np.float32(x[t['feature'][n]])) <= t['threshold'][n] else t['right'][n]
            tot += t['value'][n]
        return tot / len(spec['trees'])
    z = spec['baseline']
    for t in spec['trees']:
        n = 0
        while not t['leaf'][n]:
            xi = x[t['feature'][n]]
            n = (t['left'][n] if t['missing_left'][n] else t['right'][n]) if not np.isfinite(xi) else (t['left'][n] if xi <= t['threshold'][n] else t['right'][n])
        z += t['value'][n]
    return 1 / (1 + math.exp(-z))

if CHOSEN is None:
    print('No model passed the walk-forward test: nothing to export. The indicator keeps working without ML.')
else:
    kind, train_set = CHOSEN.split(' / ')
    tfs = TF_ORDER if train_set == 'all timeframes' else MAIN
    full = df[df.tf.isin(tfs)]
    X, y = full[FEATURES].values, full.win.values
    model = make_model(kind).fit(X, y)
    spec = export(kind, model, X)
    spec.update(threshold=oof_threshold(kind, X, y), skip_pct=SKIP * 100, preset='Most consistent 2024-2026',
                timeframes=tfs, trained_on='Exness XAUUSD 2019-01-01 -> 2026-10-01, REAPER Most consistent preset, 3R',
                config=CHOSEN, walk_forward=dict(no_ML_R=float(dec.loc[CHOSEN, 'no_ML_R']), ML_R=float(dec.loc[CHOSEN, 'ML_R']),
                                                  beats_random=float(dec.loc[CHOSEN, 'beats_random'])))
    diff = max(abs(json_prob(spec, list(r)) - p) for r, p in zip(X[:1500], model.predict_proba(X[:1500])[:, 1]))
    print(f'JSON evaluator vs scikit-learn: max difference {diff:.2e}  (must be ~0)')
    assert diff < 1e-9
    imp = permutation_importance(model, X, y, scoring='roc_auc', n_repeats=5, random_state=0)
    pd.Series(imp.importances_mean, index=FEATURES).sort_values().plot.barh(figsize=(7, 7), title='Which inputs the model uses (permutation importance)')
    plt.show()
    with open('reaper_ml_model.json', 'w') as f:
        json.dump(spec, f)
    print(f"threshold {spec['threshold']:.4f}; skips the lowest {SKIP:.0%}; timeframes {tfs}")
    files.download('reaper_ml_model.json')"""),
]


def window_2024_2026(cells):
    """Variant the user asked for: ONLY 2024-01-01 -> 2026-10-01 (the stable market), train and test inside it."""
    out = []
    for kind, src in cells:
        src = src.replace("""on Exness XAUUSD, **2019-01-01 → 2026-10-01**, "Most consistent 2024-2026" preset, timeframes 5m–4H. **The test period is
2024 → 2026-09** (the stable market you chose); the years before it are the training data the model learns from.""",
                          """on Exness XAUUSD, **only 2024-01-01 → 2026-10-01** (the stable market you chose), "Most consistent 2024-2026" preset,
timeframes 5m–4H. Nothing before 2024 is used: training and testing both happen inside this period.""")
        src = src.replace("""* Walk-forward: the model for each test year is trained only on signals **before** it —
  test 2024 (train 2019–2023), test 2025 (train 2019–2024), test 2026 Jan–Sep (train 2019–2025).
  (Training only inside 2024–2026 was tried first: too few signals, no model passed.)""",
                          """* Walk-forward inside 2024–2026: each test half-year is judged by a model trained only on signals **before** it —
  test 2025 H1 (train 2024), test 2025 H2 (train 2024 → 2025-06), test 2026 Jan–Sep (train 2024 → 2025).""")
        src = src.replace("reaper_ml_dataset.csv", "reaper_ml_dataset_2024_2026.csv")
        src = src.replace("""assert len(FEATURES) == 23 and df[FEATURES].notna().all().all()""",
                          """assert len(FEATURES) == 23 and df[FEATURES].notna().all().all()
assert df.time.min() >= int(pd.Timestamp('2024-01-01', tz='UTC').timestamp())  # only the 2024-2026 data""")
        src = src.replace("""PERIODS = [('2024', '2024-01-01', '2025-01-01'), ('2025', '2025-01-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]""",
                          """PERIODS = [('2025 H1', '2025-01-01', '2025-07-01'), ('2025 H2', '2025-07-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01')]""")
        src = src.replace("Trained on **all** 2019 → 2026-09 signals", "Trained on **all** 2024 → 2026-09 signals")
        src = src.replace("trained_on='Exness XAUUSD 2019-01-01 -> 2026-10-01", "trained_on='Exness XAUUSD 2024-01-01 -> 2026-10-01")
        src = src.replace("# REAPER [JOAT] — ML filter training", "# REAPER [JOAT] — ML filter training (2024–2026 data only)")
        out.append((kind, src))
    return out


def main(cells=None, name='colab_train.ipynb'):
    cells = cells or CELLS
    nb = dict(nbformat=4, nbformat_minor=5, metadata=dict(colab=dict(name='REAPER ML filter', provenance=[]),
                                                          kernelspec=dict(name='python3', display_name='Python 3'),
                                                          language_info=dict(name='python')),
              cells=[dict(cell_type=t, metadata={}, source=src.strip('\n').splitlines(keepends=True),
                          **(dict(outputs=[], execution_count=None) if t == CODE else {})) for t, src in cells])
    path = Path(__file__).with_name(name)
    path.write_text(json.dumps(nb, indent=1))
    return path


if __name__ == '__main__':
    print(main())
    print(main(window_2024_2026(CELLS), 'colab_train_2024_2026.ipynb'))
