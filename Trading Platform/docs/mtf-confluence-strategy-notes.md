# MTF Zone Confluence strategy: logic notes (removed 2026-10-17)

> **Removed 2026-10-17 (night):** at the user's request the whole MTF confluence strategy was deleted from the indicator and the codebase (backend module, indicator hook, settings, chart layer controls, research runner, and the validation tools and tests). Only these notes remain. Settings and file paths named below no longer exist in the code.

The strategy ran inside the SMC & ICT indicator as an optional feature. It was removed and the indicator was
restored to its original code. These notes describe the final logic so it can be rebuilt.

## 1. Zones
- **Detection:** the indicator's own Demand/Supply rules and settings ("Demand & Supply" group: ATR or
  percent mode, departure 1.5 ATR, max height 1.0 ATR, max 100 zones per side). The zones were run on 1m,
  5m, 15m, 30m, 1H and 4H candles. 1m was off by default, and any timeframe below the chart was skipped.
- **Building higher-timeframe candles:** they were built from the chart's own confirmed candles.
  - A zone exists only from the close of the candle that confirmed it, so it never uses future data.
  - If a data gap skips the end of a higher-timeframe candle, that candle (and its zone) is finished on the
    next chart candle.
- **Fresh:** a zone is fresh until any later candle touches it: low ≤ top for demand, high ≥ bottom for
  supply. Only fresh zones are used.
- **Cap:** when a side and timeframe has more than "max zones" fresh zones, the oldest one is dropped.

## 2. Confluence (overlap) groups
- **Grouping:** same-side fresh zones that overlap, or lie within 0.1 × ATR(14) of each other, form one
  group.
- **Core:** the price band covered by the most zones.
  - If those zones really overlap, the core is their common band.
  - If they are near but apart, the core is the gap between them.
- **Strength:** the number of zones covering the core ("x3" means 3 zones).
- **Entry group:** needs strength ≥ 2 and at least 2 different timeframes.
- **Precise zone:** the core by default. "Union" (the whole group) and "Lowest-TF zone in core" were options.

## 3. Entry (final default: "Zone touch")
- **Resting order:** a limit order rests at the near edge of the nearest qualifying group's precise zone:
  the top of a demand core for a buy, the bottom of a supply core for a sell. It fills the moment price
  touches that edge. There is no waiting for a candle close. "50% of the zone" was an option.
- **When it moves:** the order is re-decided only when zones change (created, touched or dropped), the
  allowed side changes, or a trade fills or closes. It moves only if the chosen zone or entry price differs.
  Its SL and TP stay as set when it was placed.
- **Once price is in the group:** when price enters any zone of the order's group (even just its outer
  part), the order is held. That touch would otherwise make those zones non-fresh and shrink the group.
  A held order stays until:
  - it fills,
  - its stop trades before entry (cancelled),
  - a candle closes through the zone's far edge,
  - 30 bars pass,
  - or a qualifying zone forms closer to price, in which case the order moves to that closer zone.
- **One trade at a time:** no new order rests while a trade is open.
- **Fills:** they used the Strategy Tester broker rules on bid candles.
  - A buy limit fills when the ask (bid + spread) reaches the price; a sell limit fills when the bid does.
  - At a gap open, the order fills at the open.
- **Old entry options:** "CHoCH confirmation" was a market entry on the close above the highest high (buy)
  of the last 5 bars up to the deepest candle in the zone. "Rejection candle" was a dip into the zone that
  closed back out in the top half (buy).

## 4. Moving-average filter (default on)
- **Rule:** close above the indicator's SMA 47 (on 5m: the blue line) means sells only. Close below it means
  buys only. A close exactly on the line means neither.
- **When it is checked:** on the close where the order is decided.
- **Options:** the opposite rule (above = buy), EMA, other lengths and timeframes, and "only when both sides
  have a confluence group within 5 ATR".

## 5. Stop loss (default "Touch extreme - ATR buffer")
- **Formula:** SL = deepest price reached in the zone (at least the far edge of the precise zone) beyond by
  ATR(14) × 1.0, plus 0.3 points.
- **Zone-touch orders:** the deepest price is not known yet when the order is placed, so the zone's far edge
  is used: SL = far edge ± ATR + 0.3.
- **Minimum distance:** 1 point from the entry. A maximum (default off) could skip wide stops.
- **Other options:**
  - entry − zone height × k,
  - far edge − zone height × k,
  - far edge − ATR × k,
  - fixed points.

## 6. Take profit (final default: "Zones, most overlap first")
- **Count:** up to 3 TPs.
- **Candidates:** fresh opposite zones ahead of the entry (supply above a buy, demand below a sell), nearest
  first. A target sits at the group's near edge, moved 0.2 points toward the entry. Groups closer than
  0.5 × risk are skipped.
