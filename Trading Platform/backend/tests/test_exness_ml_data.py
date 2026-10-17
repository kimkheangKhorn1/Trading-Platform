"""Source reconciliation and ML data boundary/organization regressions."""
import json

import pandas as pd
import pyarrow.parquet as pq

from app.market.exness_archive import ArchiveClient, atomic_json
from app.market.exness_audit import verify_dataset
from app.market.exness_catalog import normalize_tick_schemas, build_catalog
from app.market.exness_dataset import database, stage_archive, merge_month, candles_for_month
from app.market.exness_ml import prepare_ml, load_ml_split
from app.market.exness_source_audit import verify_source_month
from tests.test_exness_archive import archive, tick


def processed(root,period,rows):
    entry = archive(root,period,rows)
    quality,stages = stage_archive(root,entry,chunk_size=2)
    months = {}
    with database(root) as con:
        for month,stage in stages.items():
            stats = merge_month(con,root,month,stage,entry['filename'])
            candles_for_month(con,root,month,stats)
            months[month] = stats
    entry.update(quality=quality,processed_sha256=entry['sha256'],output_months=list(months))
    return entry,months


def test_full_original_decoder_checks_same_timestamp_returns_and_detects_changed_quote(tmp_path):
    period='2015-08'
    rows=[tick('2015-08-10 00:00:00.123456789Z',100,101),
          tick('2015-08-10 00:00:00.123456789Z',102,103),
          tick('2015-08-10 00:00:00.123456789Z',100,101)]
    entry,_=processed(tmp_path,period,rows)
    result=verify_source_month(tmp_path,period,[entry])
    assert result['status']=='passed' and result['original_rows_decoded']==3
    path=tmp_path/'ticks/2015/2015-08.parquet'
    changed=pq.read_table(path).to_pandas()
    changed.loc[0,'bid']=99  # still a valid quote, but no longer the original
    changed.to_parquet(path,index=False)
    result=verify_source_month(tmp_path,period,[entry])
    assert result['status']=='failed'
    assert result['missing_or_changed_clean_ticks']==result['extra_or_changed_clean_ticks']==1


def test_full_original_decoder_reconciles_month_daily_overlap(tmp_path):
    quote=tick('2015-08-10 00:00:00.000Z',100,101)
    month,_=processed(tmp_path,'2015-08',[quote])
    day,_=processed(tmp_path,'2015-08-10',[quote,tick('2015-08-10 00:01:00.000Z',102,103)])
    result=verify_source_month(tmp_path,'2015-08',[month,day])
    assert result['status']=='passed' and result['expected_unique_ticks']==2


def test_organization_preserves_quotes_and_marks_gaps_across_month_boundaries(tmp_path):
    entries={};months={}
    for period,rows in [('2026-01',[tick('2026-01-31 23:59:00.000Z',100,101)]),
                        ('2026-02',[tick('2026-02-01 00:01:00.000Z',102,103)]),
                        ('2026-10',[tick('2026-10-12 23:59:00.000Z',104,105)])]:
        e,m=processed(tmp_path,period,rows);entries[e['filename']]=e;months.update(m)
    path=tmp_path/'ticks/2026/2026-01.parquet'
    old=pq.read_table(path).drop(['source_last_row'])
    pq.write_table(old,path)
    assert normalize_tick_schemas(tmp_path)==1
    new=pq.read_table(path)
    assert new.drop(['source_last_row']).equals(old)
    assert new['source_row'].equals(new['source_last_row'])
    client=ArchiveClient(tmp_path)
    client.manifest.update(archives=entries,inventory=list(entries.values()),months=months,
                           coverage={'missing_weekdays_requiring_calendar_review':[]})
    client.save()
    assert verify_dataset(client,force=True)['status']=='passed'
    prepare_ml(tmp_path)
    catalog=build_catalog(tmp_path)
    assert catalog['market_data_integrity_passed']
    assert not catalog['training_dataset_ready']  # market prices are not supervised labels
    assert len(catalog['dataset_version'])==64
    sidecar=pq.read_table(tmp_path/'quality/M1/2026/2026-02.parquet').to_pandas()
    assert sidecar.gap_before_seconds.iloc[0]==60
    assert sidecar.starts_new_segment.iloc[0]
    assert sidecar.contiguous_segment_id.iloc[0]==2
    assert sidecar.available_at_utc_seconds.iloc[0]==int(pd.Timestamp('2026-02-01T00:02:00Z').timestamp())
    five=pq.read_table(tmp_path/'quality/M5/2026/2026-02.parquet').to_pandas()
    assert five.observed_M1_minutes.iloc[0]==1
    assert five.expected_M1_minutes.iloc[0]==5
    assert five.has_unobserved_M1_minutes.iloc[0]
    repeat=build_catalog(tmp_path)
    assert repeat['dataset_version']==catalog['dataset_version']
    client.http.close()


def test_ml_split_excludes_candle_that_closes_after_boundary(tmp_path):
    e,_=processed(tmp_path,'2026-01',[tick('2026-01-03 00:05:00.000Z',100,101)])
    atomic_json(tmp_path/'ml/windows.json',{'splits':{'train_24m':{
        'train':{'start':'2026-01-03T00:00:00Z','end_exclusive':'2026-01-03T00:07:00Z'}}}})
    assert len(load_ml_split(tmp_path,'M1'))==1
    assert len(load_ml_split(tmp_path,'M5'))==0
