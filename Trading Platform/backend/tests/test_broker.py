"""Execution-rule tests for the simulated broker. Each scenario uses hand-made candles and states
the expected fills explicitly, so a change in execution behaviour cannot go unnoticed."""
import pytest

from app.backtest.broker import Broker
from app.backtest.config import ExecutionConfig
from app.engine.signals import CancelIntent, OrderIntent, TakeProfit
from tests.helpers import bars_from_ohlc

BASE = dict(spread=0.0, slippage=0.0, commission_per_lot=0.0, sizing_mode="fixed_units", sizing_value=1.0,
            intrabar_policy="conservative")


def cfg(**kw):
    return ExecutionConfig(**{**BASE, **kw})


def intent(bar, side="long", order_type="market", sl=95.0, tps=((110.0, 1.0),), entry=None, **kw):
    return OrderIntent(id=f"s@{bar}#{kw.pop('n', 0)}", strategy="s", side=side, order_type=order_type,
                       stop_loss=sl, take_profits=tuple(TakeProfit(p, f) for p, f in tps), entry=entry,
                       bar_index=bar, time=bar, **kw)


def run(rows, intents, conf, cancels=()):
    bars = bars_from_ohlc(rows)
    b = Broker(conf, bars, ["s"])
    by_bar: dict[int, list] = {}
    for it in intents:
        by_bar.setdefault(it.bar_index, []).append(it)
    for i in range(len(bars)):
        b.process_bar(i)
        for c in cancels:
            if c.bar_index == i:
                b.add_cancel(c, i)
        for it in by_bar.get(i, []):
            b.add_intent(it, i)
    b.finish(len(bars) - 1)
    return b, b.trade_records(), b.books["s"].cancelled, bars


def test_market_order_fills_next_open_never_signal_bar():
    rows = [(100, 101, 99, 100), (100.5, 102, 100, 101), (101, 111, 100.5, 110)]
    b, trades, _, bars = run(rows, [intent(0, sl=95, tps=((110, 1.0),))], cfg())
    (t,) = trades
    assert t["entry_time"] == bars.time[1] and t["entry_price"] == 100.5
    assert t["exit_price"] == 110 and t["pnl"] == pytest.approx(9.5)
    assert t["exit_reason"] == "TP1" and not t["ambiguous"]
    assert b.books["s"].equity[-1] == pytest.approx(10_009.5)


def test_sl_and_tp_in_same_bar_conservative_takes_the_loss():
    rows = [(100, 101, 99, 100), (100, 100, 100, 100), (100, 105.5, 94, 100)]
    _, trades, _, _ = run(rows, [intent(0, sl=95, tps=((105, 1.0),))], cfg())
    (t,) = trades
    assert t["exit_reason"] == "SL" and t["pnl"] == pytest.approx(-5) and t["ambiguous"]


def test_sl_and_tp_in_same_bar_tradingview_heuristic():
    rows = [(100, 101, 99, 100), (100, 100, 100, 100), (100, 105.5, 94, 100)]
    # open 100 is closer to the high (5.5) than to the low (6) -> O-H-L-C -> TP first
    _, trades, _, _ = run(rows, [intent(0, sl=95, tps=((105, 1.0),))], cfg(intrabar_policy="ohlc_heuristic"))
    assert trades[0]["exit_reason"] == "TP1" and trades[0]["pnl"] == pytest.approx(5)


def test_spread_sides_and_trigger_levels():
    conf = cfg(spread=1.0)  # mid candles: bid = mid - 0.5, ask = mid + 0.5
    rows = [(100, 101, 99, 100), (100, 100.2, 99.9, 100), (100, 100.1, 99.6, 99.8), (99.8, 99.9, 99.4, 99.5)]
    _, trades, _, bars = run(rows, [intent(0, sl=99.0, tps=((120, 1.0),))], conf)
    (t,) = trades
    assert t["entry_price"] == pytest.approx(100.5)  # bought at the ask
    # bar 2 low 99.6 -> bid 99.1 > 99 (no stop); bar 3 low 99.4 -> bid 98.9 <= 99 (stopped at 99)
    assert t["exit_time"] == bars.time[3] and t["exit_price"] == pytest.approx(99.0)
    assert t["pnl"] == pytest.approx(-1.5)


