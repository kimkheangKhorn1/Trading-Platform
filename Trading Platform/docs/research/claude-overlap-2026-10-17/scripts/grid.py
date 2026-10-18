"""Run a list of candidate settings through the research runner (one data load). Usage:
    backend/.venv/bin/python grid.py candidates.json   # {"name": {"setting": value, ...}, ...}
Every run is written to ../runs/<name>.json, whatever its result."""
import json, sys, time
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'backend' / 'tools'))
import claude_overlap_research as cr

OUT = Path(__file__).resolve().parents[1] / 'runs'
DATA = None


def one(item):
    name, over = item
    global DATA
    if DATA is None:
        DATA = cr.Data()
    cost = over.pop('_cost', 1.0)
    res = cr.run(DATA, cr.default_q(**over), cost_mult=cost)
    rows = cr.trade_rows(res)
    (OUT / f'{name}.json').write_text(json.dumps(dict(settings=over, cost=cost, spread='open', cutoff=cr.CUTOFF,
                                                      rows=rows), default=float))
    return name, sum(1 for r in rows if 'R' in r)


if __name__ == '__main__':
    spec = json.load(open(sys.argv[1]))
    todo = [(k, v) for k, v in spec.items() if not (OUT / f'{k}.json').exists() or '--force' in sys.argv]
    t0 = time.time()
    with Pool(4) as p:
        for name, n in p.imap_unordered(one, todo):
            print(f'{time.time() - t0:5.0f}s {name}: {n} trades', flush=True)
