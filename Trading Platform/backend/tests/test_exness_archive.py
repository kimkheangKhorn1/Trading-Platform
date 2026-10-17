"""Synthetic archive fixtures verify real ingestion invariants; never used as market data."""
import csv
import io
import zipfile

import httpx
import pandas as pd
import pyarrow.parquet as pq
import pytest

from app.market.exness_archive import ArchiveClient, sha256
from app.market.exness_dataset import database, stage_archive, merge_month, candles_for_month, load_training_candles


def archive(root, period, rows):
    name = 'Exness_XAUUSDm_' + period.replace('-', '_') + '.zip'
    path = root / 'raw' / period[:4] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(['Exness', 'Symbol', 'Timestamp', 'Bid', 'Ask'])
    writer.writerows(rows)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(name.replace('.zip', '.csv'), text.getvalue())
    return {'filename': name, 'period': period, 'relative_path': str(path.relative_to(root)),
            'remote_size': path.stat().st_size, 'remote_modified': 'test',
            'sha256': sha256(path), 'status': 'downloaded', 'url': 'https://ticks.ex2archive.com/test.zip'}


def tick(t, bid, ask):
    return ['exness', 'XAUUSDm', t, bid, ask]


def test_chunk_seams_dedup_order_ohlc_and_same_time_distinct_quotes(tmp_path):
    rows = [tick('2015-08-10 00:00:00.001Z', 100, 101),
            tick('2015-08-10 00:00:00.001Z', 102, 103),
            tick('2015-08-10 00:00:00.001Z', 100, 101),  # exact duplicate across chunks
            tick('2015-08-10 00:01:10.000Z', 99, 100),
            tick('2015-08-10 00:00:59.999Z', 101, 102),  # chronological repair
            tick('2015-08-10 00:06:00.000Z', 103, 104)]
    entry = archive(tmp_path, '2015-08', rows)
    quality, stages = stage_archive(tmp_path, entry, chunk_size=2)
    assert quality['input_rows'] == 6 and quality['invalid_rows'] == 0
    assert quality['out_of_order_transitions'] == 1
    with database(tmp_path) as con:
        stats = merge_month(con, tmp_path, '2015-08', stages['2015-08'], entry['filename'])
        candles_for_month(con, tmp_path, '2015-08', stats)
    assert stats['ticks'] == 5 and stats['duplicates_removed_in_last_merge'] == 1
    m1 = load_training_candles(tmp_path, 'M1')
    assert len(m1) == 3  # 00:00,00:01,00:06 only; no artificial gaps
    first = m1.iloc[0]
    assert (first.bid_open, first.bid_high, first.bid_low, first.bid_close) == (100,102,100,101)
    assert (first.ask_open, first.ask_high, first.ask_low, first.ask_close) == (101,103,101,102)
    assert first.tick_count == 3 and first.spread_mean == 1
    m5 = load_training_candles(tmp_path, 'M5')
    assert len(m5) == 2 and m5.tick_count.sum() == 5
    assert (m5.iloc[0].bid_open, m5.iloc[0].bid_close) == (100,99)
    # Idempotent replacement: a resume cannot duplicate quotes or candles.
    with database(tmp_path) as con:
        assert merge_month(con, tmp_path, '2015-08', stages['2015-08'], entry['filename'])['ticks'] == 5


def test_invalid_quotes_dates_timezone_and_symbol_are_quarantined(tmp_path):
    rows = [tick('2015-08-10 00:00:00.000Z', 100, 101),
            tick('2015-08-10 00:00:01.000Z', -1, 2),
            tick('2015-08-10 00:00:02.000Z', 110, 109),
            tick('bad', 100, 101),
            tick('2015-08-10 00:00:04', 100, 101),
            tick('2015-09-01 00:00:00.000Z', 100, 101),
            ['exness','XAUUSD','2015-08-10 00:00:06.000Z',100,101]]
    entry = archive(tmp_path, '2015-08', rows)
    quality, stages = stage_archive(tmp_path, entry, chunk_size=2)
    assert quality['invalid_rows'] == 6
    assert pq.read_table(stages['2015-08']).num_rows == 1
    quarantine = pd.read_csv(tmp_path / 'quarantine' / (entry['filename'] + '.csv.gz'))
    assert len(quarantine) == 6


