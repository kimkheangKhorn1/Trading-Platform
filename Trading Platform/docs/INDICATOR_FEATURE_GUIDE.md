# SMC & ICT & Indicators + Scalping Master — project feature guide

Reviewed: 13 October 2026. Workspace: `Trading Platform`.

This guide describes the **current Python implementation and React frontend**. Its evidence is the project code, resolved project defaults, existing tests, and a run on stored XAUUSD candles. The Pine file is not the basis of this guide. Defaults below are the defaults for a new instance; a saved chart can use different inputs and Style settings.

## What the project actually is

The project has a combined analysis indicator with several partly connected systems. It produces market-structure drawings, different types of zones, directional readings, reversal labels, and detection events. It does **not currently combine all of them into a final entry decision**.

The strongest evidence is `SmcMaster.strategy()` in `backend/app/scripts/smc_master/indicator.py`: it contains only a docstring, including an example of how orders could be added. That example is documentation, not executable entry logic. `on_bar()` calls the method, but it currently creates no orders. Metadata registers an **SMC Master** strategy so it appears in the Strategy Tester; registration alone does not implement a strategy.

There are three different meanings of “signal” here:

| Output | Examples | What it means |
|---|---|---|
| Analysis state/drawing | Bullish structure, OB, demand zone, FVG, dashboard “buy” | A condition or area to inspect |
| Detection event/marker | BOS, pivot resistance break, BULB Buy, all-nine agreement | A specific module's rule occurred; it does not establish a complete trade |
| Executable trade intent | Long/short order with stop, entry rules, optional targets | Created through `ctx.signal()`; the current SMC Master does not call it in executable code |

The existing common purpose is **to organize price-action context in one chart**. A common purpose of **selecting and producing qualified trades** is still unimplemented. Some modules cooperate, so they are not completely unrelated; their cooperation stops short of a shared trading decision.

## 1. Shared runtime, data, and confirmation

**How it works.** FastAPI supplies candles and runs `SmcMaster`. The script receives OHLCV bars in chronological order. It updates persistent state and outputs plots, shapes, boxes, lines, labels, tables, and warnings. React/Lightweight Charts displays those outputs; the browser does not calculate the SMC systems itself.

`ScriptRunner` commits completed bars. For a forming live candle, it snapshots state, evaluates provisionally, and rolls that evaluation back before the next update. Thus repeated updates do not permanently create multiple copies of the same provisional event. Historical CSV bars are completed bars.

`emit()` retains module events only on confirmed bars. Those events are held in `self.events`; once the list exceeds 1,000 it is cut down to the latest 500. This is a rolling internal event list, not a complete historical alert archive. `extra_output()` exposes tables and warnings, and the runner's API response exposes chart output and trade intents. It does **not expose this module-event list**. There is no alert delivery service for these events in the inspected project.

**Purpose.** Give all modules the same candle sequence, confirmation state, feed precision, and timeframe. This is shared infrastructure, not confluence logic.

**Timeframe boundary.** Internal and Swing Structure operate on the **current chart timeframe**. Swing does not automatically mean a higher chart timeframe. Previous D/W/M levels and optional HTF FVGs use other-timeframe data, but there is no built-in H4 → H1 → M5 structural-bias pipeline.

The indicator service requests warm-up history before the visible start, with a minimum requested warm-up of 1,000 bars and potentially more for long settings. If the feed has less history, requesting warm-up cannot create missing candles. Long-lived zones, recursive indicators, and cumulative thresholds can depend on how much history is available.

Sources: `backend/app/engine/runner.py`, `backend/app/engine/script.py`, `backend/app/engine/mtf.py`, `backend/app/indicator_service.py`, `backend/app/scripts/smc_master/indicator.py`.

## 2. General display and candle coloring

**Historical / Present.** Historical retains structure and related labels subject to drawing limits. Present removes earlier drawings in selected structure/swing/EQ display paths when replacements are made. It is not a global switch that removes every historical zone or label in every module.

**Colored / Monochrome.** Controls selected SMC structure, OB, and candle-color paths. Monochrome is not a theme override for every subsystem: Demand/Supply, pivot patterns, dashboard, BULB, and envelopes retain their separate colors.

**Color Candles.** Uses the internal structure trend state. A bullish candle in bullish structure is filled; a bearish pullback is hollow. In bearish structure, the relationship reverses. The current code selects the **swing color inputs** for colored candles even though the direction comes from **internal structure**. Before the trend is bullish, the code takes the bearish color branch, including the initial neutral state.

**Hide Zones Farther Than.** Measures the high-low range of the last 120 processed chart bars, then adds padding above and below it: `padding = recent range × aoi_max_dist`, default `0.2`. It hides distant OB/FVG/reference-level drawings, pivot boxes/labels and selected D/S lines. Tracked logical zones can remain valid while hidden. Zero disables this filter. It is neither a trade-location selector nor a prediction of how far price can move, and it follows recent processed bars rather than the user's exact panned viewport. It does not apply uniformly to every drawing, notably all D/S origin boxes.

**Purpose.** Make the chart readable and show structure direction. Changing display is not automatically changing signal qualification.

## 3. Internal Structure

**Default:** internal length 5, display on, bullish/bearish selection All, confluence filter on.

**How pivots are obtained.** This is an alternating leg-state detector. It compares the candle `length` bars back with the highs/lows of the following `length` bars. A candidate beyond those later bars changes the leg state and confirms an extreme when that state changes. This is not the pivot S/R engine's symmetric left/right test. The effective internal length is capped below Swing Length.

**How breaks occur.** A confirmed close must be beyond an unbroken internal high or low. An internal level matching the corresponding larger swing level is excluded. With Confluence Filter enabled, bullish breaks need the close in the upper half of the candle's range; bearish breaks need it in the lower half. If a candle is beyond the level but fails the filter, a later qualifying confirmed close can still register the break.

**BOS versus CHoCH.** A break opposite the stored internal trend is CHoCH. A same-direction break, or a break before an opposite trend has been established, is BOS. This is a classification from the module's state, not a guarantee of future continuation/reversal.

**Output.** Dashed structure lines, small BOS/CHoCH labels, internal trend state, confirmed events, and optional internal OB creation.

**Purpose.** Show relatively small structural changes and potential local reaction confirmation. It can disagree with Swing Structure during a pullback. The rest of the project does not universally require internal confirmation before drawing its own signals.

Turning off Show Internal Structure hides its structure drawings; the structure calculation and its events still run. BOS/CHoCH selectors likewise select drawings rather than universally filtering events.

## 4. Swing Structure, swing-point labels, and strong/weak levels

**Default:** length 25; swing structure, swing-point labels, and strong/weak levels enabled.

**How it works.** Uses the same leg-state approach at a larger length. Swing breaks use confirmed close-crossing behavior and one-break-per-active-level state. A break against the stored swing trend is CHoCH; otherwise it is BOS. When an opposite pivot confirms, `leg_extreme()` can update the previous side to its true intervening extreme. It avoids inventing a new break after price had already closed beyond that corrected level.

**HH / LH / HL / LL.** New confirmed highs and lows are compared with the previous stored extreme of the same side. The first extreme is H or L. A strictly higher high is HH, otherwise LH; a strictly lower low is LL, otherwise HL. Thus exact equality goes into LH/HL in this implementation; EQH/EQL is a separate detector.

**Strong / Weak.** In bullish swing trend, the low is classified Strong and the high Weak; in bearish swing trend, the high is Strong and the low Weak. These labels are derived from trend state, not measured probabilities. The underlying high/low also tracks newer extremes. Their prices can update as price develops, including provisional updates.

**Output.** Solid structure lines, BOS/CHoCH, point labels anchored to their historical candles, current strong/weak levels, swing OBs, and range extremes used by Premium/Discount.

**Purpose.** Show the larger structure on the selected chart and contextual extremes. A point drawn at an old candle became known only after its confirmation delay; its historical anchor is not an entry that existed at that candle.

## 5. SMC Internal and Swing Order Blocks

These are separate pools, created by the corresponding confirmed structural breaks. They are **not Demand/Supply zones or pivot S/R zones**.

**Opposite Candle (ICT), the default.** Starting at the breaking candle and searching backward through the structural leg, the code identifies directional displacement. Its body must be positive in the break direction and at least `ob_disp_mult × ATR(14)` on that candle, default 1.0. It then selects the first opposite-color candle encountered before the displacement. For a bullish block this is a down-close candle; bearish is the reverse. The displacement need not be the breaking candle itself.

