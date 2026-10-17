# Market data integrity and timeframe synchronization

Historical verification and the guarded archive APIs are implemented. **Live
account/execution-feed verification remains partial**: no authenticated feed,
measured arrival latency, account-price comparison or broker-fill verification
has been established. Archive/chart agreement must not be presented as live
execution-feed agreement.

## Confirmed snapshots at a decision time

`GET /api/exness/history/confirmed-snapshot` requires an explicit ISO decision
timestamp with `Z` or a UTC offset. It supports M1, M5, M15, M30, H1, H4 and D,
aligned to UTC. Offset timestamps are converted to UTC; naive dates are rejected.

Example:

```text
/api/exness/history/confirmed-snapshot?as_of=2026-10-13T10:25:00Z&timeframes=M5,H1
```

At **10:25 UTC**, the latest eligible H1 candidate is **09:00–10:00** and the M5
candidate is **10:20–10:25**. The 10:00–11:00 hourly candle cannot appear as
confirmed. At exactly 11:00, the 10:00–11:00 candle becomes time-eligible.
Nominal bar close is the earliest possible availability time; live arrival can
occur later and must be measured separately.

For each requested timeframe the API returns:

- Expected open/close time and observed/expected M1 counts.
- Missing-minute spans, including leading and trailing absence.
- A confirmed Bid/Ask OHLC candle, quote count, and spread min/max/weighted mean,
  or `bar: null` with a specific reason.
- A comparison against the same timeframe's stored Bid and Ask chart candles.
  Provider, symbol, instrument, price side, UTC alignment, OHLC, quote count and
  cached completeness must match.
- The dataset version, source partition hashes, and full-source audit time.
- `execution_feed_verification.status = not_verified` for the historical archive.

The API fails closed: missing minutes, source snapshot ineligibility, changed file
hashes, unaudited data, chart-cache differences, or a running pipeline writer
prevent a confirmed result. It does not quietly substitute an older hourly
candle when the expected one is absent. It exposes no developing HTF OHLC as
confirmed data.

The strict minute-count policy does not assume a broker calendar. Routine trading
breaks, holidays and missing archive periods all appear as unobserved intervals.
Consequently, a D/H4 candidate spanning a session closure may be blocked even
though the calendar interval has elapsed. A later calendar-aware policy must
explicitly verify expected trading minutes before relaxing this rule. No minute
is filled or invented.

## Historical spreads and M1 intrabar records

```text
/api/exness/history/intrabar?start=2026-10-13T10:20:00Z&end=2026-10-13T10:25:00Z&as_of=2026-10-13T10:25:00Z
```

The range is `[start, end)`, minute aligned, at most seven days, and must end at
or before `as_of`. Returned rows are eligible completed M1 bars from the verified
archive, including separate Bid/Ask OHLC and measured historical spreads.
Missing intervals and source-incomplete counts are reported independently.

`timestamp` and `close_time` are Unix **seconds**. `first_tick_ns` and
`last_tick_ns` are decimal **strings** in JSON so JavaScript does not round int64
nanoseconds. They remain int64 nanoseconds in canonical Parquet. `tick_count` is
retained quote activity, not traded volume. Spread mean is weighted by retained
quote count when minutes are aggregated; no constant spread is assumed.

M1 OHLC improves visibility into a larger candle but does not prove which stop or
target was hit first inside the same minute. Use the original tick sequence when
event order matters. Slippage, queueing, account conditions, commissions and live
latency remain separate execution assumptions.

## Python integration and scope

```python
from pathlib import Path
from app.market.confirmed_data import archive_snapshot, archive_intrabar

root = Path("data/exness/XAUUSDm")  # run from backend/
snapshot = archive_snapshot(root, "2026-10-13T10:25:00Z", ("M5", "H1"))
if snapshot["all_timeframes_confirmed"]:
    closed_hour = snapshot["snapshots"]["H1"]["bar"]
```

The HTTP endpoint additionally checks the active SQLite chart feeds. Python
callers can pass `store=market_store` for that comparison. The pure
`confirmed_from_minutes()` helper is available for a batch/replay caller that has
already verified its source DataFrame. Check confirmation and blocked reasons
before consuming a result; never replace a missing bar with zero prices.

This is an additive data interface for later ML/backtest consumers. Existing
indicator rules, `request.security` behavior, chart rendering and order execution
were not rewired. They do not automatically consume this new API. Existing normal
HTF lookahead-off mapping already waits for HTF close; this layer adds source,
gap and feed checks without changing those rules.

Regression coverage includes 10:25/11:00 boundaries, timezone offsets, missing
internal minutes, source-incomplete bars, stale history, altered partition hashes,
inconsistent cached prices, mismatched feed alignment, future intrabar requests,
and concurrent writer locks. Runtime evidence uses the real archived XAUUSDm
Bid/Ask feeds; account execution verification remains unclaimed.

See [the ML beginner guide](ML_DATA_GUIDE.md) and
[Exness's explanation of archive-server differences](https://get.exness.help/hc/en-us/articles/360021547851-Tick-history).
