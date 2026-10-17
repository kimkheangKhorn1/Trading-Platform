# Claude overlap research — status (2026-10-17)

Shared note for Codex. Claude owns this file and everything under `docs/research/claude-overlap-2026-10-17/`. Codex owns `CODEX_OVERLAP_VALIDATION_2026-10-17.md`.

## Coordination

- Acknowledged. Split of roles: Claude does the research runner, representative chart review, candidate optimization and any final strategy change. Codex audits data coverage, execution causality and reporting. Claude will read Codex's validation file before accepting any result.
- **Scope is not confirmed yet.** Claude got this plan only as relayed text. No runner, optimization or strategy edit starts until the user confirms what is being tested: D/S badge overlap (`app/badge_overlap.py`), the restored MTF confluence chart strategy (`app/scripts/mtf_confluence/`), or both.

## Done

- **Baseline frozen** in [baseline-freeze.json](claude-overlap-2026-10-17/baseline-freeze.json). It holds the git HEAD (`40df803`), SHA-256 of 20 source files and all 60 `mtf_*` default inputs. The hashes are of the working tree, which has uncommitted edits. The source files are smc_master, mtf_confluence, badge_overlap, the backtest broker/metrics/service, the engine runner and the overlap frontend files.
- Defaults at freeze: `mtf_enable=True`, `mtf_strategy_tf=1H`, `mtf_start_year=2021`, `mtf_spread=0.2`, `mtf_slippage=0`.
- **Hash check against `evidence/fresh-badge-overlap-source-hashes-2026-10-17.json` (11:13):**
  - 4 of 6 smc_master files match.
  - `indicator.py` and `metadata.py` differ. The cause is the uncommitted MTF hook added at 12:16 when the strategy was restored chart-only: 1 import, `MtfFeature` construction, `on_bar` and `on_last_bar` calls, plus `INPUTS+=MTF_INPUTS` and the drawing groups.
  - The original indicator logic is otherwise unchanged (see `git diff -- backend/app/scripts/smc_master/`).
  - Implication: the badge-overlap service runs `SmcMaster` with `mtf_enable` defaulting to True. Any overlap research should pass `mtf_enable=False`, or show that the MTF feature does not change the D/S labels.

## Methodology commitments (from Codex, accepted)

- **2026 is not untouched.** Session e1a9f6ea used Jul 2025 – Oct 2026 as a check period. The only period never used for selection is 2016–2020, and even that was reported once in notes §10. So no period is fully pristine for the 1H variant. A new holdout will be named before any optimization.
- **R** = realized cash P&L including partial exits, divided by the original risk from the actual fill to the SL.
- **TP-hit win rate** (TP1/TP2 reached) is reported separately from cash profitability.
- **Unavailable data months** are reported as unavailable, never as 0-trade months.

## Planned paths (none created yet beyond the freeze)

- Runner: `backend/tools/claude_overlap_research.py`
- Outputs: `docs/research/claude-overlap-2026-10-17/` (results JSON/CSV, chart-review PNGs, `REPORT.md`)
- Strategy edits, if any are accepted, will be confined to `app/scripts/mtf_confluence/` or a new research module. smc_master's indicator logic stays as is.

## Status log

- 2026-10-17: coordination acknowledged, baseline frozen, waiting for the user to confirm scope.

## Update 13:35 (+07): session trading-platform-ba runs the research

- **Scope:** the MTF confluence strategy (`app/scripts/mtf_confluence/`). It is the only overlap system with entries, SL and TP1–TP3. `badge_overlap.py` draws only and has no signals, so it is out of scope unless the user says otherwise. Session trading-platform-7e agreed to hand the runner over.
- **Data coverage:** Exness XAUUSDm bid and ask, M1 from 2015-08-10 to 2026-10-14. **2014-01 to 2015-07 is unavailable.** 2015-08 and 2026-10 are partial months.
- **Execution:** the engine decides on 1H closes, as the chart default `strategy_tf=1H` does. Fills, stops and targets are replayed on M1 candles with the Strategy Tester broker rules (`sim.TradeBook`). Each minute uses its real spread, the Exness ask close minus the bid close. Slippage is 0.1 on market and stop fills. A 2× cost run is also reported.
- **Periods, named before any optimization:**
  - **Development** (study, chart review, candidate design): 2015-08-10 to 2022-12-31.
  - **Holdout** (chronological out-of-sample for this round): 2023-01-01 to 2026-10-14. The holdout is not pristine: a prior session used Jul 2024 – Oct 2026 as check periods.
  - **Walk-forward:** expanding window. Each test year from 2019 to 2026 uses only candidates and parameters chosen on the years before it.
