# REAPER [JOAT] upgrade — full process notes (2026-10-18)

Everything done to improve the Liquidity Reaper [JOAT] indicator, in order, with the rules used, what was kept,
what was rejected and the final long backtest. Plain-language summary first, details after.

## In one paragraph
REAPER finds swing highs / lows (liquidity pools), waits for price to wick through one and close back, and signals
the reversal. On Exness gold the original rules lost money on 15m–4H. Over four rounds we tested more than 100 extra
rules taken from the other indicators in this project. What helped: a 4H trend filter (EMA50 vs EMA200), ADX ≥ 25,
a stop just beyond the swept wick, and per-timeframe settings. On 2024–2026 the improved indicator is profitable on
15m–4H. On 2022–2023, which was never used for any decision, 15m–4H still lost (−30R and −64R), though far less than
the original (−38R and −74R); 5m lost heavily in those years. 1m loses with every setting.

## 1. How every test works (same for all rounds)
- Data: Exness XAUUSDm bid and ask candles from the local store (`backend/data/market.db`).
- A signal is known at its candle's close. Entry at the next candle's open: buy at the ask, sell at the bid, plus
  $0.05 slippage. Stop and target are checked on the 1-minute candles inside each candle; if both are hit in the same
  minute, the stop counts first (worst case). Target = 3R (REAPER's TP3), stop = the indicator's SL.
- One trade at a time (a new signal is skipped while a trade is open) — this reproduces the user's own backtest
  counts exactly: 2026 (Jan 1 → Oct 14) 2832 / 622 / 227 / 114 / 49 / 13 / 4 trades on 1m / 5m / 15m / 30m / 1H /
  4H / D.
- Periods: TRAIN 2024-01 → 2025-06 (choose settings), VALID 2025-07 → 2025-12 (check), TEST 2026-01 → 2026-10
  (report). From round 3 on, the 2026 window is Jan 1 → Oct 1.
- Trade count rule: any new setting must keep TRAIN trades per day within ±20% of the original's, per timeframe;
  swing strength / volume filter / min wick / confirmation window are re-tuned to hold the count, so a filter can't
  "win" by simply trading less.
- R = profit or loss in units of the amount risked. Win rate needed to break even at 3R ≈ 25–27% after costs.

## 2. Original REAPER and its weaknesses (measured on 2024–2025)
2026: 1m −306R, 5m +12.6R, 15m −37.9R, 30m −23.0R, 1H +18.1R, 4H −3.5R.
Weaknesses, low → high: late-session trades (17–24 UTC) worse on some timeframes; entry chases the close; no
location context; "close back inside" is not a real reversal; no trend filter (counter-trend raids lost more on
15m/30m/4H); the 1.5 ATR stop sat inside the swept wick on 45–62% of signals; spread is 0.11R per 1m trade; and the
core: price went the right way only 47–53% of the time.

## 3. Round 1 — one fix per weakness (`research/reaper_v2/steps.py`)
Rule: add one fix, re-tune the count settings, keep it only if TRAIN and VALID both improve (one setting for all TFs).
Kept: 4H trend filter (+ swing 5, wick 10). Rejected: stop beyond sweep, MSS, retest entry, SMC zones, session,
spread guard, exits. 2026 (to Oct 14): 5m–4H −33.7R → +50.9R.

## 4. Round 2 — 52 inputs from the other indicators (`forward.py`, `features.py`, `context.py`)
Inputs: 3EMA, EMA200, 1H trend, SMC structure / premium-discount / order blocks / D/S / pivot zones / patterns /
BOS-CHoCH / dashboard, Triple Confluence, footprint delta, RSI, divergence, stochastic, MACD, ADX, DMI, FVG,
previous-day levels, pool facts, candle shape, sessions, volatility. Each verified (RSI/ADX equal the SMC code, HTF
from completed candles only, signals identical to the original generator).
Kept: ADX(14) ≥ 25, then stop 0.2 ATR beyond the sweep. 2026 (to Oct 14): +73.1R.

## 5. Round 3 — every indicator, stricter checks (`v3.py`, `v3b.py`, `features3.py`, `context3.py`)
Added: 3EMA at 5 length sets (it had never been tuned), LuxAlgo swing-area count, Volume S/D zones, SMC traps /
breakouts / reactions / TSI / EQH-EQL / OB breaks / each dashboard indicator, Triple Confluence crosses, footprint
POC, ADX 20/30, every stop / entry / exit, MSS.
Extra checks: better on ≥3 of 4 timeframes, beats 80% of random filters of the same strength, total net R up.
Result: from the original it chose "engulfing + footprint delta" (−56.7R in 2026 → rejected; the mean-of-averages
score had hidden big 5m losses). From the round-2 setting nothing passed.

## 6. Round 4 — best logic per timeframe (`v4_tf.py`, `combine.py`)
Each timeframe searched alone; then each timeframe takes the round whose logic did best. Two presets were shipped:
- **Best in 2026**: 5m round 1, 15m round 2, 30m round 2, 1H round 1, 4H round 2 → 2026 +97.7R (chosen on 2026).
- **Most consistent 2024-2026**: 5m original, 15m round 3, 30m/1H/4H round 2 → every half-year 2024–2026 positive.

Round definitions: original = swing 8, window 3, wick 25 %, volume filter on, 1.5 ATR stop. Round 1 = swing 5, window
3, wick 10 %, volume on, 4H trend. Round 2 = swing 3, window 5, wick 25 %, volume off, 4H trend, ADX ≥ 25, stop 0.2
ATR beyond the sweep. Round 3 = swing 3, window 5, wick 10 %, volume off, engulfing body, 1-minute delta with the
trade. All: TP3 = 3R.

## 7. What is in the indicator now (`backend/app/scripts/liquidity_reaper.py`)
New inputs (all off by default, so the original is unchanged): Settings Per Chart Timeframe (Off / Best in 2026 /
Most consistent 2024-2026), Only With 4H Trend, Only When ADX Shows a Trend + Minimum ADX, Signal Candle Must Engulf
The Previous Body, Order Flow: 1-Minute Delta With The Trade, SL Beyond the Sweep Wick + buffer. 1m and the daily
chart keep the user's inputs and show a warning. The file header explains which parts decide trades and which only
draw. Tests: `backend/tests/test_converted_scripts.py`. The indicator's signals were checked against the research
signals on real data for every timeframe and preset (identical).

## 8. Long backtest 2022-01-01 → 2026-10-01 (`backtest_2022.py`)
Signals come from the indicator itself; trades as in section 1. 2022–2023 were never used for any choice.

| Setting, all 5m–4H | 2022 | 2023 | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|---|---|
| Original | −173.5R | −256.2R | −62.5R | −19.2R | −26.8R | −538.1R |
| Best in 2026 | −104.9R | −152.4R | +49.9R | +70.6R | +97.7R | −39.1R |
| Most consistent | −165.6R | −246.3R | +85.3R | +152.6R | +71.4R | −102.6R |

| 15m–4H only (5m left out) | 2022 | 2023 | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|---|---|
| Original | −37.9R | −74.0R | −78.3R | −47.6R | −53.7R | −291.5R |
| Best in 2026 | +8.0R | −43.7R | +62.7R | +45.9R | +67.2R | +140.1R |
| Most consistent | −30.0R | −64.1R | +69.5R | +124.2R | +44.5R | +144.0R |

What it means: the improvements are much better than the original in every year, but they are not profitable in
2022–2023; the gains are concentrated in 2024–2026, when gold trended strongly up — the regime the filters were tuned
in. 5m (original rules in "Most consistent") lost −135.6R in 2022 and −182.1R in 2023. Recommended use: 15m, 30m, 1H,
4H only, small size, demo first; expect losing years when gold is not trending.

## 9. Lessons for later work
- Choosing by the period you report makes it look good by construction; always keep a period nobody looked at
  (2022–2023 here showed the real picture).
- Average R per timeframe can hide large losses on the busy timeframe; also check total R.
- Fewer trades per timeframe = more luck; per-timeframe tuning overfits faster than one setting for all.
- Next idea discussed: an ML (LightGBM meta-label) filter on top — it must beat these rules on 2022–2023 too.

## Files
Code: `backend/app/scripts/liquidity_reaper.py`; research `backend/research/reaper/` (generator, simulator) and
`backend/research/reaper_v2/` (all rounds). Docs in this folder: `PLAN.md`, `RESULTS.md`, `SUMMARY.md`,
`INDICATOR_CATALOG.md`, this file. Re-run the long backtest: `cd backend && .venv/bin/python
research/reaper_v2/backtest_2022.py` (about 15 minutes; results in `research/reaper_v2/out/backtest_2022.json`).

## Appendix — month by month (Most consistent preset)

### Recommended: "Most consistent" preset, 15m + 30m + 1H + 4H together


**2022**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2022-01 | 47 | 19% | -13.4R |
| 2022-02 | 34 | 32% | +8.0R |
| 2022-03 | 34 | 29% | +5.3R |
| 2022-04 | 36 | 17% | -12.8R |
| 2022-05 | 38 | 21% | -6.8R |
| 2022-06 | 42 | 38% | +19.2R |
| 2022-07 | 38 | 13% | -19.2R |
| 2022-08 | 35 | 23% | -4.6R |
| 2022-09 | 47 | 30% | +6.9R |
| 2022-10 | 38 | 16% | -15.0R |
| 2022-11 | 40 | 20% | -10.4R |
| 2022-12 | 32 | 38% | +12.9R |
| **2022 total** | **461** | **25%** | **-30.0R** |

**2023**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2023-01 | 35 | 26% | -1.5R |
| 2023-02 | 32 | 19% | -9.1R |
| 2023-03 | 39 | 21% | -8.1R |
| 2023-04 | 33 | 24% | -2.9R |
| 2023-05 | 47 | 19% | -13.2R |
| 2023-06 | 39 | 15% | -17.4R |
| 2023-07 | 36 | 17% | -13.7R |
| 2023-08 | 35 | 23% | -5.4R |
| 2023-09 | 40 | 28% | +0.9R |
| 2023-10 | 42 | 24% | -4.3R |
| 2023-11 | 37 | 32% | +8.1R |
| 2023-12 | 32 | 28% | +2.5R |
| **2023 total** | **447** | **23%** | **-64.1R** |

**2024**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2024-01 | 32 | 41% | +17.4R |
| 2024-02 | 36 | 39% | +15.2R |
| 2024-03 | 25 | 52% | +24.4R |
| 2024-04 | 28 | 39% | +15.0R |
| 2024-05 | 42 | 24% | -3.5R |
| 2024-06 | 32 | 16% | -12.9R |
| 2024-07 | 45 | 18% | -14.4R |
| 2024-08 | 32 | 34% | +10.6R |
| 2024-09 | 38 | 39% | +20.4R |
| 2024-10 | 45 | 36% | +16.8R |
| 2024-11 | 45 | 24% | -1.7R |
| 2024-12 | 41 | 15% | -18.0R |
| **2024 total** | **441** | **30%** | **+69.5R** |

**2025**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2025-01 | 41 | 37% | +17.0R |
| 2025-02 | 45 | 29% | +5.3R |
| 2025-03 | 33 | 42% | +21.5R |
| 2025-04 | 36 | 25% | -1.9R |
| 2025-05 | 47 | 21% | -7.8R |
| 2025-06 | 40 | 40% | +23.7R |
| 2025-07 | 51 | 18% | -15.6R |
| 2025-08 | 47 | 23% | -4.7R |
| 2025-09 | 44 | 52% | +46.3R |
| 2025-10 | 39 | 41% | +24.4R |
| 2025-11 | 28 | 36% | +11.5R |
| 2025-12 | 39 | 28% | +4.5R |
| **2025 total** | **490** | **32%** | **+124.2R** |

**2026**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2026-01 | 41 | 32% | +10.5R |
| 2026-02 | 22 | 50% | +21.5R |
| 2026-03 | 34 | 21% | -6.1R |
| 2026-04 | 39 | 31% | +9.5R |
| 2026-05 | 42 | 24% | -3.2R |
| 2026-06 | 41 | 29% | +5.4R |
| 2026-07 | 50 | 34% | +17.8R |
| 2026-08 | 42 | 21% | -6.6R |
| 2026-09 | 36 | 22% | -4.4R |
| **2026 total** | **347** | **29%** | **+44.5R** |

**All 2022–2026: 2186 trades, win rate 27.6%, total +144.0R**

### 5m alone (original rules) — not recommended


**2022**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2022-01 | 61 | 28% | -2.1R |
| 2022-02 | 53 | 28% | -2.4R |
| 2022-03 | 58 | 22% | -9.8R |
| 2022-04 | 58 | 29% | +4.6R |
| 2022-05 | 62 | 19% | -18.1R |
| 2022-06 | 68 | 21% | -18.6R |
| 2022-07 | 50 | 20% | -14.5R |
| 2022-08 | 63 | 21% | -18.9R |
| 2022-09 | 60 | 18% | -19.9R |
| 2022-10 | 59 | 17% | -24.3R |
| 2022-11 | 58 | 26% | -5.8R |
| 2022-12 | 57 | 26% | -5.9R |
| **2022 total** | **707** | **23%** | **-135.6R** |

**2023**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2023-01 | 63 | 14% | -31.8R |
| 2023-02 | 72 | 18% | -26.4R |
| 2023-03 | 68 | 19% | -22.7R |
| 2023-04 | 61 | 23% | -11.1R |
| 2023-05 | 58 | 24% | -7.8R |
| 2023-06 | 57 | 37% | +20.2R |
| 2023-07 | 55 | 24% | -11.1R |
| 2023-08 | 78 | 21% | -27.2R |
| 2023-09 | 53 | 23% | -12.4R |
| 2023-10 | 68 | 18% | -24.6R |
| 2023-11 | 66 | 17% | -28.4R |
| 2023-12 | 45 | 29% | +1.1R |
| **2023 total** | **744** | **22%** | **-182.1R** |

**2024**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2024-01 | 57 | 25% | -7.4R |
| 2024-02 | 57 | 28% | -2.5R |
| 2024-03 | 43 | 35% | +11.6R |
| 2024-04 | 56 | 25% | +1.0R |
| 2024-05 | 65 | 23% | -12.6R |
| 2024-06 | 56 | 30% | +6.7R |
| 2024-07 | 57 | 32% | +10.3R |
| 2024-08 | 63 | 17% | -22.4R |
| 2024-09 | 74 | 30% | +5.4R |
| 2024-10 | 72 | 31% | +10.6R |
| 2024-11 | 52 | 31% | +8.5R |
| 2024-12 | 59 | 31% | +6.6R |
| **2024 total** | **711** | **28%** | **+15.8R** |

**2025**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2025-01 | 65 | 25% | -6.5R |
| 2025-02 | 49 | 22% | -7.2R |
| 2025-03 | 65 | 28% | +3.3R |
| 2025-04 | 75 | 32% | +15.8R |
| 2025-05 | 58 | 36% | +23.6R |
| 2025-06 | 68 | 28% | +3.7R |
| 2025-07 | 66 | 32% | +14.1R |
| 2025-08 | 54 | 19% | -16.3R |
| 2025-09 | 50 | 30% | +7.5R |
| 2025-10 | 67 | 24% | -4.7R |
| 2025-11 | 54 | 26% | +0.1R |
| 2025-12 | 71 | 24% | -4.9R |
| **2025 total** | **742** | **27%** | **+28.5R** |

**2026**

| Month | Trades | Win rate | Result |
|---|---|---|---|
| 2026-01 | 81 | 26% | +2.5R |
| 2026-02 | 56 | 30% | +11.3R |
| 2026-03 | 69 | 28% | +5.7R |
| 2026-04 | 60 | 23% | -5.8R |
| 2026-05 | 68 | 29% | +11.0R |
| 2026-06 | 65 | 20% | -14.5R |
| 2026-07 | 64 | 22% | -9.3R |
| 2026-08 | 65 | 34% | +20.8R |
| 2026-09 | 64 | 28% | +5.2R |
| **2026 total** | **592** | **27%** | **+26.9R** |

**All 2022–2026: 3496 trades, win rate 25.2%, total -246.6R**

### Per timeframe, per year (Most consistent preset)

| TF | 2022 | 2023 | 2024 | 2025 | 2026 (to Oct 1) | Total |
|---|---|---|---|---|---|---|
| M5 | -135.6R (707, 23%) | -182.1R (744, 22%) | +15.8R (711, 28%) | +28.5R (742, 27%) | +26.9R (592, 27%) | **-246.6R** (3496, 25%) |
| M15 | -40.3R (278, 23%) | -31.3R (261, 24%) | +31.2R (256, 29%) | +38.4R (254, 30%) | +39.8R (201, 30%) | **+37.8R** (1250, 27%) |
| M30 | +11.4R (127, 28%) | -13.7R (109, 23%) | +35.4R (121, 34%) | +28.4R (134, 31%) | +3.1R (88, 26%) | **+64.6R** (579, 29%) |
| H1 | -5.9R (45, 22%) | -18.8R (61, 18%) | +7.0R (52, 29%) | +41.6R (86, 37%) | -4.4R (44, 23%) | **+19.5R** (288, 27%) |
| H4 | +4.8R (11, 36%) | -0.3R (16, 25%) | -4.1R (12, 17%) | +15.7R (16, 50%) | +5.9R (14, 36%) | **+22.1R** (69, 33%) |

### The three choices, 5m–4H, per year

| Setting | 2022 | 2023 | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|---|---|
| Original REAPER | -173.5R | -256.2R | -62.5R | -19.2R | -26.8R | **-538.1R** |
| Best in 2026 | -104.9R | -152.4R | +49.9R | +70.6R | +97.7R | **-39.1R** |
| Most consistent | -165.6R | -246.3R | +85.3R | +152.6R | +71.4R | **-102.6R** |

| Setting, 15m–4H only | 2022 | 2023 | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|---|---|
| Original REAPER | -37.9R | -74.0R | -78.3R | -47.6R | -53.7R | **-291.5R** |
| Best in 2026 | +8.0R | -43.7R | +62.7R | +45.9R | +67.2R | **+140.1R** |
| Most consistent | -30.0R | -64.1R | +69.5R | +124.2R | +44.5R | **+144.0R** |