The selected candle must have full range **strictly below twice** the chosen volatility threshold, and subsequent closes up to the break must not have breached its invalidation boundary. If that selected opposite candle fails, the search does not substitute an older opposite candle. The zone uses the selected candle's full high-low range.

**Extreme Candle (LuxAlgo).** Uses the eligible lowest-low candle of the bullish leg or highest-high candle of the bearish leg, without requiring opposite candle color or the ICT displacement rule.

**Volatility filter.** ATR mode uses ATR(200). Cumulative Mean Range uses the average `high - low` since the run began. Historical candidate checks use the threshold stored for the candidate's bar.

**Lifecycle.** Bullish OBs are removed after a confirmed close below their bottom; bearish OBs after a confirmed close above their top. Wick touches alone do not remove them. Pools hold at most 100 blocks per structure type. The renderer chooses the nearest eligible blocks to current close, default 3 per type, after the distance filter. This is proximity selection, not a quality ranking.

**Purpose.** Identify an origin candle connected to a specific structural break, giving a potential future reaction location.

**Limits.** No dedicated Fresh/Tested flag or touch counter is maintained for OBs. A displayed block can have been revisited. “OB Breakout” events mean a block was invalidated; they do not mean an approved trade. OB creation is gated by the corresponding Internal/Swing Order Blocks input.

## 6. EQH / EQL and liquidity context

**Default:** enabled, confirmation length 5, threshold 0.1.

**How it works.** Confirms highs/lows with the `pivot()` helper, using the configured length on both sides. Each new confirmed high is compared with the previous confirmed high; lows are handled separately. The distance tolerance is `max(ATR(200) × eq_threshold, half a price tick)`.

**Output.** Dotted connections, EQH/EQL labels, and confirmed detection events. Present mode replaces the earlier displayed match on each side.

**Purpose.** Identify similarly priced extremes that can serve as potential liquidity references. It does not measure resting stop orders.

**Limits.** No consumed-liquidity state, no general wick-sweep/reclaim detector, and no sweep-plus-displacement trade rule. A sweep must be separately inferred or implemented. A new EQ match is a delayed pivot confirmation, not necessarily a new real-time touch of the old level.

## 7. Fair Value Gaps

**Default:** enabled; chart timeframe; automatic threshold on; Extend Until Filled on; 5 extension bars; at most 5 retained gaps per direction.

**Bullish rule.** Candle 3 low is above candle 1 high; candle 2 closes above candle 1 high; candle 2 has a positive open-close percentage move greater than the selected threshold. The gap is between candle 1 high and candle 3 low.

**Bearish rule.** Candle 3 high is below candle 1 low; candle 2 closes below candle 1 low; its negative percentage move exceeds the threshold in magnitude. The gap is between candle 3 high and candle 1 low.

**Automatic threshold.** Twice the running average of the absolute middle-candle percentage change at the processed source update points. Without Auto Threshold the comparison threshold is zero, so directional movement is still required.

**Timeframe.** Chart uses the three chart candles. A higher timeframe uses completed HTF candles with offsets, checked once when a new source period is encountered; creation waits for a confirmed chart bar. A requested lower timeframe falls back to chart candles and adds a warning label. It does not reconstruct lower-timeframe gaps inside a higher-timeframe candle.

**Lifecycle.** Bullish gaps are removed when chart low reaches or passes the bottom; bearish gaps when chart high reaches or passes the top. This is wick-based full traversal, not confirmed-close invalidation. These fills can be provisional on a live candle and are rolled back/re-evaluated. Partial fills do not shrink the original bounds. Oldest gaps beyond the per-side cap are discarded even if unfilled. Rendering splits each gap into two boxes at its midpoint. Active extension controls drawing length, not the fill rule.

**Purpose.** Mark a price imbalance for contextual inspection. There is no automatic “buy on FVG touch,” freshness score, or requirement that an FVG overlap an OB or a liquidity sweep.

## 8. Previous Daily / Weekly / Monthly highs and lows

**Default:** all three enabled, with separate line styles/colors.

**How it works.** Requests the previous completed period's high/low/open time from the same feed. Daily candles come from the feed's stored data; weekly/monthly reference candles are built from daily candles using feed alignment. Lines try to anchor to the last loaded chart candle in the previous period that printed the reference price. If that wick candle is outside loaded history, period-start anchoring is used.

**Output.** PDH/PDL, PWH/PWL, PMH/PML lines and labels. Missing completed periods create warnings rather than invented levels. Distance filtering can hide an otherwise available level.

**Purpose.** Provide previous-period reference prices for liquidity, obstacles, and contextual S/R. These are not current developing highs/lows, an HTF structural-bias engine, or automatically selected trade targets. Session alignment and provider history matter.

## 9. Premium / Equilibrium / Discount

Uses the current tracked swing high `H` and low `L`:

| Area | Exact interval |
|---|---|
| Premium | `0.95H + 0.05L` to `H`: top 5% of the range |
| Equilibrium | `0.475H + 0.525L` to `0.525H + 0.475L`: 47.5%–52.5% |
| Discount | `L` to `0.95L + 0.05H`: bottom 5% |

**Purpose.** Show location within this module's evolving range. The extreme zones are narrow bands, not the entire upper/lower halves. No trade is automatically rejected or approved by these bands. The reference range can develop as new extremes occur.

## 10. Independent Demand / Supply engine

**Default:** ATR threshold mode, 1.5 ATR minimum departure, 1 ATR maximum zone height, demand and supply display on.

**How a base forms.** Demand begins from a down-close base followed by movement upward; supply is the mirror. If the immediately preceding base has a body below 0.02% of its open, an older opposite candle can be used as the base. The code tracks the following run, its initial open, wicks, and closes. It needs actual movement in the expected direction and a sufficient departure measured from the run's first open to the current close.

**Thresholds.** ATR mode uses current ATR(14); Percent mode uses configured price percentages. The initial demand interval uses base open as its upper edge and the lower of the base/first-run lows as its lower edge. Supply uses base open as its lower edge and the higher relevant wick as its upper edge. Oversized zones are trimmed by the configured cap, with a special adjustment for oversized base bodies. Prices are rounded to the feed's tick precision.

**Registration.** A zone registers only on a confirmed bar once departure is large enough, current close is beyond the proximal edge in the departure direction, and tracked subsequent closes have not breached the far edge. One origin registers once per direction. Unlike SMC OBs, a BOS/CHoCH is **not required**.

**State and appearance:**

| State | Rule | Appearance |
|---|---|---|
| Fresh | Registered and no later processed bar has met its proximal hit rule | Filled, solid border |
| Tested | Demand low reaches/passes zone top; supply high reaches/passes zone bottom, without simultaneous invalidation | Transparent fill, dotted border |
| Invalidated | Confirmed demand close below bottom, or supply close above top | Transparent fill, gray dashed border |

The touch comparison is one-sided: it can count a candle gapping beyond the proximal edge, not just a wick landing inside the box. Freshness starts at registration, which can occur after the historical base candle. It does not certify that the origin region was never revisited before registration.

**Drawing and retention.** Origin boxes are short boxes around the base, not full forward rectangles spanning every bar. Forward price lines and labels are drawn for selected fresh proximal levels, default at most 3 per direction, within the configured display distance. Tested/invalidated zones can remain as origin history; they stop being fresh line candidates. Stored pools default to 100 per direction, removing older non-fresh zones first, then oldest fresh zones if necessary. Display flags hide their output but the D/S state calculation continues.

**Purpose.** Find displacement-from-base reaction locations and preserve an explicit Fresh/Tested/Invalidated lifecycle. This is the subsystem with the clearest built-in freshness presentation.

### Demand / Supply Near and Hit events

Demand/Supply Alerts toggles gate each direction's internal Hit/Near events; they do not deliver external notifications.

Hit uses the proximal edge and a fresh zone can emit it once as its state changes. Near uses candle low for demand and candle high for supply, applies only to fresh zones, and requires confirmation. It repeats after the per-zone elapsed cooldown, default 60 minutes, measured using bar close time. It is not limited to one “enter proximity” transition.

Percent mode uses the Near % input. ATR mode first computes `100 × ATR(14) / abs(close) × ds_near_atr`, then applies that percentage to the proximal price. Therefore its absolute distance is scaled by `proximal / abs(close)`; it is not exactly an ATR distance in price units. Similar conversion applies to fresh-line display distance.

Timezone Offset only changes the origin time printed in tooltips; it does not shift the candles, the zone, or the calculation.

## 11. Independent pivot Support / Resistance zones

**Actual project defaults:** Look Left 20, Look Right 10, 2 pivots per source side, ATR length 30, width multiplier 1.0, maximum zone percentage 5, **High/Low Body** source, alignment on, Extend Right off, labels on.