- **Win definition:** TP1 or TP2 reached counts as a win. TP3 is counted separately. R is the cash P&L of all partial exits divided by the risk from the actual fill to the original SL. Missed opportunities (orders never filled) are kept apart from executed trades.

## Update 14:40 (+07): Development work done, finalists frozen before any holdout read

- **Fixes applied after Codex/peer review:**
  - cutoff 2026-10-14 00:00 UTC exclusive;
  - opening-spread proxy;
  - missing or crossed quotes stop the run;
  - unresolved trades are reported;
  - a risk-floor flag;
  - the `touch_key` cache now includes the close for "Strongest nearby", with a regression test. On real data the ledger is identical with the cache disabled.
- **Disclosure:** `path.py` and `missed.py` first printed all-year (2015–2026) aggregates before the Development-only sample rule existed. Those prints included the MFE curve and the near-miss buckets. The 50-case sample and all candidate selection use Development data only.
- **Chart review:** `claude-overlap-2026-10-17/CHART_REVIEW.md`, with 50 PNGs in `charts/`, the frozen rule in `sample.json` and the scripts in `scripts/`.
- **New engine options**, all with neutral defaults (the full baseline ledger is byte-identical with them present): `entry_offset_atr`, `tp_buffer_atr`, `scalp_r`/`scalp_frac` (quick TP1) and `trail_tp2`.
- **Candidates tested on Development** (2015-09-01 to 2022-12-31; every run is in `runs/`): 35 variants. `candidates_round1.json` has 24; `candidates_round2.json` has 11.
- **Frozen finalists for the holdout** (2023-01-01 to 2026-10-13):
  - **P (profit):** `C1_front0.10_be`: limit 0.10 ATR outside the zone edge, stop to entry after TP1. Development: 347 trades, TP-win 30.5%, +0.221R per trade, t 1.69, both halves positive.
  - **W (70% TP-hit rule):** `C3_front0.25_q0.3f0.3`: limit 0.25 ATR outside, plus a quick TP1 at 0.3R for 30% of the position. Development: 352 trades, TP-win 73.9%, cash-positive 33.8%, +0.133R per trade, t 1.56.
  - **H (reference, genuine high cash win rate):** `C9_front0.10_q0.3f0.3_be`. Development: cash-positive 72.7%, −0.043R per trade.
  - **Baseline**, for comparison.
- **Multiple testing:** 35 variants were tried. A best t of 1.7 is what noise alone would produce, so none of these is a statistically demonstrated improvement.

## Update 15:05 (+07): freeze v1 revoked (implementation defect found before any holdout read); freeze v2

- **Defect:** with `entry_offset_atr > 0`, the resting order was replaced every hour, because the ATR offset changes every candle. That gave about 29k orders instead of about 2.6k, and moved the stop and targets each hour.
- **Fix:** a front-run order keeps the offset it was placed with for as long as its zone stays the same. The baseline ledger is still identical.
- The superseded runs are kept in `runs/superseded_unstable_front/`. All front-run variants were rerun.
- The v1 finalists lost most of their edge after the fix: C1 dropped from +0.221 to +0.167R per trade. This shows how sensitive these numbers are to small mechanical details.
- **Round 3:** 3 more variants, 38 in total.
- **Freeze v2 finalists** (holdout 2023-01-01 to 2026-10-13 not read yet):
  - **P:** `R3_front0.50_be`: limit 0.5 ATR outside the zone edge, stop to entry after TP1. Development: 300 trades, TP-win 32.3%, +0.234R, t 1.64.
  - **W:** `R3_front0.50_q0.3f0.2`: limit 0.5 ATR outside, quick TP1 at 0.3R for 20%. Development: 303 trades, TP-win 71.6%, cash-positive 32.0%, +0.233R, t 2.03.
  - **H:** `C9_front0.10_q0.3f0.3_be` (reference).
  - **Baseline.**
- **Multiple testing:** the best t of 2.03 out of 38 tries is not significant.

## Update 16:10 (+07): final, holdout read once after freeze v2

**Result:** no candidate beat the baseline out of sample. Chart defaults are unchanged.
- Baseline holdout: +0.150R per trade.
- Finalists on the holdout:

  | Finalist | TP-win | Cash-positive | R per trade |
  |---|---|---|---|
  | P | | | +0.053 |
  | W | 75.9% | 27.3% | −0.062 |
  | H | | 71.7% | −0.055 |

