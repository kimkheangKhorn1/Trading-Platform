# Independent overlap research validation — 2026-10-17

Status: audit in progress. No 70% win-rate or profitability claim is accepted yet.

The user authorized the task in the attached `Pasted text.txt`, to improve the existing signal system with verified history, approximately 50 visual cases, realistic costs, monthly/yearly results, chronological validation and actual realized R. The relevant signal system is `app/scripts/mtf_confluence/`; `app/badge_overlap.py` is a separate display-only service. The existing signal engine is the baseline, not a new detector. No further scope approval is needed for the authorized research.

## Coordination

- Original Claude research session: `8e0743cc-a319-4664-82e0-1f4dc6194279` (the full user task).
- Claude coordination/review session: `97d2d77b-da6b-481f-ae18-e9084d3427a8` (baseline freeze and a separate execution review). This review session should avoid duplicating the original research runner or editing strategy files.
- Codex owns this document, `backend/tools/codex_audit_overlap.py`, and `codex-overlap-2026-10-17/`. Claude owns its existing research outputs and status note. Preserve unrelated working-tree changes.

## Confirmed current limits

1. Canonical Exness XAUUSDm first quote: **2015-08-10 00:00 UTC**. Last quote: **2026-10-14 07:16:03.655 UTC**. There is no verified 2014 history. August 2015 and October 2026 are partial months. Future November/December 2026 rows, if included in a complete monthly grid, must be unavailable. Historical missing days/minutes remain absent.
2. Canonical archive policy marks the entire latest UTC-day snapshot incomplete. Use **2026-10-14 00:00 UTC exclusive** for the validated baseline, or explicitly validate a newer cutoff. SQLite's `complete` flags differ: it marks only the last candle incomplete. Merely filtering SQLite by `complete=1` therefore does not preserve canonical source completeness.
3. Research in `docs/mtf-confluence-strategy-notes.md` section 10 and the earlier Claude session already examined 2026. Neither 2026 nor previously inspected 2016–2020 can be described as pristine holdout data now. A new walk-forward reconstruction can be a chronological historical evaluation; prior human exposure remains a limitation.
4. Current defaults run the strategy on **1H**, although the UI may show M5 candles. Test the frozen existing default first, and label M5 variants explicitly. Changing strategy timeframe changes both decisions and participating zone timeframes, so it is a different hypothesis.
5. The chart engine is separate from Strategy Tester signals. Its `TradeBook` uses bid candles plus a **constant spread**, slippage on market entries/stops, and conservative OHLC paths. Canonical data already contain ask OHLC and spread summaries. A fixed 0.2 spread is an assumption rather than historical cost evidence. Include adverse-spread/slippage sensitivity; do not claim actual historical quote-side execution unless implemented and verified. Commission/swap are absent from this simulator and must be reported or modelled.
6. `SimTrade.cash` is realized P&L for one initial unit, including target fractions. R must divide total net cash by the original **actual fill-to-original-SL** risk, never by the adjusted BE stop or planned entry risk. A TP1 winner can still lose money after the remaining position stops.
7. The simulator has no end-of-window liquidation API. A runner must explicitly handle/report open trades, resting orders and unresolved boundary trades; dropping them silently biases results. Monthly cohort outcomes and calendar cash realization are different statistics and must be labelled.

## Acceptance checks for research output

- Frozen source/settings, data version/cutoff, seed and candidate definitions; existing chart settings must not be silently represented by project defaults.
- Full executed-trade and order ledger (including replaced/cancelled/resting and unresolved), fills/fractions/costs, true original risk, TP1/TP2/TP3 flags and cash/R.
- Monthly grid from 2014 through the requested 2026 period: unavailable and partial coverage distinguished from a genuine zero-trade month; missed opportunities have an explicit causal candidate rule and separate hypothetical outcomes.
- A trade counts as a TP-based win when TP1 or TP2 is hit. Keep cash-positive rate, net R, average R, profit factor, realized-equity drawdown and open-position drawdown distinct.
- Sample approximately 50 cases by a frozen representative method before drawing conclusions; preserve case IDs, timestamps and charts. Mechanical MFE/MAE tables alone are not visual inspection. Misses and near-TP losses require separate records and defined horizons.
- Chronological development/validation with boundary purging or frozen carry policy; no choosing candidate settings from the reported forward fold. Include all tested variants, not only winners.
- Compare identical coverage, starting state, entry/exit assumptions and costs. Promoting strategy changes requires positive generalizing net results, reasonable RR/trade count and a successful independent execution/report audit.

