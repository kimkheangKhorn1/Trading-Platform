"""SMC & ICT & Indicators + Scalping Master: engine integration (synthetic data only)."""
import numpy as np
import pytest

from app.engine.bars import Bars
from app.engine.registry import get_script, list_scripts
from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.market.repository import MarketStore
from app.market.resample import resample
from app.market.timeframes import TIMEFRAMES, bucket_start, get_timeframe
from app.scripts.smc_master import SmcMaster
from tests.helpers import random_walk_bars, ts

M15, D = TIMEFRAMES["M15"], TIMEFRAMES["D"]


class CalendarProvider:
    """HTF candles resampled from the full synthetic dataset (W/M from the daily candles)."""

    def __init__(self, full: Bars):
        self.daily = resample(full, M15, D)
        self.full = full

    def load(self, timeframe, start, end, warmup_bars):
        if timeframe in ("W", "M"):
            return resample(self.daily, D, get_timeframe(timeframe, reference=True))
        return resample(self.full, M15, TIMEFRAMES[timeframe])


@pytest.fixture(scope="module")
def bars():
    return random_walk_bars(4000, start="2026-03-02 00:00", step=900, seed=21)  # ~2 months of M15


def _run(bars, chunks=(), params=None):
    r = ScriptRunner(SmcMaster, params or {}, timeframe="M15", htf_provider=CalendarProvider(bars))
    for k in chunks:
        r.process(bars.slice(0, k))
    r.process(bars)
    return r


def _strip(out):
    return {k: v for k, v in out.items() if k not in ("bars_processed",)}


def test_registered_with_metadata():
    assert get_script("smc_ict_scalping_master") is SmcMaster
    assert SmcMaster in list_scripts()
    meta = SmcMaster.meta.to_dict()
    assert len(meta["inputs"]) == 210 and {"show_pwhl", "show_pmhl", "fvg_tf"} <= {i["id"] for i in meta["inputs"]}


def test_full_run_outputs(bars):
    out = _run(bars).output()
    groups = {d["group"] for d in out["drawings"]}
    assert {"internal_structure", "swing_structure", "demand_supply", "pivot_sr", "mtf"} <= groups
    assert len(out["candle_colors"]) == len(bars)
    assert any(c[1] is None for c in out["candle_colors"]) and any(c[1] for c in out["candle_colors"])
    [table] = out["tables"]
    assert table["position"] == "bottom_right" and len(table["rows"]) == 10
    assert out["warnings"] == []


def test_previous_period_levels_match_daily_candles(bars):
    r = _run(bars, params={"aoi_max_dist": 0.0})
    labels = {d["text"]: d["y"] for d in r.output()["drawings"] if d["group"] == "mtf" and d["kind"] == "label"}
    daily = resample(bars, M15, D)
    t = int(bars.time[-1])
    for tf, n in (("D", "D"), ("W", "W"), ("M", "M")):
        cur = bucket_start(t, get_timeframe(tf, reference=True))
        prev = bucket_start(cur - 1, get_timeframe(tf, reference=True))
        days = (daily.time >= prev) & (daily.time < cur)
        assert labels[f"P{n}H"] == pytest.approx(daily.high[days].max())
        assert labels[f"P{n}L"] == pytest.approx(daily.low[days].min())


def test_incremental_equals_full(bars):
    full = _run(bars).output()
    inc = _run(bars, chunks=(1500, 1501, 2600, 3999)).output()
    assert _strip(inc) == _strip(full)


def test_no_lookahead_prefix_invariance(bars):
    """Plots, markers and candle colors before bar k must not change when later bars arrive (the
    higher-timeframe provider deliberately holds the 'future' bars too)."""
    full = _run(bars).output()
    before = lambda items, t_k: [x for x in items if (x["time"] if isinstance(x, dict) else x[0]) < t_k]
    for k in (1500, 3000):
        r = ScriptRunner(SmcMaster, {}, timeframe="M15", htf_provider=CalendarProvider(bars))
        r.process(bars.slice(0, k))
        part, t_k = r.output(), int(bars.time[k])
        assert {pid: v[:k] for pid, v in part["plots"].items()} == {pid: v[:k] for pid, v in full["plots"].items()}
        assert {sid: before(v, t_k) for sid, v in part["shapes"].items()} == \
               {sid: before(v, t_k) for sid, v in full["shapes"].items()}
        assert before(part["candle_colors"], t_k) == before(full["candle_colors"], t_k)


def test_live_bar_is_rolled_back(bars):
    k = len(bars) - 1
    r = _run(bars.slice(0, k))
    for bump in (3.0, -4.0):
        cl = bars.close[: k + 1].copy()
        cl[-1] += bump
        flags = np.ones(k + 1, dtype=bool)
        flags[-1] = False
        r.process(Bars(bars.time[: k + 1], bars.open[: k + 1], np.maximum(bars.high[: k + 1], cl),
                       np.minimum(bars.low[: k + 1], cl), cl, bars.volume[: k + 1], flags))
        assert r.output()["provisional_bar"]
    r.process(bars)
    assert _strip(r.output()) == _strip(_run(bars).output())


def test_monthly_levels_need_a_completed_month(tmp_path):
    """Daily history that starts mid-month cannot give March's range: no previous month (na) on any
    March or April bar, plus a warning, instead of a partial month presented as complete."""
    store = MarketStore(tmp_path / "m.db")
    feed = store.ensure_feed("t", provider="csv", symbol="XAUUSD", instrument=None, price_type="mid",
                             display_name="t")
    src = random_walk_bars(1500, start="2026-03-11 00:00", step=900, seed=3)
    for tf, b in (("M15", src), ("D", resample(src, M15, D))):
        s = store.ensure_series(feed.id, tf)
        store.upsert_candles(s.id, zip(b.time.tolist(), b.open.tolist(), b.high.tolist(), b.low.tolist(),
                                       b.close.tolist(), b.volume.tolist(), b.complete.astype(int).tolist()))
    provider = StoreHtfProvider(store, feed)
    months = provider.load("M", int(src.time[0]), int(src.time[-1]), 500)
    assert months.time.tolist() == [ts("2026-03-31 21:00")]  # March is cut off, April opens Mar 31 17:00 NY
    weeks = provider.load("W", int(src.time[0]), int(src.time[-1]), 500)
    assert int(weeks.time[0]) > int(src.time[0])  # the partial first week is dropped too
    r = ScriptRunner(SmcMaster, {}, timeframe="M15", alignment=feed.alignment, htf_provider=provider)
    r.process(src)
    assert any("Previous M" in w for w in r.output()["warnings"])
