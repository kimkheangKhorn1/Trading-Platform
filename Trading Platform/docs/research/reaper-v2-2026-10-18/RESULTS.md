# REAPER [JOAT] step-by-step improvement — results (2026-10-18)

Code: `backend/research/reaper_v2/` — `weakness.py` (diagnosis, pre-2026 data), `steps.py` (fix → tune → check → 2026),
`ablation.py` (what did the work + random-filter placebo), `context.py` (SMC indicator state per bar). Logs in `out/`.

## Protocol
- Trades as in the user's backtest: one at a time, next-open entry on Exness bid/ask, 1.5 ATR stop, 3R target,
  stop/target order from 1-minute candles. The original reproduces 2832 / 622 / 227 / 114 / 49 / 13 on 1m–4H in 2026.
- One setting for all timeframes, score = mean TRAIN (2024-01 → 2025-06) avg R on 5m/15m/30m/1H.
- Count kept: TRAIN trades/day within ±20% of the user's 2026 rate on each of those TFs; swing strength, volume
  filter and minimum wick re-tuned at every step.
- Step kept only if VALID (2025-07 → 2025-12) mean avg R also improves. 2026 reported, never used to choose.

## Weaknesses (measured on 2024-01 → 2025-12, original rules), low → high
1. Late session 17–24 UTC: worse on 1m/5m/1H, not on 15m/30m.
2. Entry chases the close: mixed by timeframe.
3. No location: sweeps at SMC order blocks / D/S / pivot zones were not better.
4. "Close back" confirmation: MSS helped 5m/1H, hurt 1m/15m/30m.
5. No trend context: against the 4H trend was worse on 15m/30m/4H.
6. Stop inside the swept wick on 45–62% of signals; those lost more on 15m–1H.
7. Spread on 1m: 0.11R per trade.
8. Direction: right way after 50 bars only 47–52% (5m 52%, best).

## Steps (2026 net R; 5m–4H sum, then 1m)
| Step | Logic | Result |
|---|---|---|
| 0 | Original | −33.7R · 1m −306R |
| 1 | Stop beyond the sweep | sweep stop lost on TRAIN; re-tuned count settings kept: −13.3R · 1m −400R |
| 2 | MSS confirmation | worse on TRAIN, not used |
| 3 | Retest limit entry | worse on TRAIN, not used |
| 4 | Trend: 4H EMA50/200, 3EMA 50/100/200, SMC structure | **4H trend kept**: +50.9R · 1m −256R |
| 5 | SMC zone location | worse on TRAIN, not used |
| 6 | Skip 17–24 UTC | worse on TRAIN, not used |
| 7 | Spread guard | worse on TRAIN, not used |
| 8 | Exits (1–3R, breakeven, scaled) | worse on TRAIN, not used |

Final: Swing Strength 5, Min Sweep Wick 10 %, volume filter on, Only With 4H Trend on (original stop / 3R).
2026: 1m 2780 trades −255.7R, 5m 589 +30.3R, 15m 224 +21.3R, 30m 124 −31.3R, 1H 52 +23.6R, 4H 10 +7.0R.
VALID mean avg R −0.037 (5m +0.062, 15m +0.038, 30m +0.094, 1H −0.343). The 4H trend filter beat a random filter of
the same strength on 5m (62% VALID / 90% 2026) and 15m (84% / 96%), not on 30m / 1H.

Shipped: `trendFilter` input in `app/scripts/liquidity_reaper.py` (default off = original). Signals match the research
engine label for label on real data and in `tests/test_converted_scripts.py`.

## Round 2 — one input at a time from the project's other indicators (`forward.py`, log `out/forward.log`)
52 candidate inputs, all computed at the signal close and checked (RSI/ADX equal the SMC indicator's own code; 4H
values from the last completed 4H candle; footprint delta only from minutes inside the bar; signal generator
identical to the original): 3EMA, EMA200/slope, 1H/4H trend, SMC structure / premium-discount / order blocks / D/S /
pivot zones / candle patterns / BOS-CHoCH / dashboard, Triple Confluence trend and signals, footprint delta and
absorption, RSI / divergence / stochastic / MACD / ADX / DMI, FVG, previous-day high/low, pool tests / equal
levels / room to the next pool, candle body / engulfing / displacement, sessions, kill zones, volatility.
Rule: each round re-picks swing strength / volume / wick / window to hold the count; the first of the 3 best TRAIN
candidates that also improves VALID is kept. 2026 never decides.

