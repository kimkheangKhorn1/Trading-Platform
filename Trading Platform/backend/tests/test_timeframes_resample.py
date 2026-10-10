import numpy as np
import pytest

from app.engine.bars import Bars
from app.market.resample import resample
from app.market.timeframes import (TIMEFRAMES, bar_close_times, bucket_end, bucket_start, bucket_starts,
                                   get_timeframe)
from tests.helpers import random_walk_bars, ts

H4, D, H1, M5, M1 = (TIMEFRAMES[k] for k in ("H4", "D", "H1", "M5", "M1"))


def test_intraday_buckets_floor_to_utc():
    assert bucket_start(ts("2026-03-02 10:07"), M5) == ts("2026-03-02 10:05")
    assert bucket_start(ts("2026-03-02 10:59"), H1) == ts("2026-03-02 10:00")


@pytest.mark.parametrize("when,expected", [
    # winter (EST, UTC-5): trading day starts 22:00 UTC
    ("2026-01-14 21:59", "2026-01-13 22:00"),
    ("2026-01-14 22:00", "2026-01-14 22:00"),
    # summer (EDT, UTC-4): trading day starts 21:00 UTC
    ("2026-07-15 20:59", "2026-07-14 21:00"),
    ("2026-07-15 21:00", "2026-07-15 21:00"),
])
def test_daily_bucket_ny17(when, expected):
    assert bucket_start(ts(when), D, "ny17") == ts(expected)


def test_h4_ny17_boundaries_shift_with_dst():
    # EST: 17:00 NY = 22:00 UTC, so H4 opens at 22, 02, 06, 10, 14, 18 UTC
    assert bucket_start(ts("2026-01-14 11:30"), H4, "ny17") == ts("2026-01-14 10:00")
    # EDT: opens at 21, 01, 05, 09, 13, 17 UTC
    assert bucket_start(ts("2026-07-15 11:30"), H4, "ny17") == ts("2026-07-15 09:00")
    # UTC alignment ignores New York entirely
    assert bucket_start(ts("2026-07-15 11:30"), H4, "utc") == ts("2026-07-15 08:00")
    assert bucket_start(ts("2026-07-15 11:30"), D, "utc") == ts("2026-07-15 00:00")


def test_bucket_end_handles_dst_weekend():
    # Sunday 2026-03-08 is the US spring-forward date: the Sat 17:00 EST bucket is 23h long
    start = ts("2026-03-07 22:00")  # Sat 17:00 EST
    assert bucket_end(start, D, "ny17") == ts("2026-03-08 21:00")  # Sun 17:00 EDT
    assert bucket_end(ts("2026-03-02 10:00"), H4, "ny17") == ts("2026-03-02 14:00")


def test_bar_close_times_never_use_next_bar():
    times = np.array([ts("2026-03-02 10:00"), ts("2026-03-02 14:00")])
    assert list(bar_close_times(times, H4, "ny17")) == [ts("2026-03-02 14:00"), ts("2026-03-02 18:00")]


def test_get_timeframe_rejects_unknown():
    with pytest.raises(ValueError):
        get_timeframe("M2")


def test_resample_m1_to_m5_and_h1_values():
    src = random_walk_bars(240, start="2026-03-03 10:00", step=60, skip_weekends=False)
    m5 = resample(src, M1, M5)
    assert len(m5) == 48
    for k in range(48):
        seg = slice(k * 5, k * 5 + 5)
        assert m5.time[k] == src.time[k * 5]
        assert m5.open[k] == src.open[k * 5]
        assert m5.close[k] == src.close[k * 5 + 4]
        assert m5.high[k] == src.high[seg].max()
        assert m5.low[k] == src.low[seg].min()
        assert m5.volume[k] == pytest.approx(src.volume[seg].sum())
    assert m5.complete.all()
    h1 = resample(src, M1, H1)
    assert len(h1) == 4 and h1.complete.all()


def test_resample_marks_truncated_last_bucket_incomplete():
    src = random_walk_bars(90, start="2026-03-03 10:00", step=60, skip_weekends=False)  # 10:00-11:29
    h1 = resample(src, M1, H1)
    assert len(h1) == 2
    assert h1.complete[0] and not h1.complete[1]