- **Selection, overlap first:**
  - Look at the nearest 5 groups with strength ≥ 2.
  - Take the up-to-3 with the highest strength; on a tie, the nearer one wins.
  - Order them by price: TP1 is the nearest, TP3 the furthest.
- **No overlapping group ahead:** use the nearest single fresh zones (x1), up to 3.
- **Nothing ahead at all:** fall back to 1R / 2R / 3R.
- **Position split:** equal between the TPs. Optionally, the stop moves to entry after TP1.
- **Result:** any TP hit is a WIN. The stop hit before any TP is a LOSS.

## 7. History and display
- **History:** calculated from 1 January 2021 whatever part of the chart was loaded, so every view and the
  Strategy Tester gave the same signals.
- **Display:** only the last 50 trades were drawn: entry, SL and TP lines, the overlapping zones of each
  trade, and a "WIN · TP1 … hit" label.

## 8. Results seen (XAUUSDm 5m, Exness bid, 2021 – Oct 2026)
- CHoCH entry + Hybrid 1R targets: 1117 trades, TP win 50.4%, −16R, PF 0.97 without the MA filter.
  With the MA filter: 849 trades, −82R.
- Zone-touch entry (near edge) + Hybrid targets: 2353 trades, TP win 44.3%, −335R, PF 0.77.
- No setting showed a reliable edge. The final "most overlap first" targets were not backtested before
  removal.

## 9. Verification that passed
These checks were run before removal:
- Zones identical to the indicator's.
- Higher-timeframe zones identical to the stored candles.
- Overlap search identical to brute force.
- Every order re-derived from raw candles.
- Trade replay identical to the real Strategy Tester broker (about 20k trades).
- Chart trades identical to tester trades.
- Signals independent of the loaded chart range.

## 10. Research on 2026-10-17 (after the chart-only restore)

All tests used Exness XAUUSDm bid candles from 2016 to Oct 2026, with a 0.2–0.5 spread. Trades were replayed
with the Strategy Tester broker rules; 1-minute replays gave the same results. Periods:
- **Selection:** Jan 2021 – Jun 2024.
- **Check:** Jul 2024 – Jun 2025 and Jul 2025 – Oct 2026.
- **Never looked at while choosing:** 2016–2020.

**The zone-touch entry has no edge on its own.** On 5m, price reached +1R before −1R about 50% of the time,
before costs, with every stop placement tested. Filters did not change that: strength, timeframes, age, session,
MA side, H1 trend or zone height.

**Changing the targets on 5m did not help.** "Nearest single zone, then overlaps" lifted the TP win rate from
15% to 25%. It still lost about −0.06R per trade.

**Other entries were no better.** These were all about 0 before costs and negative after:
- a deeper limit at the core or group far edge;
- a 1m rejection close;
- trading the break of the zone.

**Watching replays: what reversals look like.** 61 sampled touches were drawn as charts, and every touch was
also measured.
- **Held or broke:** price left the zone by 2 ATR about 52% of the time and broke it the rest.
- **Rejection candles:** zones held 65% of the time when the touching candle closed back outside. But entering
  after that close earns nothing, because the reversal is already inside the wick.
- **Where price turns:** in held touches, 61% turned inside the whole group, but only 37% inside the core.
- **When it turns:** the median turn came 4 minutes after the touch, so the touching candle is usually the turn.
- **How far it goes:** median move after the turn is 5.8 ATR. The first opposite zone is a median 5.4 ATR away
  and was reached 47% of the time.

**The one result that held up was a 1H strategy timeframe.** The setup:
- zones from 1H and 4H only;
- MA filter on the 1H SMA 47;
- stop at the far edge + 1 ATR(1H) + 0.3;
- TP1 at the nearest single zone at least 1R away, then TP2/TP3 at the most-overlapped groups;
- entry at the strongest qualifying zone within 3 ATR.

Results with a 0.3 spread:

| Metric | Value |
|---|---|
| Average per trade | +0.17R |
| t-stat | 1.26 |
| Trades | 439 |
| Positive years | 9 of 11 |
| 2016–2020 (never looked at) | +0.16R per trade |
| Max drawdown | 32R |
| Before costs | t = 2.15 |

The same 1H entries with fixed 1R or 2R targets lost (t = −3.4 and −2.1). So the zone targets carry
information, but the edge is small and not statistically proven. This setup is the default via "Strategy
timeframe = 1H", which shows the 1H trades on lower charts. The 5m-chart logic is still available with
"Strategy timeframe = Chart".
