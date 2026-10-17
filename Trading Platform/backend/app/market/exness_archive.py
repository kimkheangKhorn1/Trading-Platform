"""Official Exness archive downloads. No account secrets or trading operations."""
from __future__ import annotations

import hashlib
import json
import os
import time
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

ARCHIVE_BASE = "https://ticks.ex2archive.com"
SOURCE_PAGE = "https://www.exness.com/tick-history/"
SYMBOL = "XAUUSDm"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class ArchiveClient:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = root / 'manifest.json'
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {
            'schema_version': 1, 'symbol': SYMBOL, 'account_type': 'Standard',
            'source_page': SOURCE_PAGE, 'archive_base': ARCHIVE_BASE,
            'timezone': 'UTC', 'archives': {},
        }
        self.http = httpx.Client(timeout=httpx.Timeout(120, connect=30), follow_redirects=False,
                                 headers={'User-Agent': 'TradingPlatform-ExnessHistory/1.0'})
        self.cancelled = threading.Event()

    def save(self):
        atomic_json(self.manifest_path, self.manifest)

    def listing(self, path: str) -> list[dict]:
        for attempt in range(5):
            try:
                r = self.http.get(ARCHIVE_BASE + path)
                if r.status_code in (401, 403):
                    raise RuntimeError(f'Archive access denied ({r.status_code}): {path}')
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(min(float(r.headers.get('retry-after', 2 ** attempt)), 30))
                    continue
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list):
                    raise ValueError(f'Unexpected archive listing: {path}')
                return rows
            except (httpx.TransportError, ValueError):
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f'Archive listing retries exhausted: {path}')

    def discover(self, start_year: int | None = None) -> list[dict]:
        """Prefer months: some published annual ZIPs are stale part-year snapshots.

        Daily files supplement the newest month (including its mutable current day).
        Directory entries, rather than speculative calendar URLs, establish availability.
        """
        base = f'/ticks/{SYMBOL}/'
        years = sorted(int(x['name']) for x in self.listing(base)
                       if x['type'] == 'directory' and x['name'].isdigit())
        self.manifest['available_years'] = years
        entries = []
        months = []
        for year in years:
            if start_year and year < start_year:
                continue
            yp = f'{base}{year}/'
            listing = self.listing(yp)
            dirs = sorted(x['name'] for x in listing if x['type'] == 'directory' and x['name'].isdigit())
            if not dirs:
                for x in listing:
                    if x['name'] == f'Exness_{SYMBOL}_{year}.zip':
                        entries.append(self._entry(yp, x, str(year)))
            for month in dirs:
                mp = f'{yp}{month}/'
                rows = self.listing(mp)
                files = [x for x in rows if x['name'] == f'Exness_{SYMBOL}_{year}_{month}.zip']
                if files:
                    entries.append(self._entry(mp, files[0], f'{year}-{month}'))
                else:
                    for day in rows:
                        if day['type'] == 'directory' and day['name'].isdigit():
                            dp = mp + day['name'] + '/'
                            for x in self.listing(dp):
                                if x['type'] == 'file' and x['name'].endswith('.zip'):
                                    entries.append(self._entry(dp, x, f'{year}-{month}-{day["name"]}'))
                months.append((year, month, rows))
        if months:
            year, month, rows = max(months, key=lambda m: (m[0], m[1]))
            for day in rows:
                if day['type'] == 'directory' and day['name'].isdigit():
                    dp = f'{base}{year}/{month}/{day["name"]}/'
                    for x in self.listing(dp):
                        if x['type'] == 'file' and x['name'].endswith('.zip'):
                            entries.append(self._entry(dp, x, f'{year}-{month}-{day["name"]}'))
        entries = list({e['url']: e for e in entries}.values())
        self.manifest['discovered_at'] = utc_now()
        self.manifest['available_periods'] = [e['period'] for e in entries]
        self.manifest['inventory'] = entries
        self.save()
        return entries

    @staticmethod
    def _entry(path: str, row: dict, period: str) -> dict:
        return {'url': ARCHIVE_BASE + path + row['name'], 'filename': row['name'],
                'period': period, 'remote_size': row['size'], 'remote_modified': row['mtime'],
                'relative_path': 'raw/' + period[:4] + '/' + row['name']}

    def download(self, entry: dict) -> dict:
        # A current-day ZIP grows while a multiyear job runs. Refresh its public
        # metadata at action time instead of repeatedly retrying a stale length.
        mutable = entry['period'].startswith(datetime.now(timezone.utc).strftime('%Y-%m'))
        if mutable:
            head = self.http.head(entry['url'])
            head.raise_for_status()
            entry['remote_size'] = int(head.headers['content-length'])
            entry['remote_modified'] = head.headers['last-modified']
        dest = self.root / entry['relative_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        receipt = dest.with_suffix('.zip.json')
        previous = self.manifest['archives'].get(entry['filename'], {})
        if receipt.exists():
            previous = {**previous, **json.loads(receipt.read_text())}
        same = (previous.get('remote_size'), previous.get('remote_modified')) == (
            entry['remote_size'], entry['remote_modified'])
        if same and dest.exists() and dest.stat().st_size == entry['remote_size']:
            if sha256(dest) == previous.get('sha256'):
                return {**previous, **entry}
        partial = dest.with_suffix('.zip.part')
        descriptor = partial.with_suffix('.part.json')
        identity = {'size': entry['remote_size'], 'mtime': entry['remote_modified']}
        if not descriptor.exists() or json.loads(descriptor.read_text()) != identity:
            partial.unlink(missing_ok=True)
            atomic_json(descriptor, identity)
        for attempt in range(5):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > entry['remote_size']:
                partial.unlink()
                offset = 0
            try:
                if offset < entry['remote_size']:
                    headers = {'Range': f'bytes={offset}-'} if offset else {}
                    with self.http.stream('GET', entry['url'], headers=headers) as response:
                        if response.status_code in (401, 403):
                            raise RuntimeError(f'Archive access denied: {response.status_code}')
                        if response.status_code in (429, 500, 502, 503, 504):
                            time.sleep(min(float(response.headers.get('retry-after', 2 ** attempt)), 30))
                            continue
                        response.raise_for_status()
                        if response.status_code == 206:
                            cr = response.headers.get('content-range', '')
                            if not cr.startswith(f'bytes {offset}-') or not cr.endswith(f'/{entry["remote_size"]}'):
                                raise ValueError(f'Unexpected Content-Range: {cr}')
                        elif response.status_code == 200:
                            offset = 0  # server ignored Range: safely restart this file
                            if mutable:
                                entry['remote_size'] = int(response.headers['content-length'])
                                entry['remote_modified'] = response.headers['last-modified']
                        else:
                            raise ValueError(f'Unexpected download status {response.status_code}')
                        with partial.open('ab' if offset else 'wb') as output:
                            for block in response.iter_bytes(1024 * 1024):
                                if self.cancelled.is_set():
                                    raise InterruptedError('Download interrupted; partial bytes retained')
                                output.write(block)
                if partial.stat().st_size != entry['remote_size']:
                    raise ValueError('Downloaded length differs from archive inventory')
                with zipfile.ZipFile(partial) as z:
                    if z.testzip() is not None:
                        raise ValueError('ZIP CRC validation failed')
                    if not any(n.endswith('.csv') for n in z.namelist()):
                        raise ValueError('ZIP has no CSV member')
                digest = sha256(partial)
                if dest.exists() and sha256(dest) != digest:
                    revisions = dest.parent / 'revisions'
                    revisions.mkdir(exist_ok=True)
                    os.replace(dest, revisions / (dest.stem + '.' + sha256(dest)[:16] + '.zip'))
                os.replace(partial, dest)
                descriptor.unlink(missing_ok=True)
                result = {**entry, 'status': 'downloaded', 'sha256': digest, 'downloaded_at': utc_now()}
                atomic_json(receipt, result)
                return result
            except (httpx.TransportError, ValueError, zipfile.BadZipFile):
                if partial.exists() and partial.stat().st_size >= entry['remote_size']:
                    partial.unlink()
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError('Download retries exhausted')

    def download_all(self, entries: list[dict], workers: int = 3, on_ready=None):
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            pending = {pool.submit(self.download, entry): entry for entry in entries}
            for future in as_completed(pending):
                entry = pending[future]
                try:
                    result = future.result()
                    print(f'DOWNLOADED {entry["period"]} {entry["remote_size"]:,} bytes', flush=True)
                except Exception as exc:
                    result = {**entry, 'status': 'failed', 'error': str(exc), 'failed_at': utc_now()}
                    print(f'FAILED {entry["period"]}: {exc}', flush=True)
                self.manifest['archives'][entry['filename']] = result
                self.save()
                if on_ready and result.get('status') == 'downloaded':
                    try:
                        on_ready()
                    except Exception as exc:
                        result['processing_error'] = str(exc)
                        self.save()
                        print(f'PROCESSING FAILED {entry["period"]}: {exc}', flush=True)
        except KeyboardInterrupt:
            self.cancelled.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
