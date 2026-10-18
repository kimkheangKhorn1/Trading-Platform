# REAPER [JOAT] — all upgrade and tuning results (2026-10-18)

How every test was done: real Exness XAUUSD prices (bid/ask, spread paid), one trade at a time, entry at the next
candle, stop/target checked on 1-minute candles. Settings were chosen on 2024 → mid-2025 only, checked on the second
half of 2025, and then backtested on 2026. 1R = the amount risked per trade.

## Step 0 — Original REAPER (2026, Jan 1 → Oct 14)
| TF | Trades | Win % | Result |
|---|---|---|---|
| 1m | 2832 | 24% | −306.0R |
| 5m | 622 | 26% | +12.6R |
| 15m | 227 | 21% | −37.9R |
| 30m | 114 | 21% | −23.0R |
| 1H | 49 | 35% | +18.1R |
| 4H | 13 | 23% | −3.5R |
| **5m–4H total** | | | **−33.7R** |

Weaknesses found: price went the right way only ~50% of the time; the 1.5 ATR stop sat inside the swept wick on
45–62% of signals; no trend filter; spread too big for 1m; many trades were in profit before hitting the stop.

Earlier attempt (Reaper Pro): tuning each timeframe separately failed the 2025 check on most timeframes; the one
1m recipe that passed traded only ~280 times in 2026 (too few vs 2832), so it was not used.

## Round 1 — one fix per weakness (8 fixes tested)
Kept: **4H trend filter** (with Swing Strength 5, Min Wick 10 to keep the trade count).
Rejected: stop beyond the sweep (at that stage), MSS confirmation, retest entry, SMC zones, session filter,
spread guard, other exits.

| TF | Trades | Result | vs original |
|---|---|---|---|
| 1m | 2780 | −255.7R | better, still losing |
| 5m | 589 | +30.3R | better |
| 15m | 224 | +21.3R | better |
| 30m | 124 | −31.3R | worse |
| 1H | 52 | +23.6R | better |
| 4H | 10 | +7.0R | better |
| **5m–4H total** | | **+50.9R** | was −33.7R |

## Round 2 — 52 inputs from the other indicators
Kept: **ADX(14) ≥ 25** and **stop 0.2 ATR beyond the sweep wick**.
Rejected: 3EMA 50/100/200, SMC order blocks / D/S / patterns / BOS-CHoCH / dashboard, RSI, MACD, stochastic,
footprint, FVG, sessions, previous-day levels and others (worse, or cut too many trades).

| TF | Trades | Wins / Losses | Result |
|---|---|---|---|
| 1m | 2745 | 624 / 2121 | −394.0R |
| 5m | 566 | 152 / 414 | +20.0R |
| 15m | 171 | 53 / 118 | +40.6R |
| 30m | 93 | 25 / 68 | +5.4R |
| 1H | 47 | 12 / 35 | −0.9R |
| 4H | 16 | 6 / 10 | +8.0R |
| **5m–4H total** | | | **+73.1R** |

## Round 3 — 100+ inputs from every indicator, stricter checks (2026, Jan 1 → Oct 1)
New: 3EMA tuned lengths, LuxAlgo swing areas, Volume S/D zones, SMC traps / breakouts / each dashboard
indicator, Triple Confluence crosses, footprint POC, all stops and exits.
- Started from the original: chose "engulfing + footprint delta" → −56.7R in 2026 → rejected.
- Started from the round-2 setting: nothing beat it.

**Best setting = round 2.** 2026 (Jan 1 → Oct 1):
| TF | Trades (original) | Result (original) |
|---|---|---|
| 5m | 533 (592) | +17.2R (+26.9R) |
| 15m | 163 (214) | +40.5R (−40.6R) |
| 30m | 88 (107) | +3.1R (−18.2R) |
| 1H | 44 (46) | −4.4R (+9.2R) |
| 4H | 14 (12) | +5.9R (−4.0R) |
| **5m–4H total** | | **+62.4R (−26.8R)** |
| 1m | 2605 (2729) | −377.0R (−293.2R) — do not trade |

Every half-year, 5m–4H total:
| Setting | 2024 H1 | 2024 H2 | 2025 H1 | 2025 H2 | 2026 Jan–Sep |
|---|---|---|---|---|---|
| Original | −25R | −38R | +37R | −56R | −27R |
| Round 1 | −34R | +11R | +30R | +38R | +41R |
| **Round 2 (best)** | −58R | +47R | +47R | +59R | **+62R** |

