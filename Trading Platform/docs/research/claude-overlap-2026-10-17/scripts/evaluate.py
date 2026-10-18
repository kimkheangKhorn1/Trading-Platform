"""Final evaluation of the frozen finalists: overall, Development/Holdout, yearly, monthly, walk-forward, costs.

Run after the finalists were frozen (CLAUDE_OVERLAP_STATUS freeze v2). Needs numpy and the M1 cache made by
cache.py (``--cache DIR``). Writes ../results/*.csv and ../results/summary.json.

Conventions
- Evaluated trades: entries from 2015-09-01 (August 2015 is the engine warm-up) to the cutoff 2026-10-14 00:00 UTC.
- Win (TP rule): TP1 or TP2 reached. TP3 counted separately. Cash-positive: realized R > 0.
- R: realized cash of all partial exits / (actual fill - original SL).
- Monthly "R (entry cohort)": the full realized R of trades entered that month. "R (by exit date)": each partial
  exit's R dated by when it filled. They differ when a trade spans months.
- Missed opportunity (separate from executed trades): an order that never filled, where price came within
  0.10 ATR(1H, at placement) of its limit before the order ended (first crossing, causal). Counted once per setup
  (side + entry core band). Hypothetical outcome from that crossing price with the order's SL and TP1, horizon
  20 calendar days: missed win (TP1 first), avoided loss (SL first) or unresolved.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, '..', 'runs')
OUT = os.path.join(HERE, '..', 'results')
T0 = calendar.timegm((2015, 9, 1, 0, 0, 0))
HOLD = calendar.timegm((2023, 1, 1, 0, 0, 0))
CUTOFF = calendar.timegm((2026, 10, 14, 0, 0, 0))
DATA_FIRST = calendar.timegm((2015, 8, 10, 0, 0, 0))
FINALISTS = {'baseline': 'Baseline (current defaults)',
             'R3_front0.50_be': 'P: entry 0.5 ATR early + stop to entry after TP1',
             'R3_front0.50_q0.3f0.2': 'W: entry 0.5 ATR early + quick TP1 0.3R (20%)',
             'C9_front0.10_q0.3f0.3_be': 'H: entry 0.1 ATR early + quick TP1 0.3R (30%) + stop to entry'}
MISS_ATR = 0.10
MISS_HORIZON = 20 * 86400


def ym(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m')


def load(name: str) -> list[dict]:
    return json.load(open(os.path.join(RUNS, name + '.json')))['rows']


def closed(rows, lo=T0, hi=CUTOFF):
    return sorted((r for r in rows if 'R' in r and lo <= r['entry_time'] < hi), key=lambda r: r['exit_time'])


def stats(tr: list[dict]) -> dict:
    if not tr:
        return dict(n=0)
    R = np.array([t['R'] for t in tr])
    eq = np.concatenate([[0], np.cumsum(R)])
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    wins = [t for t in tr if t['tps_hit'] >= 1]
    return dict(n=len(R), wins=len(wins), losses=len(R) - len(wins), tp_win_rate=len(wins) / len(R) * 100,
                cash_positive_rate=float((R > 0).mean() * 100), total_R=float(R.sum()), avg_R=float(R.mean()),
                t_stat=float(R.mean() / R.std(ddof=1) * math.sqrt(len(R))) if len(R) > 2 else None,
                profit_factor=float(pos / neg) if neg > 0 else None, max_dd_R=float(np.max(np.maximum.accumulate(eq) - eq)),
                tp1=sum(t['tps_hit'] >= 1 for t in tr), tp2=sum(t['tps_hit'] >= 2 for t in tr),
                tp3=sum(t['tps_hit'] >= 3 for t in tr), avg_win_R=float(np.mean([t['R'] for t in wins])) if wins else None,
                avg_loss_R=float(np.mean([t['R'] for t in tr if t['tps_hit'] == 0])) if len(wins) < len(R) else None,
                tp_wins_cash_negative=sum(1 for t in wins if t['R'] <= 0))


def exit_parts(t: dict):
    """(time, R) of every partial exit; the parts sum to the trade's R."""
    units = 1.0
    out = []
    for kind, ts, px in t['fills']:
        if kind.startswith('TP'):
            k = int(kind[2:]) - 1
            u = units if k == len(t['tps']) - 1 else min(t['fracs'][k], units)
        else:
            u = units
        units -= u
        out.append((ts, t['side'] * (px - t['entry']) * u / t['risk']))
    return out


