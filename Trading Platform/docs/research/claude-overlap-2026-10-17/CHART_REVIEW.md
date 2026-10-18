# Chart review of 50 baseline cases (frozen sample, Development period only)

**Sample.** The rule was frozen in `sample.json` before any chart was viewed.
- Seed 20261017. Entries, or order placements for misses, before 2023-01-01 UTC.
- Pools (Development): 73 wins, 24 near-TP losses, 215 other losses, 30 near-miss unfilled orders.
- Drawn at random from those pools: 15 wins, 10 near-TP losses, 15 other losses and 10 misses.

**Definitions.**
- *Near-TP loss*: no TP hit, and the MFE before the SL reached ≥ 60% of the entry-to-TP1 distance.
- *Miss*: an order never filled, whose closest approach (M1 ask low for buys, bid high for sells) came within 0.25 ATR(1H) of the limit. This closest-approach label is retrospective, used only to pick cases to look at. Executable alternatives are tested separately.

**What each chart shows.**
- Left panel: 1H candles, the SMA 47 (1H), and the member zones of the entry group with their timeframes. The blue box is the entry core. The dotted boxes are the opposite groups ahead when the order was placed.
- Right panel: M5 or M15 zoom.
- The PNGs are in `charts/` and are named `<kind>_<order id>.png`.

**Execution.** Baseline settings (chart defaults, strategy timeframe 1H). M1 execution with the opening-spread proxy and 0.1 slippage. No commission or swap.

## Case notes

| Case | Side | What happened | Tag |
|---|---|---|---|
| win_46 | Sell | Spike fill at the top of the 1H/4H core; turned on the fill candle. TP1 (8.35R) and TP2 eight days later. | spike-turn |
| win_346 | Sell | Spike into the core, turned at once; TP1 1.67R, TP2, TP3. | spike-turn |
| win_601 | Sell x3 | TP1 3.7R in a day. The other 2/3 rode for a month back to the original SL: +0.54R with MFE 9.3R. | runner-gives-back |
| win_628 | Buy | Clean: TP1 4.7R, then TP2. | clean |
| win_690 | Sell | TP1 6.4R. The rest returned to the original SL after 3 weeks: +1.45R. | runner-gives-back |
| win_905 | Sell | TP1, TP2, then the rest to SL 8 days later: +2.0R. | runner-gives-back |
| win_933 | Buy | TP1 3R in 12h; TP2 12 days later. | clean |
| win_940 | Sell | Pullback above a falling SMA into supply; TP1/TP2 the same day. | with-HTF-trend |
| win_1030 | Buy | Fill, dip, then TP1/TP2. | clean |
| win_1371 | Buy | TP1 skipped the nearer x2 1H/4H supply (the TP1 rule needs a single zone). Price stalled at that x2 group first, then TP1 by a hair, then all the way down to SL: +0.63R. | tp1-skips-overlap, runner-gives-back |
| win_1463 | Buy | TP1 after 4 days; the rest to SL 4 weeks later: +0.76R. | runner-gives-back |
| win_1539 | Sell | TP1–TP3, +10.3R. | clean |
| win_1592 | Buy | TP1 exactly at the supply edge; the rest to SL the next day: +2.05R. | runner-gives-back |
| win_1693 | Buy | TP1, TP2, then the rest to SL: +2.7R. | runner-gives-back |
| win_1857 | Buy | Spike fill, TP1–TP3: +8R. | spike-turn |
| loss_16 | Buy | Buying a steady downtrend under a falling SMA. MFE 0.12R. Price rallied 5R after the SL. | against-momentum, stop-hunt |
| loss_97 | Sell | Selling an impulsive rally; the zone broke at once (MFE 0). | against-momentum |
| loss_327 | Buy | Falling-knife fill in a crash. MFE 0.75R, then SL. | against-momentum |
| loss_371 | Sell | MFE 4.2R, but TP1 was 8.2R away (the nearer x2 1H/4H demand was skipped). Back to SL 5 days later. | tp1-too-far |
| loss_513 | Sell | Slow grind up through the zone. | against-momentum |
| loss_691 | Buy | Steep decline, stall in the zone, SL a few dollars lower; +2.9R rally after the SL. | against-momentum, stop-hunt |
| loss_786 | Buy | Persistent downtrend under the SMA. | against-momentum |
| loss_811 | Buy | A wick 2 ATR below the zone took the SL, then +7.9R. | stop-hunt |
| loss_902 | Sell | Uptrend; the stop went over a weekend; price fell afterwards. | against-momentum, gap |
| loss_1140 | Sell | Strong pre-holiday rally through the zone. | against-momentum |
| loss_1146 | Sell | News spike (2020-01-07) through the SL, then reversal (+6R after). TP1 was 14R away. | news-spike, tp1-too-far |
| loss_1226 | Buy x3 | A wick took the SL, then the rally reached the target zone (+6.1R after). | stop-hunt |
| loss_1707 | Sell | MFE 1.5R, drifted back; SL by a few dollars, then down 2.2R. | stop-hunt |
| loss_1747 | Buy | News wick to the SL, then up. | stop-hunt, news-spike |
| loss_1780 | Buy | SL taken at the Monday open after a weekend gap. | gap |
| near_151 | Sell | MFE 2.4R; price turned exactly at the nearer x2 1H demand that the TP1 rule skipped (TP1 3.9R). | tp1-skips-overlap |
| near_260 | Buy | Turned $1.6 below TP1, which sat at the supply edge. | tp-at-edge |
| near_507 | Sell | Turned about $4 above TP1. | tp-at-edge |
| near_520 | Buy | Reached 1270.8 vs TP1 1271.04 ($0.24 short), stalled for hours under the supply, then SL. | tp-at-edge |
| near_891 | Sell | $2.5 short of TP1, then up to SL. | tp-at-edge |
| near_1059 | Sell | MFE 7.2R; turned at the x2 1H/4H demand before TP1 (a single zone 8.4R away). | tp1-skips-overlap |
| near_1256 | Sell | $0.13 short of TP1, then SL 3 days later. | tp-at-edge |
| near_1686 | Buy | Turned about $6 below TP1 (2.1R). | tp-at-edge |
| near_1727 | Buy | MFE 1.2R; a wick took the SL, then TP2 was reached. | stop-hunt |
| near_1765 | Sell | Sold into a vertical rally; the MFE came from one wick. | against-momentum |
| missed_184 | Sell | Turned $0.02 below the limit; later went through to above the SL. | miss → would have lost |
| missed_840 | Sell | Turned $0.14 below the limit, then reached TP1. | miss → would have won |
| missed_917 | Sell | Turned $0.16 below, then reached TP1. | miss → would have won |
| missed_978 | Sell | Turned $0.50 below, later went through to the SL. | miss → would have lost |
| missed_986 | Buy | $0.45 above the limit, then later fell through the SL. | miss → would have lost |
| missed_989 | Buy | $0.30 above; later the SL first. TP1 was 15R away. | miss → would have lost |
| missed_1581 | Sell | $0.45 below, then TP1. | miss → would have won |
| missed_1697 | Sell | $1.63 below, then TP1. | miss → would have won |
| missed_1835 | Sell | $0.95 below, then TP1. | miss → would have won |
| missed_1838 | Sell | $0.86 below; TP1 22.6R away (three x2 groups skipped). The SL came first. | miss → would have lost, tp1-too-far |