**Sources.** High/Low Body uses `max(open, close)` and `min(open, close)`. High/Low uses full wicks. HA computes Heikin-Ashi open/close and uses their body bounds. HA does not turn every module's candles into Heikin-Ashi; it changes this pivot source only.

**Confirmation.** A high candidate must be at least as high as the left observations and strictly higher than all right observations; lows mirror it. Right-side equal extrema disqualify the earlier candidate. A default pivot becomes known ten bars after its origin, then the box is anchored back at that origin.

**Width.** Full zone height is `min(ATR_at_origin × mult, origin_close × per / 100)`. Half is placed above the pivot price and half below. ATR is from the configured zone ATR length, not the SMC ATR(200).

**Alignment.** An overlapping new zone can be snapped to an older zone's exact bounds. The code still retains separate zone objects and identities; this is not a single merged registry or a validated strength score. Aligned duplicate prices are labeled only once.

**Role and lifecycle.** A pivot-high-origin pool and pivot-low-origin pool are retained separately. Either zone can subsequently have a support or resistance role. At delayed registration, current price can silently initialize the zone's role; this is not reported as a new price break. Later a confirmed close beyond resistance top flips it to support; below support bottom flips it to resistance. Wick penetration alone does not flip the role. At the break the old box ends and a new box begins, preserving historical role colors. Up to 50 retired boxes are retained.

**Purpose.** Provide independently detected reaction areas and precise same-zone break/reclaim context. They are not OBs, D/S zones, or FVGs. They do not maintain the D/S Fresh/Tested/Invalidated state.

### Pivot detectors: ten marker types

| Feature | Actual rule and purpose |
|---|---|
| Resistance break | A confirmed close flips at least one tracked resistance to support. Used when not classified as the broader breakout or false breakdown. Local upward role-change event. |
| Support break | A confirmed close flips at least one support to resistance. Used when not classified as broader breakdown or false breakup. Local downward role-change event. |
| Breakout | An actual upward role-change occurred, current close is the maximum of the recent Look Right closes, **all active zones now have support roles**, and the event is not a qualifying false breakdown. Broad tracked-zone role agreement. |
| Breakdown | Mirror: actual downward role-change, minimum recent close, all active zones now resistance, and not a qualifying false breakup. |
| False breakdown / bear trap | A prior confirmed support break is followed by a later confirmed close **above the same zone's stored top**, within Lookback For Breaks, plus a recent close-minimum condition. Full reclaim, not merely back inside the zone. |
| False breakup / bull trap | Prior resistance break followed by confirmed close **below the same stored bottom**, within the window, plus a recent close-maximum condition. |
| Move off support | Bullish candle trades into support-role zone(s), no resistance-role overlap conflict, and no upward role-change on current or previous bar. Not necessarily a candle that has already departed the zone. Default off. |
| Move off resistance | Bearish mirror, with no current/previous downward role-change. Default off. |
| Bullish TSI curl | TSI rises relative to its prior value, remains **below** its signal line, and the candle trades into unopposed support. Default off. |
| Bearish TSI curl | TSI falls, remains **above** its signal line, and candle trades into unopposed resistance. Default off. |

Breakout/Breakdown requires **role agreement**, not proof that this close is geometrically above/below every zone boundary. Previously flipped zones can already have the required role while their prices remain elsewhere. Wick-only liquidity sweeps are not these close-break/full-reclaim trap rules. Several distinct marker types may occur on the same bar; the suppression rules do not make all types mutually exclusive.

## 12. Candle patterns and their actual context

These patterns are connected to **pivot S/R**, not automatically to OBs, D/S, FVGs, or HTF structure.

Single-candle context requires trading into unopposed support/resistance and a significant local low/high. Significant extremes are ranked over the configured Reflect history, allowing the Offset rank tolerance; the code's comparison window includes the current candle plus up to Reflect earlier bars. Selected two-candle tests can use current/previous-bar touch and significant-extreme context; current opposing-zone conflict vetoes them. Only engulfing and tweezers use this two-bar context path; other two-candle formations still use current-bar context.

**Defaults:** all nine pattern families on; labels and pattern boxes on; Wait For Confirmed Bar on. If confirmation waiting is disabled, labels/boxes can be provisional and disappear. Internal events still require confirmed bars regardless of that setting.

The body reference is EMA(14) of absolute candle bodies. “Large previous body” means above its preceding body average. Maximum Candle Size uses the **pivot ATR**, default ATR(30), not BULB RSI or the ATR envelope.

| Family | Implemented test, beyond the required pivot context | Purpose |
|---|---|---|
| Doji | Body ≤ configured share of range (5%); upper/lower wicks within the configured ratio (2×) of each other | Balanced indecision |
| Bullish / bearish engulfing | Opposite-color bodies; current body covers previous body, is strictly larger, and exceeds its current body EMA; optional directional rejection-wick/close-beyond-prior-extreme tests | Substantial two-candle reversal pressure |
| Hammer / shooting star | Positive body entirely in top/bottom configured portion (33%); opposite wick ≤ 5% of body; range ≥ 0.1 zone ATR; optional candle-color matching | Directional wick rejection |
| Dragonfly / gravestone doji | Body ≤ fixed 5% of range; opposite wick ≤ body | Tiny body with one-sided rejection |
| Tweezer bottom / top | Matching low/high within 5% of current body EMA, opposite candle colors, large previous body, optional close past previous candle midpoint | Repeated rejection of similar extremes |
| Spinning top | Both wicks ≥ 34% of full range; excludes the fixed 5% doji test | Indecision in a zone |
| Piercing / dark cloud cover | Opposite colors; large previous body; open at/beyond previous close and current close beyond previous body midpoint but short of complete engulfing | Partial reversal into prior body; no gap past previous wick required |
| Bullish / bearish harami | Large previous body, smaller current opposite-color body, with **the current candle's full high-low inside the previous body** | Compression within previous directional body; stricter than body-only harami |
| Long lower / upper shadow | Relevant wick **strictly exceeds** 75% of range; range ≥ configured minimum zone ATR multiple | Strong one-sided wick rejection |

Where a candle qualifies for several enabled families, every match can produce an internal event but only one pattern label/box is selected. Priority is engulfing → piercing/cloud → tweezer → harami → hammer/star → dragonfly/gravestone → long shadow → doji → spinning top. The tooltip lists other matches. Neutral patterns describe indecision; a support-side or resistance-side placement is not proof of directional reversal.

**Important filter meanings.** Engulfing Must Engulf Wick checks bullish **close ≥ previous high** or bearish **close ≤ previous low**; it does not require the entire previous wick range to be enclosed. Max Reject Wick limits bullish upper wick or bearish lower wick as a percentage of body. Hammer Ratio constrains body location; Max Doji Wick Size is a ratio. Labels and boxes are display switches, whereas family toggles and candle/context filters change detection.

**Purpose.** Add candle-level evidence at significant pivot locations. The system does not turn every candle pattern into a trade or combine its pattern qualification with the dashboard/BULB.

## 13. TSI momentum-curl subsystem

TSI double-EMA-smooths signed close changes and absolute close changes, then divides them: `100 × smoothed change / smoothed absolute change`. Its signal line is an EMA of TSI.

Fast defaults: long 25, short 5, signal 14. Slow: long 25, short 13, signal 13. The speed selector chooses one set; the other set does not affect the active calculation.

The optional curl markers require pivot-zone context and a turn toward the signal line as described above. They do not require crossing that line or the zero line. TSI is **not one of the nine dashboard members**. It has no standalone plotted oscillator in this project's metadata, and it does not govern the strategy hook. Default curl detection is off.

**Purpose.** Optional early momentum-change context inside pivot zones.

## 14. Nine-indicator directional dashboard

All nine calculations use the current chart feed/timeframe, each with its own state. Display rows are simplified directional comparisons:

| Member | Project defaults | Buy / sell rule | Main purpose |
|---|---|---|---|
| MACD | EMA 12, EMA 26, signal EMA 9 | MACD above / below signal | Relative trend momentum |
| Stochastic RSI | RSI 14, stochastic 14, K SMA 3, D SMA 3; K vs 50 | K above / below 50, or above/below D in optional mode | Position of RSI within its recent range |
| Vortex | 14 | VI+ above / below VI− | Directional movement from cross-bar high/low travel normalized by true range |
| Momentum | 10 | Close minus close ten bars ago above / below zero | Net price change over the lookback |
| RSI | 14 | Above / below 50 | Relative gains versus losses |
| PSAR | Start .02, increment .02, max .2 | Close above / below calculated SAR | Direction relative to an accelerating trailing reference |
| DMI | DI 14, ADX smoothing 14, threshold 0 | DI+ above / below DI−, only while ADX ≥ threshold | Direction plus an optional strength gate |
| MFI | 14 | Above / below 50 | Volume-weighted typical-price pressure |
| Fisher Transform | 14 | Fisher rising / falling relative to previous value | Change in a normalized, smoothed price-position transform |

