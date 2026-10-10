# XAUUSD Trading Platform (local)

A local, TradingView-style charting and backtesting environment for XAUUSD. Indicators run in a
Python engine (FastAPI backend) instead of Pine Script, so large indicators are not bound by
TradingView's compile, memory or runtime limits. The frontend is React + TypeScript with TradingView
Lightweight Charts.

The original Pine file at the repository root is kept unchanged as the reference for the conversion.

---

## What works today

| Area | Status | How it was checked |
|---|---|---|
| Dark TradingView-style UI: candles, volume, crosshair, OHLC legend, 1m–1D timeframes, timezone selector, go-to-date | Done | Browser walkthrough |
| CSV import (auto-detects layouts, timezone option, rejects bad rows, builds 5m…1D from 1m) | Done | Unit tests + UI import of 130,346 real 1m candles |
| OANDA v20 history (paging, caching in SQLite, incremental sync, rate limiting, retries) | Done, **not yet run against the real OANDA API** (no credentials were available) | Unit tests against a fake v20 endpoint; browser test with a local OANDA replay server |
| Live candles (WebSocket push, forming candle, reconnect/backoff, status pill) | Done, same caveat as above | Automated test + browser outage/recovery test via the replay server |
| Lazy history loading when scrolling left | Done | Browser |
| Indicator engine (bar-by-bar Pine model, incremental + live-bar rollback, `request.security` emulation) | Done | 95 backend tests incl. incremental = full and no-lookahead tests |
| Indicator list, settings dialog (Inputs / Style / Visibility), live recalculation, Cancel reverts | Done | Browser |
| Drawing layer: boxes, lines (solid/dashed/dotted, extend), labels, shapes/arrows/triangles, tooltips | Done | Browser |
| Strategy Tester: date range, run/rerun in a worker process, Overview, Performance Summary, List of Trades, cancelled orders, Properties | Done | Broker and metrics unit tests; the end-to-end backtest tests were removed with the EMA Cross example, so no strategy script exists to run until the Pine conversion emits signals |
| Trade inspector (click a trade → chart jumps there, loads older history if needed, shows entry/SL/TP levels and fills) | Done | Browser |
| Bar replay (pick a bar, play/step, speed; indicators are recomputed only up to the cursor) | Done | Browser, checked that nothing after the cursor is shown |
| Conversion of your Pine indicator | In progress (see below) | — |
| Drawing tools (trend line, rectangle, …) | Not started | — |

### SMC & ICT & Indicators + Scalping Master (Pine conversion)

- **SMC & ICT & Indicators + Scalping Master**: a Python port of the Pine v5 source at the repo root
  (`backend/app/scripts/smc_master/`; the `.pine` file stays the reference). Script id
  `smc_ict_scalping_master`, all 210 Pine inputs. It covers internal/swing BOS & CHoCH, HH/LH/HL/LL,
  strong/weak highs and lows, order blocks, EQH/EQL, FVGs (incl. a higher-timeframe option), previous
  day/week/month high-low, premium/discount, demand/supply zones, pivot S/R with breakout/false-break
  markers, candle patterns, BULB, the direction dashboard, the SMA/ATR envelope and trend-colored
  candles. It is also the **SMC Master** strategy (see below), but has no entry rules yet; its alert
  conditions are computed but not delivered anywhere yet.
- **Verified:** runs on the Dukascopy feed on every chart timeframe (1m–1D). Previous D/W/M highs and
  lows match values computed independently from the daily candles. Live updates and replay steps
  produce the same output as a fresh full run (`backend/tests/test_smc_master.py`).
- **Pivot S/R display:** as in the Pine source, a confirmed close through a zone splits its box at that
  candle: the part before the break keeps the old role's color and the zone continues from the break in
  the new role's color (close below support: blue, then yellow; close above resistance: yellow, then
  blue). History therefore shows what the zone was at the time. Wick-only penetrations and
  still-forming candles do not change its role. Break/reclaim markers keep their existing rules.
