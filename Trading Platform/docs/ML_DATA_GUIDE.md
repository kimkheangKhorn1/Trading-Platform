# Start here: Exness data for machine learning

The project preserves official XAUUSDm quotes and derives separate Bid and Ask
M1/M5 candles. It does **not** yet contain a trained forecasting model, trading
labels, or proof that a particular training duration is best. Trading and
indicator logic are unchanged by this data organization.

## Where to look

The dataset is in `backend/data/exness/XAUUSDm/`:

| Location | What it contains | Use |
|---|---|---|
| `README.md` | Current dataset map and limitations | Read first |
| `catalog.json` | Coverage, current audit status, version, file SHA256 hashes | Pin each experiment to a reproducible snapshot |
| `column_dictionary.json` | Column meanings, timestamp units and availability rules | Read before writing features |
| `raw/YYYY/*.zip` | Unmodified official CSV archives | Original sequence and independent verification |
| `ticks/YYYY/YYYY-MM.parquet` | Deduplicated quotes with source positions | Quote research; preserve raw files for event sequencing |
| `candles/M1` and `candles/M5` | Monthly Bid/Ask OHLC, spreads and quote counts | Canonical historical market data |
| `quality/M1` and `quality/M5` | One row per candle, joined by `timestamp` | Gap boundaries, availability and observed-minute flags |
| `ml/M1_recent.parquet`, `ml/M5_recent.parquet` | Eligible complete candles for recent candidate windows | Convenient exports; full history remains in candles |
| `ml/windows.json` | Calendar train/validation/test bounds and walk-forward comparisons | Reuse the same later evaluation periods |
| `ml/daily_regime_summary.parquet` | Descriptive daily returns, ranges and spreads | Research only; a daily summary becomes available after that day closes |
| `reports/accuracy_check.md` | Independent checks of original sources, candles and chart cache | Read before training |
| `reports/coverage.json`, `reports/observed_gaps.csv`, `reports/annual_gap_check.json` | Observed gaps and targeted annual-source recovery checks | Investigate unavailable periods |
| `reports/ml_readiness.json`, `reports/ml_export_check.json` | Readiness limitations and export checks | Distinguish verified prices from a finished training dataset |
| `reports/market_extremes.json` | Largest recorded M1 ranges, spreads and adjacent-minute returns | Review tails without deleting source observations automatically |

`raw/.../revisions/`, scratch files, quarantine, and repair backups are evidence or
working storage. They are excluded from the active training file list in the
catalog. Do not scan every file under the data folder into one dataset.
The recent exports contain multiple training and evaluation periods. Loading the
entire recent file does not make every row a training row; use the split loader.

## What “clean” means here

Each retained tick has a valid UTC timestamp, positive finite Bid/Ask, and
Ask >= Bid. Exact duplicate `(timestamp_ns, bid, ask)` quotes are deduplicated.
Different quotes at the same timestamp remain valid separate quotes. Source first
and last positions preserve candle open/close ordering when identical quotes
repeat. Use the raw CSV if you need the full event sequence or repetition count.

Tick timestamps are **nanoseconds**, candle timestamps are **seconds**, and candle
timestamps denote the **opening** time. M1 covers `[open, open + 60 seconds)`;
M5 covers `[open, open + 300 seconds)`. A candle's high, low, close, spread summary
and count cannot be used as a live input until that candle closes.

`tick_count` measures retained quote activity. It is not traded volume, futures
delta, or all original CSV events. Prices and spreads are stored as floating-point
values; tiny rounding differences in mean-spread summation are allowed by the
documented audit tolerance. Original CSV values remain available unchanged.

No absent candle is manufactured or forward filled. `complete=True` means the
bar passed the conservative elapsed-day snapshot rule. It does **not** certify
that every expected source quote or minute exists. For M5, inspect
`has_unobserved_M1_minutes`; this can reflect a session boundary or a source gap.
Check `gap_before_seconds` and `contiguous_segment_id` before building sequences.
An ordinary market closure and a missing broker period require different
interpretations; the pipeline flags absence rather than guessing the cause.

The earliest downloaded quote is August 10, 2015. January–July 2015 are not listed
for XAUUSDm. Known empty weekdays remain documented. Maximum observed history is
not a claim of uninterrupted minute coverage. Exness archive quotes also do not
certify exact parity with your account's trading server.

