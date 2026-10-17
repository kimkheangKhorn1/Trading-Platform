"""Decision-time synchronization, missing history, feed isolation and API guards."""
import fcntl
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.market.confirmed_data import (SnapshotError, archive_intrabar, archive_snapshot,
                                       confirmed_from_minutes, utc_seconds)
from app.market.exness_archive import ArchiveClient, atomic_json
from app.market.exness_audit import verify_dataset
from app.market.exness_catalog import build_catalog
from app.market.exness_dataset import build_report, import_month
from app.market.exness_ml import prepare_ml
from app.market.exness_source_audit import verify_source_month
from tests.test_exness_archive import tick
from tests.test_exness_ml_data import processed


@pytest.fixture
def source(tmp_path):
    times=pd.date_range('2026-01-02T09:00:00Z',periods=180,freq='min')
    # Huge later prices make any accidental use of the 10:00/11:00 hour obvious.
    rows=[tick(t.strftime('%Y-%m-%d %H:%M:%S.000Z'),100+i if i<60 else 1000+i,
               102+i if i<60 else 1002+i) for i,t in enumerate(times)]
    entry,months=processed(tmp_path,'2026-01',rows)
    client=ArchiveClient(tmp_path)
    client.manifest.update(archives={entry['filename']:entry},inventory=[entry],months=months)
    build_report(client)
    client.save()
    verified=verify_dataset(client,force=True)
    raw=verify_source_month(tmp_path,'2026-01',[entry])
    atomic_json(tmp_path/'reports/accuracy_check.json',{'status':'passed','finished_at':'2026-10-14T00:00:00Z',
                'integrity':verified,'full_source_months':{'2026-01':raw}})
    prepare_ml(tmp_path)
    build_catalog(tmp_path)
    client.http.close()
    return tmp_path,months


def minutes(source):
    return pq.read_table(source[0]/'candles/M1/2026/2026-01.parquet').to_pandas()


def test_1025_snapshot_does_not_use_unclosed_hour_and_retains_bid_ask_spreads(source):
    result=archive_snapshot(source[0],'2026-01-02T10:25:00Z')
    assert result['all_timeframes_confirmed']
    hour=result['snapshots']['H1']['bar']
    assert hour['timestamp']==utc_seconds('2026-01-02T09:00:00Z')
    assert hour['close_time']==utc_seconds('2026-01-02T10:00:00Z')
    assert hour['bid_open']==100 and hour['bid_high']==hour['bid_close']==159
    assert hour['ask_close']==161 and hour['tick_count']==60
    assert hour['spread_min']==hour['spread_max']==hour['spread_mean']==2
    assert isinstance(hour['first_tick_ns'],str)
    assert int(hour['first_tick_ns'])==pd.Timestamp('2026-01-02T09:00:00Z').value
    assert result['snapshots']['M5']['bar']['timestamp']==utc_seconds('2026-01-02T10:20:00Z')
    assert result['execution_feed_verification']['status']=='not_verified'


def test_hour_becomes_visible_exactly_at_close_and_offset_time_is_equivalent(source):
    before=archive_snapshot(source[0],'2026-01-02T10:59:59Z',['H1'])
    exact=archive_snapshot(source[0],'2026-01-02T11:00:00Z',['H1'])
    assert before['snapshots']['H1']['bar']['bid_close']==159
    assert exact['snapshots']['H1']['bar']['bid_close']==1119
    offset=archive_snapshot(source[0],'2026-01-02T18:00:00+07:00',['H1'])
    assert offset==exact
    with pytest.raises(SnapshotError,match='offset'):
        archive_snapshot(source[0],'2026-01-02T11:00:00',['H1'])


def test_gap_blocks_closed_hour_instead_of_falling_back_to_old_hour(source):
    frame=minutes(source)
    missing=utc_seconds('2026-01-02T10:10:00Z')
    frame=frame[frame.timestamp!=missing]
    result=confirmed_from_minutes(frame,utc_seconds('2026-01-02T11:00:00Z'))
    hour=result['snapshots']['H1']
    assert not hour['confirmed'] and hour['bar'] is None
    assert hour['observed_M1_minutes']==59
    assert hour['missing_intervals']==[{'start_utc':'2026-01-02T10:10:00+00:00',
          'end_exclusive_utc':'2026-01-02T10:11:00+00:00','missing_minutes':1}]
    assert result['snapshots']['M5']['confirmed']