## Verification artifacts

**Urgent review of Claude's 13:35 plan:** using `ask_close - bid_close` as the spread for fills earlier in that same minute is a future observation. A minute's closing spread is not known at its open and is not the spread at an intraminute target/stop. This is not historical executable bid/ask pricing. At minimum use an explicitly labelled opening-spread proxy (`ask_open - bid_open`, known at the open) plus conservative cost sensitivity, or implement native ask-side trigger/fill prices/tick replay. If the runner already uses minute-close spreads, revise that experiment before interpreting its results. Never describe a closing-spread proxy as the "real spread" of every fill.

**Runner review at first baseline launch:** `Data._series()` loads all SQLite candles without a completeness/canonical cutoff filter. Therefore the last incomplete H1/M1 candles and the uncertified latest UTC-day snapshot enter the experiment. Filter all decision and execution inputs to the common verified cutoff above. Its missing/invalid ask spread replacement uses a median from the full dataset; this fabricates missing costs and uses later observations. Reject/block missing or crossed quotes instead. The baseline run currently in progress must be treated as provisional until these issues are corrected.

The canonical M1/M5 hash/OHLC/cache audit and independent M1-to-H1 cache reconstruction completed with exit 0: **3,921,678 M1**, **785,841 M5**, **65,817 H1** eligible bars. Detailed artifacts are in `codex-overlap-2026-10-17/data-audit.json` and `monthly-data-coverage.csv`. The first audit revision accidentally reported zero H1 partitions because H1 exists as a derived cache series, not canonical partition files; the corrected audit now rebuilds every H1 candle from M1 and compares cache OHLC.

Focused existing MTF, broker and badge tests passed (**37 tests**, exit 0). New independent examples in `tests/test_overlap_execution_review.py` also passed (**12 tests**): long/short market/limit partial exits with/without BE, gap-improved entries and actual risk, gap-through-stop execution, conservative ambiguity, and a TP1 winner with **-1/3R** realized. These examples match the real broker using three units so each one-third partial can execute without integer sizing rounding. These checks establish data/execution correctness boundaries; they do not establish a profitable strategy.

The first provisional baseline ledger was independently checked with `backend/tools/codex_check_overlap_ledger.py`. Per-fill cash, original actual risk, R and TP-win arithmetic match for **469 closed trades**. The ledger audit still fails because order **2653** was filled and remains unresolved but lacks its actual entry, partial fills and realized cash in the output. Export those details for every executed trade, including open-at-end positions. The 469-trade provisional result must not be promoted while the source cutoff/spread issues above remain open. Evidence: `codex-overlap-2026-10-17/baseline-ledger-audit.json`.

## Corrected baseline review

Claude revised the runner to use the canonical cutoff, an explicitly described **opening-spread proxy**, reject missing/crossed ask prices instead of inventing a full-period median, and export unresolved fill/cash details. The earlier findings above describe the superseded first launch. The corrected ledger SHA-256 is `08117ece415c0a621b01b130a00742030e8c1af36bb94844a65b48ae9c81cfbb`.

Independent arithmetic/timing audit: **passed**, 2,612 orders, **465 closed trades**, **111 TP-based wins (23.87097%)**, closed-trade **+69.58679R**, mean **+0.149649R**, R profit factor **1.19153**, closed-trade equity drawdown **31.50423R**. One additional executed trade remains open; its realized partial profit is **+1.895006R**, with **2/3** of the quantity remaining and TP1 hit. These are historical modeled results with an opening-spread proxy and zero account-specific commission/swap, not proof of executable profitability or achievement of 70%.