## Settings to use (REAPER [JOAT])
Swing Strength 3 · Confirmation Window 5 · Min Sweep Wick 25 · Require Volume Expansion off ·
Only With 4H Trend on · Only When ADX Shows a Trend on (25) · SL Beyond the Sweep Wick on (0.2) · TP3 3R.

## Cautions
- Small profit per trade (about +0.03 to +0.25R); demo-test first, 15m first.
- 1m loses every period (spread). 1H was about flat in 2026.
- 15m trades are ~24% fewer than the original.
- No setting guarantees future profit.

## Round 4 — best logic per timeframe (`v4_tf.py`, `verify_presets.py`)
Each timeframe searched on its own (add / remove any input, any stop / exit), then the timeframe's logic chosen among
original / round 1 / round 2 / round 3 / its own search by 2024–2025 net R. Results:
- 5m: the **original rules** won on 2024–2025 (+43R vs −63R for round 2) and also in 2026 (+26.9R vs +17.2R).
- 15m: its own search (round 2 + stop 0.5 ATR + LuxAlgo area) won on 2024–2025 but made only +13.2R in 2026 vs
  +40.5R for round 2 → round 2 kept.
- 30m, 4H: round 2. 1H: its own search (+ footprint absorption + no Monday-early/Friday-late) was only −0.4R vs −4.4R
  in 2026 on ~40 trades → round 2 kept (simpler, difference is noise).
- 1m: every setting loses in every half-year → do not trade.
Note: keeping round 2 on 15m / 1H used the 2026 result, so 2026 is no longer a fully untouched test for those two.

Shipped: REAPER input **Use Best Settings For The Chart Timeframe** (5m original, 15m–4H round 2, 1m / D warn and
keep your inputs). Backtest again from the indicator's own signals (identical to the research signals):
| | 2024 H1 | 2024 H2 | 2025 H1 | 2025 H2 | 2026 Jan–Sep |
|---|---|---|---|---|---|
| Per-timeframe presets (5m–4H) | −5.1R | +84.2R | +70.0R | +51.8R | **+72.0R** |
| Round 2 for all | −58.2R | +47.1R | +46.7R | +59.3R | +62.4R |

2026 per timeframe: 5m 592 trades +26.9R (PF 1.06) · 15m 163 +40.5R (1.36) · 30m 88 +3.1R (1.05) · 1H 44 −4.4R
(0.87) · 4H 14 +5.9R (1.66).

## Combining the rounds (`combine.py`, `verify_presets.py`) — 2026 = Jan 1 → Oct 1
Earlier tables used Jan 1 → Oct 14 (round 1, round 2) and Jan 1 → Oct 1 (round 3); this compares all on Oct 1.
No logic error: the indicator's signals equal the research signals on every timeframe for both presets.

| TF | original | round 1 | round 2 | round 3 | 2026 best | 2024–2026 best |
|---|---|---|---|---|---|---|
| 5m | +26.9 | **+30.6** | +17.2 | −46.2 | round 1 | original (+70.1 total) |
| 15m | −40.6 | +17.3 | **+40.5** | +39.8 | round 2 | round 3 (+109.4) |
| 30m | −18.2 | −32.6 | **+3.1** | −31.5 | round 2 | round 2 (+66.9) |
| 1H | +9.2 | **+17.7** | −4.4 | −16.9 | round 1 | round 2 (+44.2) |
| 4H | −4.0 | +8.0 (count too low) | **+5.9** | −2.0 | round 2 | round 2 (+17.5) |

| Preset (REAPER "Settings Per Chart Timeframe") | 2024 H1 | 2024 H2 | 2025 H1 | 2025 H2 | 2026 | all |
|---|---|---|---|---|---|---|
| Best in 2026 | −34.4 | +86.3 | +31.1 | +39.5 | **+97.7** | +220.3 |
| Most consistent 2024–2026 | +51.4 | +32.8 | +90.6 | +62.1 | +71.4 | **+308.2** |
| (previous) original 5m + round 2 | −5.1 | +84.2 | +70.0 | +51.8 | +72.0 | +272.9 |
| Round 2 for all | −58.2 | +47.1 | +46.7 | +59.3 | +62.4 | +157.3 |

Added inputs: engulfFilter, deltaFilter (1-minute order flow), and tfPresetMode (Off / Best in 2026 / Most consistent
2024-2026) replacing the earlier tfPreset switch.