class M1:
    def __init__(self, cache: str):
        self.t = np.load(os.path.join(cache, 'm1t.npy'))
        self.x = np.load(os.path.join(cache, 'm1.npy'))
        self.s = np.load(os.path.join(cache, 'spr.npy'))

    def missed(self, rows: list[dict]) -> list[dict]:
        out, seen = [], set()
        for r in sorted(rows, key=lambda r: r['placed']):
            if r['fate'] not in ('withdrawn', 'invalidated') or not (T0 <= r['placed'] < CUTOFF):
                continue
            a, b = np.searchsorted(self.t, r['placed']), np.searchsorted(self.t, r['end'])
            if b <= a:
                continue
            s, L = r['side'], r['limit']
            band = MISS_ATR * r['atr']
            if s == 1:
                cross = np.nonzero(self.x[a:b, 2] + self.s[a:b] <= L + band)[0]  # ask low reaches limit + band
            else:
                cross = np.nonzero(self.x[a:b, 1] >= L - band)[0]  # bid high reaches limit - band
            if not len(cross):
                continue
            key = (s, round(r['core_bottom'], 2), round(r['core_top'], 2))
            if key in seen:
                continue
            seen.add(key)
            k = a + int(cross[0])
            px = L + s * band
            e = np.searchsorted(self.t, self.t[k] + MISS_HORIZON)
            hi, lo, sp = self.x[k + 1:e, 1], self.x[k + 1:e, 2], self.s[k + 1:e]
            tp, sl = r['tps'][0], r['sl']
            if s == 1:
                th, sh = np.nonzero(hi >= tp)[0], np.nonzero(lo <= sl)[0]
            else:
                th, sh = np.nonzero(lo + sp <= tp)[0], np.nonzero(hi + sp >= sl)[0]
            ti = th[0] if len(th) else math.inf
            si = sh[0] if len(sh) else math.inf
            res = 'missed_win' if ti < si else 'avoided_loss' if si < ti else 'unresolved'
            out.append(dict(id=r['id'], side=s, time=int(self.t[k]), month=ym(int(self.t[k])), entry_would_be=px,
                            sl=sl, tp1=tp, result=res,
                            r_tp1=abs(tp - px) / abs(px - sl) if abs(px - sl) > 0 else None))
        return out


def months():
    out = []
    for y in range(2014, 2027):
        for m in range(1, 13):
            out.append(f'{y}-{m:02d}')
    return out


def coverage(month: str) -> str:
    y, m = map(int, month.split('-'))
    start = calendar.timegm((y, m, 1, 0, 0, 0))
    end = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0))
    if end <= DATA_FIRST or start >= CUTOFF:
        return 'unavailable'
    if start < T0:
        return 'warm-up (partial data, not evaluated)'
    if end > CUTOFF:
        return 'partial (to 2026-10-13)'
    return 'full'


