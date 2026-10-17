"""Verify stored originals and every normalized month before presenting ML coverage."""
from __future__ import annotations

import zipfile
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .exness_archive import ArchiveClient, atomic_json, sha256, utc_now
from .exness_dataset import database, sql_string


def verify_dataset(client: ArchiveClient, *, force: bool = False) -> dict:
    root = client.root
    issues = []
    monthly = {}
    verified = 0
    archive_hashes = {}
    prior_path = root/'reports/verification.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    if force or prior.get('audit_version') != 3:
        prior = {}
    for inventory in client.manifest.get('inventory',[]):
        if inventory['filename'] not in client.manifest['archives']:
            issues.append(f'Archive not downloaded: {inventory["period"]}')
    for entry in client.manifest['archives'].values():
        path = root / entry['relative_path']
        if entry.get('status') != 'downloaded' or not path.exists():
            issues.append(f'Unavailable archive: {entry["period"]}')
            continue
        if sha256(path) != entry['sha256']:
            issues.append(f'Original SHA256 mismatch: {entry["filename"]}')
            continue
        if prior.get('archive_hashes',{}).get(entry['filename']) != entry['sha256']:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad:
                    issues.append(f'Original ZIP CRC failure: {entry["filename"]}/{bad}')
                    continue
        archive_hashes[entry['filename']] = entry['sha256']
        verified += 1
        if entry.get('processed_sha256') != entry['sha256']:
            issues.append(f'Archive not processed: {entry["period"]}')
    with database(root) as con:
        for month, metadata in sorted(client.manifest.get('months', {}).items()):
            issues_before = len(issues)
            path = root / 'ticks' / month[:4] / (month+'.parquet')
            print(f'VERIFYING {month}', flush=True)
            if not path.exists():
                issues.append(f'Missing tick partition: {month}')
                continue
            tick_hash = sha256(path)
            absent = [tf for tf in ('M1','M5') if not (root/'candles'/tf/month[:4]/(month+'.parquet')).exists()]
            if absent:
                issues.append(f'Missing candle partition: {month}/{absent}')
                continue
            candle_hashes = {tf:sha256(root/'candles'/tf/month[:4]/(month+'.parquet')) for tf in ('M1','M5')}
            cached = prior.get('months',{}).get(month,{})
            if cached.get('validated') and cached.get('tick_sha256') == tick_hash and all(cached.get(tf,{}).get('sha256') == candle_hashes[tf] for tf in ('M1','M5')):
                monthly[month] = cached
                continue
            previous = None
            tick_count = 0
            begin = pd.Timestamp(month+'-01',tz='UTC').value
            end = (pd.Timestamp(month+'-01',tz='UTC')+pd.DateOffset(months=1)).value
            for batch in pq.ParquetFile(path).iter_batches(columns=['timestamp_ns','bid','ask'],batch_size=100_000):
                t,b,a = (batch.column(i).to_numpy() for i in range(3))
                tick_count += len(t)
                if (np.diff(t)<0).any() or previous is not None and t[0] < previous:
                    issues.append(f'Nonchronological ticks: {month}')
                if (~np.isfinite(b) | ~np.isfinite(a) | (b<=0) | (a<b)).any():
                    issues.append(f'Invalid clean quote: {month}')
                if ((t<begin)|(t>=end)).any():
                    issues.append(f'Tick outside its partition period: {month}')
                previous = t[-1]
            dup = con.execute(f'''SELECT count(*) FROM (SELECT timestamp_ns,bid,ask,count(*) AS n
                FROM read_parquet({sql_string(path)}) GROUP BY timestamp_ns,bid,ask HAVING n>1)''').fetchone()[0]
            if dup:
                issues.append(f'{dup} duplicate tick groups: {month}')
            if tick_count != metadata['ticks']:
                issues.append(f'Tick count differs from manifest: {month}')
            detail = {'ticks':tick_count,'duplicate_tick_groups':dup,'tick_sha256':tick_hash}
            for tf, seconds in (('M1',60),('M5',300)):
                candle_path = root / 'candles' / tf / month[:4] / (month+'.parquet')
                invalid = ' OR '.join(f'''NOT (isfinite({side}_open) AND isfinite({side}_high)
                    AND isfinite({side}_low) AND isfinite({side}_close)
                    AND {side}_low>0 AND {side}_low<=least({side}_open,{side}_close)
                    AND {side}_high>=greatest({side}_open,{side}_close))''' for side in ('bid','ask'))
                n,quotes,unique,malformed,partial = con.execute(f'''SELECT count(*),sum(tick_count),
                    count(DISTINCT timestamp),count(*) FILTER (WHERE {invalid} OR spread_min<0
                        OR NOT isfinite(spread_min) OR NOT isfinite(spread_max) OR NOT isfinite(spread_mean)
                        OR spread_max<spread_min OR spread_mean<spread_min-1e-10 OR spread_mean>spread_max+1e-10
                        OR bid_open>ask_open OR bid_close>ask_close OR bid_high>ask_high OR bid_low>ask_low
                        OR timestamp%{seconds}<>0 OR tick_count<=0 OR last_tick_ns<first_tick_ns
                        OR first_tick_ns<timestamp*1000000000 OR last_tick_ns>=(timestamp+{seconds})*1000000000),
                    count(*) FILTER (WHERE NOT complete) FROM read_parquet({sql_string(candle_path)})''').fetchone()
                if n != unique or malformed or quotes != tick_count or n != metadata[tf]:
                    issues.append(f'Candle validation failure: {month}/{tf}: n={n},unique={unique},invalid={malformed},quotes={quotes},ticks={tick_count}')
                detail[tf] = {'candles':n,'partial_candles':partial,'quote_count':quotes,
                              'sha256':candle_hashes[tf]}
            detail['validated'] = len(issues) == issues_before
            monthly[month] = detail
    report = {'audit_version':3,'verified_at':utc_now(),'status':'passed' if not issues else 'failed',
              'archive_hashes':archive_hashes,
              'original_archives_verified':verified,'months_verified':len(monthly),
              'unique_ticks_verified':sum(m['ticks'] for m in monthly.values()),
              'duplicate_tick_groups':sum(m['duplicate_tick_groups'] for m in monthly.values()),
              'issues':issues,'months':monthly,
              'scope':'Local CRC/hash, quote validity, ordering, duplicate and OHLC/count consistency. Does not certify broker server completeness or Terminal parity.'}
    atomic_json(root/'reports/verification.json',report)
    return report
