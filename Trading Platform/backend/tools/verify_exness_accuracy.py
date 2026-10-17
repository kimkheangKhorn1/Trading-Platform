#!/usr/bin/env python3
"""Independent full candle recalculation, original CSV and chart-cache checks.

Reads the existing snapshot; refreshes audit reports and the ML organization catalog. No downloading, quote
substitution, candle repair, model fitting, or trading logic changes.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from app.market.exness_archive import ArchiveClient, atomic_json, utc_now
from app.market.exness_audit import verify_dataset
from app.market.exness_dataset import database, sql_string


def aggregate_batch(ns, bid, ask, seconds, source_last_row=None, priority=None, source_file=None):
    """NumPy calculation independent of the production DuckDB aggregation."""
    bucket = ns // (seconds * 1_000_000_000) * seconds
    first = np.r_[0, np.flatnonzero(np.diff(bucket)) + 1]
    last = np.r_[first[1:] - 1, len(ns) - 1]
    spread = ask - bid
    result = {'timestamp': bucket[first], 'tick_count': last-first+1,
              'spread_min': np.minimum.reduceat(spread, first),
              'spread_max': np.maximum.reduceat(spread, first),
              'spread_sum': np.add.reduceat(spread, first),
              'first_tick_ns': ns[first], 'last_tick_ns': ns[last]}
    close = last
    if source_last_row is not None:
        order = np.lexsort((source_last_row,source_file,-priority,ns))
        sorted_bucket = bucket[order]
        ends = np.r_[np.flatnonzero(np.diff(sorted_bucket)),len(ns)-1]
        close = order[ends]
        result.update(_close_priority=priority[close],_close_file=source_file[close],
                      _close_source_row=source_last_row[close])
    for side, price in (('bid', bid), ('ask', ask)):
        result.update({side+'_open': price[first], side+'_close': price[close],
                       side+'_high': np.maximum.reduceat(price, first),
                       side+'_low': np.minimum.reduceat(price, first)})
    return pd.DataFrame(result)


def finish_batches(parts):
    frame = pd.concat(parts, ignore_index=True)
    closes = None
    if '_close_source_row' in frame.columns:
        closes = frame.sort_values(['timestamp','last_tick_ns','_close_priority','_close_file','_close_source_row'],
                                   ascending=[True,True,False,True,True]).drop_duplicates('timestamp',keep='last').set_index('timestamp')
        frame = frame.drop(columns=['_close_priority','_close_file','_close_source_row'])
    agg = {key: ('first' if key.endswith('_open') else 'last' if key.endswith('_close')
                 else 'sum' if key in ('tick_count','spread_sum')
                 else 'min' if key.endswith('_low') or key in ('spread_min','first_tick_ns')
                 else 'max') for key in frame.columns if key != 'timestamp'}
    result = frame.groupby('timestamp', sort=True).agg(agg)
    result['spread_mean'] = result.pop('spread_sum') / result['tick_count']
    if closes is not None:
        for side in ('bid','ask'):
            result[side+'_close'] = closes[side+'_close'].reindex(result.index)
    return result


def recalculate(path, *, batch_size=200_000):
    parts = {'M1': [], 'M5': []}
    names = pq.read_schema(path).names
    has_order = 'source_row' in names
    order_col = 'source_last_row' if 'source_last_row' in names else 'source_row'
    columns = ['timestamp_ns','bid','ask'] + ([order_col,'source_priority','source_file'] if has_order else [])
    for batch in pq.ParquetFile(path).iter_batches(
            columns=columns, batch_size=batch_size):
        ns, bid, ask = (batch.column(i).to_numpy() for i in range(3))
        extra = [batch.column(i).to_numpy(zero_copy_only=False) for i in range(3,6)] if has_order else []
        for tf, seconds in (('M1',60),('M5',300)):
            parts[tf].append(aggregate_batch(ns,bid,ask,seconds,*extra))
    return {tf:finish_batches(values) for tf,values in parts.items()}


def differences(expected, actual, columns=None):
    if not expected.index.equals(actual.index):
        return {'timestamp_index': len(expected.index.symmetric_difference(actual.index)) or 1}
    result = {}
    for col in expected.columns if columns is None else columns:
        x,y = expected[col].to_numpy(),actual[col].to_numpy()
        # Only reduction-order rounding is allowed for the computed mean spread.
        equal = np.isclose(x,y,rtol=1e-10,atol=1e-12) if col == 'spread_mean' else x == y
        n = int((~equal).sum())
        if n:
            result[col] = n
    return result


def raw_year_candles(root, entry):
    parts = {'M1': [], 'M5': []}
    with zipfile.ZipFile(root/entry['relative_path']) as z:
        for name in z.namelist():
            if not name.lower().endswith('.csv'):
                continue
            with z.open(name) as stream:
                for chunk in pd.read_csv(stream,chunksize=200_000):
                    # This comparison uses only OHLC, not first/last tick timestamps.
                    # Decode the UTC minute key once per repeated key; preserve
                    # original CSV row order within the minute for open/close.
                    ns = pd.to_datetime(chunk.Timestamp.str.slice(0,16),utc=True,format='mixed',cache=True).dt.as_unit('ns').astype('int64').to_numpy()
                    for tf,s in (('M1',60),('M5',300)):
                        parts[tf].append(aggregate_batch(ns,chunk.Bid.to_numpy(),chunk.Ask.to_numpy(),s))
    return {tf:finish_batches(values) for tf,values in parts.items()}


def check(root, chart_db=None, *, full_source=False):
    client = ArchiveClient(root)
    report = {'started_at': utc_now(), 'status': 'running', 'issues': [],
              'scope': 'Uncached integrity audit, full independent tick-to-candle calculation, '
                       'original CSV quote samples, one full original month per year, and chart-cache comparison. '
                       'Does not certify account-server parity or missing source periods.',
              'months':{}, 'raw_samples':{}, 'original_month_ohlc':{},'full_source_months':{}}
    if full_source:
        report['scope'] += ' Every active original CSV row is independently decoded and reconciled, including source positions.'
    con = database(root)
    chart = sqlite3.connect(f'{chart_db.resolve().as_uri()}?mode=ro',uri=True) if chart_db else None
    try:
        report['integrity'] = verify_dataset(client,force=True)
        report['issues'].extend(report['integrity']['issues'])
        series = {}
        if chart:
            for side in ('bid','ask'):
                for tf in ('M1','M5'):
                    row = chart.execute('SELECT id FROM series WHERE feed_id=? AND timeframe=?',
                                        (f'exness-archive-xauusdm-{side}',tf)).fetchone()
                    if not row:
                        report['issues'].append(f'Missing chart series: {side}/{tf}')
                    else:
                        series[side,tf] = row[0]
        for month in sorted(client.manifest['months']):
            print(f'RECALCULATING {month}',flush=True)
            calculated = recalculate(root/'ticks'/month[:4]/(month+'.parquet'))
            detail = {}
            for tf, expected in calculated.items():
                saved = pq.read_table(root/'candles'/tf/month[:4]/(month+'.parquet')).to_pandas().set_index('timestamp')
                mismatch = differences(expected,saved)
                detail[tf] = {'candles':len(expected), 'mismatches':mismatch}
                if mismatch:
                    report['issues'].append(f'Tick-to-candle mismatch {month}/{tf}: {mismatch}')
                if chart:
                    begin = int(pd.Timestamp(month+'-01',tz='UTC').timestamp())
                    end = int((pd.Timestamp(month+'-01',tz='UTC')+pd.DateOffset(months=1)).timestamp())
                    for side in ('bid','ask'):
                        if (side,tf) not in series:
                            continue
                        frame = pd.read_sql_query('SELECT t,o,h,l,c,v FROM candles WHERE series_id=? AND t>=? AND t<? ORDER BY t',
                                                  chart,params=(series[side,tf],begin,end)).set_index('t')
                        frame.columns = [side+'_'+x for x in ('open','high','low','close')] + ['tick_count']
                        diff = differences(expected,frame,frame.columns)
                        detail[tf][side+'_chart_mismatches'] = diff
                        if diff:
                            report['issues'].append(f'Chart-cache mismatch {month}/{tf}/{side}: {diff}')
            report['months'][month] = detail
            atomic_json(root/'reports/accuracy_check.json',report)
        entries = sorted(client.manifest['archives'].values(),key=lambda e:e['period'])
        for entry in entries:
            print(f'ORIGINAL SAMPLE {entry["period"]}',flush=True)
            with zipfile.ZipFile(root/entry['relative_path']) as z:
                sample = next(n for n in z.namelist() if n.lower().endswith('.csv'))
                with z.open(sample) as stream:
                    frame = pd.read_csv(stream,nrows=256)
            frame['timestamp_ns'] = pd.to_datetime(frame.Timestamp,utc=True,format='mixed').dt.as_unit('ns').astype('int64')
            frame = frame.rename(columns={'Bid':'bid','Ask':'ask'})
            wrong_symbols = int(frame.Symbol.ne('XAUUSDm').sum())
            missing = 0
            for month, group in frame.groupby(pd.to_datetime(frame.timestamp_ns,unit='ns',utc=True).dt.strftime('%Y-%m')):
                con.register('sample',group[['timestamp_ns','bid','ask']])
                path = root/'ticks'/month[:4]/(month+'.parquet')
                low,high = int(group.timestamp_ns.min()),int(group.timestamp_ns.max())
                n = con.execute(f'''SELECT count(*) FROM sample s WHERE NOT EXISTS (
                    SELECT 1 FROM read_parquet({sql_string(path)}) t
                    WHERE t.timestamp_ns BETWEEN {low} AND {high}
                    AND t.timestamp_ns=s.timestamp_ns AND t.bid=s.bid AND t.ask=s.ask)''').fetchone()[0]
                missing += n
                con.unregister('sample')
            report['raw_samples'][entry['filename']] = {'rows':len(frame),'missing_quotes':missing,'wrong_symbols':wrong_symbols}
            if missing or wrong_symbols:
                report['issues'].append(f'Original CSV sample mismatch: {entry["filename"]}')
        # Preserve original duplicate quote sequences while independently checking
        # full OHLC in a historical monthly file from every available year.
        years = {}
        for entry in entries:
            if len(entry['period']) == 7:
                years.setdefault(entry['period'][:4],entry)
        originals = {entry['filename']:entry for entry in years.values()}
        repair_path = root/'reports/source_order_repair.json'
        if repair_path.exists():
            repaired = set(json.loads(repair_path.read_text())['archives_to_rebuild'])
            newest_month = max(client.manifest['months'])
            originals.update({entry['filename']:entry for entry in entries
                              if entry['filename'] in repaired and len(entry['period'])==7
                              and entry['period']<newest_month})
        ohlc = [side+'_'+key for side in ('bid','ask') for key in ('open','high','low','close')]
        for entry in ([] if full_source else sorted(originals.values(),key=lambda e:e['period'])):
            month = entry['period']
            print(f'FULL ORIGINAL OHLC {month}',flush=True)
            values = raw_year_candles(root,entry)
            report['original_month_ohlc'][month] = {}
            for tf,raw in values.items():
                saved = pq.read_table(root/'candles'/tf/month[:4]/(month+'.parquet')).to_pandas().set_index('timestamp')
                diff = differences(raw,saved,ohlc)
                report['original_month_ohlc'][month][tf] = {'candles':len(raw),'mismatches':diff}
                if diff:
                    report['issues'].append(f'Full original OHLC mismatch {month}/{tf}: {diff}')
        if full_source:
            from app.market.exness_source_audit import verify_source_month
            for month in sorted(client.manifest['months']):
                sources = [e for e in entries if month in e.get('output_months',[e['period'][:7]])]
                print(f'FULL SOURCE RECONCILIATION {month}',flush=True)
                detail = verify_source_month(root,month,sources)
                report['full_source_months'][month] = detail
                if detail['status']!='passed':
                    report['issues'].append(f'Full source mismatch {month}: {detail}')
                atomic_json(root/'reports/accuracy_check.json',report)
        report['finished_at'] = utc_now()
        report['status'] = 'passed' if not report['issues'] else 'failed'
        report['M1_candles_checked'] = sum(v['M1']['candles'] for v in report['months'].values())
        report['M5_candles_checked'] = sum(v['M5']['candles'] for v in report['months'].values())
        report['original_sample_rows_checked'] = sum(v['rows'] for v in report['raw_samples'].values())
        report['full_source_rows_checked'] = sum(v['original_rows_decoded'] for v in report['full_source_months'].values())
        atomic_json(root/'reports/accuracy_check.json',report)
        if (root/'ml/windows.json').exists():
            from app.market.exness_catalog import build_catalog
            catalog = build_catalog(root)
            if catalog['issues']:
                report['issues'].extend(catalog['issues'])
                report['status'] = 'failed'
                atomic_json(root/'reports/accuracy_check.json',report)
        integrity = report['integrity']
        lines = ['# Exness dataset accuracy check','',
                 f'Finished (UTC): {report["finished_at"]}',
                 f'Status: **{report["status"]}**','',
                 '| Check | Result |','|---|---:|',
                 f'| Active original ZIPs checked with SHA256 and uncached CRC | {integrity["original_archives_verified"]:,} |',
                 f'| Unique ticks checked | {integrity["unique_ticks_verified"]:,} |',
                 f'| Duplicate tick groups | {integrity["duplicate_tick_groups"]:,} |',
                 f'| Months independently recalculated | {len(report["months"])} |',
                 f'| M1 candles recalculated | {report["M1_candles_checked"]:,} |',
                 f'| M5 candles recalculated | {report["M5_candles_checked"]:,} |',
                 f'| Original CSV quote samples | {report["original_sample_rows_checked"]:,} |',
                 f'| Full original monthly OHLC comparisons | {len(report["original_month_ohlc"])} |','',
                 f'Full original row reconciliation: {len(report["full_source_months"])} months; {report["full_source_rows_checked"]:,} source rows.', '',
                 f'Chart cache checked: {chart is not None}.',
                 f'Issues: {report["issues"]}.','',
                 'OHLC, timestamps, tick counts and spread extrema must match exactly. Only',
                 'floating-point summation rounding is allowed for mean spreads (relative',
                 'tolerance 1e-10, absolute tolerance 1e-12). Quote samples are the first 256',
                 'CSV rows from every active archive. Full source reconciliation, when requested,',
                 'separately decodes every original CSV row and compares all unique quotes and',
                 'their first/last source positions, followed by the independent candle calculation.',
                 'Chart comparisons cover both Bid/Ask M1/M5 OHLC and retained quote counts.','',
                 'This checks the stored snapshot. It does not certify missing broker periods,',
                 '100% account-server Terminal parity, freshness of the latest remote archive,',
                 'or an optimal ML training window. See `coverage.json` for source gaps.','']
        (root/'reports/accuracy_check.md').write_text('\n'.join(lines))
        print(f'ACCURACY {report["status"]}: {report["issues"]}',flush=True)
        return report
    finally:
        client.http.close()
        con.close()
        if chart:
            chart.close()


if __name__ == '__main__':
    backend = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=backend/'data/exness/XAUUSDm')
    parser.add_argument('--chart-db',type=Path,default=backend/'data/market.db')
    parser.add_argument('--no-chart',action='store_true')
    parser.add_argument('--full-source',action='store_true',help='Independently decode and reconcile every active original CSV row')
    args = parser.parse_args()
    with (args.root/'.pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result = check(args.root,None if args.no_chart else args.chart_db,full_source=args.full_source)
        raise SystemExit(0 if result['status']=='passed' else 1)
