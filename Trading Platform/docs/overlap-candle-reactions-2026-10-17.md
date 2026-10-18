# Candle reactions at locked Demand/Supply overlaps

The overlap settings now include **Candle reactions · 1m / 5m**, enabled by default when the overlap layer is used. The feature uses the active chart timeframe: 1m patterns on a 1m chart, 5m patterns on a 5m chart. Other chart timeframes retain the overlap layer without reaction symbols.

The existing SMC candlestick formulas were moved into a shared module. Both the original pivot-context patterns and overlap reactions call the same formulas, pattern toggles, EMA body comparison and ATR/wick/body thresholds. Hammer/shooting star and long-shadow patterns cover the existing pin-bar shapes; no separate pin-bar detector was added. The original pivot context, signals, drawings and strategy behavior remain in place.

For overlap reactions, actual candle-range contact replaces pivot S/R context. Rejection requires a directional pattern closing away from the band's favorable edge (bullish above demand, bearish below supply). Continuation means a directional pattern closing through the other side (bearish below demand, bullish above supply). A matching pattern without either close confirmation receives an unconfirmed symbol. Balanced doji/spinning tops remain unconfirmed. A touch without an enabled qualifying pattern produces no pattern label.

- `↶ H`, `↶ SS`, `↶ Bull Eng`, `↶ Bear Eng`, etc.: rejection with pattern code.
- `➜ Bull Eng`, `➜ Bear Eng`, etc.: continuation through the band.
- `? D`, etc.: pattern present, reaction unconfirmed.

The tooltip gives the original locked D/S bounds, pattern names, reaction, timeframe and confirmation time. Symbols share the chart's existing collision layout. Only equivalent reactions on the same candle share a symbol: timeframe, zone side, direction/neutrality, pattern code and reaction must all agree. Different zone roles, patterns or outcomes keep distinct collision-spaced labels, so confirmed reactions cannot be turned into an ambiguous question mark by merging.

Deletion now follows each locked overlap’s own shared-band boundary (see overlap-whole-group-deletion-2026-10-17.md). Its shared-band first-touch clock also starts candle reactions. Retiring sources affects only NEW groups; already-locked untouched groups stay unchanged. The registry supplies already-consumed locks as historical reaction context; they do not become active boxes again. Only completed candles whose close is observed can confirm. Only three chart candles are eligible: the candle containing the first M1 touch and the next two candles on the active chart timeframe. Later visits do not restart this window. A consumed fresh box remains historical reaction context for those three candles, including after its original source badges disappear and across service restarts. Gaps over the entire band do not pretend to touch it. Two-candle patterns need adjacent candles. Candles beginning before the lock was observed are excluded, including a 5m candle that began before a mid-candle lock. This deliberately avoids using pre-lock candle geometry.

No zones or reactions are invented before a lock was observed. Historical evaluation uses the most recent 3,000 chart candles plus the indicator's warmup and returns at most 500 reaction records. Existing distance and side-display filters also apply. Reaction history therefore has a bounded display horizon. Pattern visibility is controlled by the new reaction switch; the original pivot pattern label/box switches continue to control the original pivot pattern drawings.

## Verification

- 62 focused backend tests passed, including 24 reaction cases: M1/M5, shooting star/hammer, bullish/bearish engulfing, toggle/threshold reuse, inside closes/doji, future/forming/pre-lock exclusions, gaps, the exact three-candle cutoff, mid-5m first touches, and no window restart after later visits or a service restart.
- 53 frontend tests passed, including reaction drawing persistence after box deletion, future/pre-lock filtering same-candle grouping and distinct bullish/bearish engulfing labels within the first-touch window.
- TypeScript and production build passed. Vite reports its existing bundle-size advisory.
- Before/after comparison: every original indicator output field was identical over 1,800 synthetic M1 candles after extracting the shared formulas.
- Live browser request: HTTP 200, reaction flag enabled, no source warnings. The current snapshot had no qualifying reaction.
- Browser display fixtures separately verified all three glyphs, placement and hover explanations. These were explicitly synthetic display checks, not evidence of real market signals. Fixture interception was removed and original indicator preferences restored afterward. Screenshot: `.playwright-mcp/overlap-candle-reactions-fixture.png`.

## Three-candle limit

Per the latest request, detection is limited to exactly three candles since first touch: first-touch candle = 1, next candle = 2, following candle = 3. Candle 4 and subsequent visits cannot produce new reactions for that lock. Bullish/bearish engulfing labels, existing pattern settings, close confirmation and saved historical symbols are preserved.

