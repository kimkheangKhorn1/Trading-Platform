"""Bounded-memory tick validation, monthly Parquet datasets and chart integration.

Timestamps retain nanoseconds as int64. Tick count is quote activity, not traded volume.
No interpolation, weekend filling, price substitution or strategy changes.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .exness_archive import ArchiveClient, SYMBOL, atomic_json, sha256, utc_now

SCHEMA = pa.schema([
    ('timestamp_ns', pa.int64()), ('bid', pa.float64()), ('ask', pa.float64()),
    ('source_file', pa.string()), ('source_row', pa.int64()), ('source_priority', pa.int32()),
    ('source_last_row', pa.int64()),
])


def sql_string(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def database(root: Path):
    temp = root / '_work/duckdb'
    temp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'")
    con.execute('SET threads=2')
    con.execute("SET preserve_insertion_order=false")
    con.execute(f'SET temp_directory={sql_string(temp)}')
    return con


def stage_archive(root: Path, entry: dict, *, chunk_size: int = 200_000) -> tuple[dict, dict[str, Path]]:
    path = root / entry['relative_path']
    work = root / '_work' / entry['filename'].removesuffix('.zip')
    work.mkdir(parents=True, exist_ok=True)
    writers = {}
    paths = {}
    stats = {'input_rows': 0, 'invalid_rows': 0, 'out_of_order_transitions': 0,
             'same_timestamp_transitions': 0, 'members': [], 'first_timestamp_ns': None,
             'last_timestamp_ns': None, 'invalid_reasons': {}}
    prior = None
    priority = len(entry['period'].split('-'))
    reject = root / 'quarantine' / (entry['filename'] + '.csv.gz')
    reject.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(path) as z:
            for member in z.infolist():
                if not member.filename.lower().endswith('.csv'):
                    continue
                stats['members'].append({'name': member.filename, 'crc32': member.CRC,
                                         'uncompressed_bytes': member.file_size})
                with z.open(member) as stream:
                    reader = pd.read_csv(stream, chunksize=chunk_size, dtype=str, keep_default_na=False)
                    for chunk in reader:
                        chunk.columns = [s.strip().lstrip('\ufeff').lower() for s in chunk.columns]
                        required = {'symbol', 'timestamp', 'bid', 'ask'}
                        if not required.issubset(chunk.columns):
                            raise ValueError(f'Missing CSV columns: {required - set(chunk.columns)}')
                        times = pd.to_datetime(chunk['timestamp'], utc=True, errors='coerce', format='mixed')
                        explicit_utc = chunk['timestamp'].str.contains(r'(?:Z|\+00:00)$', regex=True)
                        bid = pd.to_numeric(chunk['bid'], errors='coerce')
                        ask = pd.to_numeric(chunk['ask'], errors='coerce')
                        reasons = {
                            'wrong_symbol': chunk['symbol'].ne(SYMBOL),
                            'bad_timestamp_or_timezone': times.isna() | ~explicit_utc,
                            'nonpositive_or_nonfinite_price': ~np.isfinite(bid) | ~np.isfinite(ask) | (bid <= 0) | (ask <= 0),
                            'crossed_quote': ask < bid,
                        }
                        # Archive filenames must correspond to actual UTC tick periods.
                        period = entry['period']
                        start = pd.Timestamp(period + ('-01-01' if len(period) == 4 else '-01' if len(period) == 7 else ''), tz='UTC')
                        end = start + (pd.DateOffset(years=1) if len(period) == 4 else pd.DateOffset(months=1) if len(period) == 7 else pd.Timedelta(days=1))
                        reasons['outside_archive_period'] = (times < start) | (times >= end)
                        bad = pd.Series(False, index=chunk.index)
                        for name, mask in reasons.items():
                            stats['invalid_reasons'][name] = stats['invalid_reasons'].get(name, 0) + int(mask.sum())
                            bad |= mask
                        if bad.any():
                            reject.parent.mkdir(parents=True, exist_ok=True)
                            header = not reject.exists()
                            with gzip.open(reject, 'at') as output:
                                chunk[bad].to_csv(output, index=False, header=header)
                        stats['invalid_rows'] += int(bad.sum())
                        row_numbers = np.arange(stats['input_rows'], stats['input_rows'] + len(chunk), dtype=np.int64)
                        stats['input_rows'] += len(chunk)
                        good = ~bad
                        if not good.any():
                            continue
                        ns = times[good].dt.as_unit('ns').astype('int64').to_numpy()
                        delta = np.diff(ns)
                        stats['out_of_order_transitions'] += int((delta < 0).sum()) + int(prior is not None and ns[0] < prior)
                        stats['same_timestamp_transitions'] += int((delta == 0).sum()) + int(prior is not None and ns[0] == prior)
                        prior = int(ns[-1])
                        first, last = int(ns.min()), int(ns.max())
                        stats['first_timestamp_ns'] = min(stats['first_timestamp_ns'], first) if stats['first_timestamp_ns'] is not None else first
                        stats['last_timestamp_ns'] = max(stats['last_timestamp_ns'], last) if stats['last_timestamp_ns'] is not None else last
                        months = times[good].dt.strftime('%Y-%m').to_numpy()
                        b, a, seq = bid[good].to_numpy(), ask[good].to_numpy(), row_numbers[good]
                        for month in np.unique(months):
                            mask = months == month
                            if month not in writers:
                                paths[month] = work / (month + '.parquet')
                                writers[month] = pq.ParquetWriter(paths[month], SCHEMA, compression='zstd')
                            table = pa.Table.from_pydict({
                                'timestamp_ns': ns[mask], 'bid': b[mask], 'ask': a[mask],
                                'source_file': [entry['filename']] * int(mask.sum()),
                                'source_row': seq[mask], 'source_priority': [priority] * int(mask.sum()),
                                'source_last_row': seq[mask],
                            }, schema=SCHEMA)
                            writers[month].write_table(table)
    finally:
        for writer in writers.values():
            writer.close()
    if stats['input_rows'] == 0:
        raise ValueError('Empty archive: no tick rows')
    if stats['invalid_rows'] == stats['input_rows']:
        raise ValueError(f'No valid ticks: {stats["invalid_reasons"]}')
    return stats, paths


def merge_month(con, root: Path, month: str, staged: Path, source: str) -> dict:
    dest = root / 'ticks' / month[:4] / (month + '.parquet')
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.parquet.tmp')
    query = f'SELECT * FROM read_parquet({sql_string(staged)})'
    if dest.exists():
        query += f' UNION ALL BY NAME SELECT * FROM read_parquet({sql_string(dest)}) WHERE source_file <> {sql_string(source)}'
    con.execute(f'CREATE OR REPLACE TEMP VIEW input_ticks AS {query}')
    count = con.execute('SELECT count(*) FROM input_ticks').fetchone()[0]
    con.execute('''CREATE OR REPLACE TEMP VIEW clean_ticks AS
        SELECT * EXCLUDE(source_last_row),
            max(coalesce(source_last_row,source_row)) OVER (
                PARTITION BY timestamp_ns,bid,ask,source_file) AS source_last_row
        FROM input_ticks
        QUALIFY row_number() OVER (PARTITION BY timestamp_ns,bid,ask
            ORDER BY source_priority DESC,source_file DESC,source_row ASC)=1''')
    con.execute(f'''COPY (SELECT * FROM clean_ticks ORDER BY timestamp_ns,source_priority DESC,source_file,source_row)
        TO {sql_string(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)''')
    os.replace(tmp, dest)
    con.execute(f'CREATE OR REPLACE TEMP VIEW ticks AS SELECT * FROM read_parquet({sql_string(dest)})')
    n, first, last, days = con.execute('''SELECT count(*),min(timestamp_ns),max(timestamp_ns),
        count(DISTINCT timestamp_ns // 86400000000000) FROM ticks''').fetchone()
    return {'ticks': n, 'first_timestamp_ns': first, 'last_timestamp_ns': last,
            'trading_days': days, 'duplicates_removed_in_last_merge': count - n}


def candles_for_month(con, root: Path, month: str, stats: dict) -> None:
    ticks = root / 'ticks' / month[:4] / (month + '.parquet')
    con.execute(f'CREATE OR REPLACE TEMP VIEW ticks AS SELECT * FROM read_parquet({sql_string(ticks)})')
    last_row = 'coalesce(source_last_row,source_row)' if 'source_last_row' in pq.read_schema(ticks).names else 'source_row'
    complete_before = int(pd.Timestamp(utc_now()).floor('D').timestamp())
    for tf, seconds in (('M1', 60), ('M5', 300)):
        dest = root / 'candles' / tf / month[:4] / (month + '.parquet')
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix('.parquet.tmp')
        # Current-day bars are excluded from training until a later UTC day is published.
        # This conservative rule avoids claiming a complete bar from a lagging daily snapshot.
        cols = ','.join(f'''first({side} ORDER BY timestamp_ns,source_priority DESC,source_file,source_row) AS {side}_open,
            max({side}) AS {side}_high,min({side}) AS {side}_low,
            last({side} ORDER BY timestamp_ns,source_priority DESC,source_file,{last_row}) AS {side}_close'''
            for side in ('bid', 'ask'))
        con.execute(f'''COPY (SELECT (timestamp_ns // {seconds * 1_000_000_000}) * {seconds} AS timestamp,
            {cols}, count(*) AS tick_count, min(ask-bid) AS spread_min,
            max(ask-bid) AS spread_max,avg(ask-bid) AS spread_mean,
            min(timestamp_ns) AS first_tick_ns,max(timestamp_ns) AS last_tick_ns,
            (timestamp_ns // {seconds * 1_000_000_000}) * {seconds} + {seconds} <= {complete_before} AS complete
            FROM ticks GROUP BY timestamp ORDER BY timestamp)
            TO {sql_string(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)''')
        os.replace(tmp, dest)
        stats[tf] = con.execute(f'SELECT count(*) FROM read_parquet({sql_string(dest)})').fetchone()[0]


def import_month(root: Path, month: str, stats: dict) -> None:
    from ..config import load_settings
    from .repository import MarketStore
    from .resample import resample
    from .timeframes import TIMEFRAMES
    from .importer import _rows
    from ..engine.bars import Bars

    store = MarketStore(load_settings().database_path)
    snapshot_end = max(stats['last_timestamp_ns'] / 1e9, pd.Timestamp(utc_now()).floor('D').timestamp())
    for side in ('bid', 'ask'):
        feed = store.ensure_feed(
            f'exness-archive-xauusdm-{side}', provider='exness_archive', symbol=SYMBOL,
            instrument=SYMBOL, price_type=side, precision=3, alignment='utc',
            display_name=f'{SYMBOL} · Exness archive ({side})',
            meta={'source': 'official_exness_tick_archive', 'account_type': 'Standard',
                  'base_timeframe': 'M1', 'live': False, 'volume_type': 'deduplicated_quote_count',
                  'dataset_root': str(root), 'terminal_parity': 'not_verified'},
        )
        for tf in ('M1', 'M5'):
            frame = pq.read_table(root / 'candles' / tf / month[:4] / (month + '.parquet')).to_pandas()
            # The chart engine expects only the final observed bar to be provisional;
            # Parquet's current-day exclusion is a separate conservative ML rule.
            chart_complete = frame['timestamp'].to_numpy() + TIMEFRAMES[tf].seconds <= snapshot_end
            bars = Bars.from_arrays(frame['timestamp'].to_numpy(),
                                   *(frame[f'{side}_{key}'].to_numpy() for key in ('open', 'high', 'low', 'close')),
                                   frame['tick_count'].to_numpy(), complete=chart_complete)
            series = store.ensure_series(feed.id, tf, 'tick_derived')
            # Replace the affected month, including bars removed by corrected archive snapshots.
            begin = int(pd.Timestamp(month + '-01', tz='UTC').timestamp())
            end = int((pd.Timestamp(month + '-01', tz='UTC') + pd.DateOffset(months=1)).timestamp())
            existing = store.load_bars(series.id, start=begin, end=end-1)
            obsolete = np.setdiff1d(existing.time, bars.time)
            store.delete_candles(series.id, obsolete.tolist())
            store.upsert_candles(series.id, _rows(bars))
            if tf == 'M1':
                for higher in TIMEFRAMES.values():
                    if higher.seconds <= 300:
                        continue
                    # UTC monthly boundaries nest inside fixed intraday/daily chart buckets.
                    out = resample(bars, TIMEFRAMES['M1'], higher, 'utc')
                    # Historical month ends during a market closure are finished
                    # archive snapshots, without provisional bars inside the series.
                    out.complete[:] = out.time + higher.seconds <= snapshot_end
                    target = store.ensure_series(feed.id, higher.code, 'resampled')
                    old = store.load_bars(target.id, start=begin, end=end-1)
                    store.delete_candles(target.id, np.setdiff1d(old.time, out.time).tolist())
                    store.upsert_candles(target.id, _rows(out))
    stats['imported_at'] = utc_now()


def process_all(client: ArchiveClient, *, import_chart: bool = True) -> None:
    root = client.root
    con = database(root)
    client.manifest.setdefault('months', {})
    try:
        entries = sorted(client.manifest['archives'].values(), key=lambda e: (e['period'], e['filename']))
        for entry in entries:
            if entry.get('status') == 'failed':
                continue
            if entry.get('processed_sha256') == entry['sha256']:
                output_months = entry.get('output_months',[entry['period'][:7]])
                outputs_exist = all((root/'ticks'/m[:4]/(m+'.parquet')).exists()
                    and all((root/'candles'/tf/m[:4]/(m+'.parquet')).exists() for tf in ('M1','M5'))
                    for m in output_months)
                if outputs_exist:
                    continue
            path = root / entry['relative_path']
            if not path.exists() or sha256(path) != entry['sha256']:
                raise ValueError(f'Archive missing or checksum changed: {path}')
            print(f'PROCESSING {entry["period"]}', flush=True)
            quality, stages = stage_archive(root, entry)
            for month, staged in stages.items():
                stats = merge_month(con, root, month, staged, entry['filename'])
                candles_for_month(con, root, month, stats)
                if import_chart:
                    import_month(root, month, stats)
                client.manifest['months'][month] = stats
            entry['quality'] = quality
            entry['processed_sha256'] = entry['sha256']
            entry['processed_at'] = utc_now()
            entry['source_order_version'] = 2
            entry['output_months'] = sorted(stages)
            entry.pop('processing_error', None)
            client.save()
            shutil.rmtree(root / '_work' / entry['filename'].removesuffix('.zip'))
            print(f'PROCESSED {entry["period"]}: {quality["input_rows"]:,} input ticks; {quality["invalid_rows"]:,} invalid', flush=True)
    finally:
        con.close()


def load_training_candles(root: Path, timeframe: str = 'M1', *, start: str | None = None,
                          end: str | None = None, complete_only: bool = True) -> pd.DataFrame:
    """Use bounded date ranges for ML; both bid and ask OHLC are returned.

    ``end`` is exclusive. Default excludes partial archive-day candles.
    """
    if timeframe not in ('M1', 'M5'):
        raise ValueError('ML archive timeframe must be M1 or M5')
    paths = list((root / 'candles' / timeframe).glob('*/*.parquet'))
    if not paths:
        return pd.DataFrame()
    clauses = ['complete'] if complete_only else []
    for op, bound in (('>=', start), ('<', end)):
        if bound:
            stamp = pd.Timestamp(bound)
            stamp = stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
            clauses.append(f'timestamp {op} {int(stamp.timestamp())}')
    with database(root) as con:
        files = '[' + ','.join(sql_string(p) for p in paths) + ']'
        query = f'SELECT * FROM read_parquet({files})'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        frame = con.execute(query + ' ORDER BY timestamp').fetchdf()
    frame.insert(1, 'timestamp_utc', pd.to_datetime(frame['timestamp'], unit='s', utc=True))
    return frame


def build_report(client: ArchiveClient) -> str:
    months = client.manifest.get('months', {})
    entries = client.manifest['archives']
    report = {'generated_at': utc_now(), 'symbol': SYMBOL,
              'available_years': client.manifest.get('available_years', []),
              'archives_discovered': len(client.manifest.get('inventory', [])),
              'archives_downloaded': sum(e.get('status') == 'downloaded' for e in entries.values()),
              'archives_processed': sum(e.get('processed_sha256') == e.get('sha256') and 'sha256' in e for e in entries.values()),
              'failed_downloads': [e['period'] for e in entries.values() if e.get('status') == 'failed'],
              'processing_errors': {e['period']: e['processing_error'] for e in entries.values() if e.get('processing_error')},
              'years_with_ticks': sorted({m[:4] for m in months}), 'months_with_ticks': len(months),
              'total_unique_ticks': sum(m['ticks'] for m in months.values()),
              'trading_days_with_ticks': sum(m['trading_days'] for m in months.values()),
              'M1_candles': sum(m['M1'] for m in months.values()),
              'M5_candles': sum(m['M5'] for m in months.values()),
              'invalid_rows_quarantined': sum(e.get('quality', {}).get('invalid_rows', 0) for e in entries.values()),
              'input_rows': sum(e.get('quality', {}).get('input_rows', 0) for e in entries.values()),
              'storage_bytes': sum(p.stat().st_size for p in client.root.rglob('*') if p.is_file() and '_work' not in p.parts),
              'terminal_parity': 'Not verified; designated archive server can differ from account server.',
              'gap_interpretation': 'Missing calendar days/minutes are not filled. Weekends, maintenance and holidays can be legitimate closures.'}
    annual_check_path = client.root/'reports/annual_gap_check.json'
    if annual_check_path.exists():
        annual_check = json.loads(annual_check_path.read_text())
        refs = annual_check['annual_archives']
        report['annual_gap_check'] = {
            'checked_at':annual_check['checked_at'],'years_checked':sorted(refs),
            'additional_original_archives':len(refs),
            'additional_original_tick_rows':sum(v.get('original_tick_rows',0) for v in refs.values()),
            'dates_with_available_annual_ticks':annual_check['dates_with_available_annual_ticks'],
            'purpose':'Additional annual ZIPs retained as gap-check references; overlapping full-year records are not duplicated in the normalized dataset.',
        }
    report['original_zip_files_preserved'] = sum(1 for _ in (client.root/'raw').rglob('*.zip'))
    if months:
        first = min(m['first_timestamp_ns'] for m in months.values())
        last = max(m['last_timestamp_ns'] for m in months.values())
        report.update(first_tick_utc=pd.Timestamp(first, unit='ns', tz='UTC').isoformat(),
                      last_tick_utc=pd.Timestamp(last, unit='ns', tz='UTC').isoformat(),
                      coverage_days=(last-first)/86_400_000_000_000)
        # Inspect daily presence and observed intraday gaps; never invent a broker holiday calendar.
        with database(client.root) as con:
            files = '[' + ','.join(sql_string(p) for p in (client.root / 'candles/M1').glob('*/*.parquet')) + ']'
            days = {r[0] for r in con.execute(f'SELECT DISTINCT timestamp // 86400 FROM read_parquet({files})').fetchall()}
            start_day, last_day = first // 86_400_000_000_000, last // 86_400_000_000_000
            missing = [pd.Timestamp(d*86400, unit='s', tz='UTC').date().isoformat() for d in range(start_day, last_day+1) if d not in days]
            weekdays = [d for d in missing if pd.Timestamp(d).weekday() < 5]
            report['missing_calendar_dates'] = missing
            report['missing_weekdays_requiring_calendar_review'] = weekdays
            gaps = con.execute(f'''WITH t AS (SELECT timestamp,lag(timestamp) OVER (ORDER BY timestamp) AS previous
                FROM read_parquet({files})) SELECT previous,timestamp,timestamp-previous-60 AS seconds_without_M1
                FROM t WHERE timestamp-previous>60 ORDER BY previous''').fetchall()
            gap_path = client.root / 'reports/observed_gaps.csv'
            gap_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(gaps, columns=['previous_candle_utc_seconds','next_candle_utc_seconds','seconds_without_M1']).to_csv(gap_path,index=False)
            report['observed_gaps'] = len(gaps)
            report['yearly_coverage'] = []
            for year in sorted({m[:4] for m in months}):
                group = [v for k,v in months.items() if k.startswith(year)]
                report['yearly_coverage'].append({'year':year,'months':len(group),
                    'ticks':sum(m['ticks'] for m in group),'trading_days':sum(m['trading_days'] for m in group),
                    'M1':sum(m['M1'] for m in group),'M5':sum(m['M5'] for m in group)})
            expected_months = {str(p) for p in pd.period_range(pd.Timestamp(first,unit='ns').strftime('%Y-%m'),
                               pd.Timestamp(last,unit='ns').strftime('%Y-%m'),freq='M')}
            report['missing_months_between_first_and_last'] = sorted(expected_months-set(months))
            report['duplicate_or_overlapping_input_rows_removed'] = report['input_rows']-report['invalid_rows_quarantined']-report['total_unique_ticks']
            report['M1_complete_candles'] = con.execute(f'SELECT count(*) FROM read_parquet({files}) WHERE complete').fetchone()[0]
            mf = '['+','.join(sql_string(p) for p in (client.root/'candles/M5').glob('*/*.parquet'))+']'
            report['M5_complete_candles'] = con.execute(f'SELECT count(*) FROM read_parquet({mf}) WHERE complete').fetchone()[0]
    atomic_json(client.root / 'reports/coverage.json', report)
    lines = ['# Exness XAUUSDm downloaded history','',
             f'Generated: {report["generated_at"]}', '',
             f'Observed UTC coverage: **{report.get("first_tick_utc","pending")} → {report.get("last_tick_utc","pending")}**', '',
             '| Measure | Value |','|---|---:|',
             f'| Archives discovered | {report["archives_discovered"]:,} |',
             f'| Archives downloaded / processed | {report["archives_downloaded"]:,} / {report["archives_processed"]:,} |',
             f'| Unique ticks | {report["total_unique_ticks"]:,} |',
             f'| M1 / M5 candles | {report["M1_candles"]:,} / {report["M5_candles"]:,} |',
             f'| UTC dates with ticks (including Sunday sessions) | {report["trading_days_with_ticks"]:,} |',
             f'| Months with ticks | {report["months_with_ticks"]} |',
             f'| Rejected rows | {report["invalid_rows_quarantined"]:,} |',
             f'| Dataset storage (GiB, excluding chart DB) | {report["storage_bytes"]/1024**3:.2f} |', '',
             '## Coverage by year','', '| Year | Months | Days with ticks | Unique ticks | M1 | M5 |',
             '|---|---:|---:|---:|---:|---:|']
    for row in report.get('yearly_coverage',[]):
        lines.append(f'| {row["year"]} | {row["months"]} | {row["trading_days"]:,} | {row["ticks"]:,} | {row["M1"]:,} | {row["M5"]:,} |')
    lines += ['', '## Gaps and limits','',
              f'Missing whole months between the first and last tick: {report.get("missing_months_between_first_and_last",[])}.', '',
              f'Failed downloads: {report["failed_downloads"]}. Processing errors: {report["processing_errors"]}.', '',
              f'Weekdays without ticks requiring holiday/server-calendar review: {len(report.get("missing_weekdays_requiring_calendar_review",[]))}.', '',
              'Weekday gaps are listed in `coverage.json`; every observed M1 gap is in `observed_gaps.csv`.',
              'No missing bars are filled. Local verification is recorded separately in `verification.json`.',
              'This confirms downloaded source coverage, not uninterrupted trading or account-server completeness.', '',
              'Current UTC archive-day candles are excluded from the default ML loader. See `../ml/windows.json` for recent training candidates.', '',
              '**100% Exness Terminal parity is not verified.** Archive and account servers can differ.', '']
    if report.get('annual_gap_check'):
        lines += ['The older weekday gaps were also checked against official annual ZIPs for 2015, 2016 and 2021.',
                  f'Additional dates recoverable from those annual ZIPs: {report["annual_gap_check"]["dates_with_available_annual_ticks"]}.',
                  'The annual ZIP originals and their checks are retained in `annual_gap_check.json`.', '']
    (client.root/'reports/coverage.md').write_text('\n'.join(lines))
    client.manifest['coverage'] = report
    client.save()
    return json.dumps(report, indent=2)
