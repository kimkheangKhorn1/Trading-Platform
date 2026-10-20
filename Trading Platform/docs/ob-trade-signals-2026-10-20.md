# Order block trade signals (5m) — 2026-10-20

BUY / SELL signals at the SMC indicator's **order blocks** on the 5m chart, alongside the D/S overlap signals.
They do not use overlaps: each order block of each selected timeframe is traded on its own. The legend has an
**● OB Trade** button next to the D/S overlap pill, and its ⌄ panel holds the settings. No backtest was run.

## Settings (OB Trade panel)

- **Order blocks from:** 1m, 5m, 15m, 30m, 1H, 4H (any mix). The default is 15m, 30m and 1H.
- **5m confirmation candles:** 1 to 5 (the backend accepts up to 10).
- **Order block type:** Internal and / or Swing. Both are always built, whatever the chart's OB toggles say.
- **Show waiting order blocks:** dashed boxes labelled `OB 1H` and so on. The 2 nearest per timeframe and side
  are drawn, within the indicator's distance filter.

## Rules

- **Blocks:** the indicator's own structure and order block code (`OrderBlockSource`) runs on each selected
  timeframe with the chart's settings. A block can be traded from the close of the candle that created it until the
  indicator removes it on its own timeframe (closed through, or pushed out of its 100-block list).
- **Confirmation:** a *confirmation* candle closes in the reversal direction and beyond the block's near edge: bullish
  and above the top of a bullish block, or bearish and below the bottom of a bearish block. The first confirmation
  candle must touch the block, or come right after a candle that touched it. N confirmations in a row give the
  signal at the close of the last one. A candle in between that does not confirm restarts the count. A 5m close
  beyond the far edge breaks the block. Each block gives at most one signal.
- **Trade:** the same rule as the D/S overlaps (`overlap_signals`). Entry is at the signal close. The SL is $3
  beyond the deepest shadow of the confirmation candles; the $3 is cut so the stop is never over $15, and a shadow
  over $15 on its own means no trade. TP1 / TP2 / TP3 = 1R / 2R / 3R. The SL never moves: SL first = −1R, and each
  TP reached = +1R. When one candle touches both the SL and a TP, its 1m candles decide which came first. Only one
  OB trade is open at a time; a D/S trade can be open at the same time.
- When blocks from several timeframes signal on the same candle, one opens and the rest are skipped. The chart draws
  them once, for example `OB BUY · 15m+1H`.
- **Replay dashboard** (`❄ ORDER BLOCK SIGNALS`, below the D/S one): this counts the trades signalled after the
  replay start bar and adds a "By OB TF" row (net R and closed trades per source timeframe). A replay's record does
  not depend on the cache: the OB runners and the 5m scan start a fixed number of candles before the start bar.

## Where

| Part | File |
|---|---|
| Order block birth / removal log | `backend/app/scripts/smc_master/ob_source.py` |
| Confirmation scan, waiting blocks | `backend/app/ob_signals.py` |
| Runners per timeframe, `POST /api/ob-signals` | `BadgeOverlapService.order_blocks` / `ob_signals` in `backend/app/badge_overlap.py`, `backend/app/main.py` |
| Labels, plans, dashboard (shared with D/S via `SignalKind`) | `frontend/src/chart/overlapSignals.ts` (`OB_SIGNALS`) |
| Request hook, legend control, zones | `frontend/src/chart/useObSignals.ts`, `frontend/src/chart/ObTrade.tsx`, `frontend/src/api/obSignals.ts` |
| Tests | `backend/tests/test_ob_signals.py`, `frontend/src/chart/overlapSignals.test.ts` |

Speed on a copy of the database (Exness XAUUSDm, 15m + 30m + 1H): about 0.2 s for the first request and about 7 ms
per replay step. Checked in the browser with live and replay views. Chart candles, no spread: a record, not a backtest.