def monthly(rows: list[dict], miss: list[dict]) -> list[dict]:
    tr = closed(rows)
    by = defaultdict(list)
    for t in tr:
        by[ym(t['entry_time'])].append(t)
    exits = defaultdict(float)
    for t in tr:
        for ts, x in exit_parts(t):
            exits[ym(ts)] += x
    mm = defaultdict(lambda: defaultdict(int))
    for x in miss:
        mm[x['month']][x['result']] += 1
    table = []
    for mo in months():
        cov = coverage(mo)
        row = dict(month=mo, coverage=cov)
        if cov == 'unavailable' or cov.startswith('warm-up'):
            table.append(row)
            continue
        ts = by.get(mo, [])
        R = [t['R'] for t in ts]
        w = sum(t['tps_hit'] >= 1 for t in ts)
        row.update(trades=len(ts), wins=w, losses=len(ts) - w,
                   win_rate=round(w / len(ts) * 100, 1) if ts else None,
                   cash_positive=sum(1 for x in R if x > 0),
                   net_R_entry_cohort=round(sum(R), 3), avg_R=round(float(np.mean(R)), 3) if R else None,
                   net_R_by_exit_date=round(exits.get(mo, 0.0), 3),
                   tp1=sum(t['tps_hit'] >= 1 for t in ts), tp2=sum(t['tps_hit'] >= 2 for t in ts),
                   tp3=sum(t['tps_hit'] >= 3 for t in ts),
                   missed_setups=sum(mm[mo].values()), missed_wins=mm[mo]['missed_win'],
                   missed_avoided_losses=mm[mo]['avoided_loss'], missed_unresolved=mm[mo]['unresolved'])
        table.append(row)
    return table


def yearly(rows):
    out = []
    for y in range(2015, 2027):
        lo = max(T0, calendar.timegm((y, 1, 1, 0, 0, 0)))
        hi = min(CUTOFF, calendar.timegm((y + 1, 1, 1, 0, 0, 0)))
        s = stats(closed(rows, lo, hi))
        s['year'] = y
        s['coverage'] = 'from 2015-09-01' if y == 2015 else 'to 2026-10-13' if y == 2026 else 'full'
        out.append(s)
    return out