- **Tested on real XAUUSD (Dukascopy, 1H/4H, Jan 2025 – Sep 2026):** each detection was scored by what
  price did next, against the same zone or break moved to an arbitrary nearby price. Settings were chosen
  on 2025 and only kept if they also held on 2026. Order blocks beat random levels in every period and
  timeframe. Two defaults changed (see `TUNED_DEFAULTS` in `metadata.py`): pivot S/R zones are 1 ATR
  wide (was 0.5) and demand/supply zones need a 1.5 ATR departure (was 2.0); both roughly doubled how much
  better than random the zones held. Internal structure, swing length, pivot lookbacks and BULB showed
  no consistent improvement in either direction, so they keep the Pine defaults. BULB defaults to
  Fixed (non-repainting) mode. Event history, plots and markers before any bar are identical with or
  without later data (no repainting or lookahead).
- **Not verified:** no TradingView-exported comparison data exists yet, so bar-by-bar parity with the
  Pine original (structure, zones, patterns, dashboard values) is unconfirmed. Weekly/monthly candles
  are built from the feed's daily candles (week opens Sunday 17:00 New York), which can differ from
  TradingView's session for the same symbol.

The script is both the chart indicator and a strategy: it declares the **SMC Master** strategy, so the
Strategy Tester lists it and can run it. It has no entry rules yet, so a backtest currently reports 0
trades with a note saying no signals were emitted. Add the rules in `SmcMaster.strategy()` in
`backend/app/scripts/smc_master/indicator.py` by calling `ctx.signal('smc_master', ...)`; the method's
docstring has a worked example (`tests/test_smc_master.py` checks that such signals are traded).

---

## Requirements

- macOS / Linux, Python 3.12+ (tested with 3.14.4), Node.js 20+ (tested with 26).
- Optional: an OANDA account and API token for live and historical XAU_USD candles.

## Install and start

```bash
cd "Trading Platform"
./start.sh            # first run creates backend/.venv and installs npm packages
```

Then open **http://127.0.0.1:5173**. The API runs on **8010** (docs at http://127.0.0.1:8010/docs).
Port 8000 is avoided on purpose because another project on this machine already uses it.

Other modes:

```bash
./start.sh prod       # builds the frontend; FastAPI serves everything at http://127.0.0.1:8010
./start.sh test       # backend pytest + frontend type-check + vitest
BACKEND_PORT=8020 FRONTEND_PORT=5174 ./start.sh   # if the default ports are busy
```

