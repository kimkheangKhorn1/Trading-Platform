"""Round 2 (2021 onward). The engine is anchored at 2020-01-01 so zones exist when 2021 starts; only entries from
2021-01-01 are evaluated. Usage: backend/.venv/bin/python grid.py candidates.json [--force]
Spec: {"name": {"setting": value, ..., "_rules": {...}, "_cost": 1.0}}. Every run is written to ../runs/<name>.json."""
import json, sys, time
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'backend' / 'tools'))
import claude_overlap_research as cr

cr.START_YEAR = 2020
OUT = Path(__file__).resolve().parents[1] / 'runs'
DATA = None


def one(item):
    name, over = item
    global DATA
    if DATA is None:
        DATA = cr.Data()
    over = dict(over)
    cost = over.pop('_cost', 1.0)
    rules = over.pop('_rules', None)
    filters = over.pop('_filters', None)
    res = cr.run(DATA, cr.default_q(**over), cost_mult=cost, rules=rules, filters=filters)
    rows = cr.trade_rows(res)
    (OUT / f'{name}.json').write_text(json.dumps(dict(settings=over, rules=rules, filters=filters, cost=cost, spread='open',
                                                      cutoff=cr.CUTOFF, start_year=2020, rows=rows), default=float))
    return name, sum(1 for r in rows if 'R' in r and r['entry_time'] >= 1609459200)


if __name__ == '__main__':
    spec = json.load(open(sys.argv[1]))
    todo = [(k, v) for k, v in spec.items() if '--force' in sys.argv or not (OUT / f'{k}.json').exists()]
    t0 = time.time()
    with Pool(4) as p:
        for name, n in p.imap_unordered(one, todo):
            print(f'{time.time() - t0:5.0f}s {name}: {n} trades since 2021', flush=True)