Large moves and wide spreads remain in the data. The descriptive extremes report
helps identify periods for review; it does not classify them as source errors or
certify executable account prices. Do not remove large values simply because they
are inconvenient for a model. Any learned clipping/scaling must use training data
only, with the rule and affected rows recorded for reproducibility.
The first daily return and early 20-observed-day volatility values are deliberately
missing because there is insufficient earlier context. These warm-up values are
not invalid source quotes; discard appropriate warm-up rows when using a feature.
`date_utc` in the daily summary is a timezone-aware Parquet datetime, currently
stored at millisecond resolution. Do not assume that casting it directly to an
integer yields nanoseconds. Normalize the unit explicitly, for example
`pd.to_datetime(frame.date_utc, utc=True).dt.as_unit("s").astype("int64")`
when Unix seconds are required.

## A sensible first ML experiment

1. Define one prediction question, its decision time and its forward horizon.
   For example, a future return is a **label**, while previously closed candles
   are potential **inputs**. A label is not automatically an executable trade.
2. Load one declared candidate split. Compare 6, 12, 24 and 36 months against the
   same later validation/test periods. Full history is a separate benchmark.
   Twenty-four months is only an initial candidate; no winning duration is known.
3. Join the matching quality sidecars by timestamp. Reject or explicitly model
   sequences and labels that cross unobserved intervals. Include sufficient past
   context for features, then discard warm-up rows rather than inventing values.
4. Construct inputs using information available at the decision time. Never use
   future candle values, full-day statistics earlier in that day, future-confirmed
   indicator states, or labels as inputs. Specify how ambiguous tied tick times
   affect any intrabar outcome label; do not assume unavailable event precision.
5. Fit scalers, imputation, feature selection and model parameters on training
   data only. Tune using earlier walk-forward validation. Preserve the final test
   as an untouched evaluation period; do not repeatedly tune to its score.
6. Exclude labels reaching across split boundaries; conservatively reject a label
   whose future endpoint is at or after the next split's start. The current 30-minute gap is
   provisional and must cover the actual future label horizon. Use UTC calendar
   windows because this market dataset has sessions and missing rows; a fixed
   number of rows is not a fixed elapsed duration.
7. Save the dataset version, export hashes, window specification, features,
   labels and evaluation results with the experiment. If trading outcomes are
   later evaluated, define Bid/Ask execution, costs and account-server differences.

After choosing and evaluating a duration, `windows.json` also provides
`refit_after_evaluation` dates ending near the latest eligible history. That lets a
later model use recent conditions. Data used for refitting cannot simultaneously
be claimed as an untouched test of that refitted model.

Simple loading example, from `backend/` using its virtual environment:

```python
from pathlib import Path
from app.market.exness_ml import load_ml_split

root = Path("data/exness/XAUUSDm")
train = load_ml_split(root, "M1", "train_24m", "train")
validation = load_ml_split(root, "M1", "train_24m", "validation")
# Keep the final test reserved until the experiment and candidate are fixed.
```

The loader includes eligible complete bars whose open is at or after the split
start and whose close is at or before its end. It does not construct labels,
filter all gap-dependent sequences, or train a model for you.

## Updating and checking

For confirmed multi-timeframe inputs, historical spreads and M1 intrabar records,
use the [guarded archive data APIs](MARKET_DATA_INTEGRITY.md). They are additive
interfaces; current indicator/trading rules do not automatically use them.

From the project directory:

```bash
backend/.venv/bin/python backend/tools/exness_history.py update --workers 4
backend/.venv/bin/python backend/tools/verify_exness_accuracy.py --full-source
```

The first command resumes downloads, rebuilds affected partitions, refreshes
candidate exports, checks integrity and recreates the catalog. The second does
an uncached independent candle/chart check and decodes/reconciles every active
original CSV row. It can take several minutes. One writer holds the pipeline
lock; rerun after an interruption. Completed audit status and hashes must match
the current files. `organize` refreshes local schema/exports/catalog without a
remote download. An interrupted independent audit is not a completed audit.

For the validation principles, see the official
[scikit-learn time-series split documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
For the archive/account-server distinction, see
[Exness tick-history documentation](https://get.exness.help/hc/en-us/articles/360021547851-Tick-history).

Your first objective is a reproducible experiment with honest validation. A
verified market-data snapshot supports that work; it does not guarantee that a
model will predict profitably.
