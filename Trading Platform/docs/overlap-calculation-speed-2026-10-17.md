# Chart calculation latency

## Measured causes

The optional D/S overlap service runs the original indicator over six source timeframes, with 3,000 source bars plus warmup per timeframe. Initial scans and unvisited backward replay jumps need a full calculation. Normal forward replay is incremental but still takes rollback snapshots and renders copied state. The profile showed substantial deepcopy overhead and repeated higher-timeframe candle loads for separate high/low/time expressions. ChartView also intentionally waited 250 ms before ordinary indicator requests, including timeframe changes.

## Changes

- Timeframe/feed/history-window changes schedule the chart indicator immediately. Settings edits retain their existing debounce.
- SMC Master snapshot copying now copies numeric-history list containers directly; their immutable scalar/tuple entries can be shared. Mutable zones, drawing references and all other state still use the same deepcopy memo. Zone detection, settings, warmup and displayed features are unchanged.
- The runner reuses a higher-timeframe candle load for all expressions requesting that source during one refresh. This cache is discarded between observations.

## Evidence

The local Exness bid archive was benchmarked at five observation clocks: cold scan, three minute advances and an unvisited backward jump. Parameters were defaults with aoi_max_dist=0.5, not an exported user settings snapshot. These are direct service timings, not end-to-end browser timings or a claim about trade quality.

| Calculation | Before | After |
| --- | ---: | ---: |
| Cold six-timeframe scan | 2.604 s | 2.390 s |
| Forward step 1 | 315 ms | 265 ms |
| Forward step 2 | 284 ms | 216 ms |
| Forward step 3 | 308 ms | 270 ms |
| Unvisited backward jump | 2.840 s | 2.406 s |

Exact serialized overlap outputs matched at every benchmark clock. Timings are a single local comparison and will vary with system load. See research/evidence/overlap-calculation-speed-2026-10-17.json.

Validation: 57 focused backend tests passed across snapshot isolation, provisional rollback versus the previous recursive-copy path, indicator incremental/full parity, no-lookahead behavior, source badges, MTF, API, pivot lifecycle and backtest execution. All 47 frontend tests, production build and lint passed. Live browser latency was not measured.

Remaining cost: a cold scan or uncached backward jump still rebuilds the complete indicator history on each selected source timeframe. High replay rates can outrun calculation. No settings or source timeframes were silently reduced to achieve the measured improvement.