Equality or insufficient/non-finite comparisons generally produce wait. DMI explicitly waits below its ADX threshold. MFI uses `(H + L + C) / 3 × volume`, assigns positive/negative flow according to typical-price changes, and reports wait/no volume when usable volume is unavailable. Its usefulness depends on the supplied feed's volume; it is not exchange bid/ask delta or footprint data.

RSI OB/OS tags are 70/30; Stoch RSI and MFI use 80/20. The tags **do not reverse the directional vote**. RSI 75 remains buy + OB; RSI 25 remains sell + OS. A stretched bullish reading does not automatically become a sell recommendation.

**Aggregation.** All nine buy generates an All Bullish Indicators internal event; all nine sell generates the bearish event. This is the one explicit nine-way agreement rule, but it includes **only dashboard members**. It does not require any structure, zone, sweep, pattern, BULB, or target checks. Agreement can recur on every confirmed agreeing bar, rather than only at the transition into agreement.

**Display.** Dashboard/table switches and row On/Off settings change presentation; all nine still contribute to agreement events. Row order is configurable. Duplicate order numbers overwrite a cell in a fixed update order, so use distinct numbers 1–9. A later hidden row using the same number can also suppress a visible row. The table is not nine separately plotted oscillators.

**Purpose.** Secondary directional context. Nine votes are not nine independent proofs: all read the same price history and several describe closely related momentum.

## 15. BULB reversal/extreme labels

**Project defaults:** on; **Fixed (confirmed)** mode; separate RSI(13); overbought 65; oversold 30; alternating Buy/Sell on.

**Fixed Buy.** RSI was at/below oversold on the prior bar and is strictly above it on a confirmed current bar. **Fixed Sell.** RSI was at/above overbought and is strictly below it on the confirmed current bar. Labels are anchored at current low/high and do not relocate later.

With Alternate enabled, after a buy another buy is blocked until a sell, and vice versa. The initial state can admit either qualifying threshold exit. Turning off alternation allows repeated exits on the same side.

**Moving mode.** Tracks transitions between overbought and oversold states, creates Buy/Sell labels on the respective transitions once the opposite side has been tracked, and moves the current sell label to newer highs or buy label to newer lows. The first isolated extreme after initialization need not create a label. The state can keep following an extreme even after RSI leaves the threshold until the opposite state is reached. The historical label position is therefore revised as the swing develops.

**Purpose.** Highlight RSI exhaustion/reversal attempts. It is an independent RSI module, separate from dashboard RSI. Neither mode requires OB/D/S/FVG touch, premium/discount, a sweep, BOS/CHoCH, or dashboard agreement. A BULB “buy” can occur in bearish structure; it is not a unified long approval. Moving labels should not be treated as fixed-time entries.

## 16. SMA and ATR envelopes

**Defaults:** SMA 47; ATR 89; multiplier 3. Three plots: Simple Moving Average, High Envelope, Low Envelope.

The implemented formula is:

```text
middle = SMA(close, 47)
width  = middle × ATR(89) / current_close × 3
upper  = middle + width
lower  = middle − width
```

This is **not exactly `SMA ± 3 × ATR`**; it additionally scales ATR by SMA/current close. Each length/multiplier is configurable.

**Purpose.** Average-price and volatility/stretch context. No envelope-touch Buy/Sell, crossover trade, or envelope confluence gate is implemented. These lines are not OBs, D/S zones, or pivot levels. Plot color, visibility, width, and line style are handled by the frontend Style tab. The separate Inputs `bandWidth` value is currently unused.

## 17. Settings, styling, and drawing presentation

There are **210 individual input definitions**, not 210 independent market-analysis features. The settings panel groups some inputs into one inline row, so the small group badge counts displayed rows rather than every scalar input. Search looks through settings titles, help, examples, tooltips, and group names.

Inputs configure calculations and some drawing choices/colors. Style exposes controls for the three plots, ten marker types, and fourteen drawing groups. Plot styles and drawing/marker visibility are consumed by the frontend. For the current ten marker types, however, the runtime supplies per-point colors and the overlay gives those colors priority over the Style color: their Inputs colors govern those points. Visibility restricts the timeframes on which this instance is shown; it does not implement an HTF agreement rule. Edits apply immediately, Cancel restores the instance state from dialog opening, and Defaults restores the project's metadata defaults/styles.

The frontend collision layout spreads nearby labels/markers, draws connectors to their original anchors when moved, and supports tooltips. This changes screen placement, not the underlying zone prices or detected candle. Drawing limits are 500 each for boxes/lines/labels; additional subsystem caps also limit retained objects. Therefore the display is not an unlimited historical archive.

### Exposed controls that do not currently do what their wording suggests

| Setting or help | Current project behavior |
|---|---|
| Alert Frequency / `alertMode` | Defined in metadata but unused by the indicator runtime. Internal events always wait for confirmation; there is no external delivery. |
| False Break Color arrow height / `arrowMax` | Exposed but not used to size project markers. |
| ATR Bands Line Width / `bandWidth` | Exposed but unused; use each plot's Style width. |
| Marker color in Style | Exposed, but current runtime per-point colors take priority in `overlay.ts`; the corresponding Inputs colors control these marker points. |
| Structure length help says bars on each side | Actual structure uses the alternating leg detector and following-bars comparison; symmetric left/right is used by pivot S/R and EQ. |
| OB displacement help says breakout candle must qualify | A directional displacement in the structural leg can qualify; the breaking candle is not the only candidate. |
| Align Zones help implies merging/strengthening | New geometry snaps to older bounds, while separate identities remain. No computed strength improvement is established. |
| Breakout help says above ALL tracked pivots | Actual rule checks an upward break plus all zones having support roles and a recent close maximum, not close above every top. |
| False-break help says back inside | Code needs full same-zone reclaim beyond the opposite edge. |
| Harami help says body inside previous body | Code requires full current high-low inside the previous body. |
| Engulf Wick help says all wicks covered | Directional close beyond prior high/low only. |
| ATR multiplier help says distance in ATRs | Actual width includes the additional SMA/close factor. |

Some help and metadata comments describe historical tuning experiments. They establish why certain defaults were chosen, but this review did not reproduce those studies. Their numerical claims are not used here as a current win rate, accuracy percentage, or proof of future performance.

## 18. How the components actually connect

| Producer | Consumer | Implemented connection |
|---|---|---|
| Current candles | Every subsystem | Shared feed, timeframe, chronological execution, ATR/helper calculations |
| Internal / Swing Structure | Corresponding OB pool | Confirmed structural break invokes OB selection |
| Internal Structure | Candle coloring | Internal trend determines direction |
| Swing extremes/trend | Strong/Weak, Premium/Discount | Supplies range and labels |
| Pivot S/R roles/touches | Break/trap/reaction markers | Supplies exact zone context and same-zone break state |
| Pivot S/R context and significant extremes | Candle patterns | Qualifies pattern location/direction |
| Pivot S/R context | TSI curl markers | Restricts momentum turns to appropriate zones |
| Nine dashboard comparisons | All-nine internal events | Unanimous directional comparison only |
| All subsystem state | `strategy()` | State is available after calculations, but no entry rules consume it |

There is **no implemented common gate** requiring HTF bias + fresh location + liquidity sweep + structural reaction + candle confirmation + acceptable risk/targets. There is no shared zone-quality ranking, preferred entry zone, conflict arbiter, signal expiry/consumption state, structure-derived stop/target selector, or final BUY/SELL/WAIT explanation. Display distance filtering is not such a gate.

Separate systems can disagree for sensible reasons: dashboard buy describes upward momentum, while BULB sell describes an exit from an overbought extreme. Internal bearish CHoCH can occur inside a still-bullish swing pullback. A bullish pivot reaction can occur below a bearish OB. **Current code does not decide which interpretation wins.**

### What a shared trade goal would require — proposal, not current functionality

A future decision layer could consume the existing modules while preserving their separate identities:

