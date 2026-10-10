"""Pivot S/R display lifecycle: one continuous box per zone through confirmed role flips."""
import numpy as np
import pytest

from app.engine.bars import Bars
from app.engine.runner import ScriptRunner
from app.scripts.smc_master import SmcMaster
from app.scripts.smc_master.indicator import Zone
from tests.helpers import bars_from_ohlc


NO_HTF = {"show_pdhl": False, "show_pwhl": False, "show_pmhl": False}


class PivotScenario(SmcMaster):
    """Isolate the actual pivot engine with one known resistance zone (99–101)."""

    def on_bar(self, ctx):
        self.rows.append((ctx.time, ctx.open, ctx.high, ctx.low, ctx.close, ctx.volume))
        atr = self.zone_atr.update(ctx.high, ctx.low, ctx.close)
        self.zone_atrs.append(atr)
        if ctx.bar_index == 0:
            self.pivots[0] = [Zone(101, 99, ctx.time, ctx.time, -1, "pivot_sr", identity=1)]
        self.pivot_system(ctx, atr)

    def on_last_bar(self, ctx):
        pass  # avoid unrelated last-bar drawings; use the production pivot engine above


def runner(**params):
    return ScriptRunner(PivotScenario, {**NO_HTF, "dhighs": False, "dlows": False,
                                      "detectBO": False, "detectBD": False, **params}, timeframe="M5")


def pivot_boxes(r):
    return [d for d in r.output()["drawings"] if d["kind"] == "box" and d["group"] == "pivot_sr"]


@pytest.mark.parametrize("extend", [False, True])
def test_breaks_and_reclaims_recolor_one_box_from_original_pivot(extend):
    bars = bars_from_ohlc([
        (100, 100.5, 99.5, 100),
        (100, 103, 99.5, 100.5),  # wick above resistance, close still inside
        (100.5, 102, 100, 101),  # close exactly at top: no break
        (101, 103, 100, 102),  # resistance becomes support
        (102, 103, 100, 102.5),  # remains support; no repeat break
        (102.5, 103, 97, 100),  # wick below support, close inside
        (100, 101, 98, 99),  # close exactly at bottom: no break
        (99, 100, 97, 98),  # support becomes resistance (full reclaim)
        (98, 103, 97, 102),  # resistance becomes support again
    ])
    r = runner(extend=extend, lookback=10)
    original_id = None
    for k, side in enumerate([-1, -1, -1, 1, 1, 1, 1, -1, 1], start=1):
        r.process(bars.slice(0, k))
        [box] = pivot_boxes(r)
        original_id = original_id or box["id"]
        assert box["id"] == original_id
        assert (box["left"], box["top"], box["bottom"]) == (int(bars.time[0]), 101, 99)
        assert box["right"] == int(bars.time[k - 1])
        assert box["extend"] == ("right" if extend else "none")
        assert box["border_color"] == r.script.p["bullBorder" if side == 1 else "bearBorder"]
        assert box["bg_color"] == r.script.p["bullBgCol" if side == 1 else "bearBgCol"]
        assert r.script.pivots[0][0].side == side
        assert r.script.retired == []
    out = r.output()
    assert [(h["up"], h["dn"]) for h in r.script.pivot_history] == [(0, 0), (0, 0), (0, 0), (1, 0), (0, 0), (0, 0), (0, 0), (0, 1), (1, 0)]
    assert len(out["shapes"]["resistance_break"]) == 2
    assert len(out["shapes"]["false_breakup"]) == 1  # same-zone reclaim still works


def test_forming_candle_cannot_recolor_or_split_zone():
    committed = bars_from_ohlc([(100, 100.5, 99.5, 100)])
    r = runner()
    r.process(committed)
    [before] = pivot_boxes(r)
    for close in [103, 98, 100.5]:
        all_bars = bars_from_ohlc([(100, 100.5, 99.5, 100), (100, 104, 97, close)])
        flags = np.array([True, False])
        provisional = Bars(all_bars.time, all_bars.open, all_bars.high, all_bars.low,
                           all_bars.close, all_bars.volume, flags)
        r.process(provisional)
        [box] = pivot_boxes(r)
        assert box["id"] == before["id"] and box["left"] == before["left"]
        assert box["border_color"] == before["border_color"]
        assert box["bg_color"] == before["bg_color"]
        assert r.script.retired == []
    closed = bars_from_ohlc([(100, 100.5, 99.5, 100), (100, 104, 97, 103)])
    r.process(closed)
    [box] = pivot_boxes(r)
    assert box["id"] == before["id"] and box["left"] == before["left"]
    assert box["border_color"] == r.script.p["bullBorder"]
    full = runner()
    full.process(closed)
    assert r.output() == full.output()


def test_retired_zone_keeps_its_final_role_even_with_equal_border_colors():
    # Retiring an aged-out pivot remains supported; only breaks stop retiring boxes.
    r = runner(bullBorder="#FFFFFFFF", bearBorder="#FFFFFFFF")
    r.process(bars_from_ohlc([(100, 100.5, 99.5, 100)]))
    zone = r.script.pivots[0].pop()
    box = zone.box
    r.script.retire(r.ctx, zone)
    assert r.script.retired == [box]
    assert box._bull is False
    assert box.extend == "none"
    assert zone.box is None
