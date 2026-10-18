"""Monthly backtest report of the tuned Liquidity Reaper per timeframe, with the original rules and a random-entry
placebo for comparison. Reads out/tune_<tf>.json (chosen on TRAIN, checked on VALID) and out/baseline.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from app.engine.ta import atr as atr_v
from research.reaper.data import TEST, TFS, TRAIN, VALID, market
from research.reaper.events import GenParams, _htf_trend, generate
from research.reaper.sim import EXITS, Simulator
from research.reaper.study import in_window, month_of, one_at_a_time, summarize
from research.reaper.tune import OUT, keep_fn, trades_for

PERIODS = (('train', TRAIN), ('valid', VALID), ('test', TEST))


def period_of(ts):
    for name, (a, b) in PERIODS:
        if a <= ts < b:
            return name
    return ''


def chosen_rows(tf, cfg):
    m = market(tf)
    g = GenParams(pivot_len=cfg['pivot_len'], confirm=cfg['confirm'], use_vol=cfg['use_vol'])
    sigs = generate(m, g)
    sim = Simulator(m)
    trades = trades_for(sim, sigs, cfg['stop'], cfg['entry'], cfg['plan'])
    keep = keep_fn(cfg['trend_filter'], cfg['session_filter'], cfg['spread_guard'], cfg['stop'], cfg['entry'])
    return m, sim, one_at_a_time(sigs, trades, keep, m.first)


def monthly(m, rows):
    times = m.bid.time
    months = {}
    for s, t in rows:
        months.setdefault(month_of(times[s.i]), []).append((s, t))
    out, cum = [], 0.0
    first, last = month_of(TRAIN[0]), month_of(TEST[1] - 1)
    ms = sorted(set(months) | {first, last})
    # every calendar month in range, also those without trades
    y, mo = map(int, first.split('-'))
    ly, lmo = map(int, last.split('-'))
    while (y, mo) <= (ly, lmo):
        key = f'{y:04d}-{mo:02d}'
        r = summarize(months.get(key, []))
        cum += r['net_r']
        out.append(dict(month=key, period=period_of(int(np.datetime64(key + '-01').astype('datetime64[s]').astype(int))),
                        **{k: r[k] for k in ('trades', 'wins', 'losses', 'win_rate', 'tp1', 'tp2', 'tp3', 'sl', 'net_r')},
                        cum_r=cum))
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def placebo(m, sim, rows, cfg, window, draws=200, seed=7):
    """Random bars in ``window`` with the same number of trades, the same side mix (or the trend side when the trend
    filter is on), the same session filter, stop distance (in ATR) and exit plan; market entry. Returns avg R per draw."""
    times = m.bid.time
    test_rows = in_window(rows, times, window)
    if not test_rows:
        return None
    rng = np.random.default_rng(seed)
    at = atr_v(m.bid.high, m.bid.low, m.bid.close, 14)
    htf = _htf_trend(m)
    hours = (times // 3600) % 24
    idx = np.flatnonzero((times >= window[0]) & (times < window[1]) & np.isfinite(at))
    idx = idx[idx + 1 < len(times)]
    if cfg['session_filter']:
        idx = idx[hours[idx] < 17]
    if cfg['trend_filter']:
        idx = idx[htf[idx] != 0]
    k_atr = np.array([t.risk / s.atr for s, t in test_rows])
    buy_share = np.mean([s.side == 1 for s, _ in test_rows])
    plan = EXITS[cfg['plan']]
    n = len(test_rows)
    res = []
    for _ in range(draws):
        pick = np.sort(rng.choice(idx, size=min(len(idx), n * 3), replace=False))
        taken, free_from = [], -1
        for i in pick:
            if i < free_from:
                continue
            side = int(htf[i]) if cfg['trend_filter'] else (1 if rng.random() < buy_share else -1)
            k = float(rng.choice(k_atr))
            c = float(m.bid.close[i])
            stop = c - side * k * at[i]
            t = sim.run(int(i), side, stop, k * at[i], c, plan)
            if t is None:
                continue
            taken.append(t.r)
            free_from = t.exit_bar
            if len(taken) == n:
                break
        if taken:
            res.append(float(np.mean(taken)))
    return res


def main():
    base = json.loads((OUT / 'baseline.json').read_text())
    report = {}
    for tf in TFS:
        f = OUT / f'tune_{tf}.json'
        if not f.exists():
            continue
        tuned = json.loads(f.read_text())
        best = tuned['best']
        if best is None:
            report[tf] = dict(best=None)
            continue
        cfg = best['config']
        m, sim, rows = chosen_rows(tf, cfg)
        times = m.bid.time
        per = {name: summarize(in_window(rows, times, w)) for name, w in PERIODS}
        pl = placebo(m, sim, rows, cfg, TEST)
        top = sorted([r for r in tuned['all'] if r['train']['trades'] >= 1], key=lambda r: -r['score'])[:20]
        report[tf] = dict(
            config=cfg, passed_validation=tuned['passed_validation'], combos=tuned['combos'], periods=per,
            original=base.get(tf), months=monthly(m, rows),
            placebo_test=dict(mean=float(np.mean(pl)), p95=float(np.percentile(pl, 95)),
                              beat_share=float(np.mean(np.array(pl) < per['test']['avg_r']))) if pl else None,
            top20_test_avg_r=float(np.mean([r['test']['avg_r'] for r in top if r['test']['trades']])) if top else None,
            buys=sum(1 for s, _ in rows if s.side == 1), sells=sum(1 for s, _ in rows if s.side == -1))
        p = per
        print(f"{tf:4} valid_pass={tuned['passed_validation']} | train {p['train']['trades']} avgR {p['train']['avg_r']:+.3f} | "
              f"valid {p['valid']['trades']} avgR {p['valid']['avg_r']:+.3f} | TEST {p['test']['trades']} avgR "
              f"{p['test']['avg_r']:+.3f} net {p['test']['net_r']:+.1f}R | placebo test mean "
              f"{report[tf]['placebo_test']['mean'] if pl else float('nan'):+.3f}", flush=True)
    (OUT / 'report.json').write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
