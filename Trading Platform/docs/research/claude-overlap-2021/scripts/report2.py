"""Round-2 results: 2021-01 .. 2026-10-13 (runs anchored 2020) plus the 2015-09..2020 check (runs_pre2021)."""
import json, math, os, csv, calendar
import numpy as np
from datetime import datetime, timezone
H = os.path.dirname(os.path.abspath(__file__)); B = os.path.join(H, '..')
CUT = 1791936000
P = dict(dev=(1609459200, 1704067200), hold=(1704067200, CUT), pre=(1441065600, 1609459200), all21=(1609459200, CUT))
FIN = {'base': 'Current settings', 'K1_against_early0.5': 'F1: fade 4H trend + entry 0.5 ATR early',
       'C4_be_at_1.5R': 'F2: stop to entry at +1.5R', 'K7_against_early0.5_q0.3f0.2': 'F3: F1 + quick TP1 0.3R (20%)'}
ym = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m')


def st(tr):
    if not tr:
        return dict(n=0)
    R = np.array([t['R'] for t in tr]); eq = np.r_[0, np.cumsum(R)]
    w = sum(t['tps_hit'] >= 1 for t in tr); pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), wins=w, losses=len(R) - w, tp_win=round(w / len(R) * 100, 1), cash_pos=round((R > 0).mean() * 100, 1),
                total_R=round(R.sum(), 2), avg_R=round(R.mean(), 3), t=round(R.mean() / R.std(ddof=1) * math.sqrt(len(R)), 2) if len(R) > 2 else None,
                pf=round(pos / neg, 2) if neg else None, max_dd=round(float(np.max(np.maximum.accumulate(eq) - eq)), 1),
                tp1=w, tp2=sum(t['tps_hit'] >= 2 for t in tr), tp3=sum(t['tps_hit'] >= 3 for t in tr))


def closed(rows, lo, hi):
    return sorted((r for r in rows if 'R' in r and lo <= r['entry_time'] < hi), key=lambda r: r['exit_time'])


out = {}
for n, lab in FIN.items():
    rows = json.load(open(os.path.join(B, 'runs', n + '.json')))['rows']
    pre = os.path.join(B, 'runs_pre2021', n + '.json')
    o = dict(label=lab, dev=st(closed(rows, *P['dev'])), hold=st(closed(rows, *P['hold'])), all21=st(closed(rows, *P['all21'])))
    if os.path.exists(pre):
        o['pre'] = st(closed(json.load(open(pre))['rows'], *P['pre']))
    o['years'] = {y: st(closed(rows, calendar.timegm((y, 1, 1, 0, 0, 0)), min(CUT, calendar.timegm((y + 1, 1, 1, 0, 0, 0))))) for y in range(2021, 2027)}
    tr = closed(rows, *P['all21']); by = {}
    for t in tr:
        by.setdefault(ym(t['entry_time']), []).append(t)
    mon = []
    for y in range(2021, 2027):
        for m in range(1, 13):
            k = f'{y}-{m:02d}'
            if k > '2026-10':
                mon.append(dict(month=k, coverage='unavailable (after data cutoff)')); continue
            s = st(by.get(k, [])); s['month'] = k; s['coverage'] = 'partial (to 2026-10-13)' if k == '2026-10' else 'full'
            mon.append(s)
    o['monthly'] = mon
    with open(os.path.join(B, 'results', f'monthly_{n}.csv'), 'w', newline='') as f:
        cols = ['month', 'coverage', 'n', 'wins', 'losses', 'tp_win', 'cash_pos', 'total_R', 'avg_R', 'tp1', 'tp2', 'tp3']
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore'); w.writeheader(); [w.writerow(r) for r in mon]
    out[n] = o
json.dump(out, open(os.path.join(B, 'results', 'summary.json'), 'w'), indent=1)
for n, o in out.items():
    print(n)
    for p in ('pre', 'dev', 'hold', 'all21'):
        if p in o: x = o[p]; print(f"  {p:5s} n={x['n']:3d} TPwin={x['tp_win']:5.1f}% cash+={x['cash_pos']:5.1f}% total={x['total_R']:+7.1f}R avg={x['avg_R']:+.3f} t={x['t']} PF={x['pf']} DD={x['max_dd']}")
    print('  years', {y: (v['n'], v.get('total_R')) for y, v in o['years'].items()})
