# Overlap-zone (MTF confluence) strategy: backtest, chart review and optimization, 2015–2026

> **Removed 2026-10-17 (night):** at the user's request the whole MTF confluence strategy was deleted from the indicator and the codebase (backend module, indicator hook, settings, chart layer controls, research runner, and the validation tools and tests). Only these notes remain. Settings and file paths named below no longer exist in the code.

Date: 2026-10-17. Instrument: Exness XAUUSDm. Strategy: `app/scripts/mtf_confluence/` with the chart defaults (strategy timeframe 1H, zones from 1H and 4H). Independent validation is by Codex, in `../CODEX_OVERLAP_VALIDATION_2026-10-17.md`.

## Answer first

- **The 70% win-rate target can only be reached by a counting rule, not by better trades.**
  - Putting a quick TP1 at 0.3R raises the TP-hit win rate to 72–77% in every period.
  - It does not make the trades better. With the rest of the position left to run, about two-thirds of those "wins" lose money.
  - On the 2023–2026 holdout, finalist W had **75.9% TP-hit wins, but only 27.3% cash-positive trades and −0.06R per trade**.
  - When the stop also moves to entry after the quick TP1 (finalist H), 72% of trades really are profitable. The average result is still negative: **−0.02R per trade over 2015–2026**.
- **No change beat the current defaults out of sample.**
  - 38 variants were tried on 2015-09 to 2022 data. They cover entry front-running, TP placement, the TP1 rule, stop management, quick TP1 and stop width.
  - The best Development results did not carry over. Across the 39 variants (the 38 plus the baseline), the correlation between Development and Holdout average R is **0.2**.
  - A walk-forward that re-chose the best variant every year earned **0.00R per trade over 2019–2026**. The unchanged baseline earned **+0.15R**.
- **The baseline is slightly positive, and that is not proven.**
  - Over 2015-09 to 2026-10-13: 463 trades, 23.8% TP-hit wins, **+70.1R, +0.15R per trade, t = 1.15**, profit factor 1.19, max drawdown 31.5R.
  - At twice the spread and slippage, it drops to +0.06R per trade.
  - Swap and commission are not modelled, and many trades are held for days or weeks.
- **What was changed in the code:**
  - one bug fix (the resting-order cache);
  - four new options, all off by default. With them off, the trades are byte-identical to before.
  - **The chart defaults are unchanged.**

## Data and execution

- **Coverage:** Exness XAUUSDm M1 bid and ask, 2015-08-10 to the verified cutoff 2026-10-14 00:00 UTC (exclusive).
  - **2014-01 to 2015-07: no data (unavailable).**
  - August 2015 is the engine warm-up and is not evaluated.
  - October 2026 is partial, up to the 13th.
  - November and December 2026 have not happened yet.
- **Decisions:** the engine is fed closed 1H bid candles, as the chart does with "Strategy timeframe = 1H". Zones, groups, orders, stops and targets come only from data known at each close.
- **Fills:** every 1H candle is replayed minute by minute through the Strategy Tester broker rules. Each minute uses its opening spread (ask open − bid open) as a proxy, plus 0.1 slippage on market and stop fills. No commission or swap. Cost sensitivity: 2× costs, and the larger of the opening and closing spread.
- **Ledger:** every order is kept: filled, withdrawn, invalidated and resting at the cutoff. One trade per variant was still open at the cutoff. It is reported separately, never dropped.
- **Execution model:** Codex's independent H1-vs-M1 comparison found that 8 of 2,581 shared orders differ in fill or cash. The shipped chart resolves fills on 1H candles; this research uses M1.

## Periods and validation

- **Development:** 2015-09-01 to 2022-12-31. All candidate design and selection, and the 50-chart review, happened here.
- **Holdout:** 2023-01-01 to 2026-10-13. It is not pristine: earlier sessions looked at 2024–2026.
- **Freezes:** the finalists were frozen in the status file before any holdout number was computed.
  - The first freeze was revoked before the holdout read, after an implementation defect was found: front-run orders were being replaced every hour.
  - After the fix, those candidates lost most of their Development edge.
- **Walk-forward:** each year from 2019 to 2026 uses the variant with the best average R on all earlier years.
- **Disclosure:** two diagnostic scripts printed all-year aggregates once, before the Development-only rule was set: the MFE curve and the near-miss buckets.

## Baseline behaviour (what the charts and the numbers show)

### Where price goes after the fill

The table shows how often price reached +x R before the stop, measured on M1, after spread. The 2015–2026 aggregate is used.

| Target | Reached first | Random walk |
|---|---|---|
| 0.3R | 71.8% | 76.9% |
| 1R | 48.0% | 50.0% |
| 3R | 24.9% | 25.0% |
| 5R | 17.8% | 16.7% |

The zone-touch entry has no measurable directional edge. Near targets are slightly worse than a coin flip after costs. Far zone targets are about even, and the baseline's small profit comes from the occasional very large winner: the average TP-hit win is 3.9R. This is why no TP placement can lift the win rate without lowering the payoff by the same amount.

### Problem A: missed entries

