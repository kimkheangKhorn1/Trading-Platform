#!/usr/bin/env python3
"""Rebuild legacy deduplicated quotes that lost their last original row position.

Preserves raw archives. Resume by rerunning; newly processed files record version 2.
"""
from __future__ import annotations
import fcntl
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.market.exness_archive import ArchiveClient, atomic_json, utc_now
from app.market.exness_dataset import database, sql_string, process_all, build_report
from app.market.exness_ml import prepare_ml


def main():
    root = Path(__file__).resolve().parents[1]/'data/exness/XAUUSDm'
    with (root/'.pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client = ArchiveClient(root)
        report_path = root/'reports/source_order_repair.json'
        report = json.loads(report_path.read_text()) if report_path.exists() else {
            'started_at':utc_now(),'reason':'Exact repeat A-B-A at one timestamp must retain the final A for OHLC close.',
            'status':'running','archives_to_rebuild':[],
            'ticks_before':sum(v['ticks'] for v in client.manifest['months'].values())}
        backup = root/'reports/before_source_order_repair'
        backup.mkdir(exist_ok=True)
        for name in ('manifest.json','reports/accuracy_check.json','reports/verification.json'):
            source = root/name
            dest = backup/source.name
            if source.exists() and not dest.exists():
                shutil.copy2(source,dest)
        with database(root) as con:
            counts = {}
            for month in client.manifest['months']:
                path = root/'ticks'/month[:4]/(month+'.parquet')
                for source,n in con.execute(f'SELECT source_file,count(*) FROM read_parquet({sql_string(path)}) GROUP BY source_file').fetchall():
                    counts[source] = counts.get(source,0)+n
            for entry in client.manifest['archives'].values():
                if entry.get('source_order_version')==2:
                    continue
                raw_valid = entry['quality']['input_rows']-entry['quality']['invalid_rows']
                if raw_valid != counts.get(entry['filename'],0):
                    entry['processed_sha256'] = None
                    if entry['filename'] not in report['archives_to_rebuild']:
                        report['archives_to_rebuild'].append(entry['filename'])
                    for month in entry.get('output_months',[entry['period'][:7]]):
                        for tf in ('M1','M5'):
                            p = root/'candles'/tf/month[:4]/(month+'.parquet')
                            old = backup/tf/(month+'.parquet')
                            old.parent.mkdir(exist_ok=True)
                            if not old.exists():
                                shutil.copy2(p,old)
                else:
                    # No removed rows: first and last source positions are identical.
                    entry['source_order_version'] = 2
            client.save()
        atomic_json(report_path,report)
        print(f'REBUILD {len(report["archives_to_rebuild"])} affected archives',flush=True)
        try:
            process_all(client,import_chart=True)
            prepare_ml(root)
            build_report(client)
            import pyarrow.parquet as pq
            changed = {'M1':{'bid_close':0,'ask_close':0},'M5':{'bid_close':0,'ask_close':0}}
            for tf in ('M1','M5'):
                for p in (backup/tf).glob('*.parquet'):
                    before = pq.read_table(p).to_pandas().set_index('timestamp')
                    after = pq.read_table(root/'candles'/tf/p.stem[:4]/p.name).to_pandas().set_index('timestamp')
                    for col in changed[tf]:
                        changed[tf][col] += int((before[col]!=after[col]).sum())
            report.update(status='completed',finished_at=utc_now(),
                          ticks_after=sum(v['ticks'] for v in client.manifest['months'].values()),
                          close_values_corrected=changed)
            atomic_json(report_path,report)
            print(f'REPAIR {report}',flush=True)
        finally:
            client.http.close()


if __name__=='__main__':
    main()