def test_resample_propagates_incomplete_source():
    src = random_walk_bars(10, start="2026-03-03 10:00", step=60, skip_weekends=False)
    flags = np.ones(10, dtype=bool)
    flags[-1] = False
    src = Bars(src.time, src.open, src.high, src.low, src.close, src.volume, flags)
    m5 = resample(src, M1, M5)
    assert m5.complete.tolist() == [True, False]


def test_daily_resample_ny17_matches_bucket_starts():
    src = random_walk_bars(3000, start="2026-07-13 00:00", step=300)
    d = resample(src, M5, D, "ny17")
    assert np.all(bucket_starts(d.time, D, "ny17") == d.time)
    assert all(t % 3600 == 0 for t in d.time)
    # Every source bar falls inside exactly one daily bar
    keys = bucket_starts(src.time, D, "ny17")
    assert set(keys.tolist()) == set(d.time.tolist())


W, MN = get_timeframe("W", reference=True), get_timeframe("M", reference=True)


def test_weekly_monthly_are_reference_only():
    with pytest.raises(ValueError):
        get_timeframe("W")
    assert "W" not in TIMEFRAMES and "M" not in TIMEFRAMES


@pytest.mark.parametrize("when,week,month", [
    # week opens Sunday 17:00 New York; the month opens at 17:00 New York on its eve
    ("2026-06-03 12:00", "2026-05-31 21:00", "2026-05-31 21:00"),
    ("2026-06-07 20:59", "2026-05-31 21:00", "2026-05-31 21:00"),
    ("2026-06-07 21:00", "2026-06-07 21:00", "2026-05-31 21:00"),
    # 1 Aug 2026 is a Saturday: the August bucket opens Friday 31 Jul 17:00 New York
    ("2026-07-31 20:59", "2026-07-26 21:00", "2026-06-30 21:00"),
    ("2026-08-02 21:00", "2026-08-02 21:00", "2026-07-31 21:00"),
    # winter time (EST) and the March spring-forward week
    ("2026-01-01 03:00", "2025-12-28 22:00", "2025-12-31 22:00"),
    ("2026-03-08 22:00", "2026-03-08 21:00", "2026-02-28 22:00"),
])
def test_weekly_monthly_buckets_ny17(when, week, month):
    assert bucket_start(ts(when), W, "ny17") == ts(week)
    assert bucket_start(ts(when), MN, "ny17") == ts(month)


def test_weekly_monthly_bucket_ends():
    assert bucket_end(ts("2026-03-01 22:00"), W, "ny17") == ts("2026-03-08 21:00")  # 167h DST week
    assert bucket_end(ts("2026-01-31 22:00"), MN, "ny17") == ts("2026-02-28 22:00")  # February
    assert bucket_end(ts("2026-07-31 21:00"), MN, "ny17") == ts("2026-08-31 21:00")
    assert bucket_start(ts("2026-06-03 12:00"), W, "utc") == ts("2026-06-01 00:00")  # Monday
    assert bucket_start(ts("2026-06-03 12:00"), MN, "utc") == ts("2026-06-01 00:00")
    assert bucket_end(ts("2026-01-01 00:00"), MN, "utc") == ts("2026-02-01 00:00")
    assert list(bar_close_times(np.array([ts("2026-02-01 00:00")]), MN, "utc")) == [ts("2026-03-01 00:00")]


def test_daily_resamples_to_weekly_and_monthly():
    src = random_walk_bars(9000, start="2026-03-02 00:00", step=900)  # M15, ~3.5 months
    d = resample(src, TIMEFRAMES["M15"], D, "ny17")
    for tf in (W, MN):
        out = resample(d, D, tf, "ny17")
        keys = bucket_starts(src.time, tf, "ny17")
        assert out.time.tolist() == sorted(set(keys.tolist()))
        for k, t in enumerate(out.time):
            assert out.high[k] == src.high[keys == t].max() and out.low[k] == src.low[keys == t].min()