def test_short_stop_triggers_on_ask_with_slippage():
    conf = cfg(spread=1.0, slippage=0.1)
    rows = [(100, 101, 99, 100), (100, 100.2, 99.8, 100), (100, 104.6, 99.9, 104)]
    _, trades, _, _ = run(rows, [intent(0, side="short", sl=105, tps=((90, 1.0),))], conf)
    (t,) = trades
    assert t["entry_price"] == pytest.approx(99.4)  # sold at the bid minus slippage
    assert t["exit_reason"] == "SL" and t["exit_price"] == pytest.approx(105.1)  # ask 105.1 >= 105, +0.1 slip
    assert t["pnl"] == pytest.approx(99.4 - 105.1)


def test_gap_through_stop_fills_at_open_not_at_stop():
    rows = [(100, 101, 99, 100), (100, 100.5, 99.5, 100), (93, 94, 92, 93.5)]
    _, trades, _, _ = run(rows, [intent(0, sl=95, tps=((110, 1.0),))], cfg(slippage=0.2))
    t = trades[0]
    assert t["entry_price"] == pytest.approx(100.2)
    assert t["exit_price"] == pytest.approx(92.8)  # open 93 minus slippage, not 95
    assert t["r_multiple"] == pytest.approx((92.8 - 100.2) / (100.2 - 95))


def test_tp1_partial_then_breakeven_same_bar_conservative():
    conf = cfg(sizing_value=10)
    rows = [(100, 101, 99, 100), (100, 100.4, 99.8, 100), (100, 105.5, 99.5, 104), (104, 110.5, 103, 110)]
    it = intent(0, sl=95, tps=((105, 0.5), (110, 0.5)), breakeven_after_tp1=True)
    _, trades, _, _ = run(rows, [it], conf)
    (t,) = trades
    # worst path O-H-L-C: TP1 (5 @105) -> stop to 100 -> BE (5 @100)
    assert t["exit_reason"] == "TP1 → BE"
    assert t["pnl"] == pytest.approx(25.0) and t["final_stop"] == 100 and t["ambiguous"]


def test_tp1_then_tp2_with_heuristic_path():
    conf = cfg(sizing_value=10, intrabar_policy="ohlc_heuristic")
    rows = [(100, 101, 99, 100), (100, 100.4, 99.8, 100), (100, 105.5, 99.5, 104), (104, 110.5, 103, 110)]
    it = intent(0, sl=95, tps=((105, 0.5), (110, 0.5)), breakeven_after_tp1=True)
    _, trades, _, _ = run(rows, [it], conf)
    (t,) = trades
    assert t["exit_reason"] == "TP1 → TP2" and t["pnl"] == pytest.approx(75.0)
    assert [f["units"] for f in t["fills"]] == [10, 5, 5]


def test_limit_order_expires_unfilled():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 98.5, 100), (100, 100, 97, 98)]
    it = intent(0, order_type="limit", entry=98, sl=95, tps=((104, 1.0),), expiry_bars=2)
    _, trades, cancelled, bars = run(rows, [it], cfg())
    assert trades == []
    assert cancelled[0]["reason"] == "expired" and cancelled[0]["time"] == bars.time[2]


def test_limit_order_fills_at_limit_within_expiry():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 98.5, 100), (100, 100, 97, 98)]
    it = intent(0, order_type="limit", entry=98, sl=95, tps=((104, 1.0),), expiry_bars=3)
    _, trades, _, bars = run(rows, [it], cfg())
    assert trades[0]["entry_price"] == 98 and trades[0]["entry_time"] == bars.time[3]


def test_limit_gap_fills_at_better_open():
    rows = [(100, 101, 99, 100), (97, 98, 96.5, 97.5)]
    it = intent(0, order_type="limit", entry=98, sl=95, tps=((104, 1.0),))
    _, trades, _, _ = run(rows, [it], cfg())
    assert trades[0]["entry_price"] == 97


def test_gapped_resting_order_does_not_dodge_its_stop():
    # opens below both the limit entry and its stop: the resting order fills, then is stopped at the open
    rows = [(100, 101, 99, 100), (94, 94.5, 93, 94)]
    it = intent(0, order_type="limit", entry=98, sl=95, tps=((104, 1.0),))
    _, trades, _, _ = run(rows, [it], cfg())
    (t,) = trades
    assert t["entry_price"] == 94 and t["exit_reason"] == "SL" and t["exit_price"] == 94


def test_stop_entry_invalidated_when_sl_trades_first():
    rows = [(100, 101, 99, 100), (100, 101, 96.5, 98), (98, 103, 98, 102)]
    it = intent(0, order_type="stop", entry=102, sl=97, tps=((110, 1.0),))
    _, trades, cancelled, _ = run(rows, [it], cfg())
    assert trades == [] and cancelled[0]["reason"].startswith("invalidated")
    it2 = intent(0, order_type="stop", entry=102, sl=97, tps=((110, 1.0),), cancel_if_sl_before_entry=False)
    _, trades, _, _ = run(rows, [it2], cfg())
    assert trades[0]["entry_price"] == 102


