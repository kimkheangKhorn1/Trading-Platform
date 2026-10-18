"""Development table, round 2: entries 2021-01-01 .. 2023-12-31 (holdout 2024-01-01 .. cutoff is not read)."""
import json, math, sys, glob, os
import numpy as np
RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'runs')
DEV0, DEV1, MID = 1609459200, 1704067200, 1656633600  # 2021-01-01, 2024-01-01, 2022-07-01


def m(tr):
    R = np.array([t['R'] for t in tr]); W = np.array([t['tps_hit'] >= 1 for t in tr])
    if len(R) < 3:
        return dict(n=len(R), tpwin=0, cashpos=0, R=R.sum(), avg=0, t=0, pf=0, dd=0)
    eq = np.concatenate([[0], np.cumsum(R)])
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), tpwin=W.mean() * 100, cashpos=(R > 0).mean() * 100, R=R.sum(), avg=R.mean(),
                t=R.mean() / R.std(ddof=1) * math.sqrt(len(R)), pf=pos / neg if neg else 99, dd=float(np.max(np.maximum.accumulate(eq) - eq)))


def dev(name, hold=False):
    rows = json.load(open(os.path.join(RUNS, name + '.json')))['rows']
    lo, hi = (DEV1, 1791936000) if hold else (DEV0, DEV1)
    tr = sorted((r for r in rows if 'R' in r and lo <= r['entry_time'] < hi), key=lambda r: r['exit_time'])
    return m(tr), m([t for t in tr if t['entry_time'] < MID]), m([t for t in tr if t['entry_time'] >= MID])


if __name__ == '__main__':
    hold = '--holdout' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(RUNS, '*.json')))
    print(f"{'run':34s} {'n':>4s} {'TPwin%':>6s} {'cash+%':>6s} {'sumR':>7s} {'avgR':>6s} {'t':>5s} {'PF':>5s} {'DD':>5s} | {'1st half':>8s} {'2nd half':>8s}")
    for nm in names:
        a, h1, h2 = dev(nm, hold)
        print(f"{nm:34s} {a['n']:4d} {a['tpwin']:6.1f} {a['cashpos']:6.1f} {a['R']:7.1f} {a['avg']:+6.3f} {a['t']:5.2f} {a['pf']:5.2f} {a['dd']:5.1f} | {h1['avg']:+8.3f} {h2['avg']:+8.3f}")
