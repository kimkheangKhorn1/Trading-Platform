# Overlap reversal signals (5m) — 2026-10-18

BUY / SELL signals at the SMC indicator's locked **Demand / Supply** overlaps. They are drawn like
REAPER [JOAT]'s RAID signals and trade plan, and come with a win / loss / R dashboard.
The D/S overlap pill has a **Trade** button in the chart legend, plus a matching switch in its panel (on by default).

**2026-10-20:** the order block (OB) overlap layer was deleted at the user's request: its legend pill and Trade
button, its signals, the ★OB / ★D/S confluence marks, and its backend source (`ob_source.py`, `kind`, `ob_types`,
`fresh_only`, `confluence_ob` / `confluence_ds`). D/S overlaps, their locks and their signals are unchanged.
The sections below that mention order blocks describe the removed layer.
No backtest was run: the user asked for the logic and the UI only.

## Rules

- **Signal (5m chart only):** the first confirmed candle reaction at a touched overlap lock is a
  *rejection*. That means one of the enabled candlestick patterns, bullish at demand or bearish at supply,
  closing beyond the band in the reversal direction. This is the same detector as the ↶ reaction symbols
  (`overlap_reactions.py`). If the first confirmed reaction is a break-through (➜), that lock gives no signal.
- **Entry:** the close of the signal candle.
- **SL (final rule, same day):** the signal candle's shadow end (low for a BUY, high for a SELL), moved a further
  $3 away (`SIGNAL_BUFFER` / `signal_buffer`). The stop (entry to SL) may not exceed $15
  (`SIGNAL_MAX_RISK` / `signal_max_risk`), so the $3 shrinks to fit: a $14 shadow gets +$1 and a $15 shadow gets
  +$0. A shadow longer than $15 on its own means **no trade**: the label shows `SL>$15` and is dimmed, and the
  one-trade slot stays free. **TP1 / TP2 / TP3** = 1R / 2R / 3R of that stop.
  (Earlier versions used a fixed $10 stop, then wick + $5, then body + $5.)
- **Management (changed the same day, user's rule):** the SL never moves. SL before any TP = −1R (loss). Each
  TP reached = +1R: SL after TP1 = +1R, SL after TP2 = +2R, TP3 = +3R (the trade ends). Only candles after the
  entry candle count. When one 5m candle touches the SL and a TP not reached yet, its 1-minute candles decide
  which came first; if both fall in the same minute (or there is no 1m data), the SL counts first.
  (The first version moved the SL to TP1 / TP2 like JOAT. A pullback to TP2 then closed a trade at +2R before
  price ran on to TP3.)
- **One trade at a time.** A signal that arrives while a trade is open still gets a dimmed label, but it is
  marked *skipped* and opens nothing.
- **Confluence (★):** when the other overlap layer is on, a signal is marked ★OB (D/S signal) or ★D/S (order
  block signal) when a fresh lock of the other layer, same side, sits on the same price. "Fresh" means that lock
  existed by the signal's close and was still untouched when the signal's own overlap was locked. Nothing is
  filtered out. The request fields are `confluence_ob` and `confluence_ds`, using the other layer's own settings.
- **Per layer:** one trade at a time per layer, so a D/S trade and an order block trade can be open together. The
  replay dashboard combines the layers that trade, with W / L / R per layer and with / without confluence.
- **Same candle on both layers:** both layers then open the same trade (same entry, SL and TPs). The chart draws
  it once, with one label (`D/S+OB BUY · …`) and one trade plan. The dashboard totals count it once; the
  per-layer rows still count it for each layer. Reaction symbols (↶) that a signal label repeats, or that both
  layers draw on the same candle, are dropped.
- The record uses chart candles with no spread. It shows what happened; it is not a backtest.

## Where

| Part | File |
|---|---|
| Trade logic | `backend/app/overlap_signals.py` |
| Every touched lock, including ones far from price (`signal_zones`) | `backend/app/locked_overlaps.py` |
| `signals` / `signal_risk` request fields on `/api/overlap-reactions` and `/api/badge-overlaps` | `backend/app/badge_overlap.py` |
| Labels, entry / SL / TP lines and fills, dashboard table (replay only: trades signalled after the replay start bar) | `frontend/src/chart/overlapSignals.ts` |
| ● Trade button in the legend and the "Trade D/S overlaps (5m)" switch in the overlap panel | `frontend/src/chart/BadgeOverlap.tsx` |
| Tests | `backend/tests/test_overlap_signals.py`, `frontend/src/chart/overlapSignals.test.ts` |

## Limits

- Signals exist only for locks the app has recorded. Locks are created while the chart or replay clock moves.
- The dashboard shows only in replay. It counts the signals after the replay start bar, within the loaded 5m
  history (about 4,000 candles). The Position row follows the chart, so it also shows a trade opened before the start bar.

Checked in the browser with replay on Exness XAUUSDm, 2026-10-13/14, before order blocks became
confluence-only. A D/S BUY (Bull Eng) at 22:40 entered at 4161.53 with its SL at 4151.53 and closed at −1R.
After the change, a dry run of the service on a copy of the database (13 Oct) found 4 D/S trades and 1
skipped signal. Two of the trades were marked ★OB: 03:05 short, −1R, and 08:00 short, +1R. OB requests
returned no signals.

## Speed (replay)

The work per replay step was cut. All timings are on a copy of the database, 5m chart, all six source
timeframes, both overlap layers:

| Per 5m step | Before | After |
|---|---|---|
| D/S + OB overlap scans, reactions and signals (4 requests) | ~160–190 ms | ~21 ms |
| SMC indicator run | ~50 ms | ~28 ms |

- Source runners read only the candles after their last committed one (`source_bars(since=…)`), not
  ~4,000 candles per timeframe on every step.
- Candle patterns for reactions and signals sit in a `PatternTape` that is extended with new candles. A rewind
  reads a prefix of it, so its patterns are not recomputed over the whole history.
- The first 1m touch of each order block is remembered, together with how far the data was checked. The
  "untouched" test then reads only the new minutes.
- Drawings and D/S zones have their own deep copy, and drawing JSON skips `dataclasses.asdict`. These two
  dominated runner snapshots and last-bar views.

Equivalence was checked on real data. Over 150 replay steps (forward, rewind, forward again), the new path and
the old one (full window, no caches) gave identical results. Tests: `backend/tests/test_overlap_speed.py`.
