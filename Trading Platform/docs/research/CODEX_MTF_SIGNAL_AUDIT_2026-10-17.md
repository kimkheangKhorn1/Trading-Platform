# Current MTF signal producer audit — 2026-10-17

Read-only audit of the new `mtf_confluence` producer inside SmcMaster. Production source was not edited. The reviewed six strategy files matched the frozen snapshot again at completion. This report assesses correctness and design against the current implementation and the user's latest recorded requirements; it does not establish profitability or original TradingView runtime parity.

## Findings

### 1. P1 — Live touch processing waits for candle confirmation

Source: `backend/app/scripts/mtf_confluence/chart.py:46–48`.

`MtfFeature.on_bar` immediately returns for every incomplete candle. This bypasses not only new zone detection but also the replay of an already-existing resting order. Consequently, the feature's displayed fill/result state does not update when the forming candle reaches an entry. It updates when that chart candle becomes confirmed. On M5 this can delay visibility until the five-minute candle closes.

The historical replay can subsequently assign the entry to the touching candle at the correct resting level; that does not demonstrate a live touch event was produced at the moment of contact. The strategy emits limit-order intents and has no tick-based touch processing in this feature. An independently deployed real broker order could fill intrabar, but that is separate from what this chart feature implements.

Check: an existing resting buy at 100 remains resting after the feature is called with `is_confirmed=False`. Processing the corresponding crossing candle (open 102, high 103, low 99, close 101) through its TradeBook opens the trade. This confirms the feature gate prevents that update during the forming candle.

Why it matters: the user's latest instructions explicitly request entry on touching the most-overlap area without waiting for candle close. Historical intrabar simulation and live behavior must be described and implemented separately. A safe correction keeps HTF zone formation close-confirmed while tracking touches against already-confirmed zones during live updates; rollback and duplicate-event handling must be addressed.

### 2. P2 — Order-selection cache ignores inputs that change its decision

Source: `backend/app/scripts/mtf_confluence/engine.py:428–441`.

The early-return key contains zone-pool versions, allowed sides, trade changes and optional age/time state. It omits current price and ATR-based near-zone tolerance. Those omitted values are used immediately below to decide which clusters qualify and which side is nearest.

Two deterministic reproductions passed against the frozen production implementation:

* **Missed newly qualifying confluence, including with the default nonzero tolerance:** fresh demand zones 95–96 (M5) and 96.2–97 (M15), price 100. Warmed ATR gives tolerance approximately 0.02, so no cluster qualifies. A subsequent candle expands ATR and tolerance to approximately 0.3114, making one two-timeframe cluster qualify. No zones were touched or added and allowed sides did not change. Normal `feed` still produces no order. Clearing only the cache and rerunning selection produces a buy at 96.2. The engine was warmed with real `feed` calls; zones were injected as controlled fresh-zone fixtures, not discovered from market candles.
* **Stale nearest side when both sides are permitted:** fresh buy confluence at 90–91 and sell confluence at 109–110. At price 100 the buy is selected on the distance tie. Price moves to 107 without touching either cluster. The producer keeps the buy 16 points away while the sell is only 2 points away. Clearing only the cache selects the sell. This case uses MA Off; it also concerns configurations where the MA filter permits both sides.

The implementation intentionally avoids recalculation on ordinary price changes, as its docstring explains. The second case is therefore an inconsistency in the design promise of the "nearest" zone. The ATR case is a missed-dependency correctness defect: eligibility itself changes, even without an explicit new zone event.

Suggested correction: recompute candidate eligibility when effective tolerance changes and re-evaluate nearest-side selection when both sides are allowed. Preserve already-triggered order holding rules and avoid cancelling/replacing an order when its effective geometry is unchanged.

### 3. P2 — Entry drawings keep the planned price after a different actual fill

Source: `backend/app/scripts/mtf_confluence/chart.py:177–186`; reference-level drawing at `chart.py:139–166`.

On fill, the chart changes line style and start time but leaves the entry line's price and entry label at `sig.entry` / `d['entry']`. It does not apply `tr.entry_price`.

Reproduction: a market buy referenced at 100 fills at 100.2 with the configured 0.2 spread. After `_update`, the entry line remains 100 and the text remains `BUY 100.000`. Market-entry gaps and price-improved limit fills can create larger differences. This is a chart presentation defect; it is not evidence that the broker's P&L uses the wrong entry.

