# Fast closed-candle overlap reactions

Confirmed 1m/5m reactions now run independently of multi-timeframe source discovery. `/api/overlap-reactions` validates the same settings, observes saved locks at the supplied causal clock, and executes the original shared candlestick formulas. It never calls source discovery or creates a new lock. The normal overlap request skips duplicate pattern calculation.

The frontend sends reaction work without debounce in its own queue. It coalesces pending reaction clocks independently of the full source-scan queue; indicator instances request reactions concurrently. A completed-bar signature triggers another request when a candle changes from forming to closed, even at the same observation clock. Corrections to its OHLC also trigger a reaction refresh. After full source discovery captures a new lock, the reaction lane immediately rechecks that context. Scope and observation-clock guards prevent old settings or future replay results from being painted.

Confirmed annotations can render as a drawing-only layer before the main indicator output arrives. This removes another initial-load dependency without fabricating plots, candles or strategy signals. Existing full-indicator output is combined with the annotations when available.

Supply/demand roles, pattern toggles/filters, closed-candle confirmation, own-band contact, independent three-candle windows, source retirement and saved-lock geometry are unchanged. Forming candles do not produce confirmed markers. There is no extra timer waiting for the next full source scan.

## Evidence

Validation: 186 focused backend tests and 69 frontend tests passed. Production build, lint and whitespace checks passed.

- Running API, actual saved browser settings and an existing archived replay observation: three fast reaction responses completed in 41, 26 and 24 ms, HTTP 200, each returning the same 13 historical reaction records. The user's replay state remained unchanged.
- Controlled isolated browser display check: after initial chart loading, a fixture marker painted 28 ms after its reaction response while the full source response was deliberately delayed by two seconds. No browser errors. This is response-to-paint evidence with an explicit display fixture, not a live market close-to-paint claim.
- Initial-page checks also showed symbols can render before either full source discovery or full indicator output; initial chart/bootstrap loading still contributes latency. The final steady-state check used UI controls, avoiding duplicate module-store imports in the test harness. All isolated contexts were closed afterward.
- Backend API tests cover M1/M5 and supply/demand: no symbols before candle close, first/second/third confirmations, no fourth-candle confirmation, rewind, disabled patterns, no new lock creation and return while a full scan is blocked by an event.
- Queue tests verify immediate request start, latest-pending close coalescing, independent concurrent instance requests, error recovery and completion/correction detection at the same clock. Drawing-layer tests verify that markers do not require full indicator output and introduce no fake values or orders.

Limits: this removes application scheduling/calculation dependencies after confirmed data is available. Provider polling, missing/late M1 or native timeframe data, network delay, initial page/chart loading and CPU load still affect wall-clock latency. It does not pretend that a forming candle has closed. Previously reported source-retirement scope and source-cache revision concerns are separate and were not changed by this timing work.