1. Establish higher-timeframe structural bias from explicitly selected timeframes.
2. Choose an eligible location and retain its kind: OB, D/S, pivot S/R, or FVG. Track source timeframe, registration time, freshness, and invalidation as appropriate.
3. Determine liquidity interaction explicitly; EQ labels alone do not establish a sweep.
4. Require a confirmed reaction/structure change and displacement consistent with the intended setup.
5. Use range and previous-period location as context; use pivot-qualified patterns only where their actual rules apply.
6. Resolve conflicting evidence; treat dashboard, BULB, TSI, and envelopes as secondary rather than forcing every module to agree.
7. Select a premise-invalidating stop and credible targets, check costs and reward/risk, and give a rejection reason when the setup is unsuitable.
8. Emit a confirmed `ctx.signal()` intent and retain its setup identity and lifecycle.

This would implement the missing common decision. It would not establish profitability by itself; the actual rules would still need event-timing, replay, and execution validation. No such decision rules were added during this documentation task.

## 19. Platform functions surrounding the indicator

The project supports stored CSV candles, optional OANDA history/live updates, timeframe switching, indicator settings, chart drawing output, a price/time ruler in the top toolbar, and bar replay. Replay calculates only through its cursor and recomputes when stepping backward. Switching timeframe preserves its absolute replay cutoff and shows only candles completed by that time. The ruler reports price change, percentage, ticks, bar intervals, and elapsed time; it is a chart tool, not an indicator signal. CSV data is historical; live behavior needs a configured live provider.

The backtesting engine already accepts strategy intents, simulates orders becoming active on later bars, applies execution properties, and returns trades/metrics. `Context.signal()` can describe market/limit/stop entries, a required stop-loss, optional partial targets, expiry and breakeven behavior. Those capabilities are **execution infrastructure**, not existing SMC Master entry rules. The default SMC Master therefore yields zero trades. The test that adds entries uses a temporary test subclass, not the registered production indicator.

The inspected indicator does not implement Session Volume Profile/POC/VAH/VAL, VWAP, exchange footprint/bid-ask delta, economic-calendar filtering, a general ICT sweep detector, automatic broker trade submission, or a production alert-notification service. MFI's volume input does not supply any of those features.

## 20. Verification for this guide

### Added platform data layer — 14 October 2026

The official Exness XAUUSDm archive now supplies verified Bid/Ask M1/M5 history,
historical spreads, gap sidecars and dataset hashes. A separate guarded
`/api/exness/history/confirmed-snapshot` interface checks UTC candle-close timing,
observed M1 coverage, source eligibility and agreement with chart feeds. At 10:25
it cannot return the developing 10:00–11:00 hour as confirmed. The
`/api/exness/history/intrabar` interface exposes bounded historical M1 records and
spread values without inventing missing minutes.

This is an additive market-data layer for future ML/backtest callers; existing
indicator/strategy rules were not changed or automatically rewired. Live
account-feed and execution verification remain **partial**, because the archive
does not establish account-specific quotes, latency or fills. Read
[Market Data Integrity & Timeframe Synchronization](MARKET_DATA_INTEGRITY.md) for
the API examples, strict gap policy, regression checks and limitations.

The indicator checks below describe the earlier guide verification; they are
separate from this new data-layer verification.

Read: Python indicator, helper calculations, metadata/resolved inputs, market/timeframe provider, engine/runner and API output, existing tests, and frontend settings/rendering paths. Documentation only was added.

Existing tests executed successfully:

```text
backend/.venv/bin/python -m pytest
  tests/test_smc_master.py tests/test_pivot_box_lifecycle.py
  tests/test_engine_runner.py tests/test_ta_mtf.py -q
```

Runtime check: the latest 3,000 **stored completed Dukascopy XAUUSD 5-minute candles**, with default project inputs and a read-only database connection. These are historical candles, not a new live-market feed. The run returned three plot series, a direction table, zone/structure/pattern/BULB drawings, and no data warnings. Marker counts were 23 breakout, 19 breakdown, 35 resistance break, 36 support break, 7 false breakdown, and 6 false breakup. Default-disabled support/resistance reactions and TSI curls produced zero markers. The rolling internal event list held 984 events at the end; that is **not the total number of events over the run**, because it is truncated during processing. Trade intents: **0**. Rejected intents: **0**.

These checks support the feature inventory, event/order distinction, and selected lifecycle/runtime contracts. They do not prove every possible setting combination, every historical drawing, cross-platform parity, predictive accuracy, or profitability.

## Appendix: all 210 resolved project inputs

The following catalog is generated from the project's live `INPUTS` definitions, including project default overrides. Descriptions are corrected against the Python/frontend implementation where necessary. Color values use `#RRGGBBAA`, with the last byte indicating opacity. The identifier is included to locate the exact setting even when its title is blank or shared with another input.


### Smart Money Concepts — 4 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Mode (`mode`) | Historical | Historical keeps selected historical structure/swing/EQ drawings within drawing limits; Present replaces older drawings in those display paths. It does not clear every subsystem. |
| Style (`style`) | Colored | Colored/Monochrome presentation for selected SMC structure, OB and candle paths; other modules retain their own palettes. |
| Color Candles (`show_trend`) | true | Draw candles using internal trend direction, with the project swing color inputs. With-trend candles are filled; pullbacks are hollow. Initial non-bullish state uses the bearish branch. |
| Hide Zones Farther Than (× Recent Range) (`aoi_max_dist`) | 0.2 | Display padding around the latest 120 processed chart bars: multiplier × their high-low range. Hides selected distant drawings without invalidating logical zones; zero disables the filter. |

### Real Time Internal Structure — 7 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Show Internal Structure (`show_internals`) | true | Show/hide internal BOS/CHoCH drawings. Calculation, trend updates and events continue. |
| Bullish Structure (`show_ibull`) | All | Choose All/BOS/CHoCH drawings for bullish internal structure. Does not filter its underlying confirmed events. |
| swing_ibull_css (`swing_ibull_css`) | #00C8FFFF | Companion color/opacity control for this group’s drawings. |
| Bearish Structure (`show_ibear`) | All | Choose All/BOS/CHoCH drawings for bearish internal structure. Does not filter its underlying confirmed events. |
| swing_ibear_css (`swing_ibear_css`) | #FFC800FF | Companion color/opacity control for this group’s drawings. |
| Internal Length (`ilength`) | 5 | Following-bars comparison length for the alternating internal leg detector; capped below Swing Length. Not a symmetric left/right pivot test. |
| Confluence Filter (`ifilter_confluence`) | true | Ignores weak internal breaks. A bullish break only counts when the breakout candle closes in the upper half of its range (bearish: lower half), so wick fakeouts are skipped. |

### Real Time Swing Structure — 8 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Show Swing Structure (`show_Structure`) | true | Show/hide swing BOS/CHoCH drawings. Calculation, trend updates and events continue. |
| Bullish Structure (`show_bull`) | All | Choose All/BOS/CHoCH drawings for bullish swing structure. Does not filter its underlying confirmed events. |
| swing_bull_css (`swing_bull_css`) | #00C8FFFF | Companion color/opacity control for this group’s drawings. |
| Bearish Structure (`show_bear`) | All | Choose All/BOS/CHoCH drawings for bearish swing structure. Does not filter its underlying confirmed events. |
| swing_bear_css (`swing_bear_css`) | #FFC800FF | Companion color/opacity control for this group’s drawings. |
| Show Swings Points (`show_swings`) | true | Show historical H/L and HH/LH/HL/LL swing labels after confirmation. Does not change the swing detector. |
| length (`length`) | 25 | Following-bars comparison length for the larger alternating swing detector; this is on the current chart timeframe. |
| Show Strong/Weak High/Low (`show_hl_swings`) | true | Display current tracked strong/weak high/low classifications derived from swing trend; not measured likelihoods. |

### Order Blocks — 11 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Internal Order Blocks (`show_iob`) | true | Enable internal OB creation on internal structure breaks. Render nearest stored eligible blocks using the internal count. |
| iob_showlast (`iob_showlast`) | 3 | Maximum number of nearest visible internal OBs drawn; does not rank quality or track touch freshness. |
| Swing Order Blocks (`show_ob`) | true | Enable swing OB creation on swing structure breaks. Render nearest stored eligible blocks using the swing count. |
| ob_showlast (`ob_showlast`) | 3 | Maximum number of nearest visible swing OBs drawn; does not rank quality or track touch freshness. |
| Order Block Filter (`ob_filter`) | Atr | Candidate range must be strictly below 2× its historical ATR(200), or 2× cumulative mean high-low range, according to the chosen mode. |
| Order Block Candle (`ob_candle`) | Opposite Candle (ICT) | ICT selects the first opposite candle before qualifying directional displacement in the breaking structural leg, subject to size/pre-break-close filters. Extreme selects an eligible leg extreme regardless of candle color. |
| Displacement Body (× ATR) (`ob_disp_mult`) | 1.0 | ICT displacement body threshold as a multiple of that candle’s ATR(14). Displacement can be elsewhere in the structural leg, not only on the break candle. |
| Internal Bullish OB (`ibull_ob_css`) | #4A90FF40 | Fill color of internal bullish order blocks. Keep it transparent so candles stay visible. |
| Internal Bearish OB (`ibear_ob_css`) | #FF7A8540 | Fill color of internal bearish order blocks. |
| Bullish OB (`bull_ob_css`) | #2F6BFF59 | Fill color of swing bullish order blocks. |
| Bearish OB (`bear_ob_css`) | #FF3B4B4D | Fill color of swing bearish order blocks. |