def test_elapsed_time_does_not_override_source_incomplete_flag(source):
    frame=minutes(source)
    frame.loc[frame.timestamp==utc_seconds('2026-01-02T09:30:00Z'),'complete']=False
    hour=confirmed_from_minutes(frame,utc_seconds('2026-01-02T10:25:00Z'),['H1'])['snapshots']['H1']
    assert not hour['confirmed'] and hour['blocked_reasons']==['source_snapshot_incomplete']
    assert hour['bar'] is None


def test_stale_missing_history_and_tampering_are_blocked(source):
    stale=archive_snapshot(source[0],'2026-01-03T10:25:00Z',['H1'])
    assert not stale['all_timeframes_confirmed'] and stale['snapshots']['H1']['bar'] is None
    path=source[0]/'candles/M1/2026/2026-01.parquet'
    frame=pq.read_table(path).to_pandas()
    frame.loc[0,'bid_open']=99
    frame.to_parquet(path,index=False)
    with pytest.raises(SnapshotError,match='no longer matches'):
        archive_snapshot(source[0],'2026-01-02T10:25:00Z')


def test_intrabar_records_are_bounded_and_no_future_or_synthetic_minutes(source):
    result=archive_intrabar(source[0],'2026-01-02T09:00:00Z','2026-01-02T09:05:00Z','2026-01-02T09:05:00Z')
    assert result['eligible_minutes']==5 and not result['missing_intervals']
    assert result['bars'][-1]['timestamp']==utc_seconds('2026-01-02T09:04:00Z')
    assert result['bars'][0]['ask_close']-result['bars'][0]['bid_close']==2
    assert isinstance(result['bars'][0]['first_tick_ns'],str)
    with pytest.raises(SnapshotError,match='end <= as_of'):
        archive_intrabar(source[0],'2026-01-02T09:00:00Z','2026-01-02T09:06:00Z','2026-01-02T09:05:00Z')


def test_api_checks_chart_feed_identity_and_refuses_inconsistent_cached_values(source,monkeypatch):
    from app import main
    monkeypatch.setenv('DATABASE_PATH',str(source[0]/'chart.db'))
    monkeypatch.setattr(main,'EXNESS_ARCHIVE_ROOT',source[0])
    import_month(source[0],'2026-01',source[1]['2026-01'])
    with TestClient(main.app) as client:
        query={'as_of':'2026-01-02T10:25:00Z','timeframes':'M5,H1'}
        result=client.get('/api/exness/history/confirmed-snapshot',params=query)
        assert result.status_code==200 and result.json()['all_timeframes_confirmed']
        store=main.app.state.store
        series=store.get_series('exness-archive-xauusdm-bid','H1')
        store.conn.execute('UPDATE candles SET c=c+1 WHERE series_id=? AND t=?',
                           (series.id,utc_seconds('2026-01-02T09:00:00Z')))
        store.conn.commit()
        bad=client.get('/api/exness/history/confirmed-snapshot',params=query).json()
        assert not bad['snapshots']['H1']['confirmed'] and bad['snapshots']['H1']['bar'] is None
        assert bad['snapshots']['H1']['chart_feed_check']['status']=='failed'
        store.conn.execute("UPDATE feeds SET alignment='ny17' WHERE id='exness-archive-xauusdm-ask'")
        store.conn.commit()
        bad=client.get('/api/exness/history/confirmed-snapshot',params=query).json()
        assert not bad['snapshots']['M5']['confirmed']
        assert client.get('/api/exness/history/confirmed-snapshot',params={**query,'as_of':'2026-01-02T10:25:00'}).status_code==400
        assert client.get('/api/exness/history/intrabar',params={'start':'2026-01-02T09:00:00Z',
            'end':'2026-01-02T09:05:00Z','as_of':'2026-01-02T09:05:00Z'}).status_code==200


def test_writer_lock_blocks_snapshot_reads(source):
    with (source[0]/'.pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(SnapshotError,match='running'):
            archive_snapshot(source[0],'2026-01-02T10:25:00Z')


def test_catalog_identity_cannot_silently_switch_instruments(source):
    path=source[0]/'catalog.json'
    wrong=json.loads(path.read_text())
    wrong['symbol']='EURUSD'
    atomic_json(path,wrong)
    with pytest.raises(SnapshotError,match='provider, symbol or timezone'):
        archive_snapshot(source[0],'2026-01-02T10:25:00Z')