def test_daily_monthly_overlap_does_not_duplicate_and_current_day_excluded(tmp_path):
    today = pd.Timestamp.now(tz='UTC').floor('D')
    period = today.strftime('%Y-%m')
    stamp = today.strftime('%Y-%m-%d') + ' 01:00:00.123456789Z'
    month = archive(tmp_path, period, [tick(stamp,100,101)])
    day = archive(tmp_path, today.strftime('%Y-%m-%d'), [tick(stamp,100,101),tick(stamp,102,103)])
    with database(tmp_path) as con:
        for entry in (month, day):
            _, stages = stage_archive(tmp_path, entry)
            stats = merge_month(con, tmp_path, period, stages[period], entry['filename'])
        candles_for_month(con, tmp_path, period, stats)
    assert stats['ticks'] == 2
    assert len(load_training_candles(tmp_path)) == 0
    assert len(load_training_candles(tmp_path,complete_only=False)) == 1
    raw = pq.read_table(tmp_path / 'ticks' / period[:4] / (period+'.parquet')).to_pandas()
    assert (raw.timestamp_ns % 1_000_000_000 == 123456789).all()
    assert (raw.source_priority == 3).all()


def test_download_http_range_resume_crc_and_hash_skip(tmp_path):
    entry = archive(tmp_path, '2015-08', [tick('2015-08-10 00:00:00.000Z',100,101)])
    dest = tmp_path / entry['relative_path']
    data = dest.read_bytes()
    dest.unlink()
    partial = dest.with_suffix('.zip.part')
    partial.write_bytes(data[:20])
    from app.market.exness_archive import atomic_json
    atomic_json(partial.with_suffix('.part.json'), {'size':len(data),'mtime':'test'})
    requests = []
    def respond(req):
        requests.append(req)
        assert req.headers['range'] == 'bytes=20-'
        return httpx.Response(206, headers={'content-range':f'bytes 20-{len(data)-1}/{len(data)}'},content=data[20:])
    client = ArchiveClient(tmp_path)
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(respond))
    got = client.download(entry)
    assert dest.read_bytes() == data and got['sha256'] == entry['sha256']
    client.manifest['archives'][entry['filename']] = got
    assert client.download(entry) == got and len(requests) == 1
    client.http.close()


def test_bad_content_range_rejected(tmp_path, monkeypatch):
    entry = archive(tmp_path, '2015-08', [tick('2015-08-10 00:00:00.000Z',100,101)])
    dest = tmp_path / entry['relative_path']
    data = dest.read_bytes()
    dest.unlink()
    client = ArchiveClient(tmp_path)
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(
        206,headers={'content-range':f'bytes 999-{len(data)-1}/{len(data)}'},content=data)))
    monkeypatch.setattr('app.market.exness_archive.time.sleep',lambda _:None)
    with pytest.raises(ValueError,match='Content-Range'):
        client.download(entry)
    assert not dest.exists()
    client.http.close()


def test_chart_import_keeps_closed_month_ends_complete(tmp_path, monkeypatch):
    from app.market.exness_dataset import import_month
    from app.market.repository import MarketStore
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'chart.db'))
    entry = archive(tmp_path,'2015-08',[tick('2015-08-31 20:58:55.000Z',100,101)])
    _,stages = stage_archive(tmp_path,entry)
    with database(tmp_path) as con:
        stats = merge_month(con,tmp_path,'2015-08',stages['2015-08'],entry['filename'])
        candles_for_month(con,tmp_path,'2015-08',stats)
    import_month(tmp_path,'2015-08',stats)
    store = MarketStore(tmp_path/'chart.db')
    for side in ('bid','ask'):
        feed = store.get_feed(f'exness-archive-xauusdm-{side}')
        assert feed.provider == 'exness_archive' and feed.alignment == 'utc'
        for series in store.list_series(feed.id):
            bars = store.load_bars(series.id)
            assert bars.complete.all(), series.timeframe


