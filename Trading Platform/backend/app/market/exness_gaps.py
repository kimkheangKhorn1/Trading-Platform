"""Check alternative official annual bundles for whole missing UTC weekdays."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from .exness_archive import ArchiveClient, atomic_json, sha256, utc_now


def check_annual_gaps(client: ArchiveClient, years: list[int]) -> dict:
    root = client.root
    coverage = json.loads((root/'reports/coverage.json').read_text())
    dates = coverage.get('missing_weekdays_requiring_calendar_review',[])
    report = {'checked_at':utc_now(),'source':'official Exness annual ZIPs',
              'annual_archives':{},'dates_with_available_annual_ticks':[],
              'unresolved_dates':dates,
              'interpretation':'Zero counts in both monthly and annual source files cannot be repaired from those archives. Holidays and maintenance need separate calendar review.'}
    for year in years:
        target = [date for date in dates if date.startswith(str(year))]
        if not target:
            continue
        directory = f'/ticks/XAUUSDm/{year}/'
        rows = client.listing(directory)
        match = next((r for r in rows if r['name']==f'Exness_XAUUSDm_{year}.zip'),None)
        if match is None:
            report['annual_archives'][str(year)] = {'status':'not_available','dates_checked':target}
            continue
        entry = client._entry(directory,match,str(year))
        path = root/entry['relative_path']
        # The initially inspected 2015 annual ZIP already exists without a receipt.
        # Verify CRC and record a receipt, preserving the original unchanged.
        if path.exists() and path.stat().st_size == entry['remote_size'] and not path.with_suffix('.zip.json').exists():
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError(f'Annual ZIP CRC failed: {path}')
            atomic_json(path.with_suffix('.zip.json'),{**entry,'sha256':sha256(path),'status':'downloaded','downloaded_at':utc_now()})
        entry = client.download(entry)
        count = 0
        found = {date:0 for date in target}
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if not member.endswith('.csv'):
                    continue
                with archive.open(member) as stream:
                    for chunk in pd.read_csv(stream,usecols=['Timestamp'],dtype=str,chunksize=200_000):
                        count += len(chunk)
                        utc_dates = chunk.Timestamp.str[:10]
                        freq = utc_dates[utc_dates.isin(target)].value_counts()
                        for date,n in freq.items():
                            found[date] += int(n)
        report['annual_archives'][str(year)] = {
            **entry,'original_tick_rows':count,'ticks_on_missing_dates':found,
            'purpose':'gap investigation; not merged into canonical data unless separately validated',
        }
        report['dates_with_available_annual_ticks'] += [date for date,n in found.items() if n]
        atomic_json(root/'reports/annual_gap_check.json',report)
        print(f'ANNUAL GAP CHECK {year}: {found}',flush=True)
    report['unresolved_dates'] = [d for d in dates if d not in report['dates_with_available_annual_ticks']]
    atomic_json(root/'reports/annual_gap_check.json',report)
    return report