`baseline-independent-monthly.csv` separately reports entry-cohort counts/outcomes and **calendar cash realization from each partial exit**. A full trade's final R must not be assigned to its entry month and called calendar realized performance. Missing months remain unavailable. Its unfilled-order count is deliberately not called missed profitable opportunities. The researcher's separate causal near-miss study must establish that label. A monthly reporting regression verifies a January entry with February partial/stop exits records its cash in February while keeping January cohort statistics.

Additional gap screening of the superseded provisional baseline found 7 closed trades crossing >72 hours with no M1 quote, including a 110-hour gap in January 2016. Most other flagged spans may be holiday closures; this screening makes no calendar classification. Preserve and flag missing periods rather than interpreting absent candles as observed safe holding time. Artifact: `baseline-large-gaps.json` (superseded provisional IDs; rerun against accepted final ledgers if needed).

Current new accounting/execution/reporting regression total: **17 passed**. The full target/improvement/chart-review/walk-forward work is still in progress with Claude; no strategy change has been accepted by this review.

## Diagnostic/selection boundary review

The first scratch `an/path.py` and `an/missed.py` revisions load the full baseline ledger and compute MFE, near-TP and missed-order outcome summaries across all years. These are selection diagnostics, not just final performance reporting. The declared development window ends in 2022: filter the inputs to that window **before** producing summaries/charts that determine candidate rules, and censor forward diagnostic horizons at its boundary. If full-period diagnostics already influenced candidate design, disclose that exposure rather than presenting 2023–2026 as an untouched current-round test. Historical walk-forward remains a retrospective evaluation with this limitation.

Those helpers define horizons by numbers of **observed M1 rows** (e.g. `60*24*40`), not wall-clock 40 days; missing/session minutes extend the calendar horizon. Label them precisely or use timestamp bounds. Near misses chosen at the closest later minute are retrospective labels only: executable early-entry variants must enter at the **first causal crossing** of a preselected buffer while the order remains valid, never at the retrospectively closest point. Replaced unfilled orders can revisit one zone, so order counts must not automatically become counts of unique missed setups.

## Independent disposition of Claude's execution-review leads

Claude's source-read review is saved as `CLAUDE_OVERLAP_EXECUTION_REVIEW_2026-10-17.md`; these are leads rather than automatically confirmed findings. **Its finding 1 is overstated:** the research runner already uses `abs(actual_entry - original_sl)`, so a gap past the stop cannot turn a negative cash result into positive R via a negative denominator. Zero/near-zero actual risk can still make R undefined/large and must remain visible rather than be silently floored or excluded. Our gap-through-stop regression verifies negative cash is preserved.

The warmup warning also needs qualification: with default ATR-based D/S detection and two-timeframe confluence, source zones cannot normally form before their own ATR warmups, so the existence of a NaN fallback alone does not establish default warmup trades. Prove an affected configured case before changing strategy behavior. Object-copy identity/history/withdrawn-list points are caller contracts already handled by the current id-keyed ledger, not evidence its trade count is wrong. Different busy predicates implement different entry modes and need an affected-mode reproducer before being labelled a defect.

Confirmed design distinction: chart fills at default 1H strategy timeframe use H1 conservative paths; research fills use M1 conservative paths. Report their disagreement, and do not imply an optimized M1 backtest is the chart's identical fill history. Both retain within-candle ordering uncertainty. An ambiguity-count lower bound and the `Strongest nearby` cache's missing close dependency merit focused reproduction; no unrelated production edits are justified solely by source suspicion.