def test_ml_candidate_splits_share_holdouts_and_aware_date_loader(tmp_path):
    from app.market.exness_ml import prepare_ml,load_ml_split
    # Sparse synthetic samples exercise split definitions; never exposed as real history.
    dates = ['2022-01-03','2024-01-03','2025-01-03','2026-01-03','2026-04-03','2026-07-03','2026-10-12']
    with database(tmp_path) as con:
        for date in dates:
            period = date[:7]
            entry = archive(tmp_path,period,[tick(date+' 12:00:00.000Z',100,101)])
            _,stages = stage_archive(tmp_path,entry)
            stats = merge_month(con,tmp_path,period,stages[period],entry['filename'])
            candles_for_month(con,tmp_path,period,stats)
    spec = prepare_ml(tmp_path,gap_minutes=30)
    assert spec['recommendation_status'] == 'candidate_not_model_validated'
    ref = spec['splits']['train_24m']
    for months in (6,12,24,36):
        splits = spec['splits'][f'train_{months}m']
        assert splits['validation'] == ref['validation'] and splits['test'] == ref['test']
        assert pd.Timestamp(splits['train']['end_exclusive']) < pd.Timestamp(splits['validation']['start'])
    train = load_ml_split(tmp_path,'M1','train_24m','train')
    test = load_ml_split(tmp_path,'M1','train_24m','test')
    assert len(train) and len(test) and train.timestamp.max()<test.timestamp.min()
    assert train.complete.all() and test.complete.all()
    assert (tmp_path/'ml/M1_recent.parquet').exists()


def test_independent_accuracy_check_carries_candle_across_batch_seams(tmp_path):
    from tools.verify_exness_accuracy import recalculate, differences
    # Row groups deliberately split a minute and preserve distinct same-time quotes.
    import pyarrow as pa
    import numpy as np
    path = tmp_path/'ticks.parquet'
    table = pa.table({'timestamp_ns':np.array([0,1,59,60,299,300],dtype=np.int64)*1_000_000_000,
                      'bid':[100.,103.,101.,99.,104.,102.],
                      'ask':[101.,104.,102.,100.,105.,103.]})
    pq.write_table(table,path,row_group_size=2)
    result = recalculate(path,batch_size=2)
    first = result['M1'].iloc[0]
    assert (first.bid_open,first.bid_high,first.bid_low,first.bid_close,first.tick_count)==(100,103,100,101,3)
    five = result['M5'].iloc[0]
    assert (five.bid_open,five.bid_high,five.bid_low,five.bid_close,five.tick_count)==(100,104,99,104,5)
    altered = result['M1'].copy()
    altered.loc[0,'bid_close'] = 102  # valid OHLC bounds, but not the true last quote
    assert differences(result['M1'],altered)=={'bid_close':1}
    assert differences(result['M1'],altered,altered.columns)=={'bid_close':1}


def test_forced_integrity_audit_bypasses_cached_month_results(tmp_path):
    from app.market.exness_archive import atomic_json
    from app.market.exness_audit import verify_dataset
    entry = archive(tmp_path,'2015-08',[tick('2015-08-10 00:00:00.000Z',100,101)])
    _,stages = stage_archive(tmp_path,entry)
    with database(tmp_path) as con:
        stats = merge_month(con,tmp_path,'2015-08',stages['2015-08'],entry['filename'])
        candles_for_month(con,tmp_path,'2015-08',stats)
    entry['processed_sha256'] = entry['sha256']
    client = ArchiveClient(tmp_path)
    client.manifest.update(archives={entry['filename']:entry},months={'2015-08':stats},inventory=[entry])
    first = verify_dataset(client)
    assert first['status']=='passed'
    first['months']['2015-08']['duplicate_tick_groups'] = 123  # untrusted cached content
    atomic_json(tmp_path/'reports/verification.json',first)
    second = verify_dataset(client,force=True)
    assert second['status']=='passed' and second['duplicate_tick_groups']==0
    client.http.close()


