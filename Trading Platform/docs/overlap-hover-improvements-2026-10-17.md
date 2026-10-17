# Demand/Supply overlap display improvements

The optional overlap layer still uses the existing indicator's displayed fresh Demand/Supply badges. Order Blocks, pivot S/R, FVGs and other systems remain separate. The underlying SMC Master detector and backend overlap geometry were not edited.

## Changes

- Labels show the actual common price band and distinct timeframe count.
- A star and stronger border identify the highest timeframe count for each side independently. All ties remain visible. The overlap settings optionally show only those bands.
- Rich hover details explain the shared band, width, participating timeframes, original zone ranges, freshness and observation time in the chart timezone. Base candle times are explicitly distinguished from zone confirmation times. Unfinished source candles can still change the result.
- Clicking an overlap pins its popup for scrolling; Escape, its close button or clicking elsewhere dismisses it. The popup is measured and positioned inside the chart. Popups reset when the observation clock, feed, timeframe or indicator settings change.
- A visible status reports calculation progress, empty results, source warnings and request errors.
- Future-dated overlap results are hidden immediately when replay is rewound, including results from requests already in flight. Earlier snapshots may remain during forward updates, with updating status and their own snapshot timestamp.

## Validation

- Production build, typecheck and lint passed.
- All 47 frontend tests passed, including four new overlap regression cases for exact bounds/provenance, per-side maxima/ties, rewind clock gating and duplicate badge counts.
- All nine existing backend overlap tests passed, including forward/backward equivalence with a fresh indicator calculation, exact interval geometry, independent timeframe counts, source visibility and reconstruction of unfinished HTF candles from observed M1 data.
- Opened and inspected the user's current XAUUSDm/5m Exness historical chart before editing. Browser automation subsequently became intermittent: native capture failed, the browser extension disconnected and the Playwright profile was already in use. Full interactive replay, hover/pin behavior and final chart-edge layout have **not** been visually verified. Automated tests do not establish trade performance or chart-specific replay validation.
