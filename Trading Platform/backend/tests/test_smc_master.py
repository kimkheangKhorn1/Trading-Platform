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


def test_smc_master_is_also_a_strategy(bars):
    """The script plays both roles: it declares the SMC Master strategy, a backtest with no entry rules yet
    runs cleanly with no trades, and signals emitted from SmcMaster.strategy() are traded."""
    from app.backtest.config import ExecutionConfig
    from app.backtest.engine import run_backtest
    from app.scripts.smc_master.metadata import STRATEGY_ID

    assert [s.id for s in SmcMaster.meta.strategies] == [STRATEGY_ID]
    kw = dict(inputs={}, timeframe="M15", start=int(bars.time[400]), end=int(bars.time[-1]),
              execution=ExecutionConfig(), htf_provider=CalendarProvider(bars))
    empty = run_backtest(bars, script_cls=SmcMaster, **kw)
    assert empty["summary"]["signals"] == 0 and empty["trades"] == []
    assert any("no trade signals" in w for w in empty["warnings"])

    class WithEntries(SmcMaster):
        def strategy(self, ctx):
            if ctx.bar_index % 200 == 0 and ctx.bar_index > 0:
                sl = ctx.low - 5.0
                ctx.signal(STRATEGY_ID, "long", stop_loss=sl, take_profits=[(ctx.close + 2 * (ctx.close - sl), 1.0)])

    traded = run_backtest(bars, script_cls=WithEntries, **kw)
    assert traded["summary"]["signals"] > 0 and len(traded["trades"]) > 0
    assert {t["strategy"] for t in traded["trades"]} == {STRATEGY_ID}


class _Events(SmcMaster):
    swing_length = None  # a short swing length keeps hand-built scenarios small (the input's minimum is 10)

    def setup(self, ctx):
        if self.swing_length: self.p = {**self.p, "length": self.swing_length}
        super().setup(ctx); self.all_events = []

    def emit(self, ctx, title, group, **extra):
        if ctx.is_confirmed: self.all_events.append((ctx.bar_index, title))
        super().emit(ctx, title, group, **extra)


def test_swing_low_is_the_leg_extreme_after_a_wick_sweep():
    """A wick below a confirmed swing low (close back above) is not a new pivot while the detector waits for a
    swing high. When that high confirms, the swing low becomes the leg's real low, so a close below the old low
    but above the sweep is not a break of structure; the close below the sweep low is."""
    from tests.helpers import bars_from_ohlc
    b = bars_from_ohlc([
        (105, 106, 104, 105), (105, 105.5, 103, 103.5), (103.5, 104, 101, 101.5), (101.5, 102, 100, 100.5),  # swing low 100
        (100.5, 102, 100.5, 101.5), (101.5, 102.5, 101, 102), (102, 103, 101.5, 102.5),
        (102.5, 102.8, 99, 101.5),  # wick sweep to 99, close back above 100
        (101.5, 103.5, 101, 103), (103, 105, 102.5, 104.5),  # swing high 105
        (104.5, 104.8, 103, 103.5), (103.5, 104, 101.5, 102), (102, 102.5, 100.5, 101),
        (101, 101.5, 99.5, 99.7),  # closes below 100 but above the 99 sweep
        (99.7, 100, 98, 98.5),  # closes below 99
    ], step=900)
    cls = type("Len3", (_Events,), {"swing_length": 3})
    r = ScriptRunner(cls, {"show_pdhl": False, "show_pwhl": False, "show_pmhl": False}, timeframe="M15")
    r.process(b)
    swing = [(i, t) for i, t in r.script.all_events if ("BOS" in t or "CHoCH" in t) and "Internal" not in t]
    assert [i for i, t in swing if t.startswith("Bearish")] == [14]
    assert r.script.struct[1]["bottom"] == 99 and r.script.struct[1]["bx"] == 7


def test_mfi_row_is_the_standard_money_flow_index(bars):
    """Dashboard MFI = 100 - 100 / (1 + positive/negative money flow) on the typical price (H+L+C)/3."""
    b = bars.slice(0, 300)
    r = ScriptRunner(SmcMaster, {"show_pdhl": False, "show_pwhl": False, "show_pmhl": False}, timeframe="M15")
    r.process(b)
    n = r.script.p["mfiLength"]
    tp = (b.high + b.low + b.close) / 3
    flow = tp * b.volume
    up = sum(flow[i] for i in range(len(b) - n, len(b)) if tp[i] > tp[i - 1])
    dn = sum(flow[i] for i in range(len(b) - n, len(b)) if tp[i] < tp[i - 1])
    [row] = [x for x in r.script.tables[0]["rows"] if x["id"] == "mfi"]
    assert row["value"] == pytest.approx(100 - 100 / (1 + up / dn))


def test_every_drawing_is_anchored_on_a_bar_time(bars):
    """The chart can only place drawings at candle times (or at future candle times right of the last bar).
    A midpoint computed by averaging two timestamps falls between candles and was drawn at the far left."""
    out = _run(bars).output()
    t0, step = int(bars.time[0]), 900
    off_grid = []
    for d in out["drawings"]:
        for key in ("x", "x1", "x2", "left", "right"):
            if d.get(key) is not None and (int(d[key]) - t0) % step:
                off_grid.append((d["group"], d["kind"], key, d.get("text", "")))
    assert off_grid == []


@pytest.mark.parametrize("seed", [2, 3, 4, 5])
def test_pivot_levels_shared_by_aligned_zones_get_one_label(seed):
    """Align Zones snaps an overlapping new pivot zone onto an older one's edges; the shared level is one badge."""
    data = random_walk_bars(4000, start="2026-03-02 00:00", step=900, seed=seed)
    out = _run(data).output()
    texts = [d["text"] for d in out["drawings"] if d["group"] == "pivot_sr" and d["kind"] == "label"]
    assert texts and len(texts) == len(set(texts))
