import pytest

from app.market.csv_import import CsvImportError, parse_csv
from app.market.importer import import_csv
from app.market.repository import MarketStore
from tests.helpers import ts


def test_header_comma_utc():
    text = "time,open,high,low,close,volume\n" + "\n".join(
        f"2026-03-02 10:0{i}:00,{2000 + i},{2001 + i},{1999 + i},{2000.5 + i},{10 + i}" for i in range(5))
    bars, tf, rep = parse_csv(text)
    assert tf.code == "M1"
    assert bars.time[0] == ts("2026-03-02 10:00")
    assert bars.close[-1] == 2004.5
    assert rep.rows_imported == 5 and not rep.warnings


def test_mt5_tab_export_with_angle_headers():
    text = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n" + "\n".join(
        f"2026.03.02\t10:{i * 5:02d}:00\t2000\t2002\t1999\t2001\t{100 + i}\t0\t20" for i in range(4))
    bars, tf, _ = parse_csv(text)
    assert tf.code == "M5"
    assert bars.volume[0] == 100
    assert bars.time[1] == ts("2026-03-02 10:05")


def test_headerless_mt4_history():
    text = "\n".join(f"2026.03.02,10:{i:02d},2000,2002,1999,2001,5" for i in range(6))
    bars, tf, _ = parse_csv(text)
    assert tf.code == "M1" and len(bars) == 6


def test_headerless_histdata_semicolon_with_fixed_offset():
    # HistData uses EST without DST (UTC-5)
    text = "\n".join(f"20260302 0{i}0000;2000;2002;1999;2001;0" for i in range(3))
    bars, tf, _ = parse_csv(text, timezone="UTC-5")
    assert tf.code == "H1"
    assert bars.time[0] == ts("2026-03-02 05:00")


def test_dukascopy_day_first_dates():
    text = "Gmt time,Open,High,Low,Close,Volume\n" + "\n".join(
        f"02.03.2026 10:{i:02d}:00.000,2000,2002,1999,2001,1.5" for i in range(3))
    bars, _, _ = parse_csv(text)
    assert bars.time[0] == ts("2026-03-02 10:00")


def test_unix_milliseconds():
    base = ts("2026-03-02 10:00") * 1000
    text = "timestamp,open,high,low,close\n" + "\n".join(
        f"{base + i * 60000},2000,2002,1999,2001" for i in range(3))
    bars, tf, _ = parse_csv(text)
    assert tf.code == "M1" and bars.time[2] == ts("2026-03-02 10:02")


def test_broker_server_time_ny_plus_7():
    # 00:00 server time (NY+7) in July = 17:00 EDT = 21:00 UTC
    text = "time,open,high,low,close\n2026-07-15 00:00,1,2,0.5,1.5\n2026-07-15 01:00,1,2,0.5,1.5\n2026-07-15 02:00,1,2,0.5,1.5"
    bars, _, _ = parse_csv(text, timezone="NY+7")
    assert bars.time[0] == ts("2026-07-14 21:00")


def test_invalid_rows_rejected_not_repaired():
    text = ("time,open,high,low,close\n"
            "2026-03-02 10:00,2000,2002,1999,2001\n"
            "2026-03-02 10:01,2000,1990,1999,2001\n"  # high below open/close
            "2026-03-02 10:02,abc,2002,1999,2001\n"  # unparsable
            "2026-03-02 10:03,2000,2002,1999,2001\n"
            "2026-03-02 10:03,2000,2003,1999,2002\n"  # duplicate -> last kept
            "2026-03-02 10:04,2000,2002,1999,2001\n")
    bars, _, rep = parse_csv(text, timeframe="M1")
    assert rep.rejected_invalid_ohlc == 1
    assert rep.rejected_unparsable == 1
    assert rep.duplicates_dropped == 1
    assert len(bars) == 3
    assert bars.high[1] == 2003


def test_misaligned_rows_rejected():
    text = "time,open,high,low,close\n" + "\n".join(
        f"2026-03-02 10:{m:02d}:00,1,2,0.5,1.5" for m in (0, 5, 10, 12, 15))
    bars, tf, rep = parse_csv(text)
    assert tf.code == "M5" and rep.rejected_misaligned == 1 and len(bars) == 4


def test_empty_and_garbage_files():
    with pytest.raises(CsvImportError):
        parse_csv("")
    with pytest.raises(CsvImportError):
        parse_csv("hello world\nfoo bar")


def test_import_builds_higher_timeframes(tmp_path):
    store = MarketStore(tmp_path / "t.db")
    rows = []
    t = ts("2026-03-03 10:00")
    for i in range(600):  # 10 hours of M1
        p = 2000 + (i % 17) - 8
        rows.append(f"{t + i * 60},{p},{p + 1},{p - 1},{p + 0.5},3")
    text = "timestamp,open,high,low,close,volume\n" + "\n".join(rows)
    feed, rep = import_csv(store, text, symbol="XAUUSD", dataset="unit", price_type="bid")
    assert rep.rows_imported == 600
    tfs = {s.timeframe: s for s in store.list_series(feed.id)}
    assert set(tfs) == {"M1", "M5", "M15", "M30", "H1", "H4", "D"}
    assert tfs["M1"].origin == "native" and tfs["H1"].origin == "resampled"
    h1 = store.load_bars(tfs["H1"].id)
    assert len(h1) == 10
    assert h1.volume[0] == 180
    # re-importing the same file is idempotent
    import_csv(store, text, symbol="XAUUSD", dataset="unit", price_type="bid")
    assert store.series_stats(tfs["M1"].id)["count"] == 600
    with pytest.raises(CsvImportError):  # different base timeframe into the same dataset
        m5 = "timestamp,open,high,low,close\n" + "\n".join(f"{t + i * 300},1,2,0.5,1.5" for i in range(5))
        import_csv(store, m5, symbol="XAUUSD", dataset="unit", price_type="bid")
