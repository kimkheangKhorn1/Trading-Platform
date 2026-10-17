"""Simulated broker: bracket orders (entry + SL + TP1/TP2...) per strategy, bar by bar.

Execution rules (all deliberate, all tested in ``tests/test_broker.py``):

1. Timing - an intent generated on the close of bar *i* can first execute on bar *i+1*. Market
   orders fill at bar *i+1*'s open. Nothing ever fills on the bar that generated the signal.
2. Bid/ask - candle prices are converted to bid and ask with the configured spread
   (``price_basis`` says whether candles are mid, bid or ask). Buys execute at the ask and sells at
   the bid; stop/limit triggers are checked against the side that would execute them, so a long
   stop-loss triggers when the *bid* reaches it and a short stop-loss when the *ask* reaches it.
3. Fill prices - market and stop fills get adverse slippage. A stop that is gapped through fills at
   the (worse) open price, not at its level. Limit orders fill at their price; only a gap open
   through the price fills at the (better) open, as a resting order would.
4. Intrabar path - each bar is modelled as a path through its prices: O-H-L-C or O-L-H-C. Orders are
   triggered in the order the path reaches them, so SL/TP sequencing, TP1 -> move stop to breakeven
   -> BE hit within the same bar, and fills of orders created during the bar are all handled
   explicitly. Under the default ``conservative`` policy both paths are simulated and the outcome
   with the lower end-of-bar equity is kept; trades whose result depended on that choice are flagged
   ``ambiguous``. ``ohlc_heuristic`` reproduces TradingView's broker-emulator assumption.
5. Pending orders - limit/stop entries expire after ``expiry_bars`` bars, can be cancelled by the
   strategy, are replaced by newer signals of the same strategy (``replace_pending``) and, when
   ``cancel_if_sl_before_entry`` is set, are cancelled if price trades through the stop-loss level
   before the entry fills. A resting order that is gapped through still fills (and its stop is then
   evaluated at that same open) - gaps are never used to dodge a loss.
6. Sizing happens at fill time from the strategy's realised equity; sizes are rounded down to
   ``unit_step`` and orders smaller than ``min_units`` are cancelled rather than rounded up.

Each strategy has an independent book (own equity, own positions) so per-strategy statistics stay
separate even when several strategies are tested together.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from ..engine.bars import Bars
from ..engine.signals import CancelIntent, OrderIntent
from .config import ExecutionConfig

ENTRY, EXIT, INVALIDATE = 0, 1, 2


@dataclass
class PendingOrder:
    intent: OrderIntent
    active_from: int
    expires_after: int | None  # last bar index on which the order may still fill
    seq: int


@dataclass
class TPLevel:
    index: int  # 1-based (TP1, TP2, ...)
    price: float
    units: float
    filled: bool = False


@dataclass
class Fill:
    index: int
    time: int
    price: float
    units: float
    kind: str  # ENTRY, TP1, TP2.., SL, BE, END, OPP

    def to_dict(self) -> dict:
        return {"index": self.index, "time": self.time, "price": self.price, "units": self.units,
                "kind": self.kind}


@dataclass
class Trade:
    intent: OrderIntent
    seq: int
    sign: int
    entry_index: int
    entry_time: int
    entry_price: float
    units_initial: float
    units_open: float
    sl_initial: float
    sl: float
    tps: list[TPLevel]
    fills: list[Fill] = field(default_factory=list)
    commission: float = 0.0
    gross: float = 0.0
    mae: float = 0.0  # max adverse excursion, price units (>= 0)
    mfe: float = 0.0  # max favourable excursion, price units (>= 0)
    be_moved: bool = False
    ambiguous: bool = False
    exit_index: int | None = None
    exit_time: int | None = None


@dataclass
class BookState:
    """Mutable part of a strategy book that a bar simulation may change (cheap to clone)."""

    cash: float  # realised equity (initial capital + net realised P&L)
    pending: list[PendingOrder]
    open: list[Trade]
    seq: int = 0


@dataclass
class Book:
    strategy: str
    state: BookState
    closed: list[Trade] = field(default_factory=list)
    cancelled: list[dict] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)  # mark-to-market at each processed bar's close


@dataclass
class _Trigger:
    level: float  # in candle-basis price
    direction: int  # +1 fires when price >= level, -1 when price <= level
    priority: int
    seq: int
    kind: str  # 'entry' | 'invalidate' | 'sl' | 'tp'
    order: PendingOrder | None = None
    trade: Trade | None = None
    tp: TPLevel | None = None


class Broker:
    def __init__(self, cfg: ExecutionConfig, bars: Bars, strategies: list[str]):
        cfg.validate()
        self.cfg = cfg
        self.bars = bars
        if cfg.price_basis == "mid":
            self.bid_off, self.ask_off = -cfg.spread / 2, cfg.spread / 2
        elif cfg.price_basis == "bid":
            self.bid_off, self.ask_off = 0.0, cfg.spread
        else:
            self.bid_off, self.ask_off = -cfg.spread, 0.0
        self.books = {s: Book(s, BookState(cfg.initial_capital, [], [])) for s in strategies}
        self.equity_times: list[int] = []
        self.ambiguous_bars = 0

    # ---------------------------------------------------------------- orders in
    def add_intent(self, intent: OrderIntent, bar_index: int) -> None:
        book = self.books.get(intent.strategy)
        if book is None:
            return
        st = book.state
        if self.cfg.replace_pending and st.pending:
            for po in list(st.pending):
                self._cancel(book, st, po, bar_index, "replaced by a newer signal")
        expires = None
        if intent.order_type != "market" and intent.expiry_bars is not None:
            expires = bar_index + intent.expiry_bars
        st.seq += 1
        st.pending.append(PendingOrder(intent, bar_index + 1, expires, st.seq))

    def add_cancel(self, cancel: CancelIntent, bar_index: int) -> None:
        book = self.books.get(cancel.strategy)
        if book is None:
            return
        for po in list(book.state.pending):
            if po.intent.id == cancel.order_id:
                self._cancel(book, book.state, po, bar_index, cancel.reason)

    def _cancel(self, book: Book, st: BookState, po: PendingOrder, bar_index: int, reason: str,
                sink: list | None = None) -> None:
        if po in st.pending:
            st.pending.remove(po)
        rec = {"order_id": po.intent.id, "strategy": po.intent.strategy, "side": po.intent.side,
               "order_type": po.intent.order_type, "signal_time": po.intent.time, "entry": po.intent.entry,
               "stop_loss": po.intent.stop_loss, "time": int(self.bars.time[bar_index]), "reason": reason}
        (sink if sink is not None else book.cancelled).append(rec)

    # ------------------------------------------------------------- price helpers
    def bid(self, p: float) -> float:
        return p + self.bid_off

    def ask(self, p: float) -> float:
        return p + self.ask_off

    def _units(self, cash: float, entry: float, stop: float) -> float:
        cfg = self.cfg
        if cfg.sizing_mode == "fixed_units":
            units = cfg.sizing_value
        elif cfg.sizing_mode == "fixed_lots":
            units = cfg.sizing_value * cfg.contract_size
        else:
            risk_per_unit = abs(entry - stop)
            if risk_per_unit <= 0:
                return 0.0
            budget = cash * cfg.sizing_value / 100.0 if cfg.sizing_mode == "risk_percent" else cfg.sizing_value
            units = budget / risk_per_unit
        if cfg.max_units is not None:
            units = min(units, cfg.max_units)
        units = math.floor(units / cfg.unit_step + 1e-9) * cfg.unit_step
        return units if units >= cfg.min_units else 0.0

    def _commission(self, units: float) -> float:
        return units / self.cfg.contract_size * self.cfg.commission_per_lot

    # --------------------------------------------------------------- triggers
    def _triggers(self, st: BookState, i: int) -> list[_Trigger]:
        out: list[_Trigger] = []
        for po in st.pending:
            if po.active_from > i:
                continue
            it = po.intent
            if it.order_type == "market":
                continue  # handled at the open
            long = it.side == "long"
            assert it.entry is not None
            if it.order_type == "limit":
                lvl, d = (it.entry - self.ask_off, -1) if long else (it.entry - self.bid_off, +1)
            else:  # stop entry
                lvl, d = (it.entry - self.ask_off, +1) if long else (it.entry - self.bid_off, -1)
            out.append(_Trigger(lvl, d, ENTRY, po.seq, "entry", order=po))
            if it.cancel_if_sl_before_entry:
                if long:
                    out.append(_Trigger(it.stop_loss - self.bid_off, -1, INVALIDATE, po.seq, "invalidate", order=po))
                else:
                    out.append(_Trigger(it.stop_loss - self.ask_off, +1, INVALIDATE, po.seq, "invalidate", order=po))
        for tr in st.open:
            if tr.sign > 0:
                out.append(_Trigger(tr.sl - self.bid_off, -1, EXIT, tr.seq, "sl", trade=tr))
                for tp in tr.tps:
                    if not tp.filled:
                        out.append(_Trigger(tp.price - self.bid_off, +1, EXIT, tr.seq, "tp", trade=tr, tp=tp))
            else:
                out.append(_Trigger(tr.sl - self.ask_off, +1, EXIT, tr.seq, "sl", trade=tr))
                for tp in tr.tps:
                    if not tp.filled:
                        out.append(_Trigger(tp.price - self.ask_off, -1, EXIT, tr.seq, "tp", trade=tr, tp=tp))
        return out

    @staticmethod
    def _satisfied(t: _Trigger, p: float) -> bool:
        return p >= t.level if t.direction > 0 else p <= t.level

    # ---------------------------------------------------------------- actions
    def _fire(self, book: Book, st: BookState, t: _Trigger, i: int, p: float, gap: bool,
              closed: list, cancelled: list, events: list) -> None:
        time = int(self.bars.time[i])
        if t.kind == "entry":
            self._enter(book, st, t.order, i, p, gap, cancelled, events)  # type: ignore[arg-type]
        elif t.kind == "invalidate":
            self._cancel(book, st, t.order, i, "invalidated: stop-loss level traded before entry", cancelled)  # type: ignore[arg-type]
            events.append(("invalidate", t.order.intent.id, None))  # type: ignore[union-attr]
        elif t.kind == "sl":
            tr = t.trade
            assert tr is not None
            # selling (long) at the bid, buying (short) at the ask; stops slip adversely
            if tr.sign > 0:
                price = min(tr.sl, self.bid(p)) - self.cfg.slippage
            else:
                price = max(tr.sl, self.ask(p)) + self.cfg.slippage
            kind = "BE" if tr.be_moved else "SL"
            self._exit(st, tr, i, time, price, tr.units_open, kind, closed, events)
        elif t.kind == "tp":
            tr, tp = t.trade, t.tp
            assert tr is not None and tp is not None
            price = max(tp.price, self.bid(p)) if tr.sign > 0 else min(tp.price, self.ask(p))
            tp.filled = True
            units = min(tp.units, tr.units_open)
            last_tp = all(x.filled for x in tr.tps)
            if last_tp and abs(sum(x.units for x in tr.tps) - tr.units_initial) < 1e-9:
                units = tr.units_open  # the final target closes whatever is left
            self._exit(st, tr, i, time, price, units, f"TP{tp.index}", closed, events)
            if tp.index == 1 and tr.intent.breakeven_after_tp1 and tr.units_open > 0 and not tr.be_moved:
                tr.sl = tr.entry_price + tr.sign * tr.intent.breakeven_offset
                tr.be_moved = True
                events.append(("be", tr.intent.id, tr.sl))

    def _enter(self, book: Book, st: BookState, po: PendingOrder, i: int, p: float, gap: bool,
               cancelled: list, events: list) -> None:
        it = po.intent
        long = it.side == "long"
        slip = self.cfg.slippage
        if it.order_type == "market":
            price = self.ask(p) + slip if long else self.bid(p) - slip
        elif it.order_type == "limit":
            assert it.entry is not None
            price = min(it.entry, self.ask(p)) if long else max(it.entry, self.bid(p))
        else:
            assert it.entry is not None
            price = max(it.entry, self.ask(p)) + slip if long else min(it.entry, self.bid(p)) - slip
        if len(st.open) >= self.cfg.max_open_trades:
            self._cancel(book, st, po, i, "max open trades reached", cancelled)
            events.append(("cancel", it.id, None))
            return
        units = self._units(st.cash, price, it.stop_loss)
        if units <= 0:
            self._cancel(book, st, po, i, "position size below minimum", cancelled)
            events.append(("cancel", it.id, None))
            return
        st.pending.remove(po)
        sign = 1 if long else -1
        tps: list[TPLevel] = []
        step = self.cfg.unit_step
        for k, tp in enumerate(it.take_profits, start=1):
            u = math.floor(units * tp.fraction / step + 1e-9) * step
            tps.append(TPLevel(k, tp.price, u))
        total_frac = sum(tp.fraction for tp in it.take_profits)
        if tps and abs(total_frac - 1.0) < 1e-9:
            # the last target takes the rounding remainder so the full position is covered
            tps[-1].units = units - sum(t.units for t in tps[:-1])
        tr = Trade(intent=it, seq=po.seq, sign=sign, entry_index=i, entry_time=int(self.bars.time[i]),
                   entry_price=price, units_initial=units, units_open=units, sl_initial=it.stop_loss,
                   sl=it.stop_loss, tps=tps)
        fee = self._commission(units)
        tr.commission += fee
        st.cash -= fee
        tr.fills.append(Fill(i, tr.entry_time, price, units, "ENTRY"))
        st.open.append(tr)
        events.append(("entry", it.id, round(price, 10)))

    def _exit(self, st: BookState, tr: Trade, i: int, time: int, price: float, units: float, kind: str,
              closed: list, events: list) -> None:
        units = min(units, tr.units_open)
        if units > 0:
            pnl = tr.sign * (price - tr.entry_price) * units
            fee = self._commission(units)
            tr.gross += pnl
            tr.commission += fee
            st.cash += pnl - fee
            tr.units_open -= units
            tr.fills.append(Fill(i, time, price, units, kind))
        else:
            tr.fills.append(Fill(i, time, price, 0.0, kind))
        events.append((kind, tr.intent.id, round(price, 10)))
        if tr.units_open <= 1e-12:
            tr.units_open = 0.0
            tr.exit_index, tr.exit_time = i, time
            st.open.remove(tr)
            closed.append(tr)

    def _excursion(self, st: BookState, a: float, b: float) -> None:
        if not st.open:
            return
        lo, hi = (a, b) if a <= b else (b, a)
        for tr in st.open:
            if tr.sign > 0:
                tr.mfe = max(tr.mfe, self.bid(hi) - tr.entry_price)
                tr.mae = max(tr.mae, tr.entry_price - self.bid(lo))
            else:
                tr.mfe = max(tr.mfe, tr.entry_price - self.ask(lo))
                tr.mae = max(tr.mae, self.ask(hi) - tr.entry_price)

    # ------------------------------------------------------------ simulation
    def _settle(self, book, st, i, p, gap, closed, cancelled, events) -> None:
        """Fire every trigger satisfied at price ``p`` (entries, then exits, then invalidations)."""
        for _ in range(1000):
            trig = [t for t in self._triggers(st, i) if self._satisfied(t, p)]
            if not trig:
                return
            trig.sort(key=lambda t: (t.priority, t.seq))
            self._fire(book, st, trig[0], i, p, gap, closed, cancelled, events)
        raise RuntimeError("trigger loop did not settle")  # pragma: no cover

    def _simulate(self, book: Book, st: BookState, i: int, path: list[float]):
        closed: list[Trade] = []
        cancelled: list[dict] = []
        events: list[tuple] = []
        o = path[0]
        # Opening auction: opposite-signal exits, market orders, then anything gapped through.
        if self.cfg.close_on_opposite:
            newest = [po for po in st.pending if po.active_from == i]
            if newest:
                side = newest[-1].intent.side
                for tr in list(st.open):
                    if tr.intent.side != side:
                        price = self.bid(o) - self.cfg.slippage if tr.sign > 0 else self.ask(o) + self.cfg.slippage
                        self._exit(st, tr, i, int(self.bars.time[i]), price, tr.units_open, "OPP", closed, events)
        for po in sorted([po for po in st.pending if po.intent.order_type == "market" and po.active_from <= i],
                         key=lambda x: x.seq):
            if po in st.pending:
                self._enter(book, st, po, i, o, True, cancelled, events)
        self._settle(book, st, i, o, True, closed, cancelled, events)
        cur = o
        for nxt in path[1:]:
            for _ in range(1000):
                up = nxt > cur
                best: _Trigger | None = None
                best_key = None
                for t in self._triggers(st, i):
                    if up and t.direction > 0 and cur < t.level <= nxt:
                        dist = t.level - cur
                    elif (not up) and t.direction < 0 and nxt <= t.level < cur:
                        dist = cur - t.level
                    else:
                        continue
                    key = (dist, t.priority, t.seq)
                    if best_key is None or key < best_key:
                        best, best_key = t, key
                if best is None:
                    self._excursion(st, cur, nxt)
                    break
                self._excursion(st, cur, best.level)
                cur = best.level
                self._fire(book, st, best, i, cur, False, closed, cancelled, events)
                self._settle(book, st, i, cur, False, closed, cancelled, events)
            cur = nxt
        return closed, cancelled, events

    def _mtm(self, st: BookState, close: float) -> float:
        eq = st.cash
        for tr in st.open:
            px = self.bid(close) if tr.sign > 0 else self.ask(close)
            eq += tr.sign * (px - tr.entry_price) * tr.units_open
        return eq

    def _paths(self, i: int) -> list[tuple[str, list[float]]]:
        b = self.bars
        o, h, l, c = float(b.open[i]), float(b.high[i]), float(b.low[i]), float(b.close[i])
        high_first = ("OHLC", [o, h, l, c])
        low_first = ("OLHC", [o, l, h, c])
        heuristic = high_first if abs(h - o) < abs(o - l) else low_first
        if self.cfg.intrabar_policy == "ohlc_heuristic" or h == l:
            return [heuristic]
        other = low_first if heuristic is high_first else high_first
        return [heuristic, other]

    def process_bar(self, i: int) -> None:
        close = float(self.bars.close[i])
        paths = self._paths(i)
        bar_ambiguous = False
        for book in self.books.values():
            st = book.state
            if not st.pending and not st.open:
                book.equity.append(self._mtm(st, close))
                continue
            if len(paths) == 1:
                closed, cancelled, events = self._simulate(book, st, i, paths[0][1])
            else:
                results = []
                for name, path in paths:
                    clone = copy.deepcopy(st)
                    res = self._simulate(book, clone, i, path)
                    results.append((self._mtm(clone, close), name, clone, res))
                (eq_a, _, st_a, res_a), (eq_b, _, st_b, res_b) = results
                differs = res_a[2] != res_b[2]
                # keep the worse outcome; on a tie keep the heuristic path (listed first)
                chosen_state, (closed, cancelled, events) = (st_b, res_b) if eq_b < eq_a - 1e-9 else (st_a, res_a)
                book.state = st = chosen_state
                if differs:
                    bar_ambiguous = True
                    touched = {e[1] for e in events}
                    for tr in st.open + closed:
                        if tr.intent.id in touched:
                            tr.ambiguous = True
            book.closed.extend(closed)
            book.cancelled.extend(cancelled)
            # expiry: orders whose last eligible bar was this one
            for po in list(st.pending):
                if po.expires_after is not None and po.expires_after <= i:
                    self._cancel(book, st, po, i, "expired")
            book.equity.append(self._mtm(st, close))
        if bar_ambiguous:
            self.ambiguous_bars += 1
        self.equity_times.append(int(self.bars.time[i]))

    def finish(self, last_index: int) -> None:
        """End of the test window: close open trades (if configured) and cancel pending orders."""
        close = float(self.bars.close[last_index])
        time = int(self.bars.time[last_index])
        for book in self.books.values():
            st = book.state
            for po in list(st.pending):
                self._cancel(book, st, po, last_index, "end of test window")
            if self.cfg.close_at_end:
                closed: list[Trade] = []
                for tr in list(st.open):
                    price = self.bid(close) if tr.sign > 0 else self.ask(close)
                    self._exit(st, tr, last_index, time, price, tr.units_open, "END", closed, [])
                book.closed.extend(closed)
                if book.equity:
                    book.equity[-1] = self._mtm(st, close)

    # ---------------------------------------------------------------- results
    def trade_records(self) -> list[dict]:
        out = []
        for book in self.books.values():
            for tr in book.closed:
                out.append(trade_to_dict(tr))
            for tr in book.state.open:  # still open (close_at_end disabled)
                out.append(trade_to_dict(tr))
        out.sort(key=lambda r: (r["entry_time"], r["strategy"], r["id"]))
        return out


def trade_to_dict(tr: Trade) -> dict:
    it = tr.intent
    exits = [f for f in tr.fills if f.kind != "ENTRY"]
    exit_units = sum(f.units for f in exits)
    exit_price = (sum(f.price * f.units for f in exits) / exit_units) if exit_units > 0 else None
    risk_per_unit = tr.sign * (tr.entry_price - tr.sl_initial)
    risk_cash = risk_per_unit * tr.units_initial
    net = tr.gross - tr.commission
    ref = it.entry if it.entry is not None else tr.entry_price
    planned_risk = abs(ref - it.stop_loss)
    planned_rr = None
    if planned_risk > 0 and it.take_profits:
        total = sum(tp.fraction for tp in it.take_profits)
        planned_rr = sum(abs(tp.price - ref) * tp.fraction for tp in it.take_profits) / total / planned_risk
    kinds = [f.kind for f in exits]
    closed = tr.units_open <= 0
    return {
        "id": it.id,
        "strategy": it.strategy,
        "side": it.side,
        "order_type": it.order_type,
        "comment": it.comment,
        "signal_time": it.time,
        "entry_time": tr.entry_time,
        "entry_price": tr.entry_price,
        "units": tr.units_initial,
        "stop_loss": tr.sl_initial,
        "final_stop": tr.sl,
        "take_profits": [tp.price for tp in it.take_profits],
        "tp1": it.take_profits[0].price if len(it.take_profits) > 0 else None,
        "tp2": it.take_profits[1].price if len(it.take_profits) > 1 else None,
        "exit_time": tr.exit_time,
        "exit_price": exit_price,
        "gross_pnl": tr.gross,
        "commission": tr.commission,
        "pnl": net,
        "r_multiple": (net / risk_cash) if risk_cash > 0 else None,
        "planned_rr": planned_rr,
        "risk_cash": risk_cash if risk_cash > 0 else None,
        "exit_reason": " → ".join(kinds) if closed else "Open",
        "is_open": not closed,
        "fills": [f.to_dict() for f in tr.fills],
        "mae": tr.mae,
        "mfe": tr.mfe,
        "mae_r": (tr.mae / risk_per_unit) if risk_per_unit > 0 else None,
        "mfe_r": (tr.mfe / risk_per_unit) if risk_per_unit > 0 else None,
        "bars_held": (tr.exit_index - tr.entry_index) if tr.exit_index is not None else None,
        "ambiguous": tr.ambiguous,
        # Target-based outcome: any take-profit filled = win; stopped out (SL) before any TP = loss.
        "tps_hit": sum(1 for k in kinds if k.startswith("TP")),
        "outcome": ("win" if any(k.startswith("TP") for k in kinds) else "loss" if "SL" in kinds
                    else "open" if not closed else "none"),
    }


def equity_arrays(broker: Broker) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    times = np.asarray(broker.equity_times, dtype=np.int64)
    return times, {s: np.asarray(b.equity, dtype=np.float64) for s, b in broker.books.items()}
