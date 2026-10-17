"""Read-only Exness archive snapshots for future ML/backtest callers.

UTC bar closes gate visibility. Missing minutes and incomplete source snapshots
block a candidate; an older candle is never silently substituted. This module
does not change indicator rules or certify a live execution feed.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .exness_archive import sha256
from .timeframes import TIMEFRAMES


class SnapshotError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def utc_seconds(value: str) -> int:
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise SnapshotError('Use an ISO timestamp with Z or an explicit UTC offset') from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise SnapshotError('Timestamp must include Z or an explicit UTC offset')
    return int(stamp.tz_convert('UTC').timestamp())


def iso(seconds: int) -> str:
    return pd.Timestamp(seconds, unit='s', tz='UTC').isoformat()


def frame_codes(values) -> list[str]:
    codes = list(dict.fromkeys(v.strip().upper() for v in values))
    if not codes or any(v not in TIMEFRAMES for v in codes):
        raise SnapshotError(f'Timeframes must be from {list(TIMEFRAMES)}')
    return codes


def validate_minutes(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    if not pd.api.types.is_integer_dtype(frame.timestamp.dtype) or frame.isna().any().any():
        raise SnapshotError('M1 data contains nulls or noninteger timestamps', 409)
    t = frame.timestamp.to_numpy()
    if np.any(t % 60) or np.any(np.diff(t) <= 0):
        raise SnapshotError('M1 timestamps are unaligned, duplicated or unsorted', 409)
    for side in ('bid', 'ask'):
        o,h,l,c = (frame[f'{side}_{k}'].to_numpy() for k in ('open','high','low','close'))
        if (not np.isfinite(np.column_stack((o,h,l,c))).all() or np.any(l<=0)
                or np.any(l>np.minimum(o,c)) or np.any(h<np.maximum(o,c))):
            raise SnapshotError('Invalid M1 OHLC values', 409)
    if any((frame[f'bid_{k}']>frame[f'ask_{k}']).any() for k in ('open','high','low','close')):
        raise SnapshotError('Crossed Bid/Ask candle values', 409)
    spreads = frame[['spread_min','spread_max','spread_mean']].to_numpy()
    if (not np.isfinite(spreads).all() or (frame.spread_min<0).any()
            or (frame.spread_max<frame.spread_min).any()
            or (frame.spread_mean<frame.spread_min-1e-10).any()
            or (frame.spread_mean>frame.spread_max+1e-10).any()
            or (frame.tick_count<=0).any()):
        raise SnapshotError('Invalid spread or quote-count values', 409)
    if ((frame.first_tick_ns<t*1_000_000_000).any()
            or (frame.last_tick_ns>=(t+60)*1_000_000_000).any()
            or (frame.last_tick_ns<frame.first_tick_ns).any()):
        raise SnapshotError('Quote timestamps lie outside their M1 candles', 409)


def missing_spans(frame: pd.DataFrame, start: int, end: int) -> list[dict]:
    """Unobserved minutes, including leading/trailing absence; no calendar guesses."""
    absent = np.setdiff1d(np.arange(start,end,60,dtype=np.int64), frame.timestamp.to_numpy())
    if not len(absent):
        return []
    starts = np.r_[0,np.flatnonzero(np.diff(absent)>60)+1]
    ends = np.r_[starts[1:]-1,len(absent)-1]
    return [{'start_utc':iso(int(absent[a])), 'end_exclusive_utc':iso(int(absent[b])+60),
             'missing_minutes':int(b-a+1)} for a,b in zip(starts,ends)]


def confirmed_from_minutes(frame: pd.DataFrame, as_of: int, codes=('M5','H1')) -> dict:
    codes = frame_codes(codes)
    # Filter before validating/aggregating: later bars cannot change this decision's input.
    minutes = frame[frame.timestamp+60<=as_of].copy()
    validate_minutes(minutes)
    snapshots = {}
    for code in codes:
        seconds = TIMEFRAMES[code].seconds
        end = as_of//seconds*seconds
        start = end-seconds
        selected = minutes[(minutes.timestamp>=start)&(minutes.timestamp<end)]
        gaps = missing_spans(selected,start,end)
        reasons = []
        if gaps: reasons.append('unobserved_M1_minutes')
        if not selected.empty and not selected.complete.all(): reasons.append('source_snapshot_incomplete')
        bar = None
        if not reasons and not selected.empty:
            count = int(selected.tick_count.sum())
            bar = {'timestamp':start,'close_time':end,'available_at_utc':iso(end),
                   'tick_count':count,'volume_type':'deduplicated_quote_count',
                   'spread_min':float(selected.spread_min.min()),
                   'spread_max':float(selected.spread_max.max()),
                   'spread_mean':float(np.sum(selected.spread_mean*selected.tick_count)/count),
                   # Decimal strings preserve int64 nanoseconds in JavaScript clients.
                   'first_tick_ns':str(int(selected.first_tick_ns.min())),
                   'last_tick_ns':str(int(selected.last_tick_ns.max())),'complete':True}
            for side in ('bid','ask'):
                bar.update({f'{side}_open':float(selected[f'{side}_open'].iloc[0]),
                            f'{side}_high':float(selected[f'{side}_high'].max()),
                            f'{side}_low':float(selected[f'{side}_low'].min()),
                            f'{side}_close':float(selected[f'{side}_close'].iloc[-1])})
        snapshots[code] = {'confirmed':bar is not None,'expected_open_utc':iso(start),
                           'expected_close_utc':iso(end),'observed_M1_minutes':len(selected),
                           'expected_M1_minutes':seconds//60,'missing_intervals':gaps,
                           'blocked_reasons':reasons,'bar':bar}
    return {'as_of_utc':iso(as_of),'alignment':'utc','snapshots':snapshots,
            'all_timeframes_confirmed':all(v['confirmed'] for v in snapshots.values())}


@contextmanager
def verified_minutes(root: Path, start: int, end: int):
    if not (root/'catalog.json').exists():
        raise SnapshotError('Archive catalog is unavailable; finish data verification first', 409)
    with (root/'.pipeline.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SnapshotError('Archive update/audit is running; retry after it completes', 409) from exc
        catalog = json.loads((root/'catalog.json').read_text())
        accuracy = json.loads((root/'reports/accuracy_check.json').read_text())
        if (catalog.get('provider')!='official_exness_archive' or catalog.get('symbol')!='XAUUSDm'
                or catalog.get('timezone')!='UTC'):
            raise SnapshotError('Archive provider, symbol or timezone does not match XAUUSDm UTC',409)
        if (catalog.get('issues') or not catalog.get('market_data_integrity_passed')
                or not catalog.get('full_original_row_reconciliation_current') or accuracy.get('status')!='passed'):
            raise SnapshotError('A current full-source integrity audit is required', 409)
        hashes = {a['path']:a['sha256'] for a in catalog['artifacts']}
        first_month = pd.Timestamp(start,unit='s',tz='UTC').strftime('%Y-%m')
        last_month = pd.Timestamp(end-1,unit='s',tz='UTC').strftime('%Y-%m')
        parts = []
        evidence = []
        for path in sorted((root/'candles/M1').glob('*/*.parquet')):
            if first_month<=path.stem<=last_month:
                rel = str(path.relative_to(root))
                digest = sha256(path)
                if hashes.get(rel)!=digest:
                    raise SnapshotError(f'M1 partition no longer matches its audit: {path.stem}', 409)
                parts.append(pq.read_table(path,filters=[('timestamp','>=',start),('timestamp','<',end)]).to_pandas())
                evidence.append({'path':rel,'sha256':digest})
        if parts:
            minutes = pd.concat(parts,ignore_index=True)
        else:
            minutes = pd.DataFrame({'timestamp':pd.Series(dtype='int64')})
        yield minutes, {'dataset_version':catalog['dataset_version'],
                         'audit_finished_at_utc':accuracy.get('finished_at'),
                         'M1_partitions_verified':evidence,
                         'provider':'official_exness_archive','symbol':'XAUUSDm'}


def check_chart_feed(store, code: str, bar: dict) -> dict:
    issues = []
    for side in ('bid','ask'):
        feed = store.get_feed(f'exness-archive-xauusdm-{side}')
        if (feed is None or feed.provider!='exness_archive' or feed.symbol!='XAUUSDm'
                or feed.instrument!='XAUUSDm' or feed.price_type!=side or feed.alignment!='utc'):
            issues.append(f'{side}: feed identity/alignment mismatch')
            continue
        series = store.get_series(feed.id,code)
        cached = store.load_bars(series.id,start=bar['timestamp'],end=bar['timestamp']) if series else None
        if cached is None or len(cached)!=1:
            issues.append(f'{side}/{code}: chart candle unavailable')
            continue
        for key in ('open','high','low','close'):
            if float(getattr(cached,key)[0])!=bar[f'{side}_{key}']:
                issues.append(f'{side}/{code}: {key} differs from archive')
        if float(cached.volume[0])!=bar['tick_count'] or not bool(cached.complete[0]):
            issues.append(f'{side}/{code}: quote count/completeness differs')
    return {'status':'failed' if issues else 'passed','issues':issues}


def archive_snapshot(root: Path, as_of: str, codes=('M5','H1'), *, store=None) -> dict:
    decision = utc_seconds(as_of)
    codes = frame_codes(codes)
    start = min(decision//TIMEFRAMES[c].seconds*TIMEFRAMES[c].seconds-TIMEFRAMES[c].seconds for c in codes)
    with verified_minutes(root,start,decision) as (minutes,evidence):
        result = confirmed_from_minutes(minutes,decision,codes)
        result['source'] = evidence
        if store is not None:
            for code,snapshot in result['snapshots'].items():
                if snapshot['confirmed']:
                    check = check_chart_feed(store,code,snapshot['bar'])
                    snapshot['chart_feed_check'] = check
                    if check['issues']:
                        snapshot['confirmed'] = False
                        snapshot['blocked_reasons'].append('chart_feed_mismatch')
                        snapshot['bar'] = None
            result['all_timeframes_confirmed'] = all(s['confirmed'] for s in result['snapshots'].values())
        result['execution_feed_verification'] = {'status':'not_verified',
            'reason':'Historical archive/chart consistency does not verify the account live feed, latency or fills.'}
        return result


def archive_intrabar(root: Path, start: str, end: str, as_of: str) -> dict:
    begin,finish,decision = (utc_seconds(v) for v in (start,end,as_of))
    if begin%60 or finish%60 or finish<=begin or finish-begin>7*86400 or finish>decision:
        raise SnapshotError('Use minute-aligned start < end <= as_of and a maximum seven-day range')
    with verified_minutes(root,begin,finish) as (minutes,evidence):
        validate_minutes(minutes)
        eligible = minutes[minutes.complete & (minutes.timestamp+60<=decision)] if not minutes.empty else minutes
        records = eligible.to_dict(orient='records')
        for bar in records:
            for key in ('first_tick_ns','last_tick_ns'):
                bar[key] = str(bar[key])
        return {'source':evidence,'as_of_utc':iso(decision),'start_utc':iso(begin),
                'end_exclusive_utc':iso(finish),'bars':records,
                'observed_minutes':len(minutes),'eligible_minutes':len(eligible),
                'source_incomplete_minutes':len(minutes)-len(eligible),
                'missing_intervals':missing_spans(minutes,begin,finish),
                'volume_type':'deduplicated_quote_count',
                'execution_limitation':'M1 OHLC cannot resolve which level was hit first within a minute; use original tick sequences when order matters.'}
