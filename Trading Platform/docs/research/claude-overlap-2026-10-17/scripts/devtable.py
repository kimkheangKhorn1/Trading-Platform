"""Development-period table for candidate runs (entries 2015-09-01 .. 2022-12-31). Holdout rows are not read."""
import json, math, sys, glob, os
import numpy as np
RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'runs')
DEV0, DEV1, MID = 1441065600, 1672531200, 1561939200  # 2015-09-01, 2023-01-01, 2019-07-01


def m(tr):
    if not tr:
        return None
    R = np.array([t['R'] for t in tr]); W = np.array([t['tps_hit'] >= 1 for t in tr])
    eq = np.concatenate([[0], np.cumsum(R)]); dd = float(np.max(np.maximum.accumulate(eq) - eq))
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), tpwin=W.mean() * 100, cashpos=(R > 0).mean() * 100, R=R.sum(), avg=R.mean(),
                t=R.mean() / R.std(ddof=1) * math.sqrt(len(R)), pf=pos / neg if neg else float('inf'), dd=dd,
                tp3=int(sum(t['tps_hit'] >= 3 for t in tr)))


def dev(name):
    rows = json.load(open(os.path.join(RUNS, name + '.json')))['rows']
    tr = sorted((r for r in rows if 'R' in r and DEV0 <= r['entry_time'] < DEV1), key=lambda r: r['exit_time'])
    return m(tr), m([t for t in tr if t['entry_time'] < MID]), m([t for t in tr if t['entry_time'] >= MID])


if __name__ == '__main__':
    names = sys.argv[1:] or sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(RUNS, '*.json')))
    print(f"{'run':22s} {'n':>4s} {'TPwin%':>6s} {'cash+%':>6s} {'sumR':>7s} {'avgR':>6s} {'t':>5s} {'PF':>5s} {'DD':>5s} | {'H1 avgR':>7s} {'H2 avgR':>7s}")
    for nm in names:
        if nm.startswith('audit') or nm.startswith('check'):
            continue
        a, h1, h2 = dev(nm)
        print(f"{nm:22s} {a['n']:4d} {a['tpwin']:6.1f} {a['cashpos']:6.1f} {a['R']:7.1f} {a['avg']:+6.3f} {a['t']:5.2f} {a['pf']:5.2f} {a['dd']:5.1f} | {h1['avg']:+7.3f} {h2['avg']:+7.3f}")
