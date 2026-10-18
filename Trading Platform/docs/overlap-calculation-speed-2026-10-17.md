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

## D/S-only overlap source execution

The overlap service previously ran all independent SMC systems on every selected source timeframe to obtain only D/S badges. It now uses `DemandSupplyBadgeSource`, which calls the original `SmcMaster.demand_supply` and shared `render_demand_supply` methods with the same validated settings, ATR(14), history and rollback model. Other systems (structure, OBs, pivots, patterns, dashboard, BULB, envelopes and HTF security requests) are skipped in this internal source scan. The actual chart still runs the full SMC indicator. No thresholds, timeframes or warmup/history lengths were reduced.

The D/S rendering block was extracted without formula changes so the full chart and overlap source cannot diverge through separately copied rules. Source alerts/strategy intents are not emitted by this internal badge-only path. Retirement, locked bounds/counts, own-band deletion, candidate ranking and candle-reaction rules are unchanged.

### Measured comparison

Five sources M5/M15/M30/H1/H4 over the existing Exness archive, fixed D/S settings: cold 2489 → 824 ms; next observed-minute steps 189 → 95 ms and 208 → 61 ms; unvisited backward jump 1872 → 703 ms. All serialized service outputs matched exactly at all four clocks. These are single local direct-service samples without locks/reactions; detailed evidence is in `research/evidence/overlap-ds-only-latency-2026-10-17.json`.

The running API was also measured using the actual browser's saved inputs, selected source timeframes, indicator lock ID and candle reactions enabled. Responses took 52, 108 and 78 ms, all HTTP 200 without warnings. Band count advanced from 3 to 4 at the next observation and remained 4. The user's replay state was unchanged. These measure response completion, not browser paint or a guarantee of zero latency. Cold loads and uncached backward jumps still require a source scan; queued work and machine load can add delay.

### Validation

182 focused backend tests and 61 frontend tests passed, plus production build, lint and whitespace checks. Twelve added parity cases cover all six source timeframes, ATR/Percent modes, distance/line/timezone settings and provisional-to-confirmed rollback. They compare fresh source badges against the full indicator at each observation, with nonempty badges required. A separate exact comparison of every full-indicator output field across 1,800 M1 candles matched the original code. The prior latency from unrelated source systems is removed without making optimistic overlaps from stale/retired source data.