### EQH/EQL — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Equal High/Low (`show_eq`) | true | Enable confirmed EQH/EQL matching and drawings. No automatic sweep or consumed-liquidity state. |
| Bars Confirmation (`eq_len`) | 5 | Left and right bars used by the EQ pivot confirmation helper. |
| Threshold (`eq_threshold`) | 0.1 | Allowed difference between successive confirmed same-side pivots: max(ATR(200) × threshold, half tick). |

### Fair Value Gaps — 8 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Fair Value Gaps (`show_fvg`) | true | Enable three-candle imbalance detection, lifecycle and drawings. No automatic trade on gap touch. |
| Auto Threshold (`fvg_auto`) | true | Require directional middle-candle percentage movement above twice the running average absolute movement at processed source update points. |
| Timeframe (`fvg_tf`) | Chart (empty) | Timeframe used to find fair value gaps. Chart = the current timeframe. A higher timeframe shows its gaps on your chart; a lower one falls back to the chart timeframe. |
| Bullish FVG (`bull_fvg_css`) | #00FF684D | Fill color of bullish fair value gaps. |
| Bearish FVG (`bear_fvg_css`) | #FF525259 | Fill color of bearish fair value gaps. |
| Extend FVG (`fvg_extend`) | 5 | Drawing extension in chart bars. With active extension off, measured from gap registration time; with it on, measured from current bar. |
| Extend Until Filled (`fvg_extend_active`) | true | Extend retained gaps to current bar plus extension. Fill removal still uses full chart-wick traversal; partial fills do not shrink bounds. |
| Max Active FVGs (per side) (`fvg_max_active`) | 5 | Retained unfilled gaps per direction; oldest excess gaps are discarded. |

### Highs & Lows MTF — 9 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Daily (`show_pdhl`) | true | Draws the previous day's high and low (PDH / PDL) with the line style and color you pick. |
| pdhl_style (`pdhl_style`) | ⎯⎯⎯ | Line style for the corresponding previous-period reference. |
| pdhl_css (`pdhl_css`) | #5B9CFFFF | Companion color/opacity control for this group’s drawings. |
| Weekly (`show_pwhl`) | true | Draws the previous week's high and low (PWH / PWL). |
| pwhl_style (`pwhl_style`) | ---- | Line style for the corresponding previous-period reference. |
| pwhl_css (`pwhl_css`) | #5B9CFFFF | Companion color/opacity control for this group’s drawings. |
| Monthly (`show_pmhl`) | true | Draws the previous month's high and low (PMH / PML). Needs at least one completed month of data in the feed. |
| pmhl_style (`pmhl_style`) | ···· | Line style for the corresponding previous-period reference. |
| pmhl_css (`pmhl_css`) | #5B9CFFFF | Companion color/opacity control for this group’s drawings. |

### Premium & Discount Zones — 4 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Premium/Discount Zones (`show_sd`) | true | Show narrow 5% Premium/Discount extremes and the 47.5–52.5% equilibrium band of the current tracked swing range. No entry gate. |
| Premium Zone (`premium_css`) | #FFC800FF | Color of the top 5% swing-range band. |
| Equilibrium Zone (`eq_css`) | #B2B5BEFF | Color of the 47.5–52.5% middle swing-range band. |
| Discount Zone (`discount_css`) | #00C8FFFF | Color of the bottom 5% swing-range band. |

### Demand & Supply — 16 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Threshold Mode (`ds_mode`) | ATR | How demand/supply distances are measured. ATR: multiples of ATR(14), adapts to volatility and works on every timeframe. Percent: fixed percentages of price (the original settings). |
| Min departure (× ATR) (`ds_min_atr`) | 1.5 | ATR mode: minimum directional departure from the run’s initial open to current close, in current ATR(14). Project default 1.5; no win-rate claim is made here. |
| Max zone height (× ATR) (`ds_max_atr`) | 1.0 | ATR mode: zone-height cap based on current ATR(14); oversized geometry is trimmed. |
| Show lines within (× ATR) (`ds_line_atr`) | 10.0 | ATR mode fresh-line distance: converted to a percentage through current close, then compared with proximal-line distance. Not an exact absolute ATR offset. |
| Min % change (Percent mode) (`ds_min_pc_change`) | 0.05 | Percent mode. Minimum % move away from the base candle to create a zone. |
| Max % zone height (Percent mode) (`ds_max_pc_zone`) | 0.05 | Percent mode. Maximum zone height as a % of price; taller zones are trimmed toward the base candle. |
| % line (Percent mode) (`ds_line_pc_display`) | 3.0 | Percent mode. Lines are drawn for active zones when the close is within this % of them. Max line = how many lines per zone type. |
| Max line (`ds_line_max`) | 3 | Maximum fresh proximal price lines per direction, subject to display-distance filtering. |
| Max number of zones (`ds_max_zones`) | 100 | Stored D/S pool capacity per direction; remove older non-fresh zones first, otherwise oldest fresh zones. |
| Timezone offset (H) (`ds_hours_offset`) | 0.0 | Offset applied only to origin dates in D/S tooltips. Does not shift candles or calculations. |
| Demand (`showDemandZone`) | true | Show demand origin boxes/fresh lines. State calculation continues. Fresh: filled solid; Tested: transparent dotted; Invalidated by confirmed close below bottom: gray dashed. |
| Border (`demand_bord_col`) | #0AFF02B2 | Companion color/opacity control for this group’s drawings. |
| Background (`demand_bg_col`) | #0AFF0226 | Companion color/opacity control for this group’s drawings. |
| Supply (`showSupplyZone`) | true | Show supply origin boxes/fresh lines. State calculation continues. Fresh: filled solid; Tested: transparent dotted; Invalidated by confirmed close above top: gray dashed. |
| Border (`supply_bord_col`) | #FF4D4DCC | Companion color/opacity control for this group’s drawings. |
| Background (`supply_bg_col`) | #FF5C5C33 | Companion color/opacity control for this group’s drawings. |

### Demand & Supply Alerts — 5 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Demand (`dsAlertDemand`) | true | Enable internal Demand Hit/Near events. No external notification delivery. |
| Supply (`dsAlertSupply`) | true | Enable internal Supply Hit/Near events. No external notification delivery. |
| Mins no repeat (`ds_alert_mins_no_repeat`) | 60 | Elapsed per-zone cooldown for repeated confirmed Near events. Hit occurs on first fresh-state transition. Zero removes Near cooldown. |
| Near % (`ds_alerts_pc_near`) | 1.5 | Percent mode: one-sided low/high proximity comparison against the fresh proximal price; Near events remain confirmed-only. |
| Near (× ATR) (`ds_near_atr`) | 1.0 | ATR mode Near threshold, first converted into a percentage through current close and applied to zone proximal price. Not exactly this many ATR in absolute price. |

### Zones — 12 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Look Left (`left`) | 20 | Bars to the left that a pivot must beat to become a support/resistance zone. Higher = fewer, more important pivots. |
| Look Right (`right`) | 10 | Bars to the right that must confirm the pivot. Higher = more reliable but appears later. |
| Number of Pivots (`nPiv`) | 2 | Retained pivot-high-origin zones and pivot-low-origin zones per pool. Roles may later flip independently of origin. |
| ATR Length (`atrLen`) | 30 | ATR length for pivot-zone width and candle-pattern size filters. Separate from SMC ATR(200), D/S ATR(14), and envelope ATR. |
| Zone Width (ATR) (`mult`) | 1.0 | Full pivot-zone height multiplier on ATR at the pivot origin, subject to the percentage cap. Project default 1.0; no performance percentage is asserted. |
| Max Zone Percent (`per`) | 5 | Full pivot-zone height is capped at this percent of pivot-origin close before splitting equally above/below pivot price. |
| Max Boxes for Patterns (`max`) | 10 | Maximum selected pattern boxes retained; zero suppresses their creation. Pattern labels/events have separate behavior. |
| Offset For Labels (`fut`) | 30 | How many bars to the right of the last candle the price-level labels are placed. |
| Source For Pivots (`src`) | High/Low Body | Source only for pivot S/R: regular candle bodies (default), full high-low, or computed HA bodies. Does not change the other modules’ candle source. |
| Align Zones (`alignZones`) | true | Snap an overlapping new pivot zone to older bounds. Separate identities remain; no strength score or true object merge. |
| Extend Right (`extend`) | false | Extend current pivot boxes to the chart’s right side; regardless of this flag, current box right edges update to processed time. |
| Show Level Labels (`lLab`) | true | Shows price labels on the key-level lines extended off pivot zones. |