def test_dedup_preserves_original_open_and_close_for_repeated_same_time_quote(tmp_path):
    from tools.verify_exness_accuracy import recalculate, differences, raw_year_candles
    # A -> B -> A at the opening AND closing timestamp. Removing the final A must
    # never turn the bar close into B, nor moving A to its last row turn open into B.
    rows = [tick('2015-08-10 00:00:00.000Z',100,101),
            tick('2015-08-10 00:00:00.000Z',102,103),
            tick('2015-08-10 00:00:00.000Z',100,101),
            tick('2015-08-10 00:04:59.000Z',110,111),
            tick('2015-08-10 00:04:59.000Z',112,113),
            tick('2015-08-10 00:04:59.000Z',110,111)]
    entry = archive(tmp_path,'2015-08',rows)
    _,stages = stage_archive(tmp_path,entry,chunk_size=2)
    with database(tmp_path) as con:
        stats = merge_month(con,tmp_path,'2015-08',stages['2015-08'],entry['filename'])
        candles_for_month(con,tmp_path,'2015-08',stats)
    path = tmp_path/'ticks/2015/2015-08.parquet'
    canonical = pq.read_table(path).to_pandas()
    assert stats['ticks']==4
    assert list(canonical.source_row)==[0,1,3,4]
    assert list(canonical.source_last_row)==[2,1,5,4]
    independent = recalculate(path,batch_size=1)  # ties cross every batch boundary
    original = raw_year_candles(tmp_path,entry)
    for tf in ('M1','M5'):
        saved = pq.read_table(tmp_path/'candles'/tf/'2015/2015-08.parquet').to_pandas().set_index('timestamp')
        assert saved.iloc[0].bid_open==100
        assert saved.iloc[-1].bid_close==110
        assert saved.iloc[-1].ask_close==111
        assert not differences(independent[tf],saved)
        ohlc = [side+'_'+key for side in ('bid','ask') for key in ('open','high','low','close')]
        assert not differences(original[tf],saved,ohlc)


def test_source_order_merge_accepts_legacy_partition_with_other_archive(tmp_path):
    month = archive(tmp_path,'2015-08',[tick('2015-08-10 00:00:00.000Z',100,101)])
    day = archive(tmp_path,'2015-08-11',[tick('2015-08-11 00:00:59.000Z',110,111),
                                      tick('2015-08-11 00:00:59.000Z',112,113),
                                      tick('2015-08-11 00:00:59.000Z',110,111)])
    with database(tmp_path) as con:
        _,stages = stage_archive(tmp_path,month)
        merge_month(con,tmp_path,'2015-08',stages['2015-08'],month['filename'])
        path = tmp_path/'ticks/2015/2015-08.parquet'
        legacy = pq.read_table(path).drop(['source_last_row'])
        pq.write_table(legacy,path)
        _,stages = stage_archive(tmp_path,day)
        stats = merge_month(con,tmp_path,'2015-08',stages['2015-08'],day['filename'])
        candles_for_month(con,tmp_path,'2015-08',stats)
    assert stats['ticks']==3
    frame = pq.read_table(path).to_pandas()
    assert frame.source_last_row.notna().all()
    assert frame.iloc[0].source_last_row==frame.iloc[0].source_row==0
    candles = load_training_candles(tmp_path)
    assert candles.iloc[-1].bid_close==110 and candles.iloc[-1].ask_close==111


def test_history_status_does_not_show_old_pass_during_source_order_repair(tmp_path,monkeypatch):
    from app import main
    from app.market.exness_archive import atomic_json
    monkeypatch.setattr(main,'BACKEND_DIR',tmp_path)
    root = tmp_path/'data/exness/XAUUSDm'
    entry = {'filename':'test.zip','period':'2015-08','status':'downloaded','sha256':'abc','processed_sha256':None}
    manifest = {'archives':{'test.zip':entry},'inventory':[entry],'months':{}}
    atomic_json(root/'manifest.json',manifest)
    audit = {'status':'passed','archive_hashes':{'test.zip':'abc'},'verified_at':'2026-10-13T17:00:00+00:00'}
    atomic_json(root/'reports/verification.json',audit)
    assert main.exness_history_status()['verification']=='pending'
    entry['processed_sha256']='abc'
    atomic_json(root/'manifest.json',manifest)
    atomic_json(root/'reports/source_order_repair.json',{'status':'running','started_at':'2026-10-13T17:01:00+00:00'})
    assert main.exness_history_status()['verification']=='pending'
    atomic_json(root/'reports/source_order_repair.json',{'status':'completed','finished_at':'2026-10-13T17:02:00+00:00'})
    assert main.exness_history_status()['verification']=='pending'
    audit['verified_at']='2026-10-13T17:03:00+00:00'
    atomic_json(root/'reports/verification.json',audit)
    atomic_json(root/'reports/accuracy_check.json',{'status':'failed','started_at':'2026-10-13T17:04:00+00:00'})
    assert main.exness_history_status()['verification']=='failed'
    atomic_json(root/'reports/accuracy_check.json',{'status':'passed','started_at':'2026-10-13T17:04:00+00:00'})
    assert main.exness_history_status()['verification']=='passed'
