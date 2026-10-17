# MTF signal-producer fixes — 2026-10-17

## Current context checked before editing

Claude's latest session was implementing and verifying the user's revised TP rule: up to three opposite Demand/Supply zone targets, most overlap first, single fresh zones if no overlap exists. Its latest recorded response reports completion. The latest accepted entry is a touch at the near edge of the precise overlap band, not the superseded 50% entry. The request to require at least two TPs was withdrawn. The user explicitly chose any TP hit as WIN. Existing SL calculation and per-order SL/TP locking remain intact.

This is an indicator-derived multi-timeframe Demand/Supply strategy. It is not model retraining, and overlapping D/S zones are not OB, pivot S/R or FVG signals. This patch does not change those independent indicator systems.

## Changes

1. **Forming-candle fills:** `MtfFeature.on_bar` now evaluates existing orders and open trades against the current forming candle instead of skipping it. Zone discovery, MA/ATR updates, and new order selection still use confirmed candles. The runner's existing rollback mechanism replays each evolving candle from its last committed state, preventing duplicate fills and drawings. The completed candle is then committed normally.
2. **Selection-cache dependencies:** order selection refreshes when ATR/tolerance changes and when price changes with both sides allowed. This fixes newly eligible clusters and a previously rejected wide-stop candidate remaining suppressed, and fixes retaining the farther side. Re-evaluation does not replace an unchanged order or alter its frozen SL/targets.
3. **Execution-price drawings:** entry line and label switch to the actual fill price, with original plan and actual fill time/price retained in tooltips. Already-open trades restored from history use their actual entry too. SL and TP levels are not relocated by this drawing fix.
4. **Zone-only TP edge case:** the new `Zones, most overlap first` mode no longer substitutes R multiples when no suitable opposite zone exists, even if a saved legacy fallback checkbox is enabled. That mode skips the signal when no zone target exists. Explicit R-multiple and Hybrid options remain available.
5. **Setting explanations:** Entry help explains forming-candle previews, and identifies the optional CHoCH trigger as a stored-lookback breakout, distinct from native internal/swing SMC CHoCH events. Target fallback help explains the zone-only exception.

## Verification

- Full final backend suite: **185 passed in 35.00 seconds**, one existing Starlette/httpx deprecation warning.
- New regression file: **11 passed**. It covers ATR-created eligibility, nearest-side replacement, rejected-stop retry, unchanged-order SL/TP locking, forming entry and stop updates, repeated-update deduplication, rollback followed by final commit, actual entry line/label coordinates for both market and price-improved limit fills, and zone-based TP ranking/fallback in both directions.
- The first seven regression cases were run against the frozen pre-fix audit source: **all seven failed**. They pass with this patch. The four additional target cases cover the newer TP rule Claude added after that audit snapshot.
- Stored-market replay: **408,700 complete M5 candles from the 2021 anchor through the archive end** were processed with current defaults. This was a correctness/example replay, not a profitability evaluation or parameter search. The script found the user's October 13 trade with the expected entry, SL and all three zone targets.
- Browser verification used the actual app at `http://localhost:5173/`, via native Chrome inspection and a separate Chrome automation window. Real indicator API responses returned 200 and no browser console errors were observed. No production market data was altered.

## Observed chart evidence

At **2026-10-13 05:10 UTC**, the touch-entry sell is **4198.864**, with **SL 4203.269**, and targets **4156.973 / 4145.002 / 4111.944**. The API entry drawing starts at Unix time `1791868200` (05:10), matching the engine fill, and the rendered blue entry line starts on that candle at the overlap edge. Screenshot: [touch entry](evidence/mtf-signal-fixes-2026-10-17/signal-fixed-entry.png). The chart payload and independent engine replay agree; compact evidence is in [verified-trade.json](evidence/mtf-signal-fixes-2026-10-17/verified-trade.json).

The optional market-confirmation mode was temporarily selected only in the separate verification window to check a real non-identical fill. The October 13 example planned **4190.973** at 05:40, filled at **4191.254** at 05:45, and now draws **4191.254**. The response tooltip retains both prices and times. Screenshot: [actual fill](evidence/mtf-signal-fixes-2026-10-17/signal-fixed-actual-fill.png). The verification window was restored to Zone touch afterward.

These browser checks use archived prices. The forming-candle behavior was checked through the production runner's automated update/rollback tests; this is not a claim of an observed live market tick or a real broker order. Forming OHLC previews still use the existing conservative intrabar path assumption and can revise until candle close. There is no new external trading execution or promise of improved win rate.

## Files changed by this patch

- `backend/app/scripts/mtf_confluence/chart.py`
- `backend/app/scripts/mtf_confluence/engine.py`
- `backend/app/scripts/mtf_confluence/inputs.py`
- `backend/tests/test_mtf_confluence_regressions.py`

The original audit remains a record of the pre-fix version. Its confirmed findings are resolved as described above; original TradingView runtime parity and profitability are outside these checks.

## Final source hashes

- `chart.py`: `59cfc52f098d6fc2b29c92dd0528ef217ab18b30519af64bcc88eef517f74200`
- `engine.py`: `eb12c3f90b5a7a7c530d4efbc87fbdbba45ace2930077ec549cdfb72347f85ee`
- `inputs.py`: `a11fe142746efbbea2140437b7a7fc51cfcd7b254aac458ebcb47a05d2f82cb2`