Suggested correction: distinguish planned order level from executed entry; on fill, use actual execution price for the executed-entry line and label, while retaining the plan in the tooltip. Keep stop and target levels at their actual order prices.

## Design details that must not be mistaken for other indicator systems

* The strategy uses the indicator's **Demand/Supply** detection. It is not a combined OB, FVG, pivot S/R or liquidity-sweep strategy. Overlapping D/S boxes are not independent confirmation from those other systems.
* `CHoCH confirmation` here means a close beyond a stored rolling high/low around the deepest touch (`engine.py:220–241`). It does not consume the indicator's confirmed internal/swing SMC CHoCH events. This may be a valid alternative trigger, but should be named as a lookback breakout if users expect the indicator's actual SMC structure logic.
* Default `gap_atr=0.1` intentionally lets separated zones count as overlap. `zones.py:217–224` can place the "core" in the empty gap between them. This is documented behavior, not a hidden numerical defect. If the intended entry must lie in a genuine shared price band, use zero tolerance and test that explicit specification.
* Near-edge entry is consistent with the latest instruction to stop waiting for 50%. An earlier 50% request was superseded. The instruction to require at least two targets was also withdrawn. Neither is reported as a bug.

## WIN label is intentional, but is not a profit measure

The recorded instruction explicitly says any TP hit counts as a win and a stop before any TP counts as a loss. The code follows that instruction (`sim.py:63–67`, `chart.py:201–203`). It would be incorrect to report the convention itself as an implementation bug.

A reproduced example with equal thirds, entry 100, SL 90 and targets 110/120/130 hits TP1 and then SL. It is labelled WIN, but realised P&L is approximately **−3.3333 per original one-unit position**, or **−0.3333 initial R**, before any additional fees. Use net-P&L win rate and expectancy alongside TP-hit rate when judging improvement. The Strategy Tester metrics module already calculates separate P&L `win_rate` and `tp_win_rate`; keep them clearly distinguished in the UI.

## Verification and limits

* Frozen-copy command: `backend/.venv/bin/python -m pytest -o addopts= -q tests/test_mtf_confluence.py` — **22 passed in 14.78 seconds**. Executed with the workspace virtual environment against the frozen backend.
* Five targeted audit probes completed: ATR cache, nearest-side cache, intentional TP-hit convention, incomplete-bar guard, and executed-entry drawing. The two cache probes drive actual engine feeds with controlled fresh-zone fixtures; the chart probes use minimal contexts to isolate the relevant update paths.
* Reproduction script: `/tmp/tp-signal-audit-1792186743/backend/audit_probes.py` (temporary local evidence). Frozen backend and manifest: `/tmp/tp-signal-audit-1792186743/`.
* Existing tests passing does not refute these reproductions: candidate-cache dependencies, executed-entry coordinates and the real-time forming-candle contract are not established by broker-fill parity alone.
* This audit did not run another multi-year performance optimization, a live broker test, or a tick-by-tick UI session. Synthetic reproductions establish the specific defects; they do not quantify their frequency or profit impact on historical market data.
* No strategy source, indicator rules, SL rules or training settings were changed.

## Reviewed strategy hashes (SHA-256)

* `backend/app/scripts/mtf_confluence/sim.py`: `c6427f68644eae27b7bcfe3f0e4be746a9339f6556a84276a24aacb41b2228c4`
* `backend/app/scripts/mtf_confluence/__init__.py`: `033f908222c3bd8fe05a810ab5237f28571fb7ccea3e57f96685ba54d3eea768`
* `backend/app/scripts/mtf_confluence/chart.py`: `9827dd6ccab4a395d6fce9f6d91aad40dae8b76d30d275340fe3dd9f2237b76a`
* `backend/app/scripts/mtf_confluence/engine.py`: `eee9f64b6f2d8ddc1fbc285ab5cebc1799c4d928102da24662c355b103b000b2`
* `backend/app/scripts/mtf_confluence/inputs.py`: `b4d6e749343b0a455e612649af96d90097283e6b407bbbe8bfa965b4707c17e2`
* `backend/app/scripts/mtf_confluence/zones.py`: `cdf83050be9ba698c56ce0ce98e45dcd2d4f69bfb06e7d6d62f08cfaf0670ce3`

## Follow-up

The confirmed findings were addressed in the [fix and verification report](CODEX_MTF_SIGNAL_FIXES_2026-10-17.md). This audit records the earlier source version.
