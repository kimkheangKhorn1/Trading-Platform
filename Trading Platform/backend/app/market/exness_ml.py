"""Research candidate data windows; no features, targets, model fitting or trading logic."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import numpy as np

from .exness_archive import atomic_json, utc_now
from .exness_dataset import database, sql_string

RESEARCH = [
    {'url': 'https://www.gold.org/goldhub/gold-focus/2026/04/you-asked-we-answered-has-golds-performance-structurally-changed',
     'finding': 'Volatility and spreads change over time; older observations are not automatically representative.'},
    {'url': 'https://www.gold.org/goldhub/research/gold-market-commentary-september-2026',
     'finding': 'Recent macro drivers and investor flows changed. Monthly research does not establish an intraday ML window.'},
    {'url': 'https://arxiv.org/abs/2412.08435',
     'finding': 'Forecasting faces concept drift and delayed availability of target values.'},
    {'url': 'https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html',
     'finding': 'Evaluate chronologically, cap training windows, and exclude a gap between train and test.'},
]


def prepare_ml(root: Path, *, gap_minutes: int = 30) -> dict:
    """Export complete recent candles and time-range definitions for fair comparisons.

    The gap is provisional for a 1-30-minute forecast. Set it to at least the actual
    target's forward-label horizon before training. Features/scalers must be fitted
    within each training split. Missing trading minutes stay absent.
    """
    if gap_minutes < 0:
        raise ValueError('gap_minutes cannot be negative')
    out = root / 'ml'
    out.mkdir(parents=True, exist_ok=True)
    report = {'generated_at': utc_now(), 'recommendation_status': 'candidate_not_model_validated',
              'initial_candidate_training_months': 24, 'candidate_training_months': [6,12,24,36],
              'assumed_prediction_horizon': 'intraday 1-30 minutes; change gap for actual target',
              'gap_minutes': gap_minutes, 'validation_months': 3, 'test_months': 3,
              'sources': RESEARCH, 'datasets': {}, 'splits': {}, 'walk_forward_folds':{},
              'refit_after_evaluation':{},
              'rules': ['Do not randomly shuffle time into train/validation/test.',
                        'Use identical later validation/test periods for each training-duration candidate.',
                        'Reserve the final test for once-only evaluation; tune on earlier walk-forward folds.',
                        'Exclude labels whose future horizon crosses a split boundary or a missing-data gap.',
                        'Fit scalers, imputers and feature selection only on each training fold.',
                        'OHLC/spread/count features of a bar become available only after that bar closes.',
                        'Compare spreads and returns/volatility across periods; do not use raw price level alone as regime similarity.',
                        'Bid/Ask quotes are not traded volume, exchange footprint or futures delta.',
                        'Full history is retained as a benchmark; longer training is not presumed better.']}
    with database(root) as con:
        m1_files = sorted((root / 'candles/M1').glob('*/*.parquet'))
        if not m1_files:
            raise ValueError('No processed M1 candles; finish the history update first')
        files = '[' + ','.join(sql_string(p) for p in m1_files) + ']'
        last = con.execute(f'SELECT max(timestamp) FROM read_parquet({files}) WHERE complete').fetchone()[0]
        if last is None:
            raise ValueError('No complete candles available for training')
        m5_files = '['+','.join(sql_string(p) for p in sorted((root/'candles/M5').glob('*/*.parquet')))+']'
        last_m5 = con.execute(f'SELECT max(timestamp) FROM read_parquet({m5_files}) WHERE complete').fetchone()[0]
        end = pd.Timestamp(max(last+60,(last_m5+300) if last_m5 is not None else last+60),unit='s',tz='UTC')
        test_start = end - pd.DateOffset(months=3)
        val_start = test_start - pd.DateOffset(months=3)
        recent_start = val_start - pd.DateOffset(months=36)
        gap = pd.Timedelta(minutes=gap_minutes)
        report['data_end_exclusive_utc'] = end.isoformat()
        report['scope_start_utc'] = recent_start.isoformat()
        report['boundary_convention'] = 'UTC calendar bounds; bar open >= start and bar close <= end_exclusive. No label has been created.'
        for months in (6,12,24,36):
            report['splits'][f'train_{months}m'] = {
                'train': {'start': (val_start-pd.DateOffset(months=months)).isoformat(), 'end_exclusive': (val_start-gap).isoformat()},
                'validation': {'start':val_start.isoformat(),'end_exclusive':(test_start-gap).isoformat()},
                'test': {'start':test_start.isoformat(),'end_exclusive':end.isoformat()},
            }
            report['refit_after_evaluation'][f'train_{months}m'] = {
                'start':(end-pd.DateOffset(months=months)).isoformat(),
                'end_exclusive':(end-gap).isoformat(),
                'usage':'Only after candidate selection and final out-of-sample evaluation; no untouched score can be claimed on refit data.',
            }
            report['walk_forward_folds'][f'train_{months}m'] = []
            for offset in (9,6,3,0):
                origin = val_start-pd.DateOffset(months=offset)
                report['walk_forward_folds'][f'train_{months}m'].append({
                    'train':{'start':(origin-pd.DateOffset(months=months)).isoformat(),
                             'end_exclusive':(origin-gap).isoformat()},
                    'validation':{'start':origin.isoformat(),
                                  'end_exclusive':(origin+pd.DateOffset(months=3)-gap).isoformat()},
                })
        report['splits']['full_history_benchmark'] = {
            **report['splits']['train_24m'],
            'train': {'start':None,'end_exclusive':(val_start-gap).isoformat()},
        }
        for tf,seconds in (('M1',60),('M5',300)):
            inputs = '[' + ','.join(sql_string(p) for p in sorted((root/'candles'/tf).glob('*/*.parquet'))) + ']'
            query = f'''SELECT * FROM read_parquet({inputs}) WHERE complete
                AND timestamp >= {int(recent_start.timestamp())} AND timestamp+{seconds} <= {int(end.timestamp())}'''
            dest = out / (tf + '_recent.parquet')
            tmp = dest.with_suffix('.parquet.tmp')
            con.execute(f'COPY ({query} ORDER BY timestamp) TO {sql_string(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)')
            os.replace(tmp,dest)
            counts = {}
            for name, splits in report['splits'].items():
                counts[name] = {}
                for split, bounds in splits.items():
                    clause = f'timestamp+{seconds} <= {int(pd.Timestamp(bounds["end_exclusive"]).timestamp())}'
                    if bounds['start']:
                        clause += f' AND timestamp >= {int(pd.Timestamp(bounds["start"]).timestamp())}'
                    counts[name][split] = con.execute(f'SELECT count(*) FROM read_parquet({inputs}) WHERE complete AND {clause}').fetchone()[0]
            report['datasets'][tf] = {'file':str(dest.relative_to(root)),
                'rows':con.execute(f'SELECT count(*) FROM ({query})').fetchone()[0], 'split_counts':counts}
        # Descriptive, same-source daily data for comparing window regimes; no model or label generation.
        daily = con.execute(f'''SELECT timestamp // 86400 AS utc_day,
            first(bid_open ORDER BY timestamp) AS bid_open,max(bid_high) AS bid_high,
            min(bid_low) AS bid_low,last(bid_close ORDER BY timestamp) AS bid_close,
            sum(tick_count) AS tick_count, count(*) AS observed_M1,
            sum(spread_mean*tick_count)/sum(tick_count) AS quote_weighted_spread_mean
            FROM read_parquet({files}) WHERE complete GROUP BY utc_day ORDER BY utc_day''').fetchdf()
        daily['date_utc'] = pd.to_datetime(daily['utc_day'],unit='D',utc=True)
        daily['close_to_close_log_return'] = np.log(daily['bid_close']).diff()
        daily['realized_vol_20_observed_days'] = daily['close_to_close_log_return'].rolling(20).std()
        daily['quote_weighted_spread_bps'] = 10_000*daily['quote_weighted_spread_mean']/daily['bid_close']
        daily['intraday_range_fraction'] = (daily['bid_high']-daily['bid_low'])/daily['bid_open']
        daily.to_parquet(out/'daily_regime_summary.parquet',index=False,compression='zstd')
        report['descriptive_recent_regimes'] = {}
        def finite_or_none(value):
            return float(value) if pd.notna(value) and np.isfinite(value) else None
        for months in (6,12,24,36):
            subset = daily[daily.date_utc>=end-pd.DateOffset(months=months)]
            report['descriptive_recent_regimes'][f'{months}m'] = {
                'observed_days':len(subset),
                'daily_log_return_std':finite_or_none(subset.close_to_close_log_return.std()),
                'median_intraday_range_fraction':finite_or_none(subset.intraday_range_fraction.median()),
                'median_quote_weighted_spread_bps':finite_or_none(subset.quote_weighted_spread_bps.median()),
                'interpretation':'Descriptive comparison only; does not determine model accuracy or optimal duration.',
            }
    atomic_json(out/'windows.json',report)
    return report


def load_ml_split(root: Path, timeframe: str='M1', candidate: str='train_24m', split: str='train') -> pd.DataFrame:
    """Read a defined candidate split; use the canonical loader for the full-history benchmark."""
    from .exness_dataset import load_training_candles
    spec = json.loads((root/'ml/windows.json').read_text())
    bounds = spec['splits'][candidate][split]
    # The loader accepts UTC-aware ISO strings as well as dates.
    frame = load_training_candles(root,timeframe,start=bounds['start'],end=bounds['end_exclusive'])
    seconds = {'M1':60,'M5':300}[timeframe]
    return frame[frame.timestamp+seconds <= int(pd.Timestamp(bounds['end_exclusive']).timestamp())].reset_index(drop=True)
