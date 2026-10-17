# Exness XAUUSDm history for machine learning

This pipeline downloads Standard-account ticks from the archive used by
[Exness Tick History](https://www.exness.com/tick-history/), hosted at
`https://ticks.ex2archive.com`. It requires no API keys, OANDA, MT5 bridge or
continuous live connection. The first downloaded tick is **10 August 2015**;
January–July 2015 are not listed for XAUUSDm. Do not infer continuous M1 history
from a directory's year. Exact final coverage is in `reports/coverage.json` below.

## Update and resume

From the project directory:

```bash
cd backend
.venv/bin/python tools/exness_history.py update
.venv/bin/python tools/exness_history.py verify
.venv/bin/python tools/exness_history.py verify --force  # uncached row/CRC audit
.venv/bin/python tools/verify_exness_accuracy.py         # full independent accuracy check
.venv/bin/python tools/verify_exness_accuracy.py --full-source  # every original CSV row
```

`update` discovers years/months/latest-month days, skips unchanged SHA256-verified
files, resumes `.zip.part` files using HTTP Range, checks ZIP CRCs, validates ticks,
rebuilds affected candle months and imports the chart feeds. Rerun after an
interruption. One writer holds the pipeline lock. Transient errors retry with
backoff; access denial is surfaced. No account cookies or secrets are extracted.
Updates also run the local audit; unchanged partitions reuse their hash-verified
audit results. Updates normalize the tick schema, refresh ML exports and gap
sidecars, and write a dataset catalog with SHA256 hashes. `verify` can be run
independently at any time. Read [the beginner ML guide](ML_DATA_GUIDE.md) before
creating features or labels; verified market prices are not a finished model.
The independent accuracy check recalculates every M1/M5 candle with NumPy, compares
both chart-cache price sides, checks original CSV samples from every archive, and
compares full original monthly OHLC from one month per year. Its results are saved
in `reports/accuracy_check.json`. It does not certify missing source periods or
account-server parity.
When a source-order repair report exists, the checker also compares full original
OHLC for every rebuilt historical month. The mutable latest month is covered by
tick recalculation and quote samples, since its monthly file may be only a snapshot.
With `--full-source`, the checker independently decodes every active original CSV
row and reconciles unique quotes and their first/last source positions across all
monthly/daily overlaps. This replaces the selected-month raw OHLC checks with
complete source reconciliation plus the full independent candle calculation.

Monthly files are preferred because some annual bundles are stale part-year
snapshots. Daily files supplement the latest month; overlaps are deduplicated.
Updated ZIP originals retain previous versions in `raw/YYYY/revisions/`. Annual
archives are supported when monthly directories are absent. Default coverage is
the earliest available year onward; `--start-year` can restrict it.

```bash
.venv/bin/python tools/exness_history.py download  # originals only
.venv/bin/python tools/exness_history.py process   # resume processing
.venv/bin/python tools/exness_history.py report    # coverage and gap report
.venv/bin/python tools/exness_history.py import    # restore/reimport chart feeds
.venv/bin/python tools/exness_history.py check-gaps --years 2015,2016,2021
.venv/bin/python tools/exness_history.py prepare-ml --gap-minutes 30
.venv/bin/python tools/exness_history.py organize  # local schema, exports, quality/catalog
```

`--workers 1` through `--workers 4` controls downloads. `--no-import` keeps Parquet
without chart import. Local data is ignored by Git.

## Storage

```text
backend/data/exness/XAUUSDm/
  raw/YYYY/*.zip                       original ZIPs containing unchanged CSVs
  raw/YYYY/revisions/                  previous mutable ZIP copies
  ticks/YYYY/YYYY-MM.parquet           sorted, validated, unique ticks
  candles/M1/YYYY/YYYY-MM.parquet       one-minute Bid/Ask candles
  candles/M5/YYYY/YYYY-MM.parquet       five-minute Bid/Ask candles
  quarantine/*.csv.gz                  rejected rows, if any
  manifest.json                       inventory, hashes, progress and quality
  reports/coverage.json                exact timestamps/counts/yearly coverage
  reports/observed_gaps.csv             gaps between observed M1 bars
  reports/verification.json            CRC/hash/duplicate/OHLC audit
  reports/accuracy_check.json          independent full candle/source/chart check
  reports/accuracy_check.md            readable accuracy check summary
  reports/annual_gap_check.json        alternate official annual-source checks
  ml/M1_recent.parquet                 complete recent candles
  ml/M5_recent.parquet                 complete recent candles
  ml/windows.json                      candidate date splits and row counts
  ml/daily_regime_summary.parquet      descriptive returns/volatility/spreads
  _work/                              processing scratch space
```

CSV reading uses 200,000-row chunks. DuckDB sorts/deduplicates with a 512 MB memory
limit and disk spill. The original CSVs stay inside ZIPs; `ticks` is their monthly
normalized Parquet representation. No multiyear CSV is loaded into RAM.

Clean ticks retain `timestamp_ns` (UTC nanoseconds), `bid`, `ask`, `source_file`,
`source_row` and `source_priority`. New/rebuilt partitions also retain
`source_last_row`: the last occurrence of an identical quote in its selected source.
Exact timestamp/Bid/Ask repeats are removed; different quotes sharing a timestamp
are retained. Original first-row order resolves candle opens, and last-occurrence
order resolves closes. This preserves A → B → A when all three have the same
timestamp, while storing A only once. Legacy partitions without removed quotes
use `source_row` as their last occurrence. Use DuckDB `union_by_name=true` when
scanning tick partitions with both schemas. Daily sources take priority over
monthly, then annual, for exact overlaps.
For exact tick-sequence replay or tick-sequence training, use the original CSVs:
the normalized unique rows collapse repeats, while the originals preserve the
entire A → B → A sequence. First/last row positions preserve candle endpoints.
Use the supplied M1/M5 files or candle loader for training. Resampling normalized
ticks yourself requires both occurrence positions to preserve those endpoints.
Original timestamp granularity can vary: older records include whole-second
timestamps and newer records include fractional seconds. Nanosecond storage
preserves those values; it does not invent nanosecond measurement precision.

An independent source comparison found that the earlier converter could select
the wrong close after removing a repeated same-time quote. The corrected converter
retains first/last occurrence positions. The one-time resumable migration is:

```bash
.venv/bin/python tools/repair_exness_source_order.py
.venv/bin/python tools/verify_exness_accuracy.py
```

`reports/source_order_repair.json` records affected archives and corrected close
counts; `reports/before_source_order_repair/` preserves the prior manifest, reports
and affected candles. Routine future `update` runs use the corrected converter.

Candles contain UTC opening `timestamp` in seconds, separate `bid_*`/`ask_*` OHLC,
`tick_count`, spread minimum/maximum/mean, first/last tick timestamps and `complete`.
Mean spread is quote-weighted. Tick count is retained quote activity, **not traded
volume** and not necessarily the terminal's native tick-volume value.

Invalid symbols, timezones, timestamps, nonfinite/nonpositive prices, crossed
quotes and out-of-period rows are quarantined/countable. No missing candles,
midpoint quotes, market events, footprint or model labels are fabricated.
The default ML loader excludes the current UTC archive day because its ZIP can
lag/change. This does not certify broker-server completeness of older bars.
The chart store uses observed candle closure for its provisional flag; this is
separate from the Parquet loader's conservative current-day ML exclusion.
Weekends, holidays and maintenance gaps stay absent; missing weekdays are listed
for calendar review rather than silently classified as corruption.

The completed archive snapshot contains **31 weekdays without ticks**, including
older multi-day gaps. Checks of the official 2015, 2016 and 2021 annual ZIPs also
found zero ticks on the affected dates. Those originals are retained as diagnostic
references; their overlapping full-year records are not duplicated into training
data. **Minute history exists from August 2015, but continuous M1 coverage is not
certified.** Consult the exact gap dates in the coverage report before generating
features or labels across them. The recent 24-month window avoids the older gaps,
while still preserving market closures and holidays without fabricated bars.

## Python access

Run in `backend` using its virtual environment:

```python
from pathlib import Path
from app.market.exness_dataset import load_training_candles
from app.market.exness_ml import load_ml_split

root = Path("data/exness/XAUUSDm")
recent = load_training_candles(root, "M5", start="2025-01-01")
train = load_ml_split(root, "M5", candidate="train_24m", split="train")
validation = load_ml_split(root, "M5", candidate="train_24m", split="validation")
test = load_ml_split(root, "M5", candidate="train_24m", split="test")
```

Both price sides are returned, ordered by UTC time. End bounds are exclusive.
For raw ticks, scan monthly files in PyArrow batches or with date-filtered DuckDB.

These are clean market-data inputs, not labeled trade examples. A supervised ML
model still needs causal features, a defined prediction/outcome target and an
evaluation pipeline. This task does not create entries, stops, targets or labels.

## Training duration for today's market

**Candidate, not a validated optimum:** start comparisons with a rolling 24-month
training window; compare 6, 12, 24 and 36 months against full history. Candidates
share the following three-month validation and three-month test periods. Recent
exports include 42 months to support 36 months of training plus six of evaluation.
Exact dates track the latest complete candle in `ml/windows.json`. All history
from 2015 remains preserved separately.

`windows.json` also defines earlier walk-forward folds and a latest-data refit
window for each candidate. Refit only after selection/evaluation; refitting on
recent test data means that period is no longer an untouched performance test.

Research supports testing recency, without proving a specific Exness M1/M5 window:

- [World Gold Council, April 2026](https://www.gold.org/goldhub/gold-focus/2026/04/you-asked-we-answered-has-golds-performance-structurally-changed): higher 2026 volatility and wider spreads since 2024; shocks often fade over months.
- [September 2026 commentary, published 7 October](https://www.gold.org/goldhub/research/gold-market-commentary-september-2026): recent macro drivers and investor flows changed.
- [Forecasting research](https://arxiv.org/abs/2412.08435): concept drift and delayed target values matter.
- [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html): chronological evaluation, capped training size and gaps.

Choose duration on earlier walk-forward validation folds; evaluate the final test
once after selection. Fit preprocessing only on each training fold. Do not randomly
split time. OHLC/spread/count information becomes available only after a bar closes.
Exclude labels crossing split boundaries or data gaps. The provisional 30-minute
gap assumes a short intraday target; increase it to cover your actual prediction
horizon. Evaluate spread costs and simple baselines. No model is trained or claimed
profitable by this pipeline.

## Integration and terminal parity

Select **XAUUSDm · Exness archive (bid)** or **(ask)** in the symbol menu. These feeds
are isolated from CSV/OANDA and authenticated Exness API feeds. Exness selected
without a live feed defaults to the Bid archive. Data manager and
`/api/exness/history` show progress. M1/M5 enter the existing SQLite store; higher
chart timeframes use the existing UTC resampler. Indicators/strategies are unchanged.

The toolbar calendar loads a bounded candle window directly around the selected
UTC date/time. It does not page through every minute between that date and today.
Indicator calculations end at the loaded window's last candle. Scrolling loads
neighboring pages. Dates without source quotes show the next available candle
with an explanatory notice; for example, 1 January 2020 has no quotes in the
stored archive, and the next M1 candle opens on 2 January at 01:00 UTC.

**100% Exness Terminal parity is not verified.** Exness says archive ticks originate
from designated MT4 servers and can differ from another account server due to
latency. A signed-in terminal does not make this archive account-specific. Price
side, server, display timezone, sampling and quote counts need direct comparison.
See [Exness tick history documentation](https://get.exness.help/hc/en-us/articles/360021547851-Tick-history).
