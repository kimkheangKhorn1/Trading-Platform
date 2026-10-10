"""Trade intents emitted by scripts and consumed by the backtester.

A script decides *what* it wants to trade on the close of a confirmed bar; the backtester's broker
decides *whether and how* that order is filled on later bars. Keeping the two apart means
execution assumptions (spread, slippage, intrabar ordering) can be changed without touching the
trading rules, and signals never see fills they could not have known about.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Side = Literal["long", "short"]
OrderType = Literal["market", "limit", "stop"]


@dataclass(frozen=True)
class TakeProfit:
    price: float
    fraction: float  # share of the *initial* position closed at this level (0..1]


@dataclass(frozen=True)
class OrderIntent:
    id: str
    strategy: str
    side: Side
    order_type: OrderType
    stop_loss: float
    take_profits: tuple[TakeProfit, ...]
    entry: float | None = None  # limit/stop price; for market orders the reference (signal close)
    expiry_bars: int | None = None  # pending orders: bars the order stays working (None = until filled/cancelled)
    breakeven_after_tp1: bool = False
    breakeven_offset: float = 0.0  # added in the trade's favour when the stop moves to entry
    cancel_if_sl_before_entry: bool = True  # pending order cancelled when SL level trades first
    comment: str = ""
    bar_index: int = -1  # bar on whose close the intent was generated (set by the runner)
    time: int = 0  # open time of that bar

    def validate(self) -> str | None:
        """Return a reason string when the intent is internally inconsistent, else None."""
        if self.side not in ("long", "short"):
            return "invalid side"
        if self.order_type not in ("market", "limit", "stop"):
            return "invalid order type"
        if self.order_type != "market" and self.entry is None:
            return "pending order without entry price"
        if not self.take_profits and self.stop_loss is None:
            return "no exit levels"
        ref = self.entry
        total = sum(tp.fraction for tp in self.take_profits)
        if total > 1.0 + 1e-9 or any(tp.fraction <= 0 for tp in self.take_profits):
            return "take-profit fractions must be positive and sum to at most 1"
        if ref is not None:
            if self.side == "long":
                if not self.stop_loss < ref:
                    return "long stop-loss must be below entry"
                if any(tp.price <= ref for tp in self.take_profits):
                    return "long take-profits must be above entry"
            else:
                if not self.stop_loss > ref:
                    return "short stop-loss must be above entry"
                if any(tp.price >= ref for tp in self.take_profits):
                    return "short take-profits must be below entry"
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["take_profits"] = [asdict(tp) for tp in self.take_profits]
        return d


@dataclass(frozen=True)
class CancelIntent:
    """Cancel a still-pending order (no effect once it has filled)."""

    order_id: str
    strategy: str
    reason: str = "cancelled by strategy"
    bar_index: int = -1
    time: int = 0


@dataclass
class SignalLog:
    intents: list[OrderIntent] = field(default_factory=list)
    cancels: list[CancelIntent] = field(default_factory=list)
    rejected: list[tuple[OrderIntent, str]] = field(default_factory=list)
