# Overlapped order blocks (2026-10-18)

A second overlap layer next to the D/S overlap, toggled from the SMC legend (**OB overlap**). It finds price bands
where the order blocks the chart displays on several timeframes stack, and applies the D/S overlap lifecycle.

## Rules

- **Sources:** the order blocks the chart itself draws on each source timeframe (`displayed_order_blocks`: within
  "Hide Zones Farther Than", nearest `ob_showlast` / `iob_showlast`), Swing and/or Internal. Bullish = demand side
  (proximal edge = top), bearish = supply side (proximal edge = bottom).
- **Untouched only** (default on): a block counts only if no completed 1m candle since its structure-break candle
  closed has reached its proximal edge.
- **Lifecycle (shared with D/S):** locked at discovery (bounds and timeframe count frozen), deleted on the first touch
  of its own band, its source blocks retired from new overlaps, hidden (not deleted) when far, 1m/5m candle reactions
  (10 / 3 candles from the first touch).
- **Separate locks:** OB scopes are `ob:`-prefixed and keyed by the order block settings; D/S scopes are unchanged.

## Code

- `scripts/smc_master/indicator.py`: `structure_step` and `displayed_order_blocks` extracted from `on_bar` / `render`
  without behaviour change (identical full output on real M5 and M15 data).
- `scripts/smc_master/ob_source.py`: order-block-only runner; same blocks as the full indicator on M1–H4, about 9× faster.
- `badge_overlap.py`: `kind='ob'`, `ob_types`, `fresh_only`; `locked_overlaps.py`: per-kind scope.
- Frontend: `OverlapControl` (redesigned settings panel shared by both layers), `OverlapStatusLine`, kind-aware
  drawings (blue / orange, own drawing ids) and hover card. The status line now draws above the chart canvas.

## Verification

Real XAUUSDm data, 6-hour replay in 5m steps: no locked band ever changed, touched bands were deleted, 60–90 ms per
warm step (about 300 ms cold). 337 backend and 73 frontend tests pass.
