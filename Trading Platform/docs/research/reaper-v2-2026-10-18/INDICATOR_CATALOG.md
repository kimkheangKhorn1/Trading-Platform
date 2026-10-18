# What every indicator in the project does — and what REAPER can borrow

Written while improving REAPER [JOAT] (2026-10-18). For each script: what it computes, which parts make trading
decisions and which only draw, and how its logic was turned into a REAPER filter (tested in
`backend/research/reaper_v2/features.py` / `features3.py`). Every borrowed input is computed only from data
available at the REAPER signal's close.

## REAPER [JOAT] — `app/scripts/liquidity_reaper.py`
Swing highs / lows (pivot, `pivotLen` bars each side, known `pivotLen` bars late) become liquidity pools (max
`maxPools` per side). A wick through a pool is a raid; a close back inside within `confirmWindow` bars, with the
signal's candle colour, volume ≥ `volMultReq` × SMA(`volLen`) and a sweep wick ≥ `minWickPct` % of the raid range,
is RAID BUY / SELL. Added filters (off by default): 4H EMA50/200 trend, ADX ≥ `adxMin`. Trade plan display: entry =
signal close, SL = ATR × 1.5 (or % or, added, beyond the sweep wick), TP1–3 = 1/2/3 R; the drawn trade ends at SL or
the furthest shown TP. Display only: rails, swept zones, labels, TP/SL boxes, dashboard. Full map in the file header.

## Liquidity Reaper Pro — `liquidity_reaper_pro.py`
Same detection plus research options: MSS confirmation (close beyond the raid candle), stop beyond the sweep
(+0.2/0.5 ATR), limit entry on the retest of the swept level (5 bars), 4H trend, no 17–24 UTC, spread guard (fixed
0.26), exit plans 1R / 2R / 3R / scaled + breakeven, per-timeframe presets, Strategy Tester signals.
Borrowed: MSS, sweep stops, retest entry, session, spread guard, exit plans.

## 3EMA — `three_ema.py`
Three EMAs of the close (50 / 100 / 200 by default; Pine `ta.ema`, SMA-seeded). Pure display, no signals, never tuned.
Borrowed: stacked-with-trade and close-vs-slow-EMA for 8/21/55, 9/21/50, 10/30/60, 20/50/100, 50/100/200.

## SMC & ICT & Indicators + Scalping Master — `smc_master/`
- Market structure (internal + swing): pivots of `ilength`/`length`, BOS / CHoCH on a close beyond the last
  structure high / low, trend +1/−1; HH/LH/HL/LL and strong / weak highs; premium / discount from the trailing
  swing range. Borrowed: internal / swing trend, BOS / CHoCH (internal, swing), premium / discount.
- Order blocks (internal / swing): last opposite candle before the break (ICT displacement rule); removed on a close
  through. Borrowed: sweep at an order block, order block broken in direction.
- Demand / supply: base + departure ≥ `ds_min_atr` ATR, states fresh / tested / invalidated. Borrowed: sweep at a
  fresh D/S zone.
- Pivot S/R zones (ATR-wide) and their events: breakout / breakdown, resistance / support break, false breakdown
  (bear trap) / false breakup (bull trap), reactions, TSI curls. Borrowed: all as filters.
- EQH / EQL (equal highs / lows within 0.1 ATR), FVGs (3-candle imbalance), previous D / W / M levels.
  Borrowed: EQH/EQL before the raid, FVG in direction, previous-day high / low swept.
- Candle patterns near pivot S/R (Bjorgum formulas: engulfing, hammer, star, harami, doji…). Borrowed: pattern in
  direction around the raid.
- Dashboard: 9 directions — MACD, Stochastic, Vortex, Momentum, RSI, PSAR, DMI (ADX ≥ threshold), MFI, Fisher.
  Borrowed: each one with the trade, and 5-of-9.
- BULB (RSI extremes), SMA47 ± 3 × ATR(89) bands. Borrowed: sweep outside the band, buy below / sell above the middle.

## Triple Confluence Navigator — `triple_confluence.py`
Three signals that must line up within `confluenceWindow` bars: (1) Cardwell RSI cross (Kalman-filtered fast /
slow of RSI(14)), (2) adaptive-RSI crossing its supertrend-merged signal, (3) a structure break of the last custom
swing (5). Optional HTF bias and ADX chop filter; SL = min(structure + 0.25 ATR, 1.5 ATR), TP 1/2/3 R. Borrowed:
ARSI supertrend direction, structure trend, each of the three crosses recently in direction, the combined signal.

## Footprint IQ Pro — `footprint_iq.py`
Builds a per-price footprint from 1-minute candles: an up minute adds its volume as buying, a down minute as selling,
spread over the price levels it covered; per level delta, delta %, POC, value area, stacked imbalances, CVD option.
Display only. Borrowed: candle delta (buy − sell) / total, absorption (raid candle delta against the trade), and
where the candle's busiest minute (POC) sits in its range.

## Volume Supply and Demand [TradingIQ] — `volume_supply_demand.py`
After ≥ 4 same-colour candles whose move is steep (angle ≥ 69° against the average range), marks the supply / demand
zone at the start of the run with a lower-timeframe volume profile (POC, value area); zones die on a close through or
after `max_age`. Borrowed: sweep inside a live zone of the matching side.

## Liquidity Swings [LuxAlgo] — `liquidity_swings.py`
Pivot highs / lows (14) with a "swing area" (wick area of the pivot candle) and how many candles (or how much volume)
traded into it until price closed through. Borrowed: how many candles traded into the swept pool's area before the
raid (more = more resting orders, or a worn-out level).