- Round 1 kept **ADX(14) ≥ 25** (TRAIN +0.039 → +0.108, VALID −0.037 → +0.213).
- Round 2: nothing improved both → stop. Most other inputs could not keep the trade count or lost on TRAIN/VALID.
- Exits: **stop 0.2 ATR beyond the sweep wick** kept (TRAIN +0.114, VALID +0.259); 3R target stays.

Final: Swing Strength 3, Confirmation Window 5, Min Wick 25 %, volume filter off, 4H trend on, ADX ≥ 25 on, SL beyond
the sweep (+0.2 ATR), TP3 = 3R.

| TF | 2026 trades (user) | W / L | net R round 2 | round 1 | original |
|---|---|---|---|---|---|
| 1m | 2745 (2832) | 624 / 2121 | −394.0 | −255.7 | −306.0 |
| 5m | 566 (622) | 152 / 414 | +20.0 | +30.3 | +12.6 |
| 15m | 171 (227) | 53 / 118 | +40.6 | +21.3 | −37.9 |
| 30m | 93 (114) | 25 / 68 | +5.4 | −31.3 | −23.0 |
| 1H | 47 (49) | 12 / 35 | −0.9 | +23.6 | +18.1 |
| 4H | 16 (13) | 6 / 10 | +8.0 | +7.0 | −3.5 |

5m–4H total: +73.1R (round 1 +50.9R, original −33.7R). Out-of-sample 2025-H2 + 2026 total: +132.4R vs +89.1R
(round 1). ADX vs a random filter of the same strength: beaten in 50–100% of 50 seeds on 2025-H2 and 20–82% on 2026
— real on some timeframes, luck-sized on others. Indicator signals and stops match the research engine on real data
for 5m–4H.

## Round 3 — every indicator in the project, stricter checks (`v3.py`, `v3b.py`, `compare.py`; logs in `out/`)
Inputs: 100+ (round-2 set + 3EMA lengths 8/21/55 … 50/100/200, LuxAlgo swing-area count, Volume S/D zones, SMC
traps / breakouts / reactions / TSI / EQH-EQL / OB breaks / swing vs internal structure / each dashboard indicator,
Triple Confluence crosses, footprint POC, ADX 20/30, every stop / entry / exit plan, MSS). Window for 2026: 1 Jan → 1 Oct.
Checks for a change: TRAIN and VALID better, better on ≥ 3 of 4 timeframes, beats 80% of random filters of the same
strength; 3b also requires the total net R on TRAIN+VALID to improve.

- From the original (v3): accepted "engulfs previous body" then "footprint delta with trade". 2026: −56.7R (5m–4H),
  worse than the original. Cause: the score (mean of per-timeframe average R) let 15m/1H gains hide large 5m losses.
- From the round-2 setting (v3b, with the total-R check): nothing passes. Round 2 stays.

Half-years, 5m–4H net R (compare.py):
| Setting | 2024 H1 | 2024 H2 | 2025 H1 | 2025 H2 | 2026 Jan–Sep | positive |
|---|---|---|---|---|---|---|
| Original | −25.4 | −38.1 | +37.2 | −56.3 | −26.8 | 1/5 |
| Round 1 (4H trend) | −34.2 | +11.4 | +30.0 | +38.2 | +40.9 | 4/5 |
| **Round 2 (4H + ADX + sweep stop)** | −58.2 | +47.1 | +46.7 | +59.3 | **+62.4** | 4/5 |
| Round 3 (engulfing + delta) | +22.2 | −20.5 | −31.2 | +2.1 | −56.7 | 2/5 |

Round 2 in 2026 (Jan → Oct 1): 1m 2605 trades −377.0R · 5m 533 +17.2R · 15m 163 +40.5R · 30m 88 +3.1R ·
1H 44 −4.4R · 4H 14 +5.9R. Original counts in the same window: 2729 / 592 / 214 / 107 / 46 / 12.
