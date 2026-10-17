# Timeframe loading audit — 2026-10-17

## Scope and current source

Audited the restored SmcMaster Python indicator and its chart loading path. Claude's latest completed session removed the MTF strategy at the user's explicit request; `backend/app/scripts/mtf_confluence` is absent. The earlier strategy's 2021-anchored replay is therefore not the cause in the current source. No production source or chart settings were edited by this audit.

Browser measurements used the existing local platform in a separate Chrome automation window, Exness archive bid feed, current saved/default indicator inputs after metadata cleanup. Local provider profiling used stored candles only, with external synchronization disabled. Different browser profiles can have different indicator settings; these measurements are not a claim to reproduce every private-profile setting or a delay length not yet reported by the user.

## Confirmed findings

### 1. P2 — Counting millions of rows to answer a yes/no question delays candle loading

`backend/app/main.py:247` calls `count_before(...) > 0` to set `has_more`. `backend/app/market/repository.py:217` implements that with `SELECT COUNT(*) ... t<?`. The client only needs to know whether one older candle exists.

On the current M1 dataset, loading 3,000 candles took 14.4 ms while counting the 3,919,115 older candles took 893.4 ms in one measured run. Three subsequent comparisons measured a count median of 1098.157 ms versus 2.109 ms for an existence query, with identical true/false answers. These times vary with cache and machine load; they are measured examples, not guarantees.

A safe focused optimization is a separate existence check in the candle endpoint: `SELECT EXISTS(SELECT 1 FROM candles WHERE series_id=? AND t<? LIMIT 1)`. Keep `count_before` for sync code that actually needs an integer count. Do not replace the return type/semantics of `count_before` globally.

### 2. P2 — A previous timeframe blocks the latest indicator request

`frontend/src/chart/ChartView.tsx:108–123` clears outputs and calls `IndicatorScheduler.forget()` on timeframe changes. `frontend/src/chart/scheduler.ts:43–55` clears timers/pending keys but leaves the previous in-flight job associated with the same indicator UID. New work is queued until that old request settles. The fetch layer has no request cancellation signal or timeout.

Controlled reproduction through Vite's actual scheduler module, with deferred API promises:

- Start an M5 indicator request.
- Switch context to M1 using `forget` and `request`.
- Before the M5 promise settles, the only API call is M5; M1 has not started.
- Once M5 settles, M1 starts.

Serial execution avoids unconstrained backend work, but the queue is not scoped to a generation/context and adds an obsolete request's remaining latency to a rapid switch. Prioritize the latest context and reuse known timeframe results while refreshing; cancellation must be designed with the synchronous backend's behavior in mind, because aborting the client fetch alone does not prove backend CPU work stopped.

### 3. P2 — An obsolete request's error leaks into the selected timeframe

The success path checks context freshness at `scheduler.ts:64–66`, but the catch path at `scheduler.ts:68–69` does not. The same controlled reproduction rejected the old M5 request after M1 was queued. M1 then began loading while its output state still contained `Old M5 request failed`.

This can show an error belonging to a different timeframe. Apply a context/generation guard to errors and pending-job lifecycle too. Regression coverage should include old success, old failure, removed instances, and rapid A → B → A switches.

## Measured loading behavior and ordinary work

The browser returned HTTP 200 for every tested indicator calculation and no console errors were observed. The first measured 1m switch took about **3.55 seconds**, including its 1.60-second indicator request. A subsequent series of warmed switches measured:

| Selected TF | Full switch | Candle request | Indicator request |
| --- | ---: | ---: | ---: |
| 15m | 1.18 s | 0.38 s | 0.22 s |
| 1H | 0.73 s | 0.20 s | 0.16 s |
| 1m | 1.28 s | 0.57 s | 0.26 s |
| 5m | 1.16 s | 0.21 s | 0.52 s |

These are browser measurements under the current local machine load, not a multi-run performance benchmark. The main sequence also includes a configured 250 ms indicator debounce after candles arrive.

