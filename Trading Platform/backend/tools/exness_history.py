#!/usr/bin/env python3
"""Run from backend with .venv/bin/python tools/exness_history.py update."""
from __future__ import annotations
import argparse
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.market.exness_archive import ArchiveClient


def main():
    parser = argparse.ArgumentParser(description='Download and prepare official Exness XAUUSDm history')
    parser.add_argument('command', choices=['download', 'process', 'update', 'report', 'prepare-ml', 'verify', 'import', 'check-gaps','organize'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'data/exness/XAUUSDm')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--start-year', type=int, help='Default: earliest available year')
    parser.add_argument('--no-import', action='store_true', help='Only Parquet; do not import chart candles')
    parser.add_argument('--force', action='store_true', help='Verify every row and ZIP CRC without reusing prior audit results')
    parser.add_argument('--gap-minutes', type=int, default=30, help='ML split gap: must cover actual forecast label horizon')
    parser.add_argument('--years',default='2015,2016,2021',help='Annual archive gap-check years')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / '.pipeline.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, 'Another history job is running for this directory.\n')
        client = ArchiveClient(args.root)
        try:
            if args.command in ('download', 'update'):
                entries = client.discover(args.start_year)
                print(f'DISCOVERED {len(entries)} archives; years {client.manifest["available_years"]}', flush=True)
                # Current-market training gets the newest files first; all older
                # discovered history is still downloaded in the same run.
                entries.sort(key=lambda entry: entry['period'], reverse=True)
                on_ready = None
                if args.command == 'update':
                    from app.market.exness_dataset import process_all
                    on_ready = lambda: process_all(client, import_chart=not args.no_import)
                client.download_all(entries, args.workers, on_ready=on_ready)
            if args.command in ('process', 'update', 'report'):
                from app.market.exness_dataset import process_all, build_report
                if args.command != 'report':
                    process_all(client, import_chart=not args.no_import)
                if args.command != 'update':
                    print(build_report(client), flush=True)
            if args.command in ('update', 'prepare-ml','organize'):
                from app.market.exness_catalog import normalize_tick_schemas
                if args.command in ('update','organize'):
                    print(f'NORMALIZED {normalize_tick_schemas(args.root)} tick schemas',flush=True)
                from app.market.exness_ml import prepare_ml
                spec = prepare_ml(args.root, gap_minutes=args.gap_minutes)
                print(f'ML windows prepared through {spec["data_end_exclusive_utc"]}', flush=True)
                if args.command in ('update','organize'):
                    from app.market.exness_dataset import build_report
                    print(build_report(client),flush=True)
                    from app.market.exness_audit import verify_dataset
                    audit = verify_dataset(client)
                    print(f'VERIFICATION {audit["status"]}: {audit["issues"]}',flush=True)
                    if audit['issues']:
                        return 1
                    from app.market.exness_catalog import build_catalog
                    catalog = build_catalog(args.root)
                    print(f'ML ORGANIZATION {catalog["status"]}: {catalog["issues"]}',flush=True)
                    if catalog['issues']:
                        return 1
            if args.command == 'verify':
                from app.market.exness_audit import verify_dataset
                result = verify_dataset(client, force=args.force)
                print(f'VERIFICATION {result["status"]}: {result["issues"]}', flush=True)
                if result['issues']:
                    return 1
            if args.command == 'import':
                from app.market.exness_dataset import import_month
                for month, stats in sorted(client.manifest.get('months',{}).items()):
                    print(f'IMPORTING {month}',flush=True)
                    import_month(args.root,month,stats)
                client.save()
            if args.command == 'check-gaps':
                from app.market.exness_gaps import check_annual_gaps
                result = check_annual_gaps(client,[int(y) for y in args.years.split(',')])
                if result['dates_with_available_annual_ticks']:
                    print('ANNUAL RECOVERY CANDIDATES:',result['dates_with_available_annual_ticks'],flush=True)
            failures = [e for e in client.manifest['archives'].values() if e.get('status') == 'failed']
            failures += [e for e in client.manifest['archives'].values() if e.get('processing_error')]
            if failures:
                return 1
        finally:
            client.http.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