def test_strategy_cancel_intent():
    rows = [(100, 101, 99, 100)] * 4
    it = intent(0, order_type="limit", entry=90, sl=85, tps=((104, 1.0),))
    _, trades, cancelled, _ = run(rows, [it], cfg(), cancels=[CancelIntent(it.id, "s", "zone mitigated", 2, 2)])
    assert trades == [] and cancelled[0]["reason"] == "zone mitigated"


def test_risk_percent_sizing_and_commission():
    conf = cfg(sizing_mode="risk_percent", sizing_value=1.0, commission_per_lot=7.0, contract_size=100)
    rows = [(100, 101, 99, 100), (100, 100.5, 99.5, 100), (100, 111, 100, 110)]
    _, trades, _, _ = run(rows, [intent(0, sl=95, tps=((110, 1.0),))], conf)
    t = trades[0]
    assert t["units"] == 20  # 1% of 10,000 = 100 risk / 5 per unit
    assert t["commission"] == pytest.approx(2 * 20 / 100 * 7)
    assert t["pnl"] == pytest.approx(20 * 10 - 2.8)
    assert t["r_multiple"] == pytest.approx((200 - 2.8) / 100)


def test_size_below_minimum_cancels_instead_of_rounding_up():
    conf = cfg(sizing_mode="risk_cash", sizing_value=3.0)  # 3 / 5 = 0.6 units < 1
    rows = [(100, 101, 99, 100), (100, 100.5, 99.5, 100)]
    _, trades, cancelled, _ = run(rows, [intent(0, sl=95)], conf)
    assert trades == [] and cancelled[0]["reason"] == "position size below minimum"


def test_max_open_trades_and_replacement():
    rows = [(100, 101, 99, 100)] * 2 + [(100, 100.5, 99.5, 100)] * 3
    _, trades, cancelled, _ = run(rows, [intent(0, sl=90), intent(2, sl=90, n=1)], cfg())
    assert len([t for t in trades]) == 1
    assert cancelled[0]["reason"] == "max open trades reached"
    lim = [intent(0, order_type="limit", entry=95, sl=90), intent(1, order_type="limit", entry=96, sl=90, n=1)]
    _, trades, cancelled, _ = run(rows, lim, cfg())
    assert cancelled[0]["reason"] == "replaced by a newer signal" and cancelled[0]["order_id"] == lim[0].id


def test_close_at_end_and_opposite_signal():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100.5), (101, 102, 100, 101)]
    _, trades, _, _ = run(rows, [intent(0, sl=90, tps=((120, 1.0),))], cfg())
    assert trades[0]["exit_reason"] == "END" and trades[0]["exit_price"] == 101
    rows = [(100, 101, 99, 100), (100, 101, 99, 100.5), (101, 102, 100, 101), (101.5, 102, 100, 101)]
    its = [intent(0, sl=90, tps=((120, 1.0),)), intent(2, side="short", sl=110, tps=((80, 1.0),), n=1)]
    _, trades, _, _ = run(rows, its, cfg(close_on_opposite=True))
    assert trades[0]["exit_reason"] == "OPP" and trades[0]["exit_price"] == 101.5
    assert trades[1]["side"] == "short" and trades[1]["entry_price"] == 101.5


def test_stop_inside_spread_is_hit_immediately():
    rows = [(100, 101, 99, 100), (100, 100.5, 99.5, 100)]
    _, trades, _, _ = run(rows, [intent(0, sl=100.2, tps=((110, 1.0),), entry=100.3)], cfg(spread=1.0))
    t = trades[0]
    assert t["entry_price"] == pytest.approx(100.5) and t["exit_price"] == pytest.approx(99.5)
    assert t["exit_reason"] == "SL"


def test_mae_mfe_tracked_after_entry():
    rows = [(100, 101, 99, 100), (100, 103, 98, 101), (101, 111, 100, 110)]
    _, trades, _, _ = run(rows, [intent(0, sl=95, tps=((110, 1.0),))], cfg())
    t = trades[0]
    assert t["mae"] == pytest.approx(2.0)  # low 98 after entry at 100
    assert t["mfe"] == pytest.approx(10.0)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        ExecutionConfig.from_dict({"spread": -1})
    with pytest.raises(ValueError):
        ExecutionConfig.from_dict({"bogus": 1})