## Patterns (case counts are out of the 50; small numbers, hypotheses only)

1. **Runner gives back (8 of 15 wins).** After TP1, the rest of the position often rode for days or weeks back to the *original* SL. Nothing manages the stop after TP1. Holds of weeks also carry swap costs, which are not modelled. → Test stop to entry after TP1, and stop to TP1 after TP2.
2. **The TP1 rule skips nearer overlapped groups.** By default TP1 must be a single (non-overlapped) zone. Price repeatedly turned at the nearer overlapped group that the rule skipped: 151, 1059, 1371, 371. It also produced absurd TP1 distances: 14R in 1146, 15R in 989, 22.6R in 1838. → Test TP1 = nearest opposite group of any strength.
3. **TPs sit at the zone edge.** The 0.2-point front-run is a few cents at gold's 1H scale. Price turned $0.1–6 before TP1: 260, 507, 520, 891, 1256, 1686. → Test a front-run in ATR units.
4. **Against-momentum entries (9 of 25 losses).** The zone touch was part of a steady grind or an impulsive leg through the zone. The default MA filter makes every trade counter to the SMA 47 by design. → Measure the approach momentum and the trend slope on the Development period; filter only if the separation is clear and causal.
5. **Stop hunts (8 losses).** A wick beyond the far edge + 1 ATR took the SL, and price then reversed toward the target. A wider stop lowers R for every trade. → Test the SL buffer.
6. **Misses are coin flips.** Price turned $0.02–1.6 before the limit; 5 of 10 would have reached TP1 first and 5 would have lost. Moving the limit outward also worsens every filled entry. → Test executable front-run limits causally (first crossing), not the closest-approach label.
7. **Spike fills that turn on the fill candle are the typical winners** (46, 346, 1857), but losers look the same at the moment of the fill (327, 1765). Nothing visible at the fill reliably separates them.