Manual start, if you prefer:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8010
cd ../frontend && npm install && npx vite          # proxies /api and /ws to :8010
```

## Market data

### OANDA (live + history)

```bash
cp backend/.env.example backend/.env    # then set OANDA_API_TOKEN (and optionally OANDA_ACCOUNT_ID)
```

`OANDA_ENVIRONMENT` is `practice` or `live`; `OANDA_PRICE_COMPONENT` defaults to `M` (mid prices).
Restart the backend afterwards. The token is only read from the environment and never sent to the
browser.

- History is downloaded on demand while you scroll left, and cached in `backend/data/market.db`. For
  deep history use **Data → OANDA → Download … history back to** (runs in the background with
  progress). How far back data goes depends on OANDA's retention and your account; when OANDA returns
  no older candles the series is marked complete and is not requested again.
- Each timeframe is fetched natively from OANDA with `dailyAlignment=17`, `alignmentTimezone=America/New_York`
  (the same session boundaries TradingView uses for FX/metals).
- The live candle is polled every `LIVE_POLL_SECONDS` (default 2 s) for every open chart and pushed over
  WebSocket. The still-forming candle is stored with `complete=0` and replaced when OANDA closes it.
  Indicators show it provisionally; backtests never use it.
- OANDA's market-data terms apply. This app is for personal, local use and does not redistribute data.

### CSV import (works without OANDA)

**Data → Import CSV**. Accepted layouts: header rows with time (or date + time), open, high, low, close
and optional volume; MT4/MT5 exports (`<DATE> <TIME> …`, `2024.01.02,00:00,…`); HistData
(`20240102 000000;…`); Unix seconds/milliseconds. Choose the timestamp timezone (UTC, broker server
time "New York + 7h", fixed offsets, or an IANA zone) and whether prices are bid, mid or ask.
Rows with inconsistent OHLC (e.g. high below close), unparsable rows, duplicates and candles not on
the timeframe grid are **rejected and reported, never repaired**. Higher timeframes are built from the
imported candles.

**Already in the database:** `XAUUSD · CSV dukascopy (bid)`, 130,346 real 1m candles from
2026-06-01 to 2026-10-09 (Dukascopy public feed, bid prices). It was imported during development to
test the CSV path with real prices. Delete it under **Data → Stored feeds** if you don't want it.

## Using the app

- **Indicators**: the `ƒx Indicators` button adds scripts. Hover the indicator's legend row for
  show/hide, settings and remove. Settings changes apply immediately; **Cancel** restores the previous
  values; **Defaults** resets to the script defaults.
- **Strategy Tester** (bottom panel): pick strategies, dates (UTC) or a quick range (1W…All), and the
  timeframe, then **Run backtest**. The tester runs the strategies of the indicator on the chart, using
  that indicator's current inputs. Execution assumptions are on the **Properties** tab. Results are
  stored, so the last run is shown again after a reload.
- **Trade inspector**: click a row in *List of Trades* (↑/↓ keys step through trades) or click a trade
  marker on the chart.
- **Replay**: click **Replay**, then click a bar. Use play/pause, step and speed. **Exit replay** returns
  to real time.

## Backtest execution rules

These rules are always applied and are covered by `backend/tests/test_broker.py`:

1. Signals are taken on the close of confirmed bars only. Orders become active on the **next** bar; nothing
   fills on the signal bar.
2. Candles are converted to bid/ask with the configured spread (the candle basis is mid, bid or ask).
   Buys fill at the ask, sells at the bid. Stop and limit triggers are checked on the side that would
   execute them, so a long stop triggers when the bid reaches it.
3. Market orders fill at the next open, plus adverse slippage. A stop that is gapped through fills at
   the worse open price, plus slippage. Limit orders fill at their price and only get a better price
   on a gap open. A resting order that is gapped through still fills, and its stop is then checked at
   that same open, so gaps never hide a loss.
4. **Intrabar ambiguity:** each bar is modelled as a price path. *Conservative* (the default) simulates
   both O-H-L-C and O-L-H-C and keeps the worse result for that strategy. *TradingView heuristic*
   assumes O-H-L-C when the open is nearer the high. Inside a bar, orders fire in the order the path
   reaches them: TP1 → stop to breakeven → breakeven hit can all happen in one bar. Trades whose
   outcome depended on this assumption are flagged ⚠ and counted.
5. Pending limit/stop entries expire after `expiry_bars`. A strategy can also cancel them, a newer
   signal can replace them, and they are invalidated when price trades through the stop-loss before
   the entry fills.
6. Position size is set when the entry fills: fixed units, fixed lots, % risk of the strategy's
   realised equity, or fixed cash risk. Sizes are rounded **down**, and orders below the minimum size
   are cancelled, not rounded up.
7. Bars before the start date are only indicator warm-up; only signals inside the window are traded.
   At the end of the window, pending orders are cancelled and open trades are closed at the last close
   (marked `END`).
8. Each strategy keeps its own book (equity, positions, statistics). *Combined* is the sum of their P&L.
9. Max drawdown is measured on bar-close mark-to-market equity, which includes open positions. Profit
   factor is shown as "—" when there are no losing trades.

## Indicator engine (for the Pine conversion)

- `backend/app/engine/script.py`: a script implements `on_bar(ctx)`. It is executed oldest to newest,
  and `ctx` only exposes the current and past bars (`ctx.hist('close', 1)` is Pine's `close[1]`). There
  is no API to read later bars.
- `runner.py`: complete bars are committed. A still-forming bar is processed on a snapshot and rolled
  back on the next update, which is Pine's realtime model. Running all bars at once and running them
  incrementally give identical output (tested).
- `mtf.py`: emulates `request.security`. With `lookahead=False`, a higher-timeframe value becomes
  visible only once that HTF bar has closed. `lookahead=True` with offset 0 (future leak) is refused
  unless explicitly allowed for reproducing a repainting Pine script.
- `ta.py`: Pine-style `sma/ema/rma/atr/tr/highest/lowest/crossover`. Conventions the Pine manual does not
  pin down are marked `VERIFY` and must be confirmed against TradingView output.
- Outputs: plots, plotshape markers, Pine-style box/line/label objects with `created_at` (when the
  information became known) and Pine's object limits, plus trade intents
  (`ctx.signal(strategy, side, order_type, stop_loss, take_profits=[(price, fraction)…], entry, expiry_bars, breakeven_after_tp1, …)`).
- To check parity with TradingView, export the chart data with the indicator plotted from TradingView
  (same symbol/feed and timeframe), then compare bar by bar. That comparison is the next step for the
  SMC conversion.

Chart labels and plotshape markers share a screen-space collision layout across visible indicators
and neighbouring candles. It measures text (including multiple lines), glyphs and label pointers on
each repaint so zooming, scrolling and resizing recalculate spacing. Moved annotations have dotted
connectors to their original anchors, and tooltips follow the displayed annotation. Labels and markers
render above chart lines and the crosshair; connectors are clipped around every annotation and painted
before the label bodies/text so they cannot run through BULB buy/sell or other label text. This affects
presentation only; indicator detections, time/price values and zone geometry remain unchanged.
If annotations exceed the pane's available space, some can extend outside the pane; zoom in to
inspect them individually.

## Project layout

```
backend/
  app/main.py                FastAPI routes, WebSocket, serves frontend/dist in prod mode
  app/config.py              settings from environment / backend/.env
  app/market/                timeframes & session alignment, SQLite store, CSV import, resampling,
                             OANDA client + sync, live hub, data providers
  app/engine/                bars, inputs, drawings, signals, TA, script model, runner, MTF, registry
  app/scripts/               smc_master/ (Pine conversion)
  app/backtest/              execution config, broker, metrics, backtest engine, job service
  app/indicator_service.py   cached incremental indicator runs for the chart
  tests/                     pytest suite
