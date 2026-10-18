# Round 2: overlap-zone strategy from 2021, the user's ideas and new hypotheses

> **Removed 2026-10-17 (night):** at the user's request the whole MTF confluence strategy was deleted from the indicator and the codebase (backend module, indicator hook, settings, chart layer controls, research runner, and the validation tools and tests). Only these notes remain. Settings and file paths named below no longer exist in the code.

Date: 2026-10-17. Instrument: Exness XAUUSDm, 1H strategy (chart default), M1 execution (opening-spread proxy, 0.1 slippage, no swap or commission).

**Engine and periods:**
- The engine is anchored on 2020-01-01 so zones exist when 2021 starts. Only entries from 2021-01-01 count.
- Development: 2021–2023.
- Holdout: 2024-01-01 to 2026-10-13. It was read once, after the freeze. It is not pristine, because earlier sessions looked at 2024–2026 in aggregate.
- Robustness check: 2015-09 to 2020, a period never used for this round's choices.

## Chart review: all 162 Development cases

- **What was viewed:** 149 executed trades (32 TP-hit wins) and 13 near-miss setups (price turned within 0.25 ATR of the limit). The contact sheets are `charts/sheet_000.png` to `sheet_053.png`, 3 cases per sheet. Each case shows 1H context plus an M5/M15 zoom.
- **What the charts suggested,** and what the numbers then said (2021–2023):

| Seen on the charts | Tested in numbers | Verdict |
|---|---|---|
| Losers fade strong trends. Runs of failed buys in Jun 2021, Apr–May 2022, Jul–Sep 2022 and Sep 2023; runs of failed sells in Mar 2022 and Oct–Dec 2023 | Trades **with** the 4H SMA47 slope: −0.44R per trade (59 trades). **Against** it: +0.24R (90). P(+1R before −1R) was 51% in both. | The opposite of what the charts suggested. Fading the 4H trend did better in 2021–2023. It was weaker in 2015–2020 on its own (+0.08R, against the baseline's +0.13R). |
| Many losers first went 1.5–2.5R in profit | 26% of losers reached +1.5R and 10% reached +2R | True. Stop to entry at +1.5R gave +0.13R on Development, +0.15R on 2015–2020 and +0.12R on the holdout. That is no better than the baseline. |
| TP1 often far (8–28R) because the rule skips nearer overlapped zones (#661 reached 11R, #772 7.7R, #788 5R, then stopped out) | TP1 at any zone: +0.02R; with stop to entry: +0.05R | No improvement. The rule also produces the rare +16–22R winners. |
| Stop hunts at news times (12:30–14:00 UTC wicks, then a reversal) | Results by fill hour were flat; wider stops lost in round 1 | Not usable. |
| Same-side loss streaks | 48h cooldown after a loss: −0.04R | No. |
| Price turning just before the zone, then going to TP1 (10 of 13 near-misses) | Executable front-run limits: 0.10 ATR −0.03R, 0.25 +0.07R, 0.50 +0.18R | Only 0.5 ATR helped, and only modestly. The closest-approach label is biased. |

- **Price behaviour after the fill (2021–2023):**

| Reached first, before the stop | Observed | Random walk |
|---|---|---|
| +0.5R | 65% | 67% |
| +1R | 51% | 50% |
| +2R | 29% | 33% |
| +5R | 15% | 17% |

  The zone touch still shows no direction edge.

## Your ideas (Development 2021–2023, baseline −0.03R per trade, 149 trades)

| Idea | Trades | TP-hit win % | R per trade |
|---|---|---|---|
| Entry at the zone middle | 162 | 14.8 | **−0.31** |
| Early entry 0.10 / 0.25 / 0.50 ATR | 148 / 133 / 129 | 22 / 29 / 32 | −0.03 / +0.07 / **+0.18** |
| Lower-TF confirmation: M5 close back out of the zone | 114 | 26.3 | +0.03 |
| Lower-TF confirmation: M15 close back | 109 | 29.4 | +0.02 |
| Lower-TF break of structure, M5 / M15 | 105 / 95 | 30.5 | −0.06 / −0.15 |
| Only if a target is an overlapped zone | 146 | 22.6 | −0.01 |
| Only x3+ entry zones | 2 | – | too few |

Confirmation raises the TP-hit rate a little, but it enters later and further from the stop, so the result per trade does not improve.

## Finalists (frozen before the holdout read)

| Version | 2015-09 to 2020 | 2021–2023 (Development) | 2024 to 2026-10 (Holdout) | 2021 to 2026-10 total |
|---|---|---|---|---|
| Current settings | 219 trades, +0.13R, +29R | 149, −0.03R, −4R | 95, +0.47R, +45R | 244 trades, 23.8% TP-win, **+41R**, +0.17R, PF 1.22, max DD 31.5R |
| **F1: fade the 4H trend + entry 0.5 ATR early** | 136, **+0.28R**, +38R | 97, **+0.35R**, +34R | 53, **+0.54R**, +29R | 150 trades, 33.3% TP-win, **+62R**, +0.42R, t 1.85, PF 1.61, max DD 14.7R |
| F2: stop to entry at +1.5R | 261, +0.15R | 151, +0.13R | 125, +0.12R | 276, +35R, +0.13R |
| F3: F1 + quick TP1 0.3R (20%) | – | 104, 75% TP-win, +0.12R | 75, **78.7% TP-win**, +0.10R | 179, 76.5% TP-win, 27.4% cash-positive, +20R |

- **F1** was better per trade than the current settings in all three periods. It also had half the drawdown and more total R over 2021–2026.
- F1 also takes about **40% fewer trades.** In 2024–2026 alone, its total (+29R) was below the current settings' (+45R).
- The edge is not statistically proven. Holdout t is 1.34; across 2021–2026, t is 1.85. 38 variants were tried, so a t near 2 can come from chance.
- **F3** shows again that a 75–80% TP-hit win rate can be made with a quick first target. Most of those "wins" still lose money (27% cash-positive), and it earns less than F1.

## Code changes

- **New option `trend_filter`** ("4H trend filter": Off / against / with the trade), with `trend_len` 47 and `trend_bars` 6. Off by default.
  - F1 = this filter set to "against", plus "Zone touch: front-run entry (ATR)" set to 0.5.
  - The production option reproduces the research ledger trade for trade from 2021 on. The only difference is one trade in the 2020 warm-up week.
- **New option `need_overlap_target`** ("Trade only with an overlapped target"). Off by default.
- **Research-only rules** in `backend/tools/claude_overlap_research.py`: lower-TF confirmation, stop to entry at +X R, weekend exit and time stop. None of these is in the indicator, because none helped.
- **Checks:**
  - With all new options off, the 2015–2026 ledger is identical to before.
  - 194 backend tests pass, including a new trend-filter test.

## Files

| Path | Contents |
|---|---|
| `runs/` | Every round-2 ledger |
| `runs_pre2021/` | The 2015–2020 check runs |
| `candidates_r1.json`, `candidates_r2.json` | Development variant settings |
| `candidates_pre2021_check.json` | Settings checked on 2015–2020 |
| `candidates_prod_check.json` | Production-option check settings |
| `results/summary.json` | Round-2 statistics |
| `results/monthly_<version>.csv` | Every month from 2021-01 to 2026-12 |
| `review_cases.json` | The reviewed cases |
| `scripts/` | Reproduction scripts |
