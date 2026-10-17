# Fresh Demand/Supply badge overlaps

Updated 2026-10-17: removed the separate calendar-age restriction and the chart status box. Eligibility follows the original indicator's rendered fresh D/S price badges and current display settings. The original indicator source and all 210 inputs are unchanged. This remains visualization only, without entries or orders.

## Existing setting controls eligibility

**Hide Zones Farther Than (× Recent Range)** (`aoi_max_dist`) is a price-distance display filter, not an age setting. On each source timeframe, the indicator measures the highest high and lowest low of its last 120 candles. With a value of 0.5, it adds half of that range as padding above the high and below the low. A value of 0 disables that distance filter.

For example, if those candles span 4100–4200, the range is 100. At 0.5 the display window is 4050–4250. The original D/S renderer checks the zone's proximal badge price against that window. It also applies the configured maximum badge count, D/S ATR-or-percentage line distance, enabled sides, and Fresh state. Those other settings can still hide a badge inside the window.

The overlap service runs the original `SmcMaster` with the same inputs separately on **1m, 5m, 15m, 30m, 1H and 4H**, and consumes only the numeric Fresh D/S labels its renderer emits. It does not apply another distance formula or a calendar cutoff. Each timeframe therefore uses its own last 120 candles with the same configured multiplier.

An older zone can contribute only while the original indicator still displays its badge as Fresh. Age alone no longer includes or excludes it. Tested, Invalidated, hidden, or unbadged zones cannot contribute. OB, pivot S/R, FVG, BULB, Premium/Discount and reference levels remain separate and are excluded.

## Visual controls

Use **D/S overlap on/off** beside the indicator name. The arrow controls participating timeframes and minimum timeframe count. The previous maximum-age control and saved age preference have been removed. There is no floating overlap-count/age status box.

Demand ranges are green/turquoise; supply ranges are pink/red. Labels show side, distinct-timeframe count and contributing timeframes. Hover for exact prices, contributing badge ranges and the common observation time. Strength is confluence, not a profit probability.

Overlaps require positive-width price intersections between the same side on different timeframes. Touching endpoints and chains of pairwise overlaps do not become false common overlaps. Multiple badges on one timeframe count once. The strongest portions of each connected overlap area are kept; at most eight bands are drawn. Highlights start at the current bar and extend right.

## Observation and replay

All sources use a common completed-minute observation clock. Completed native candles are retained; incomplete higher-timeframe candles are reconstructed only from already observed M1 bars, excluding future final native OHLC. Missing source data is reported in the response, not guessed. This is not tick-by-tick intraminute analysis.

The single-flight frontend queue retains only the latest pending chart/settings request and does not publish obsolete responses. Original source display calculations use 3000 bars plus original warmup. Historical candles are required to calculate active source state; there is no separate search for old zones.

## Verification

- **161 backend tests passed**, including nine overlap tests. **41 frontend tests passed**; production build and lint passed.
- Regression checks cover exact original-renderer parity, Fresh numeric badge eligibility, no calendar cutoff, rejection of future origins, exact intersections, distinct-timeframe counting, false three-way chains, replay snapshots and exclusion of future final HTF OHLC.
- A new regression verifies changing `aoi_max_dist` at the same observation time changes eligible badges and every resulting badge satisfies the original timeframe-specific range filter.
- In-browser verification changed the actual indicator setting from **0 to 0.5**: eligible badges changed **25 → 22**, and selected overlap ranges **5 → 4**. Requests and responses contain no age parameter. The original value **0.5** was restored. The status box and age input are absent, and saved overlap preferences contain no age field.
- [Current chart screenshot](research/evidence/fresh-badge-overlap-distance-setting-2026-10-17.png).
- [SHA-256 comparison](research/evidence/fresh-badge-overlap-source-hashes-2026-10-17.json): all six original indicator files remain unchanged.

## Revert

Click **D/S overlap off** for immediate removal of the added drawings. For complete code removal, the [feature-only patch](research/fresh-badge-overlap-2026-10-17.patch) includes the latest changes and preserves unrelated pre-existing workspace edits. Its reverse dry-run passed. From the Trading Platform directory:

```sh
patch --dry-run -R -p1 < docs/research/fresh-badge-overlap-2026-10-17.patch
patch -R -p1 < docs/research/fresh-badge-overlap-2026-10-17.patch
```

Check the dry-run first if later work changes the same files. Restart the backend and frontend after full removal.
