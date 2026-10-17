"""Versioned dataset organization, gap annotations, and auditable ML exports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .exness_archive import atomic_json, sha256, utc_now
from .exness_dataset import SCHEMA, database, sql_string

DATA_DICTIONARY = {
    'timezone': 'UTC throughout. Numeric timestamps require the unit below.',
    'ticks': {
        'timestamp_ns': 'UTC quote time as int64 nanoseconds since Unix epoch; original precision retained.',
        'bid': 'Broker Bid quote in USD per troy ounce; not an executed trade.',
        'ask': 'Broker Ask quote; ask >= bid.',
        'source_file': 'Selected active original ZIP filename.',
        'source_row': 'Zero-based first occurrence of this exact quote in the selected original CSV sequence.',
        'source_last_row': 'Zero-based last occurrence; preserves the close when an identical quote repeats.',
        'source_priority': 'Source period specificity: annual=1, monthly=2, daily=3; daily wins exact duplicate quotes across archives. Distinct same-time quotes remain.',
    },
    'candles': {
        'timestamp': 'UTC bar OPEN time as int64 seconds since Unix epoch; intervals are [open, close).',
        'bid_open/high/low/close': 'OHLC from Bid quotes, using original source positions to resolve tied quote times.',
        'ask_open/high/low/close': 'Separate Ask OHLC; not Bid OHLC plus an assumed constant spread.',
        'tick_count': 'Number of retained distinct (timestamp_ns, bid, ask) quotes; NOT traded volume or all CSV events.',
        'spread_min/max/mean': 'Ask minus Bid across retained quotes, in USD; mean is quote weighted after deduplication.',
        'first_tick_ns/last_tick_ns': 'Earliest/latest observed quote time within the bar, in nanoseconds.',
        'complete': 'Conservative elapsed-day snapshot eligibility; does not certify source coverage or every minute.',
    },
    'quality': {
        'timestamp': 'Join key equal to canonical bar open seconds.',
        'available_at_utc_seconds': 'Bar close seconds: earliest time complete OHLC can be used as an input.',
        'previous_timestamp': 'Previous observed bar open, including across monthly file boundaries.',
        'gap_before_seconds': 'Unobserved time between adjacent bar intervals; NULL for the first bar.',
        'starts_new_segment/contiguous_segment_id': 'Sequence boundary and global ID when there is an unobserved interval.',
        'observed_M1_minutes': 'Count of observed M1 bars within this bar: 1 for M1; 1..5 for M5.',
        'expected_M1_minutes': '1 for M1, 5 for M5; calendar closures and source gaps can reduce observed count.',
        'has_unobserved_M1_minutes': 'True when M5 contains fewer than five observed M1 bars; no synthetic minutes added.',
        'eligible_complete_bar': 'Same flag as canonical complete; labels and gap eligibility need additional rules.',
    },
    'ml_daily_summary': 'Descriptive UTC-day statistics, available only after the day closes. Returns are between observed days, not guaranteed adjacent calendar days. Never join a full-day summary to an earlier intraday input.',
}


def normalize_tick_schemas(root: Path) -> int:
    """Legacy no-repeat partitions can safely use first row as last occurrence."""
    fixed = 0
    for path in sorted((root/'ticks').glob('*/*.parquet')):
        schema = pq.read_schema(path)
        if schema.names == SCHEMA.names:
            continue
        if schema.names != SCHEMA.names[:-1]:
            raise ValueError(f'Unexpected tick schema: {path}')
        print(f'NORMALIZING TICK SCHEMA {path.stem}',flush=True)
        tmp = path.with_suffix('.parquet.tmp')
        with pq.ParquetWriter(tmp,SCHEMA,compression='zstd') as writer:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=200_000):
                table = pa.Table.from_batches([batch])
                table = table.append_column('source_last_row',table['source_row'])
                writer.write_table(table.cast(SCHEMA))
        os.replace(tmp,path)
        fixed += 1
    return fixed


def write_quality(root: Path) -> dict:
    """Sidecars add gap flags without changing canonical candles or filling gaps."""
    result = {}
    with database(root) as con:
        for tf,seconds in (('M1',60),('M5',300)):
            files = '['+','.join(sql_string(p) for p in sorted((root/'candles'/tf).glob('*/*.parquet')))+']'
            minute_files = '['+','.join(sql_string(p) for p in sorted((root/'candles/M1').glob('*/*.parquet')))+']'
            con.execute(f'''CREATE OR REPLACE TEMP VIEW minute_counts AS
                SELECT timestamp // {seconds} * {seconds} AS timestamp,count(*) AS observed_M1_minutes
                FROM read_parquet({minute_files}) GROUP BY 1''')
            con.execute(f'''CREATE OR REPLACE TEMP TABLE quality AS WITH prior AS (
                SELECT timestamp,complete,lag(timestamp) OVER (ORDER BY timestamp) AS previous_timestamp
                FROM read_parquet({files}))
                SELECT timestamp,timestamp+{seconds} AS available_at_utc_seconds,
                    previous_timestamp,
                    CASE WHEN previous_timestamp IS NULL THEN NULL
                         ELSE greatest(0,timestamp-previous_timestamp-{seconds}) END AS gap_before_seconds,
                    previous_timestamp IS NULL OR timestamp-previous_timestamp>{seconds} AS starts_new_segment,
                    cast(sum(CASE WHEN previous_timestamp IS NULL OR timestamp-previous_timestamp>{seconds}
                             THEN 1 ELSE 0 END) OVER (ORDER BY timestamp) AS BIGINT) AS contiguous_segment_id,
                    complete AS eligible_complete_bar,
                    observed_M1_minutes,{seconds//60} AS expected_M1_minutes,
                    observed_M1_minutes<{seconds//60} AS has_unobserved_M1_minutes
                    FROM prior JOIN minute_counts USING(timestamp)''')
            for p in sorted((root/'candles'/tf).glob('*/*.parquet')):
                begin = int(pd.Timestamp(p.stem+'-01',tz='UTC').timestamp())
                end = int((pd.Timestamp(p.stem+'-01',tz='UTC')+pd.DateOffset(months=1)).timestamp())
                dest = root/'quality'/tf/p.parent.name/p.name
                dest.parent.mkdir(parents=True,exist_ok=True)
                tmp = dest.with_suffix('.parquet.tmp')
                con.execute(f'COPY (FROM quality WHERE timestamp>={begin} AND timestamp<{end} ORDER BY timestamp) TO {sql_string(tmp)} (FORMAT PARQUET,COMPRESSION ZSTD)')
                os.replace(tmp,dest)
            n,gaps,incomplete,partial_minutes = con.execute('SELECT count(*),count(*) FILTER (WHERE gap_before_seconds>0),count(*) FILTER (WHERE NOT eligible_complete_bar),count(*) FILTER (WHERE has_unobserved_M1_minutes) FROM quality').fetchone()
            canonical_n = con.execute(f'SELECT count(*) FROM read_parquet({files})').fetchone()[0]
            invalid = con.execute('SELECT count(*) FROM quality WHERE observed_M1_minutes<1 OR observed_M1_minutes>expected_M1_minutes').fetchone()[0]
            if n!=canonical_n or invalid:
                raise ValueError(f'Quality sidecars do not match canonical candles: {tf}')
            result[tf] = {'rows':n,'observed_gaps':gaps,'incomplete_rows':incomplete,
                          'bars_with_unobserved_M1_minutes':partial_minutes,
                          'path':f'quality/{tf}/YYYY/YYYY-MM.parquet'}
    atomic_json(root/'quality/catalog.json',{'generated_at':utc_now(),'timeframes':result,
        'rules':['Gap flags mark absence of observed candles, not broker-calendar corruption.',
                 'Do not carry sequence lags or labels across segments unless explicitly modeled.',
                 'M5 bars with unobserved M1 minutes need explicit session/gap handling; eligibility does not fill them.',
                 'available_at_utc_seconds is bar close time, not proof of source completeness.',
                 'Eligibility is conservative source snapshot eligibility, not a filled trade label.']})
    return result


def snapshot_identity(root: Path) -> tuple[str,list[dict]]:
    manifest = json.loads((root/'manifest.json').read_text())
    artifacts = []
    for entry in sorted(manifest['archives'].values(),key=lambda e:e['filename']):
        artifacts.append({'path':entry['relative_path'],'sha256':sha256(root/entry['relative_path']),'layer':'raw'})
    for layer in ('ticks','candles','quality'):
        for path in sorted((root/layer).rglob('*.parquet')):
            artifacts.append({'path':str(path.relative_to(root)),'sha256':sha256(path),
                              'rows':pq.ParquetFile(path).metadata.num_rows,'layer':layer})
    version = hashlib.sha256(json.dumps(artifacts,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return version,artifacts


def write_market_extremes(root: Path, version: str) -> None:
    """Describe tails for later research; never delete or label quotes as bad."""
    paths = '['+','.join(sql_string(p) for p in sorted((root/'candles/M1').glob('*/*.parquet')))+']'
    queries = {
        'intrabar_range_fraction': '''SELECT timestamp,bid_open,bid_high,bid_low,bid_close,tick_count,
            (bid_high-bid_low)/bid_open AS magnitude FROM bars ORDER BY magnitude DESC LIMIT 10''',
        'maximum_spread_bps': '''SELECT timestamp,bid_open,spread_min,spread_max,spread_mean,tick_count,
            10000*spread_max/bid_open AS magnitude FROM bars ORDER BY magnitude DESC LIMIT 10''',
        'absolute_adjacent_minute_close_return': '''WITH p AS (SELECT *,
            lag(timestamp) OVER (ORDER BY timestamp) AS prev_t,
            lag(bid_close) OVER (ORDER BY timestamp) AS prev_close FROM bars)
            SELECT timestamp,prev_close,bid_close,tick_count,abs(bid_close/prev_close-1) AS magnitude
            FROM p WHERE timestamp-prev_t=60 ORDER BY magnitude DESC LIMIT 10''',
    }
    report = {'generated_at':utc_now(),'dataset_version':version,'scope':'Complete M1 candles only',
              'interpretation':'Descriptive tails, not automatically errors. Verify against original quotes and independent evidence before excluding a period. Fit any outlier treatment on training data only.',
              'extremes':{}}
    with database(root) as con:
        con.execute(f'CREATE TEMP VIEW bars AS SELECT * FROM read_parquet({paths}) WHERE complete')
        for name,query in queries.items():
            frame = con.execute(query).fetchdf()
            frame['timestamp_utc'] = pd.to_datetime(frame.timestamp,unit='s',utc=True).map(lambda t:t.isoformat())
            report['extremes'][name] = frame.to_dict(orient='records')
    atomic_json(root/'reports/market_extremes.json',report)


def verify_ml_exports(root: Path) -> dict:
    spec = json.loads((root/'ml/windows.json').read_text())
    report = {'checked_at':utc_now(),'status':'passed','datasets':{},'issues':[]}
    with database(root) as con:
        for tf,seconds in (('M1',60),('M5',300)):
            paths = '['+','.join(sql_string(p) for p in sorted((root/'candles'/tf).glob('*/*.parquet')))+']'
            con.execute(f'CREATE OR REPLACE TEMP VIEW canonical AS FROM read_parquet({paths})')
            path = root/spec['datasets'][tf]['file']
            con.execute(f'CREATE OR REPLACE TEMP VIEW recent AS FROM read_parquet({sql_string(path)})')
            columns = pq.read_schema(path).names
            mismatch = ' OR '.join(f'r."{col}" IS DISTINCT FROM c."{col}"' for col in columns)
            bad = con.execute(f'SELECT count(*) FROM recent r LEFT JOIN canonical c USING(timestamp) WHERE c.timestamp IS NULL OR {mismatch}').fetchone()[0]
            n,unique,incomplete = con.execute('SELECT count(*),count(DISTINCT timestamp),count(*) FILTER (WHERE NOT complete) FROM recent').fetchone()
            lo = int(pd.Timestamp(spec['scope_start_utc']).timestamp())
            hi = int(pd.Timestamp(spec['data_end_exclusive_utc']).timestamp())
            expected = con.execute(f'SELECT count(*) FROM canonical WHERE complete AND timestamp>={lo} AND timestamp+{seconds}<={hi}').fetchone()[0]
            previous = None
            ordered = True
            for batch in pq.ParquetFile(path).iter_batches(columns=['timestamp']):
                values = batch.column(0).to_numpy()
                if len(values) and ((values[1:]<=values[:-1]).any() or previous is not None and values[0]<=previous):
                    ordered = False
                if len(values): previous = int(values[-1])
            split_counts = {}
            for candidate,bounds in spec['splits'].items():
                split_counts[candidate] = {}
                for name,interval in bounds.items():
                    end = int(pd.Timestamp(interval['end_exclusive']).timestamp())
                    start = '' if interval['start'] is None else f' AND timestamp>={int(pd.Timestamp(interval["start"]).timestamp())}'
                    actual = con.execute(f'SELECT count(*) FROM canonical WHERE complete AND timestamp+{seconds}<={end}{start}').fetchone()[0]
                    split_counts[candidate][name] = actual
                    if actual != spec['datasets'][tf]['split_counts'][candidate][name]:
                        report['issues'].append(f'Split row count differs: {tf}/{candidate}/{name}')
            outside = con.execute(f'SELECT count(*) FROM recent WHERE timestamp<{lo} OR timestamp+{seconds}>{hi}').fetchone()[0]
            detail = {'rows':n,'canonical_rows_in_export_period':expected,'duplicate_timestamps':n-unique,
                      'incomplete_rows':incomplete,'mismatched_rows':bad,'outside_declared_period':outside,
                      'chronological':ordered,'sha256':sha256(path),'split_counts':split_counts}
            report['datasets'][tf] = detail
            if bad or n!=expected or n!=unique or incomplete or outside or not ordered:
                report['issues'].append(f'ML export validation failure: {tf}')
    report['status'] = 'failed' if report['issues'] else 'passed'
    atomic_json(root/'reports/ml_export_check.json',report)
    return report


def build_catalog(root: Path) -> dict:
    quality = write_quality(root)
    exports = verify_ml_exports(root)
    version,artifacts = snapshot_identity(root)
    write_market_extremes(root,version)
    manifest = json.loads((root/'manifest.json').read_text())
    coverage = manifest.get('coverage',{})
    spec = json.loads((root/'ml/windows.json').read_text())
    spec['dataset_version'] = version
    spec['export_sha256'] = {tf:v['sha256'] for tf,v in exports['datasets'].items()}
    atomic_json(root/'ml/windows.json',spec)
    verification = json.loads((root/'reports/verification.json').read_text())
    accuracy_path = root/'reports/accuracy_check.json'
    accuracy = json.loads(accuracy_path.read_text()) if accuracy_path.exists() else {}
    matches = all(verification.get('months',{}).get(Path(a['path']).stem,{}).get('tick_sha256')==a['sha256']
                  for a in artifacts if a['layer']=='ticks')
    raw_matches = all(verification.get('archive_hashes',{}).get(Path(a['path']).name)==a['sha256']
                      for a in artifacts if a['layer']=='raw')
    candles_match = all(verification.get('months',{}).get(Path(a['path']).stem,{}).get(Path(a['path']).parts[1],{}).get('sha256')==a['sha256']
                        for a in artifacts if a['layer']=='candles')
    warnings = [f'{len(coverage.get("missing_weekdays_requiring_calendar_review",[]))} weekdays have no quotes; calendar/source review remains required.',
                'Quotes can differ from the account server; Terminal parity is not certified.',
                'Training labels, indicator features and a model have not been created.',
                '24 months is an initial comparison candidate, not a validated optimum.',
                '30-minute split gap is provisional; it must cover the actual label horizon.',
                'Old whole-second quotes have lower timestamp resolution than recent fractional quotes.']
    issues = list(exports['issues'])
    if not matches or not raw_matches or not candles_match or verification.get('status')!='passed':
        issues.append('Integrity audit is absent, failed, or does not match the current dataset hashes.')
    if coverage.get('invalid_rows_quarantined',0):
        warnings.append(f'{coverage["invalid_rows_quarantined"]} invalid original rows are quarantined; investigate before using affected periods.')
    latest_audit = (accuracy.get('status')=='passed'
                    and accuracy.get('integrity',{}).get('months')==verification.get('months')
                    and accuracy.get('integrity',{}).get('archive_hashes')==verification.get('archive_hashes'))
    full_source_current = (latest_audit and set(accuracy.get('full_source_months',{}))==set(verification.get('months',{}))
                           and all(v.get('status')=='passed' for v in accuracy.get('full_source_months',{}).values()))
    if not latest_audit:
        warnings.append('Full independent accuracy check does not match the current integrity audit; run it before training.')
    elif not full_source_current:
        warnings.append('Every-row original source reconciliation is not current; run verify_exness_accuracy.py --full-source.')
    report = {'schema_version':1,'generated_at':utc_now(),'dataset_version':version,
              'status':'attention' if issues else 'market_data_validated_with_limits',
              'training_dataset_ready':False,'market_data_integrity_passed':not issues,
              'full_independent_accuracy_current':latest_audit,
              'full_original_row_reconciliation_current':full_source_current,
              'provider':'official_exness_archive','symbol':'XAUUSDm','timezone':'UTC',
              'coverage':{k:coverage.get(k) for k in ('first_tick_utc','last_tick_utc','total_unique_ticks','M1_candles','M5_candles')},
              'quality':quality,'issues':issues,'warnings':warnings,'artifacts':artifacts,
              'layers':{'raw':'Unmodified official ZIP/CSV; exact tick sequence.',
                        'ticks':'Monthly unique quotes with first/last source positions.',
                        'candles':'Canonical Bid/Ask M1/M5 OHLC, spread and quote counts.',
                        'quality':'Gap/segment/availability sidecars joined by timestamp.',
                        'ml':'Complete recent exports and chronological candidate splits.',
                        'reports':'Audit evidence, known gaps and source-order repair history.'}}
    atomic_json(root/'catalog.json',report)
    atomic_json(root/'column_dictionary.json',DATA_DICTIONARY)
    atomic_json(root/'reports/ml_readiness.json',{k:v for k,v in report.items() if k not in ('artifacts','layers')})
    lines = ['# Exness XAUUSDm dataset — start here','',
             f'Dataset version: `{version}`',f'Checked: {report["generated_at"]} (UTC)','',
             'This is market data for later ML development. It is not a trained model or a',
             'labeled BUY/SELL training dataset. Read [the beginner guide](../../../../docs/ML_DATA_GUIDE.md)',
             'and `column_dictionary.json` before constructing features or labels.','',
             '| Folder | Purpose |','|---|---|']
    lines += [f'| {name}/ | {description} |' for name,description in report['layers'].items()]
    lines += ['',f'Errors: {issues}.','', 'Known limitations:','']+[f'- {w}' for w in warnings]
    lines += ['', 'Use `catalog.json` to pin an experiment to source/output SHA256 hashes.',
              'Save windows.json with each experiment; updates move candidate dates.',
              'Use `load_ml_split()` for calendar splits. Do not shuffle rows randomly.',
              'Use quality sidecars to avoid constructing lag sequences across data gaps.',
              'Tick counts are quote activity, not traded volume. No absent candles are invented.','']
    (root/'README.md').write_text('\n'.join(lines))
    return report