Rule: an order never filled, where price came within 0.10 ATR of the limit (first crossing). Each setup is counted once.
- This happened **19 times in 11 years**: 13 would have reached TP1 first, 4 would have lost, and 2 were unresolved.
- Moving the limit outward catches them, but it also moves every other entry, and that did not help:
  - 0.05–0.25 ATR: equal or worse on Development.
  - 0.5 ATR: better on Development (+0.23R), but −0.02R on the holdout.
- In the chart sample, 5 of 10 near-misses would have won and 5 would have lost.

### Problem B: almost reaching TP

- Only **4%** of losing trades reached 75% of the way to TP1 before stopping out, and 2% reached 90%. TP1 is usually far away (median 3.9R).
- The chart review did find these real patterns:
  - TP1 sits at the opposite zone's edge with only 0.2 points of front-run.
  - The default TP1 rule skips nearer overlapped zones, where price often turned.
  - After TP1, the rest of the position often went back to the original stop.
- The fixes tested for these did not help out of sample:
  - front-running targets by 0.1–0.5 ATR;
  - TP1 at any nearer zone;
  - stop to entry after TP1;
  - stop to TP1 after TP2.
- Stop to entry after TP1 was the closest: +0.19R on Development, +0.08R on the holdout, against the baseline's +0.15R.

### Problem C: losing trades

- In the charts, losers often looked like:
  - a steady grind or a fast spike through the zone;
  - a stop taken by a wick just before price reversed;
  - news or weekend gaps.
- The numbers did not confirm a usable filter. On the Development trades, P(+1R before −1R) stayed between 42% and 56% (noise level) across terciles of:
  - approach momentum over 6h and 24h;
  - SMA slope;
  - distance from the SMA;
  - zone height, zone age and strength;
  - hour of day;
  - waiting time.
- Wider stops (1.5–2 ATR) and a tighter stop (0.5 ATR) were both worse.

## Finalists (holdout = 2023-01 to 2026-10-13)

| Strategy | Period | Trades | TP-hit win % | Cash-positive % | Total R | R per trade | t | PF |
|---|---|---|---|---|---|---|---|---|
| Baseline (current defaults) | Development | 310 | 23.2 | 22.6 | +47.2 | +0.152 | 0.92 | 1.19 |
| | Holdout | 153 | 24.8 | 22.9 | +22.9 | +0.150 | 0.69 | 1.20 |
| | All | 463 | 23.8 | 22.7 | +70.1 | +0.151 | 1.15 | 1.19 |
| P: entry 0.5 ATR early + stop to entry after TP1 | Development | 300 | 32.3 | 32.3 | +70.2 | +0.234 | 1.64 | 1.34 |
| | Holdout | 160 | 28.7 | 28.7 | +8.4 | +0.053 | 0.29 | 1.07 |
| W: entry 0.5 ATR early + quick TP1 0.3R (20%) | Development | 303 | 71.6 | 32.0 | +70.7 | +0.233 | 2.03 | 1.39 |
| | Holdout | 187 | **75.9** | 27.3 | −11.7 | −0.062 | −0.55 | 0.90 |
| H: entry 0.1 ATR early + quick TP1 0.3R (30%) + stop to entry | Development | 471 | 73.7 | 72.8 | −1.0 | −0.002 | −0.04 | 0.99 |
| | Holdout | 251 | 71.7 | 71.7 | −13.7 | −0.055 | −0.78 | 0.81 |

**Walk-forward over 2019–2026:**
- Picking the best R each year: 345 trades, +0.00R per trade.
- Picking the best R among variants with ≥70% TP-hit: 365 trades, 75.6% TP-hit, −0.01R per trade.

**2× costs over 2015–2026:**

| Strategy | R per trade at 2× costs |
|---|---|
| Baseline | +0.06 |
| P | +0.02 |
| W | −0.00 |
| H | −0.10 |

## Files

| Path | Contents |
|---|---|
| `results/summary.json` | Every statistic above, the yearly tables, walk-forward picks, costs, and all 39 variants on Development and Holdout. |
| `results/monthly_<strategy>.csv` | Every month from 2014-01 to 2026-12, with coverage status, trades, wins, losses, win %, cash-positive count, net R by entry cohort and by exit date, average R, TP1/TP2/TP3 counts, and missed setups with their hypothetical outcomes. |
| `results/trades_<strategy>.csv` | Every executed trade with its fills. |
| `results/missed_<strategy>.csv` | Every missed setup. |
| `runs/` | Raw ledgers of every run, including superseded ones. |
| `candidates_round*.json` | The settings of each variant. |
| `CHART_REVIEW.md`, `charts/`, `sample.json` | The 50-case chart review. |
| `scripts/` | Reproduction scripts. |
| `../../../backend/tools/claude_overlap_research.py` | The runner. |

## Code changes

- `engine.py`:
  - The resting-order cache now includes the close when the zone choice is "Strongest nearby". This fixes a stale-order bug that Codex reproduced, and comes with a regression test.
  - New options, all off by default:
    - `entry_offset_atr` (front-run entry; the order keeps its offset while its zone is unchanged);
    - `tp_buffer_atr`;
    - `scalp_r` / `scalp_frac` (quick TP1).
- `sim.py`: the new option `trail_tp2` (stop to TP1 after TP2) and a `TS` exit label.
- `inputs.py`: help text for the new options states their backtest result.
- Checks: with every new option off, the full 2015–2026 order ledger is identical to before. All 193 backend tests pass.