### Detection — 11 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Detect Pivot Highs (`dhighs`) | true | Enable pivot-high-origin zone creation; its role can initialize/flip to support later. |
| Detect Pivot Lows (`dlows`) | true | Enable pivot-low-origin zone creation; its role can initialize/flip to resistance later. |
| Detect Breakout (`detectBO`) | true | Enable confirmed upward-break marker with recent Look Right close maximum and all active zones now support-role. Does not require current close above every zone top. |
| Detect Breakdown (`detectBD`) | true | Enable confirmed downward-break marker with recent Look Right close minimum and all active zones now resistance-role. Does not require current close below every zone bottom. |
| Detect Resistance Break (`breakUp`) | true | Enable local resistance-role flip marker, subject to broader-breakout/false-breakdown suppression. |
| Detect Support Break (`breakDn`) | true | Enable local support-role flip marker, subject to broader-breakdown/false-breakup suppression. |
| Detect False Breakdown (`falseBull`) | true | Enable same-zone confirmed support break followed within the lookback by full close reclaim above stored top, with recent close-minimum filter. |
| Detect False Breakup (`falseBear`) | true | Enable same-zone confirmed resistance break followed within the lookback by full close reclaim below stored bottom, with recent close-maximum filter. |
| Detect Moves Off Support (`supPush`) | false | Enable bullish support-touch triangles without opposing role overlap and without current/previous upward role change. Project default off. |
| Detect Moves Off Resistance (`resPush`) | false | Enable bearish resistance-touch triangles without opposing role overlap and without current/previous downward role change. Project default off. |
| Detect TSI Curl (`curl`) | false | Enable zone-qualified TSI turns toward its signal line, without requiring a crossover. Project default off. |

### Candle Patterns — 12 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Wait For Confirmed Bar (`repaint`) | true | Despite internal identifier, true means wait for a confirmed candle for patterns. False allows provisional pattern drawings; events still require confirmation. |
| Show Label (`labels`) | true | Show one highest-priority pattern label per matching candle; tooltip lists other enabled matches. |
| Show Boxes Around Patterns (`sBox`) | true | Show a box for the selected highest-priority pattern on a qualifying candle, subject to Max Boxes. |
| Detect Doji (`d_`) | true | Enable balanced-wick Doji family near significant pivot S/R. Body threshold and wick balance are configurable. |
| Detect Engulfing (`be_`) | true | Enable engulfing near pivot S/R; opposite-color body must cover and exceed prior body and exceed current body EMA(14), plus optional filters. |
| Detect Hammers and Stars (`hs_`) | true | Enable hammer/shooting-star family with body location, short opposite wick and minimum range filters near pivot S/R. |
| Detect Dragons and Graves (`dg_`) | true | Enable dragonfly/gravestone family using a fixed 5% doji-body test and opposite wick no larger than body, near pivot S/R. |
| Detect Tweezers (`tw_`) | true | Enable opposite-color tweezer bottom/top near pivot S/R; extreme tolerance 5% of current body EMA and prior body must be large. |
| Detect Spinning Top (`st_`) | true | Enable spinning tops: both wicks at least 34% of full range, excluding fixed 5% doji, near pivot S/R. |
| Detect Piercing and Clouds (`pc_`) | true | Enable piercing/dark-cloud family near pivot S/R: open at/beyond previous close, resolve past prior body midpoint without full engulfing. |
| Detect Harami (`bh_`) | true | Enable harami near pivot S/R: small opposite candle with full high-low inside prior large body, not only its body. |
| Detect Long Shadows (`ls_`) | true | Enable long-shadow family near pivot S/R: relevant wick strictly exceeds configured range share, plus minimum range filter. |

### Alert Frequency — 1 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Alerts Mode (`alertMode`) | once_per_bar_close | Exposed, but unused by project runtime. Changing it does not change delivery or event timing; internal events wait for confirmed bars. |

### Candle Filters — 11 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Engulfing Must Engulf Wick (`ecWick`) | false | Engulfing extra check: bullish close reaches/exceeds previous high; bearish close reaches/breaches previous low. Not full enclosure of both prior wicks. |
| H&S Must Match Color (`colorMatch`) | false | Hammers must be green candles and shooting stars must be red. Stricter, fewer signals. |
| Tweezer Close Over Half (`closeHalf`) | false | Tweezer patterns must close beyond the middle of the previous candle. |
| Max Candle Size (× ATR) (`atrMax`) | 0.0 | Ignores pattern candles bigger than this many ATRs (for example news spikes). 0 turns it off. |
| [EC] Max Reject Wick Size (`rejectWickMax`) | 0.0 | Engulfing: bullish upper wick or bearish lower wick must be strictly below this percent of current body. Zero disables filter. |
| [HS] H&S Ratio (%) (`hammerFib`) | 33 | Body-location constraint: hammer body must lie in upper selected range share; shooting star in lower share. Not solely a body-length cutoff. |
| [HS] H&S Opposing Shadow (%) (`hsShadowPerc`) | 5 | Hammers and stars: the opposite wick may be at most this % of the body. |
| [HS] H&S Min Size (× ATR) (`hammerSize`) | 0.1 | Minimum full candle range in pivot ATR units for hammer/star and long-shadow families. |
| [DJ] Doji Size (%) (`dojiSize`) | 5 | Doji: the body may be at most this % of the whole candle. |
| [DJ] Max Doji Wick Size (`dojiWickSize`) | 2 | Doji: one wick may be at most this many times the other, so the doji stays balanced. |
| [LS] Long Shadow (%) (`luRatio`) | 75 | Long lower/upper shadow must strictly exceed this percentage of full candle range. |

### Lookback — 4 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Lookback For Breaks (`lookback`) | 2 | Maximum bar-index separation for same-zone confirmed break and full opposite-edge reclaim; also supplies recent close-extreme trap context. |
| Swing High/Low (`swing`) | 5 | Close-lookback used for minimum/maximum-close context of false-break patterns; not the SMC Swing Length input. |
| Significant High/Low (`reflect`) | 10 | Prior-bar window for ranking significant high/low pattern context, with current bar also included in comparison. |
| Consider Bar From High/Low (`offset`) | 1 | Allowed rank tolerance in that extreme comparison, capped by Reflect. Not a required number of bars since the extreme. |

### Pivot Color — 4 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Bullish levels (`bullBorder`) | #7EC4FDFF | Border and background colors of bullish key levels (support zones). |
| bullBgCol (`bullBgCol`) | #64B5F61A | Companion color/opacity control for this group’s drawings. |
| Bearish levels (`bearBorder`) | #F7EA7EFF | Border and background colors of bearish key levels (resistance zones). |
| bearBgCol (`bearBgCol`) | #FFEB3B1A | Companion color/opacity control for this group’s drawings. |

### Breakout Color — 2 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Breakout / breakdown (`upCol`) | #FF8A3DE6 | Colors of the breakout (up) and breakdown (down) arrows. |
| dnCol (`dnCol`) | #FF5CFFE6 | Companion color/opacity control for this group’s drawings. |

### S&R Break Color — 2 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Resistance / support break (`supCol`) | #17FF00E6 | Colors of the resistance-break (up) and support-break (down) arrows. |
| resCol (`resCol`) | #FF4D4DE6 | Companion color/opacity control for this group’s drawings. |

### False Break Color — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Bear trap / bull trap (`fBull`) | #17FF00E6 | Input color for bullish false-breakdown points; companion fBear colors bearish false-breakup. Arrow height input is unused. |
| fBear (`fBear`) | #FF4D4DE6 | Companion color/opacity control for this group’s drawings. |
| arrowMax (`arrowMax`) | 75 | Exposed legacy false-break arrow-height value, currently unused by marker rendering. |