def walk_forward(pool: list[str], need_70: bool) -> dict:
    """Each test year uses the variant with the best average R on all earlier evaluated years (>= 100 trades);
    with ``need_70`` only variants whose earlier TP-win rate was >= 70% qualify."""
    data = {n: closed(load(n)) for n in pool}
    picks, chain = [], []
    for y in range(2019, 2027):
        cut = calendar.timegm((y, 1, 1, 0, 0, 0))
        best = None
        for n, tr in data.items():
            past = [t for t in tr if t['entry_time'] < cut]
            if len(past) < 100:
                continue
            s = stats(past)
            if need_70 and s['tp_win_rate'] < 70:
                continue
            if best is None or s['avg_R'] > best[1]:
                best = (n, s['avg_R'], s['tp_win_rate'])
        hi = min(CUTOFF, calendar.timegm((y + 1, 1, 1, 0, 0, 0)))
        test = [t for t in data[best[0]] if cut <= t['entry_time'] < hi]
        chain += test
        ts = stats(test)
        picks.append(dict(year=y, chosen=best[0], train_avg_R=best[1], train_tp_win=best[2], test_n=ts['n'],
                          test_tp_win=ts.get('tp_win_rate'), test_avg_R=ts.get('avg_R'), test_total_R=ts.get('total_R')))
    late = [t for t in chain if t['entry_time'] >= HOLD]
    return dict(years=picks, all_2019_2026=stats(sorted(chain, key=lambda t: t['exit_time'])),
                test_2023_2026=stats(sorted(late, key=lambda t: t['exit_time'])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    m1 = M1(a.cache)
    summary = dict(conventions=__doc__, finalists={}, costs={}, walk_forward={}, variants={})
    for name, label in FINALISTS.items():
        rows = load(name)
        miss = m1.missed(rows)
        unresolved = [r for r in rows if r.get('unresolved')]
        resting = [r for r in rows if r['fate'] == 'resting_at_end']
        summary['finalists'][name] = dict(
            label=label, overall=stats(closed(rows)), development=stats(closed(rows, T0, HOLD)),
            holdout=stats(closed(rows, HOLD, CUTOFF)), yearly=yearly(rows),
            missed=dict(setups=len(miss), missed_win=sum(x['result'] == 'missed_win' for x in miss),
                        avoided_loss=sum(x['result'] == 'avoided_loss' for x in miss),
                        unresolved=sum(x['result'] == 'unresolved' for x in miss)),
            open_at_cutoff=[dict(id=r['id'], entry_time=r['entry_time'], realized_cash_R=r['realized_cash'] / r['risk'],
                                 mtm_R_at_cutoff=r['mtm_R_at_cutoff'], tps_hit=r['tps_hit']) for r in unresolved],
            resting_at_cutoff=len(resting), risk_floor_violations=sum(1 for r in rows if r.get('risk_floor_violation')),
            orders=len(rows))
        tab = monthly(rows, miss)
        with open(os.path.join(OUT, f'monthly_{name}.csv'), 'w', newline='') as f:
            cols = ['month', 'coverage', 'trades', 'wins', 'losses', 'win_rate', 'cash_positive', 'net_R_entry_cohort',
                    'avg_R', 'net_R_by_exit_date', 'tp1', 'tp2', 'tp3', 'missed_setups', 'missed_wins',
                    'missed_avoided_losses', 'missed_unresolved']
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in tab:
                w.writerow({c: row.get(c, '') for c in cols})
        summary['finalists'][name]['monthly'] = tab
        with open(os.path.join(OUT, f'trades_{name}.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['id', 'side', 'placed_utc', 'entry_utc', 'exit_utc', 'entry', 'sl', 'risk', 'tps', 'fracs',
                        'fills', 'tps_hit', 'tp_win', 'R', 'strength', 'timeframes'])
            for t in closed(rows):
                f_ = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M')
                w.writerow([t['id'], t['side'], f_(t['placed']), f_(t['entry_time']), f_(t['exit_time']),
                            round(t['entry'], 3), t['sl'], round(t['risk'], 3), ' '.join(f'{x:.2f}' for x in t['tps']),
                            ' '.join(f'{x:.3f}' for x in t['fracs']),
                            '; '.join(f'{k} {f_(ts)} {px:.3f}' for k, ts, px in t['fills']), t['tps_hit'],
                            int(t['tps_hit'] >= 1), round(t['R'], 4), t['strength'], '/'.join(t['tfs'])])
        with open(os.path.join(OUT, f'missed_{name}.csv'), 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['id', 'side', 'time', 'month', 'entry_would_be', 'sl', 'tp1', 'result', 'r_tp1'])
            w.writeheader()
            w.writerows(miss)
    for name in ['baseline', 'R3_front0.50_be', 'R3_front0.50_q0.3f0.2', 'C9_front0.10_q0.3f0.3_be']:
        for suf in ('_cost2x', '_spreadmax'):
            p = os.path.join(RUNS, name + suf + '.json')
            if os.path.exists(p):
                rows = load(name + suf)
                summary['costs'][name + suf] = dict(overall=stats(closed(rows)), development=stats(closed(rows, T0, HOLD)),
                                                    holdout=stats(closed(rows, HOLD, CUTOFF)))
    pool = sorted(f[:-5] for f in os.listdir(RUNS) if f.endswith('.json') and not any(
        f.startswith(x) for x in ('audit', 'check')) and not any(x in f for x in ('_cost2x', '_spreadmax')))
    summary['walk_forward'] = dict(pool=pool, best_avg_R=walk_forward(pool, False), best_avg_R_with_70pct=walk_forward(pool, True))
    for n in pool:
        rows = load(n)
        summary['variants'][n] = dict(development=stats(closed(rows, T0, HOLD)), holdout=stats(closed(rows, HOLD, CUTOFF)))
    json.dump(summary, open(os.path.join(OUT, 'summary.json'), 'w'), indent=1, default=float)
    print('written', OUT)


if __name__ == '__main__':
    main()
