"""Independent CSV decoder verifies every active source row against clean ticks."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .exness_dataset import database, sql_string


def verify_source_month(root: Path, month: str, entries: list[dict]) -> dict:
    """Use DuckDB's CSV parser instead of the production Pandas ingestion.

    Original ZIP members are extracted one at a time into temporary audit storage.
    Disk-backed Parquet stages bound memory; every record is checked, not sampled.
    """
    with tempfile.TemporaryDirectory(prefix='source-audit-',dir=root/'_work') as temp:
        work = Path(temp)
        with database(root) as con:
            con.execute('SET preserve_insertion_order=true')
            stages = []
            rows = invalid = 0
            for entry in entries:
                period = entry['period']
                begin = pd.Timestamp(period+('-01-01' if len(period)==4 else '-01' if len(period)==7 else ''),tz='UTC')
                end = begin+(pd.DateOffset(years=1) if len(period)==4 else pd.DateOffset(months=1) if len(period)==7 else pd.Timedelta(days=1))
                offset = 0
                with zipfile.ZipFile(root/entry['relative_path']) as z:
                    for member in z.infolist():
                        if not member.filename.lower().endswith('.csv'):
                            continue
                        csv = work/'original.csv'
                        with z.open(member) as source, csv.open('wb') as dest:
                            shutil.copyfileobj(source,dest,1024*1024)
                        stage = work/f'part-{len(stages)}.parquet'
                        # Ordinality is assigned by the table function before any
                        # filtering/grouping, and matches the original CSV row position.
                        con.execute(f'''CREATE OR REPLACE TEMP VIEW decoded AS SELECT
                            epoch_ns(try_cast("Timestamp" AS TIMESTAMP_NS)) AS timestamp_ns,
                            try_cast("Bid" AS DOUBLE) AS bid,try_cast("Ask" AS DOUBLE) AS ask,
                            "Symbol" AS symbol,"Timestamp" AS original_timestamp,
                            ordinality-1+{offset} AS source_row
                            FROM read_csv({sql_string(csv)}, header=true, auto_detect=false,
                              columns={{'Exness':'VARCHAR','Symbol':'VARCHAR','Timestamp':'VARCHAR',
                                        'Bid':'VARCHAR','Ask':'VARCHAR'}},parallel=false) WITH ORDINALITY''')
                        bad = f'''symbol IS DISTINCT FROM 'XAUUSDm' OR timestamp_ns IS NULL
                            OR NOT regexp_matches(original_timestamp,'(Z|\\+00:00)$')
                            OR bid IS NULL OR ask IS NULL OR NOT isfinite(bid) OR NOT isfinite(ask)
                            OR bid<=0 OR ask<bid OR timestamp_ns<{begin.value} OR timestamp_ns>={end.value}'''
                        n,bad_n = con.execute(f'SELECT count(*),count(*) FILTER (WHERE {bad}) FROM decoded').fetchone()
                        rows += n; invalid += bad_n; offset += n
                        con.execute(f'''COPY (SELECT timestamp_ns,bid,ask,source_row,
                            {sql_string(entry['filename'])} AS source_file,{len(period.split('-'))} AS source_priority,
                            ({bad}) AS invalid FROM decoded) TO {sql_string(stage)}
                            (FORMAT PARQUET,COMPRESSION ZSTD)''')
                        stages.append(stage)
                        csv.unlink()
                if offset != entry.get('quality',{}).get('input_rows',offset):
                    raise ValueError(f'CSV row count differs from manifest: {entry["filename"]}')
            if not stages:
                raise ValueError(f'No original CSV sources for {month}')
            files = '['+','.join(sql_string(p) for p in stages)+']'
            begin = pd.Timestamp(month+'-01',tz='UTC').value
            end = (pd.Timestamp(month+'-01',tz='UTC')+pd.DateOffset(months=1)).value
            expected_path = work/'expected.parquet'
            con.execute(f'''COPY (WITH per_source AS (
                SELECT timestamp_ns,bid,ask,source_file,source_priority,
                       min(source_row) AS source_row,max(source_row) AS source_last_row
                FROM read_parquet({files}) WHERE NOT invalid
                    AND timestamp_ns>={begin} AND timestamp_ns<{end}
                GROUP BY timestamp_ns,bid,ask,source_file,source_priority)
                SELECT * FROM per_source QUALIFY row_number() OVER (
                    PARTITION BY timestamp_ns,bid,ask ORDER BY source_priority DESC,source_file DESC)=1)
                TO {sql_string(expected_path)} (FORMAT PARQUET,COMPRESSION ZSTD)''')
            con.execute(f'CREATE TEMP VIEW expected AS FROM read_parquet({sql_string(expected_path)})')
            path = root/'ticks'/month[:4]/(month+'.parquet')
            last = 'coalesce(source_last_row,source_row)' if 'source_last_row' in pq.read_schema(path).names else 'source_row'
            con.execute(f'''CREATE TEMP VIEW actual AS SELECT timestamp_ns,bid,ask,source_file,source_priority,
                source_row,{last} AS source_last_row
                FROM read_parquet({sql_string(path)},union_by_name=true)''')
            changed = ' OR '.join(f'e.{c} IS DISTINCT FROM a.{c}' for c in
                                 ('source_file','source_priority','source_row','source_last_row'))
            expected_n,missing,extra = con.execute(f'''SELECT count(e.timestamp_ns),
                count(*) FILTER (WHERE e.timestamp_ns IS NOT NULL AND (a.timestamp_ns IS NULL OR {changed})),
                count(*) FILTER (WHERE a.timestamp_ns IS NOT NULL AND (e.timestamp_ns IS NULL OR {changed}))
                FROM expected e FULL OUTER JOIN actual a
                    ON e.timestamp_ns=a.timestamp_ns AND e.bid=a.bid AND e.ask=a.ask''').fetchone()
            return {'original_rows_decoded':rows,'invalid_original_rows':invalid,
                    'expected_unique_ticks':expected_n,'missing_or_changed_clean_ticks':missing,
                    'extra_or_changed_clean_ticks':extra,
                    'status':'passed' if invalid==missing==extra==0 else 'failed'}