frontend/src/
  api/                       REST + WebSocket clients and types
  chart/                     chart controller, drawing overlay (custom primitive), legend, replay
  components/                top bar, dialogs (indicators, settings, data), Strategy Tester
  state/                     zustand stores (app, indicators, tester, replay)
start.sh                     install / start / test helper
```

## Tests

```bash
./start.sh test
```

- **Backend (111 tests):** timeframe and session alignment (incl. DST), resampling, CSV formats and
  rejections, OANDA paging/retries/rate limits and the forming candle, live hub polling/backoff/
  recovery, TA functions, `request.security` timing, incremental = full runs, live-bar rollback,
  no-lookahead prefix invariance, broker execution rules, metrics, backtest determinism and
  window eligibility, API integration (import → candles → indicator → background backtest → WebSocket),
  and the SMC conversion.
- **Frontend:** strict TypeScript, oxlint and vitest (time-to-bar mapping, annotation collision layout,
  label/marker rendering across layers and moved tooltip regions).

## Known limitations / next steps

- The OANDA integration has not been run with a real token yet. Do that first once you have
  credentials: watch the status pill and **Data → Stored feeds**.
- Pine parity for the SMC conversion is unverified until TradingView-exported data is compared.
- Intrabar resolution uses 4-point OHLC paths. Resolving ambiguous bars with 1m data is the planned
  next accuracy step.
- Spread is a fixed value. OANDA bid/ask candles could supply a per-bar spread.
- Drawing tools, alerts delivery and multi-symbol support are not built.
- The chart computes indicators from the first loaded bar (minus warm-up). Path-dependent indicators
  can therefore differ slightly from a backtest that starts at another date, just as on TradingView
  with different amounts of history.

## Troubleshooting

- **"Cannot reach the backend"**: start it (`./start.sh`), or check that port 8010 is free (`lsof -i :8010`).
- **Port busy**: `BACKEND_PORT=8020 ./start.sh`.
- **Timestamps look shifted after a CSV import**: delete the dataset under **Data → Stored feeds**, then
  re-import with the correct *Timestamps in* option. Re-importing into the same dataset without deleting
  it would merge the shifted and corrected candles.