**Cache dependency now reproduced:** one allowed long side, `entry_pick='Strongest nearby'`, ATR fixed at 5 and `pick_atr=3`. Fresh two-zone H1/H4 confluence at 89–90, stronger three-zone H1/H1/H4 confluence at 79–80, opposite target at 110–111. At close 100 only the nearer group is within the 15-point search reach; order entry is 90. Next close 94 brings the stronger group within reach. With unchanged ATR/pools the cache omits close, returns without selecting, and incorrectly retains entry 90. Clearing `touch_key` then calling with the same second bar correctly replaces entry with 80. Fix: include the close in the key whenever selection uses `Strongest nearby`, even with only one allowed side. This is a reproducible correctness fix, not a profitability parameter change. Claude owns the engine and has been sent this reproducer for a minimal patch and regression.

## Codex's independent visual spot-check

Read the actual rendered H1/context and LTF charts for development cases **46, 151, 16 and 184**, not just their numerical summaries. Case 46 shows a supply-overlap short retest followed by a sustained decline through the opposing targets, whose TP1 is about 8.35 original R away. Case 151 shows a short working into nearer opposing demand around 1205, above its TP1 at 1197.97, then reversing through the stop. This supports testing the existing `TP1 zone='Any zone'` choice rather than skipping a nearer overlapped obstacle solely to demand a single-zone target; it is a hypothesis, not a proven improvement. Case 16 buys a demand overlap during a visible downswing and is stopped shortly after entry: confluence count alone is not trend/reaction confirmation. Case 184 approaches a short limit by roughly 0.02 price units, drops, but does not hit the original TP1 before later crossing the stop; capturing that missed entry alone does not establish a winning trade.

These four spot-checks independently confirm relevant patterns and that misses include poor outcomes. They do not replace Claude's approximately 50-case review, and no pattern frequency or parameter effectiveness is inferred from four selected examples. The plots distinguish D/S overlaps and opposing D/S groups; no OB/FVG/liquidity detector is assumed from their rectangles.

## Controlled execution-resolution comparison

`backend/tools/codex_compare_overlap_resolution.py` replayed the same H1 decision engine using H1 paths versus M1 paths, holding costs fixed at 0.2 bid spread, zero slippage/fees, common verified cutoff and extended 2015 history. Both emitted **2,581 identical order plans** and **459 closed trades**, but **8 shared orders had different execution/cash fields**. Thus H1 and M1 execution are close on this controlled example but not identical. This controls execution resolution only; it is not a profitability optimization or a comparison of native historical bid/ask execution.

Source hash stayed `832a4c7f815f8bbf76ad69b314780d403e6eb144b4fbfca263a62778bcaf3fb6` throughout the successful comparison. Full details: `codex-overlap-2026-10-17/execution-resolution-comparison.json`. The first attempt failed because the concurrently extended research serializer required an `ahead` field; the comparison adapter was corrected and the complete run then finished successfully.

## Verified implementation and visual-review status

- Claude applied the minimal cache-dependency fix and added `test_strongest_nearby_order_is_redecided_when_only_the_close_changes`. Codex independently ran it: passed. Claude's uncached full-history audit has a **byte-equivalent parsed row ledger** to the cached baseline (2,612 orders), so this constant-ATR edge-case correction does not manufacture an improved historical baseline.
- New data/cost boundary tests also pass: verified cutoff, opening-spread rather than closing-spread baseline, and rejection of missing ask candles.
- Full backend suite independently completed with exit 0: **189 passed**. Existing Starlette/httpx deprecation warning only. Saved output: `codex-overlap-backend-tests.log` beside this document.
- Only `app/scripts/mtf_confluence/engine.py` differed from the 20-file source freeze at this check; all original SMC Master files still matched the freeze.
- Claude actually opened **all 50 distinct sampled chart PNGs** in its research session: 15 winners, 15 other losses, 10 near-TP losses and 10 unfilled-near-entry orders. This is verified from its image-read tool records, not inferred merely from the existence of 50 generated images. Codex independently spot-checked 8 of them. The sample is deliberately stratified, not a random estimate of the strategy's win rate.