### Moves From S&R Color — 2 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Off support / resistance (`moveBullCol`) | #A78BFFFF | Colors of the triangles for candles reacting off support (bullish) and resistance (bearish). |
| moveBearCol (`moveBearCol`) | #F8EB79FC | Companion color/opacity control for this group’s drawings. |

### Momentum Curl Color — 2 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Bullish / bearish curl (`curlBullCol`) | #17FF00CC | Colors of the bullish and bearish TSI momentum-curl markers. |
| curlBearCol (`curlBearCol`) | #F3FF00CC | Companion color/opacity control for this group’s drawings. |

### Pattern Box Color — 6 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Backgrounds (`patBullBg`) | #17FF0026 | Background colors of candle-pattern boxes: bullish, neutral, bearish. |
| patNeutBg (`patNeutBg`) | #B2B5BE26 | Companion color/opacity control for this group’s drawings. |
| patBearBg (`patBearBg`) | #FF4D4D26 | Companion color/opacity control for this group’s drawings. |
| Borders (`patBullBo`) | #17FF0080 | Border colors of candle-pattern boxes: bullish, neutral, bearish. |
| patNeutBo (`patNeutBo`) | #B2B5BE80 | Companion color/opacity control for this group’s drawings. |
| patBearBo (`patBearBo`) | #FF6B6BA6 | Companion color/opacity control for this group’s drawings. |

### Label Color (Text/Bg) — 6 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Text (`textBullCol`) | #17FF00FF | Text colors of pattern labels: bullish, neutral, bearish. |
| textNeutCol (`textNeutCol`) | #B2B5BEFF | Companion color/opacity control for this group’s drawings. |
| textBearCol (`textBearCol`) | #FF5C5CFF | Companion color/opacity control for this group’s drawings. |
| Label background (`labBullCol`) | #17FF0040 | Background colors of pattern labels: bullish, neutral, bearish. |
| labNeutCol (`labNeutCol`) | #B2B5BE40 | Companion color/opacity control for this group’s drawings. |
| labBearCol (`labBearCol`) | #FF4D4D40 | Companion color/opacity control for this group’s drawings. |

### TSI Speed Control — 1 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Select a Speed (`strat`) | Fast | TSI speed preset for the momentum-curl detection. Fast reacts sooner (more signals); Slow is smoother (fewer, later signals). |

### TSI Fast Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Long Length (`longf`) | 25 | Fast preset: long smoothing length of the TSI. |
| Short Length (`shortf`) | 5 | Fast preset: short smoothing length of the TSI. |
| Signal Length (`signalf`) | 14 | Fast preset: signal-line length. A curl is the TSI turning relative to this line. |

### TSI Slow Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Long Length (`longs`) | 25 | Slow preset: long smoothing length of the TSI. |
| Short Length (`shorts`) | 13 | Slow preset: short smoothing length of the TSI. |
| Signal Length (`signals`) | 13 | Slow preset: signal-line length. |

### Info الاتجاه — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Directions Dashboard (`directions`) | true | Show the direction table when its separate table switch is also on. Hiding it does not stop nine-way agreement calculation/events. |
| Direction table (`infoDataTableOn`) | true | Additional display gate for the whole table; does not disable calculation or all-nine events. |
| Position (`itablePosition`) | bottom_right | Where the direction dashboard sits on the chart. |

### MACD Settings — 5 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn MACD Label On/Off (`macdOn`) | true | Show/hide the MACD dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`macdOrder`) | 1 | Dashboard row position for MACD. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| MACD Fast Length (`macdFast`) | 12 | MACD fast EMA length. |
| MACD Slow Length (`macdSlow`) | 26 | MACD slow EMA length. |
| MACD Signal Length (`macdSignal`) | 9 | MACD signal-line EMA length. |

### Stochastic RSI Settings — 7 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn Stochstic RSI Label On/Off (`stochasticOn`) | true | Show/hide the Stochastic RSI dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`stochasticOrder`) | 2 | Dashboard row position for Stochastic RSI. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| K Value - Stochastic RSI (`smoothK`) | 3 | Smoothing of the Stochastic RSI K line. |
| D Value - Stochastic RSI (`smoothD`) | 3 | Smoothing of the D line (the average of K). Used only by the 'K vs D' direction rule. |
| Direction Rule (`stochDirectionMode`) | K vs 50 | How the Stoch RSI direction is decided. K vs 50: K above 50 = buy. K vs D: K above D = buy (reacts faster). |
| Stochastic RSI Length (`lengthRSI`) | 14 | RSI length used inside the Stochastic RSI. |
| Stochastic Length (`lengthStoch`) | 14 | Stochastic length applied to that RSI. |

### Vortex Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn Vortex Label On/Off (`vortexOn`) | true | Show/hide the Vortex dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`vortexOrder`) | 3 | Dashboard row position for Vortex. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| Vortex Length (`vortexLength`) | 14 | Vortex indicator length. |

### Momentum Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn Momentum Label On/Off (`momOn`) | true | Show/hide the Momentum dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`momOrder`) | 4 | Dashboard row position for Momentum. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| Momentum Length (`momLength`) | 10 | How many bars back Momentum compares the close with. |

### RSI Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn RSI Label On/Off (`rsiOn`) | true | Show/hide the RSI dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`rsiOrder`) | 5 | Dashboard row position for RSI. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| Relative Strength Index Length (`rsiLength`) | 14 | RSI length. |

### PSAR Settings — 5 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn PSAR Label On/Off (`psarOn`) | true | Show/hide the PSAR dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`psarOrder`) | 6 | Dashboard row position for PSAR. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| PSAR Start (`psarStart`) | 0.02 | PSAR starting acceleration factor. |
| PSAR Increment (`psarInc`) | 0.02 | How much the acceleration grows each time a new extreme is made. |
| PSAR Max (`psarMax`) | 0.2 | Maximum acceleration factor. Higher = the SAR catches up with price faster. |

### DMI Settings — 5 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn DMI Label On/Off (`dmiOn`) | true | Show/hide the DMI dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`dmiOrder`) | 7 | Dashboard row position for DMI. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| DMI Length (`dmiLength`) | 14 | DI length. |
| DMI Smoothing (`dmiSmooth`) | 14 | ADX smoothing length. |
| ADX Trend Threshold (`adxThreshold`) | 0 | DMI shows 'wait' while ADX (trend strength) is below this level. 0 = always show a direction. |

### Money Flow Index Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn Money Flow Index Label On/Off (`mfiOn`) | true | Show/hide the MFI dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`mfiOrder`) | 8 | Dashboard row position for MFI. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| Money Flow Index Length (`mfiLength`) | 14 | Money Flow Index length. |

### Fisher Settings — 3 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| Turn Fisher Label On/Off (`fisherOn`) | true | Show/hide the Fisher dashboard row only. Its calculation still contributes to all-nine agreement events. |
| Place On The List (`fisherOrder`) | 9 | Dashboard row position for Fisher. Duplicate positions overwrite cells in fixed update order; use distinct values 1–9. |
| Fisher Length (`fisherLength`) | 14 | Fisher Transform length. |

### BULB Signals — 6 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| BULB (`recommendation`) | true | Enable BULB labels/state transitions. Independent of structure, zones and dashboard; no profitable-entry claim. |
| Mode (`bulbMode`) | Fixed (confirmed) | Fixed prints on confirmed RSI exits from extremes. Moving tracks alternating extreme states and relocates active labels as price extends. |
| RSI Length (`rsiLength1`) | 13 | RSI length used by BULB. |
| Overbought (`rsiOverbought`) | 65 | BULB overbought threshold: Fixed Sell requires prior RSI ≥ threshold then confirmed RSI < threshold. Moving uses entry into the extreme state. |
| Oversold (`rsiOvesold`) | 30 | BULB oversold threshold: Fixed Buy requires prior RSI ≤ threshold then confirmed RSI > threshold. Moving uses entry into the extreme state. |
| Alternate Buy/Sell (Fixed mode) (`bulbAlternate`) | true | Fixed mode: block another same-side threshold exit until the opposite side has printed. Initial state permits either side. |

### ATR Bands — 4 inputs

| Input / ID | Current default | Actual purpose |
|---|---|---|
| SMA Length (`mal`) | 47 | Length of the SMA in the middle of the ATR bands. |
| Atr Length (`atrl`) | 89 | ATR length used for the band distance. |
| Atr Multiplier (`atrm`) | 3.0 | Width multiplier for SMA × ATR/current close. Not exactly a pure ATR-unit distance unless SMA equals current close. |
| Line Width (`bandWidth`) | 1 | Exposed but unused by current runtime/rendering. Set width separately for each envelope/SMA plot in Style. |