The initial indicator calculation processes **4,000 bars**: 3,000 requested bars plus at least 1,000 warm-up bars. It runs the structure, zone, pattern and dashboard modules. In an instrumented CPU profile, the nine-indicator dashboard was the largest individual calculation group; pivot S/R was next. The service's runner cache is working: repeat indicator-only calls were about 93–106 ms, compared with roughly 598–891 ms cold in a separate unprofiled measurement. Do not claim it fully recalculates from scratch on every return to a timeframe.

Cached requests still reload bars, copy state for provisional processing and final rendering, and serialize roughly 790 KB with about 1,050 drawings. These are secondary optimization opportunities. Preserve warm-up and causal indicator rules rather than reducing history or removing detectors merely to make a loading number smaller.

## Recommended order

1. Replace the endpoint's boolean full-count with a dedicated existence query.
2. Correct scheduler generations and stale-error handling; handle latest-context work without replaying obsolete queued contexts.
3. Reuse per-timeframe chart outputs for immediate display, then refresh as needed. Retain feed/settings/history provenance to avoid stale signals.
4. Optimize measured rolling calculations, duplicate HTF loads and rendering copies only with output-equivalence checks.

This audit establishes loading inefficiencies and the reproduced stale-error bug. It does not establish complete indicator/TradingView parity, and it did not change market-structure, OB, FVG, Demand/Supply, pivot or BULB rules.

## Raw local evidence

- `/tmp/tp_scheduler_audit.mjs` — actual scheduler module reproduction; only M5 starts before its deferred promise settles, and its error appears in M1's loading state.
- `/tmp/tp_indicator_perf_audit.py`, `/tmp/tp_perf_M1_cold.txt`, `/tmp/tp_perf_M1_cached.txt` — CPU profile.
- `/tmp/tp_perf_compare.json` — unprofiled indicator and query comparison.

### Query and indicator comparison

```json
[
  {
    "tf": "M1",
    "count_median_ms": 1098.157,
    "exists_median_ms": 2.109,
    "matching": true,
    "cold_indicator_ms": 890.7,
    "cached_indicator_ms": 92.6
  },
  {
    "tf": "M5",
    "count_median_ms": 30.845,
    "exists_median_ms": 0.047,
    "matching": true,
    "cold_indicator_ms": 599.9,
    "cached_indicator_ms": 106.0
  },
  {
    "tf": "M15",
    "count_median_ms": 10.117,
    "exists_median_ms": 0.025,
    "matching": true,
    "cold_indicator_ms": 598.2,
    "cached_indicator_ms": 98.1
  }
]
```

### Reviewed source hashes

- `backend/app/main.py`: `6a40100606844db860168e75f18ac5eb535a6ca7c0e026f9bde1896ae8eafd23`
- `backend/app/market/repository.py`: `c1785eca949e9c8fbef80f5576adf0fed4ea019760e3b803ee3c720f85e8948b`
- `backend/app/indicator_service.py`: `51612795aef3df7cfedcdbd0b2238f117c1a958aee86bef95792cc20d4f6e4c7`
- `backend/app/engine/runner.py`: `62109344a99925a0fd6ebdbde777555d256f20075b950ab1c676abdde440fe9f`
- `backend/app/scripts/smc_master/indicator.py`: `133d12d1f96583b5879d8d5824375ec2ef5fa3f284272f0bfcae2bb8982914c6`
- `frontend/src/chart/ChartView.tsx`: `b0eb488ca850dfd108d9e6f76eda1bc5b743551555ca6d700bd14fd486897b01`
- `frontend/src/chart/scheduler.ts`: `f7f883676d3ab4d97840c988149ae49268ee96a0623ac20159d0a139acb51ce8`
- `frontend/src/api/client.ts`: `4609846c9486ba70c2a8f0bb815fb3657582623cd341221f03cb18e33dd4f497`
