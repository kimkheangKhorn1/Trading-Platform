# Liquidity Reaper research (2026-10-18)

Full report with monthly tables per timeframe: https://claude.ai/artifact/KiDXb3aXEEBq5pu2L3fbnu (private).

## Data and rules

- Exness XAUUSDm archive, bid candles for signals and the aligned ask candles for fills, Jan 2024 – Oct 2026.
- Signal on a bar's close, entry at the next open (buys at the ask, sells at the bid) + $0.05 slippage; exits on the
  closing side. Stop/target order inside a bar resolved with 1-minute candles; both inside one minute = stop first.
- Windows: train Jan 2024 – Jun 2025 (tuning), validation Jul – Dec 2025 (pass / fail), test Jan – Oct 2026 (scored once).
- Selection: among 2,304 settings per timeframe, the one whose weaker train half has the best average R.

## Findings

- Original rules (`liquidity_reaper_joat`): net R over the whole period 1m −1,947, 5m +56, 15m −71, 30m −76, 1H −16,
  4H −10, 1D +5 (11 trades). After the spread, price moves against the signal at every horizon on every timeframe.
- Weaknesses: stop inside the swept wick on 44–62% of signals; "close back inside" is not a reversal; spread is a large
  share of risk on 1m/5m; market entry chases the close; no trend / session context.
- Tuned settings failed validation on 5m, 15m, 1H, 1D. 30m passed validation and lost in 2026. 4H passed on 7 trades.
- 1m (structure-shift confirmation, limit on retest of the swept level, stop beyond sweep + 0.5 ATR, 1R target, 4H trend,
  no 17–24 UTC, spread guard): train +0.097R (217), validation +0.114R (196), 2026 test +0.144R (280, +40.4R).
  Survives $0.20 trade-through fills (+0.094R) and doubled slippage; beats 99% of random pull-back limit placebos.
  The same recipe does not work on 5m–1D. A candidate for demo forward testing, not proof.

## Code

- `backend/research/reaper/`: `events.py` (signals, identical to the chart port), `sim.py` (fills), `tune.py`,
  `report.py`, `stress.py`, `recipe.py`, `run_baseline.py`, `diagnose.py`, `edge.py`; results in `out/`.
- Re-run: `cd backend && .venv/bin/python research/reaper/run_baseline.py && .venv/bin/python research/reaper/tune.py
  && .venv/bin/python research/reaper/report.py` (1m tuning takes about 15 minutes).
- Chart: `Liquidity Reaper Pro (improved)` (`app/scripts/liquidity_reaper_pro.py`); presets in
  `liquidity_reaper_pro_presets.json`. The chart's spread guard uses a fixed typical spread (0.26) because the chart
  has bid prices only.
