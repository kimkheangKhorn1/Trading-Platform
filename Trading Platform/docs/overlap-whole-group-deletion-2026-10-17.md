# Independent locked overlaps and future source retirement

Each locked overlap is deleted only when observed price reaches its own shared-band boundary: demand top or supply bottom. Touching a wider constituent source outside that band does not delete it. A gap through the shared edge also consumes the band. Geometry and captured timeframe count never shrink.

When a band is deleted, all of its original sources are retired from NEW overlap construction. Retirement does not invalidate other overlaps that were already locked before that retirement clock. Existing untouched groups keep their original range, source list and timeframe count even if they share the retired source. Their HTF contribution is captured historical confluence, not a claim that the original source remains fresh now.

Retirement uses stable timeframe/side/origin identity across all configured source timeframes: M1, M5, M15, M30, H1 and H4, for supply and demand. Retired sources are removed before new intersections/counts/ranking are computed. One old retired source plus one new source cannot form a two-timeframe overlap; two genuinely new sources can.

The original shared-band first-touch clock also starts the three-candle reaction window. Deletion and reaction timing now use the same shared-band touch. Saved source-edge deletions from the earlier policy are repaired in place using each lock's actual shared-band touch. Lock identities, birth times, geometry and counts are preserved. Saved groups are evaluated against retirement as of their own creation clock; replay before consumption restores the original state without future leakage.

The frontend's pending-snapshot filter also uses each band's own boundary. Source retirement never deletes a separate already-locked band through the frontend. Settings and tooltips describe these distinctions.

## Verification

164 focused backend tests and 57 frontend tests passed, along with production build, lint and whitespace checks. Added four-band regressions for supply/demand with a shared source on each of the six supported timeframes: first band deleted, three untouched groups unchanged, new shared-source combinations blocked, subsequent own-band touches delete individually, restart/replay retain the correct historical state. A separate test verifies repair of an old source-edge deletion without resetting locks. Existing three-candle reaction tests still pass.

The real running API was checked using the user's saved settings and archived replay time 2026-10-13 04:30 UTC, with chart timeframe M30. HTTP 200, no warnings. The lowest supply band beginning 4190.640 was absent. The three higher groups remained, each with its original strength 3:

- 4198.864–4199.067
- 4207.428–4208.304
- 4208.950–4214.237

This was an actual archived market-data and saved-ledger request, not a display fixture. The browser's current chart settings and replay position were unchanged.
