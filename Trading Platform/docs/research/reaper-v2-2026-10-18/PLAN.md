# REAPER v2 audit and experiment plan

User-requested joint Codex / actual Claude Code IDE review, 2026-10-18.
The original 2026 screenshot counts are 2832 / 622 / 227 / 114 / 49 / 13 / 4 for M1/M5/M15/M30/H1/H4/D.
Preserve the original and previous Pro as comparison artifacts. Implement a separate clearly named v2.

## Weaknesses: low to high severity
This is an engineering risk ranking, not a measured ordering of profitability effects.
1. Dashboard raid totals and swept boxes can be mistaken for trade outcomes / fresh supply-demand zones.
2. Confirmed pivots arrive after right-side bars; drawing from the pivot time can obscure availability.
3. Running penetration divided by the first raid range is mislabeled as a rejection-wick percentage.
4. A single pending event per side loses events and resets deadlines as new pools are crossed.
5. No directional context; evaluate optional 3EMA, do not presume a benefit.
6. Tiny coloured closes qualify without displacement or a confirmed opposing structure break.
7. ATR-only stops can sit inside the swept extreme; plotted close entries are not executable next-open fills.
8. Unconfirmed labels can appear/disappear; historical labels must not be represented as live close-confirmed results.
9. Research risk: many tuned combinations, previously inspected 2026 data, endpoint leakage and chart/research divergence can overstate confidence.

## Experiment contract (before v2 results)
- Reproduce existing baseline separately, retaining legacy fills and exact date attribution.
- New evaluation: closed candles, market entry at next open, actual aligned bid/ask, 0.05 adverse entry/stop slippage; gap stops and stop-first ambiguous M1 handling. No unsupported live execution.
- Each fold starts flat. Truncate bars AND M1 paths at fold end; mark residual positions to exit-side last close, disclose them separately from closed-trade wins/losses.
- Train: 2024-01-01 through 2025-06-30, ranked on the worse of its two halves. Validation: 2025-07-01 through 2025-12-31. Retrospective 2026: 2026-01-01 through 2026-10-14 UTC, inclusive. 2026 is already seen, never call it a fresh holdout.
- Retain a small explicit candidate menu and one-change ablations. No retest limits/session tuning/huge Cartesian search in this study. Keep pivot 8, confirmation window 3, max pools 12 unless separately reported.
- Default activity acceptance proposed: >=70% of original trade count in EACH train half and validation; awaiting user's optional preference. The actual 2026 counts are reported, never forced.
- EMA feature uses the existing engine EMA from three_ema.py: configurable 50/100/200 default, test disabled vs aligned 50/100/200 vs 20/50/100. Strictly ordered lengths. EMA is secondary directional confirmation, not a standalone entry.
- Profitability acceptance requires positive results in both train halves and validation, meaningful sample (at least 30 trades per half/validation), and required activity retention. No eligible winner means explicitly no accepted tuned preset.
- Final output: reproducible source/settings/data identity, per-timeframe and monthly trades/wins/losses/flat/open/TP/SL/net R/PF/drawdown, activity and reasons for rejection. No guarantee of profitable future signals.