- Walk-forward 2019–2026:
  - best-R choice: +0.00R per trade;
  - with a ≥70% TP-win constraint: −0.01R per trade.
- Across 39 variants, Development and Holdout average R correlate at 0.2.

**Artifacts** (all under `claude-overlap-2026-10-17/`):
- `REPORT.md`, `report.html` (published as a private artifact for the user)
- `results/summary.json`
- `results/monthly_*.csv`, `results/trades_*.csv`, `results/missed_*.csv`
- `scripts/evaluate.py`, `scripts/report_data.py`
- `runs/`, holding every ledger including the cost and spread sensitivity runs

**Code** (`app/scripts/mtf_confluence/`):
- the cache fix;
- 4 new options, all off by default, with a byte-identical baseline ledger;
- 3 new tests. All 193 backend tests pass.

Ready for Codex's final audit.

## Round 2 (user request: 2021 onward), 2026-10-17 evening, outputs in `claude-overlap-2021/`

- **Engine:** anchored 2020-01-01. Only entries from 2021-01-01 are evaluated.
- **Periods:** Development 2021-01-01 to 2023-12-31; holdout 2024-01-01 to 2026-10-13 (seen in earlier sessions at aggregate level).
- **Chart review:** all 149 Development trades and 13 near-miss setups (162 cases), 3 per sheet, in `charts/sheet_*.png`.
- **Candidates:** 31 Development variants (`candidates_r1.json`, `candidates_r2.json`). A 2015-09 to 2020 robustness check was run on 8 of them before the holdout.
- **Freeze (holdout not read):**
  - **F1:** `K1_against_early0.5`: 4H SMA47 slope against the trade + limit 0.5 ATR early. Development +0.347R, t 1.29; 2015–2020 +0.280R, t 1.26.
  - **F2:** `C4_be_at_1.5R`: stop to entry once +1.5R is reached. Development +0.132R; 2015–2020 +0.146R.
  - **F3 (70% TP rule):** `K7_against_early0.5_q0.3f0.2`. Development 75% TP-win, +0.116R.
  - **Base:** the baseline.
- **Round-2 holdout (read once after the freeze):**

  | Version | Trades | R per trade | Total |
  |---|---|---|---|
  | Base | 95 | +0.47R | +44.8R |
  | F1 | 53 | +0.54R | +28.6R |
  | F2 | | +0.12R | |
  | F3 | | +0.10R (78.7% TP-win) | |

- **F1 over 2021–2026:** 150 trades, +62.3R, +0.42R per trade, t 1.85, max DD 14.7R. Base: 244 trades, +41R, +0.17R, DD 31.5R.
- **Not a proven edge.**
- **Code:**
  - `trend_filter`/`trend_len`/`trend_bars` and `need_overlap_target` options, all off by default;
  - the production `trend_filter` reproduces the research ledger from 2021 on (one 2020 warm-up trade differs);
  - 194 tests pass.
- **Report:** `claude-overlap-2021/REPORT.md` and `report.html`. The artifact was republished.

## Final: strategy removed (2026-10-17, night, user request)

- **Deleted from the code:**
  - `backend/app/scripts/mtf_confluence/`.
  - The SmcMaster hook. `indicator.py` and `metadata.py` were restored to git HEAD, and all 6 smc_master files now match the original hashes in `evidence/fresh-badge-overlap-source-hashes-2026-10-17.json`.
  - The `mtf_enable` reference in `badge_overlap.py` and its test.
  - The frontend MTF layer controls and signal toggle (`MtfLayers.tsx`; the `signalsVisible` code in Legend, controller and indicators state). The `.legend-signals` CSS stays, because the badge-overlap button uses it.
  - Research and validation tools: `claude_overlap_research.py`, `codex_audit_overlap.py`, `codex_compare_overlap_resolution.py`, `codex_check_overlap_ledger.py` and `codex_overlap_mtm.py`.
  - Their tests: `test_mtf_confluence.py`, `test_overlap_execution_review.py`, `test_overlap_ledger_review.py`, `test_overlap_mtm_review.py` and `test_overlap_research_data_review.py`.
- **Kept:** every note, report, chart image, run ledger and CSV under `docs/`.
- **Checks after removal:**
  - A code search for strategy identifiers finds nothing.
  - Backend: 161 tests pass.
  - Frontend: typecheck, 43 tests, lint and build all pass.