## Additional endpoint verification

Added parameterized HTTP tests for both M1 and M5. These use a temporary SQLite ledger, native chart bars and completed M1 data, the real overlap endpoint and shared pattern formulas. Only source badge discovery is controlled to create a known overlap. Endpoint responses contain 1, 2, 3, 3 and 3 reactions after candles 1, 2, 3, 4 and 8 respectively. Recreating the service retains the cutoff; rewinding to candle 2 returns only two confirmations. This verifies controlled-data runtime behavior, not a live market pattern claim. All 62 focused backend tests and 53 frontend tests passed, along with production build and lint. The running browser/backend health check returned HTTP 200; overlap and candle reactions were both enabled in the current browser preferences.

## Supply/demand design audit

Confirmed a neutral-pattern display bug with failing tests: doji and spinning-top candidates matched both directions, and the old tie-breaking chose bullish first even for supply. The response now explicitly carries `neutral`; these patterns stay unconfirmed, their tooltip says Neutral, and placement follows the zone (above supply, below demand). Directional patterns still evaluate both bullish and bearish formulas independently of zone side. The original pivot-context indicator is unchanged by this repair.

Expanded HTTP coverage to supply and demand, M1 and M5, pin bars, bullish engulfing and bearish engulfing. Supply fixtures begin below the overlap; demand fixtures begin above it, exercising both branches of the real first-touch ledger. Tests assert the side, pattern direction, rejection/continuation classification, three-candle boundary, rewind and service restart. Added both-side/both-direction checks for dragonfly/gravestone, tweezers, piercing/dark cloud, harami and long shadows, plus neutral doji/spinning tops on both timeframes. Source badge discovery is controlled in the HTTP fixtures; the endpoint, saved ledger, candles and pattern formulas run normally. Frontend tests cover the neutral tooltip and above-supply placement.

Audit verification: all 120 focused backend tests and 54 frontend tests passed. Production build, lint and whitespace checks passed.

## Pattern presentation and independent windows

Fixed a confirmed presentation bug: grouping only by candle/direction merged a demand rejection with a supply continuation into a misleading `?` symbol. It also merged neutral and directional patterns. Regression tests reproduced both failures before the grouping rule was tightened.

Hover details now use exact directional names (Hammer versus Shooting Star, bullish versus bearish engulfing/harami, dragonfly versus gravestone, tweezer bottom/top, piercing/dark cloud and lower/upper shadow), the original overlap range, reaction, close-confirmation time and Candle 1/2/3 of 3. The shared formulas and user pattern/filter settings are unchanged. Neutral patterns remain neutral/unconfirmed. Backend records expose the window ordinal; the frontend independently rejects out-of-window, pre-lock, future and malformed close-time records.

New tests prove that two separate zones have independent first-touch windows on both 1m/5m and both supply/demand. A later touch of the second zone neither extends the first zone’s expired window nor suppresses the second zone’s first candle. Mid-5m touches count their containing candle as candle one.

Verification: 170 focused backend tests and 61 frontend tests passed; build, lint and whitespace checks passed. An isolated browser context with clearly marked display fixtures verified demand rejection, supply continuation and neutral symbols on one candle, distinct hover details and three non-overlapping vertical positions. Screenshot: `.playwright-mcp/overlap-reaction-separate-labels.png`. The context was closed afterward; the user’s browser/replay was untouched. A separate real API request at the archived 2026-10-13 04:30 UTC M5 snapshot returned HTTP 200 and no qualifying reaction, so no market pattern was forced.

## Long Lower Shadow excluded from confirmation

Per the user request, standalone Long Lower Shadow (LLS) is excluded from overlap reaction candidates on both demand/supply and M1/M5. It cannot create a rejection, continuation or unconfirmed overlap symbol or contribute an additional confirmation name. The frontend also rejects LLS records from older in-flight responses.

The original SMC pivot-context Long Lower Shadow formula, display and `Detect Long Shadows` setting are unchanged. Long Upper Shadow remains eligible. A candle meeting the independent Hammer or another enabled pattern's own rules can still qualify under that pattern, even if it also geometrically has a long lower wick. The overlap settings explain the exception.

## Per-timeframe window (2026-10-18)

The reaction window now depends on the chart timeframe (`REACTION_WINDOW` in `backend/app/overlap_reactions.py`,
mirrored in `frontend/src/api/badgeOverlap.ts`): **1m checks 10 candles** (the first-touch candle and the next 9),
**5m still checks 3** (the first-touch candle and the next 2). Later visits still never restart the window.
