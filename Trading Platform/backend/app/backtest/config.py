"""Backtest execution assumptions. Every assumption is explicit and shown in the UI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Literal

SizingMode = Literal["fixed_units", "fixed_lots", "risk_percent", "risk_cash"]
IntrabarPolicy = Literal["conservative", "ohlc_heuristic"]


@dataclass(frozen=True)
class ExecutionConfig:
    initial_capital: float = 10_000.0
    # What the candle prices represent. OANDA candles are requested as mid prices; most CSV
    # vendors (MT4/MT5, Dukascopy) export bid prices.
    price_basis: Literal["mid", "bid", "ask"] = "mid"
    # Spread in price units (USD per ounce for XAUUSD). Buys fill at the ask, sells at the bid; stop
    # and limit triggers are evaluated on the side of the book that would execute them.
    spread: float = 0.30
    # Adverse slippage in price units applied to market and stop fills (stop-loss included).
    # Limit fills (entries and take-profits) never receive slippage - but never receive price
    # improvement inside a bar either; they only fill better than their price on a gap open.
    slippage: float = 0.05
    commission_per_lot: float = 0.0  # charged on every fill (entry and each exit), per lot
    contract_size: float = 100.0  # units per lot (100 oz for XAUUSD)
    sizing_mode: SizingMode = "risk_percent"
    sizing_value: float = 1.0  # units / lots / % of equity / cash amount depending on the mode
    unit_step: float = 1.0  # position sizes are rounded DOWN to this step
    min_units: float = 1.0  # orders that size below this are cancelled, not rounded up
    max_units: float | None = None
    # How price is assumed to move inside a bar when that matters (e.g. both SL and TP in range):
    #  conservative   - simulate O-H-L-C and O-L-H-C and keep whichever is worse for the strategy
    #  ohlc_heuristic - TradingView's broker-emulator rule: if the open is closer to the high,
    #                   O-H-L-C, otherwise O-L-H-C
    intrabar_policy: IntrabarPolicy = "conservative"
    max_open_trades: int = 1  # per strategy; an entry that would exceed it is cancelled
    replace_pending: bool = True  # a new signal cancels the same strategy's older pending orders
    close_on_opposite: bool = False  # an opposite signal closes open trades at the next open
    close_at_end: bool = True  # close open trades at the last bar's close of the window

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial capital must be positive")
        if self.spread < 0 or self.slippage < 0 or self.commission_per_lot < 0:
            raise ValueError("spread, slippage and commission cannot be negative")
        if self.contract_size <= 0 or self.unit_step <= 0 or self.min_units <= 0:
            raise ValueError("contract size, unit step and minimum units must be positive")
        if self.sizing_value <= 0:
            raise ValueError("sizing value must be positive")
        if self.sizing_mode == "risk_percent" and self.sizing_value > 100:
            raise ValueError("risk percent cannot exceed 100")
        if self.max_open_trades < 1:
            raise ValueError("max open trades must be >= 1")
        if self.price_basis not in ("mid", "bid", "ask"):
            raise ValueError("price basis must be mid, bid or ask")
        if self.intrabar_policy not in ("conservative", "ohlc_heuristic"):
            raise ValueError("unknown intrabar policy")
        if self.sizing_mode not in ("fixed_units", "fixed_lots", "risk_percent", "risk_cash"):
            raise ValueError("unknown sizing mode")

    @staticmethod
    def from_dict(d: dict | None) -> "ExecutionConfig":
        d = dict(d or {})
        names = {f.name for f in fields(ExecutionConfig)}
        unknown = sorted(set(d) - names)
        if unknown:
            raise ValueError(f"unknown execution settings: {unknown}")
        cfg = ExecutionConfig(**d)
        cfg.validate()
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)
