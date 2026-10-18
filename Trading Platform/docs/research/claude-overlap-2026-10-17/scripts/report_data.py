"""Compact data for the HTML report (from ../results/summary.json and the finalists' run ledgers)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(HERE, '..', 'results', 'summary.json')))
T0, CUT = 1441065600, 1791936000
keep = ['n', 'wins', 'losses', 'tp_win_rate', 'cash_positive_rate', 'total_R', 'avg_R', 't_stat', 'profit_factor',
        'max_dd_R', 'tp1', 'tp2', 'tp3', 'avg_win_R', 'avg_loss_R', 'tp_wins_cash_negative']
r = lambda x: None if x is None else round(x, 3) if isinstance(x, float) else x
out = dict(finalists={}, variants={}, walk=s['walk_forward'], costs={})
for n, f in s['finalists'].items():
    rows = json.load(open(os.path.join(HERE, '..', 'runs', n + '.json')))['rows']
    tr = sorted((x for x in rows if 'R' in x and T0 <= x['entry_time'] < CUT), key=lambda x: x['exit_time'])
    eq, c = [], 0.0
    for x in tr:
        c += x['R']; eq.append([x['exit_time'], round(c, 2)])
    out['finalists'][n] = dict(label=f['label'], overall={k: r(f['overall'][k]) for k in keep},
                               development={k: r(f['development'][k]) for k in keep},
                               holdout={k: r(f['holdout'][k]) for k in keep},
                               yearly=[{k: r(y.get(k)) for k in keep + ['year', 'coverage']} for y in f['yearly']],
                               monthly=f['monthly'], missed=f['missed'], equity=eq, open=f['open_at_cutoff'])
for n, v in s['variants'].items():
    out['variants'][n] = {p: {k: r(v[p][k]) for k in ('n', 'tp_win_rate', 'cash_positive_rate', 'avg_R', 'total_R', 't_stat')} for p in ('development', 'holdout')}
for n, v in s['costs'].items():
    out['costs'][n] = {p: {k: r(v[p][k]) for k in ('n', 'tp_win_rate', 'avg_R', 't_stat')} for p in ('overall', 'development', 'holdout')}
json.dump(out, open(sys.argv[1], 'w'), separators=(',', ':'), default=float)
