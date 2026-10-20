"""Indicator tuning: search a script's settings for the best trade result over a chosen period.

A script opts in with ``ScriptMeta.tuning`` (the settings that may be tuned and their default ranges) and by recording
its finished trades in ``script.closed`` (dicts with ``open_time``, ``hits`` = TPs reached, ``outcome`` 'tp3' / 'sl').

The user picks a period, a minimum trade count, a number of tunes and a range per setting. The space of all
combinations is the product of the per-setting value counts. Tune #1 is always the current settings; then:

* if the whole space fits in the budget, every combination is tried (in random order);
* otherwise the search explores random combinations first and then refines around the best ones (small steps of one
  or two settings at a time), with occasional random jumps so it does not get stuck near one early winner.

Every tune is one full script run over the period, executed in worker processes that load the candles once.
Results stream into the job state, which the UI polls: tune count, the best so far and each time a tune beats it.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

from .config import Settings, load_settings
from .engine.bars import concat_bars
from .engine.inputs import InputError
from .engine.registry import get_script
from .engine.runner import ScriptRunner
from .market.providers import StoreHtfProvider
from .market.repository import MarketStore
from .market.timeframes import get_timeframe
from .trade_score import OBJECTIVES, summarize

MAX_VALUES_PER_SETTING = 1000
MAX_TUNES = 10000

class TuningError(ValueError):
    pass



# ------------------------------------------------------------------------------------------------ the search space
@dataclass
class Dim:
    id: str
    values: list


def build_space(script_cls, ranges: list[dict]) -> list[Dim]:
    """Value lists of the settings to tune, validated against the script's inputs."""
    specs = {s.id: s for s in script_cls.meta.inputs}
    allowed = {p['id'] for p in (script_cls.meta.tuning or {}).get('params', [])}
    dims = []
    for r in ranges:
        sid = r.get('id')
        if sid not in specs or sid not in allowed:
            raise TuningError(f'{sid!r} cannot be tuned')
        spec = specs[sid]
        if 'options' in r and r['options'] is not None:
            values = [spec.validate(v) for v in r['options']]
        else:
            lo, hi, step = float(r['min']), float(r['max']), float(r['step'])
            if step <= 0 or hi < lo:
                raise TuningError(f'{spec.title}: need min ≤ max and a step above 0')
            count = int(math.floor((hi - lo) / step + 1e-9)) + 1
            if count > MAX_VALUES_PER_SETTING:
                raise TuningError(f'{spec.title}: {count} values, at most {MAX_VALUES_PER_SETTING} per setting')
            decimals = max(0, -int(math.floor(math.log10(step)))) + 2 if step < 1 else 0
            raw = [lo + k * step for k in range(count)]
            values = [int(round(v)) if spec.type == 'int' else round(v, decimals) for v in raw]
            try:
                values = [spec.validate(v) for v in values]
            except InputError as exc:
                raise TuningError(str(exc)) from exc
        values = list(dict.fromkeys(values))  # no duplicates (int rounding)
        if not values:
            raise TuningError(f'{spec.title}: no values')
        dims.append(Dim(sid, values))
    if not dims:
        raise TuningError('pick at least one setting to tune')
    return dims


class Search:
    """Proposes the next combinations to try, from the results so far."""

    def __init__(self, dims: list[Dim], budget: int, seed: int):
        self.dims = dims
        self.rng = random.Random(seed)
        self.total = math.prod(len(d.values) for d in dims)
        self.budget = budget
        self.seen: set[tuple] = set()
        self.exhaustive = self.total <= budget - 1  # tune #1 is the current settings
        if self.exhaustive:
            self.queue = [self._unflat(k) for k in range(self.total)]
            self.rng.shuffle(self.queue)
        self.explore_n = max(8, int(budget * 0.3))
        self.proposed = 0

    def _unflat(self, k: int) -> tuple:
        idx = []
        for d in self.dims:
            k, j = divmod(k, len(d.values))
            idx.append(j)
        return tuple(idx)

    def _random(self) -> tuple:
        return tuple(self.rng.randrange(len(d.values)) for d in self.dims)

    def _mutate(self, parent: tuple) -> tuple:
        idx = list(parent)
        for k in self.rng.sample(range(len(self.dims)), k=min(len(self.dims), self.rng.choice((1, 1, 2, 2, 3)))):
            n = len(self.dims[k].values)
            if n == 1:
                continue
            step = self.rng.choice((1, 1, 1, 2, 3)) * self.rng.choice((-1, 1))
            idx[k] = min(n - 1, max(0, idx[k] + step))
        return tuple(idx)

    def phase(self) -> str:
        if self.exhaustive:
            return 'every combination'
        return 'explore' if self.proposed < self.explore_n else 'refine'

    def propose(self, ranked: list[tuple]) -> tuple | None:
        """Next unseen combination (index tuple); ``ranked`` = index tuples of the best results so far, best first."""
        if self.exhaustive:
            while self.queue:
                c = self.queue.pop()
                if c not in self.seen:
                    self.seen.add(c)
                    self.proposed += 1
                    return c
            return None
        if len(self.seen) >= self.total:
            return None
        for _ in range(200):
            if self.proposed < self.explore_n or not ranked or self.rng.random() < 0.15:
                c = self._random()
            else:
                # parents: mostly the best, sometimes one of the top five
                parent = ranked[min(len(ranked) - 1, int(self.rng.random() ** 2.5 * min(5, len(ranked))))]
                c = self._mutate(parent)
            if c not in self.seen:
                self.seen.add(c)
                self.proposed += 1
                return c
        return None

    def values(self, idx: tuple) -> dict:
        return {d.id: d.values[j] for d, j in zip(self.dims, idx)}


# ---------------------------------------------------------------------------------------------- worker processes
_W: dict = {}


def _load(store: MarketStore, feed_id: str, timeframe: str, start: int, end: int, warmup: int):
    feed = store.get_feed(feed_id)
    if feed is None:
        raise TuningError(f'unknown data feed {feed_id!r}')
    tf = get_timeframe(timeframe)
    series = store.get_series(feed.id, tf.code)
    if series is None:
        raise TuningError(f'no {tf.label} candles stored for {feed.display_name}')
    pre = store.load_bars(series.id, before=start, limit=warmup, complete_only=True) if warmup else None
    window = store.load_bars(series.id, start=start, end=end, complete_only=True)
    if len(window) == 0:
        stats = store.series_stats(series.id)
        raise TuningError(f'no complete {tf.label} candles between the chosen times; stored data covers '
                          f'{_fmt(stats["first"])} – {_fmt(stats["last"])}')
    bars = concat_bars(pre, window) if pre is not None and len(pre) else window
    return feed, tf, bars, len(window), len(pre) if pre is not None else 0


def _watch_parent(parent: int) -> None:
    """A worker whose server died (crash, reload) exits instead of lingering as an orphan."""
    while True:
        time.sleep(3)
        if os.getppid() != parent:
            os._exit(0)


def _init(db_path: str, script_id: str, feed_id: str, timeframe: str, start: int, end: int, warmup: int) -> None:
    threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
    load_settings()
    store = MarketStore(Path(db_path))
    feed, tf, bars, _, _ = _load(store, feed_id, timeframe, start, end, warmup)
    cls = get_script(script_id)
    # no drawings, and trades only from the period start (a warm-up trade must not block the period's first signals)
    _W.update(store=store, feed=feed, tf=tf, bars=bars, start=start, end=end,
              cls=type(cls.__name__ + 'Tuning', (cls,), {'drawing': False, 'trade_from': start}),
              provider=_CachedProvider(StoreHtfProvider(store, feed, complete_only=True)))


class _CachedProvider:
    """Every tune asks for the same higher / 1-minute candles: load them once per worker."""

    def __init__(self, inner):
        self.inner, self.cache = inner, {}

    def load(self, timeframe, start, end, warmup):
        key = (timeframe, start, end, warmup)
        if key not in self.cache:
            self.cache[key] = self.inner.load(timeframe, start, end, warmup)
        return self.cache[key]

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _evaluate(params: dict) -> dict:
    w = _W
    runner = ScriptRunner(w['cls'], params, timeframe=w['tf'].code, alignment=w['feed'].alignment,
                          precision=w['feed'].precision, htf_provider=w['provider'], ticker=w['feed'].symbol)
    runner.process(w['bars'])
    p = runner.script.p
    rr = tuple(float(p.get(k, d)) for k, d in (('rr1', 1.0), ('rr2', 2.0), ('rr3', 3.0)))
    return summarize(runner.script.closed, w['start'], w['end'], rr)


# --------------------------------------------------------------------------------------------------------- jobs
@dataclass
class TuningRequest:
    feed_id: str
    timeframe: str
    script_id: str
    inputs: dict
    start: int
    end: int
    min_trades: int
    tunes: int
    ranges: list[dict]
    objective: str = 'rule'
    seed: int | None = None

    @staticmethod
    def from_dict(d: dict) -> 'TuningRequest':
        try:
            r = TuningRequest(feed_id=str(d['feed_id']), timeframe=str(d['timeframe']), script_id=str(d['script_id']),
                              inputs=dict(d.get('inputs') or {}), start=int(d['start']), end=int(d['end']),
                              min_trades=int(d.get('min_trades', 30)), tunes=int(d.get('tunes', 200)),
                              ranges=list(d['ranges']), objective=str(d.get('objective', 'rule')),
                              seed=int(d['seed']) if d.get('seed') is not None else None)
        except (KeyError, TypeError, ValueError) as exc:
            raise TuningError(f'invalid tuning request: {exc}') from exc
        if r.end <= r.start:
            raise TuningError('the end time must be after the start time')
        if not 1 <= r.tunes <= MAX_TUNES:
            raise TuningError(f'number of tunes must be between 1 and {MAX_TUNES}')
        if r.min_trades < 0:
            raise TuningError('minimum trades cannot be negative')
        if r.objective not in OBJECTIVES:
            raise TuningError(f'unknown score {r.objective!r}')
        return r


class TuningJob:
    def __init__(self, req: TuningRequest, dims: list[Dim], search: Search, base: dict, bars: int, warmup: int):
        self.id = uuid.uuid4().hex[:12]
        self.req, self.dims, self.search, self.base = req, dims, search, base
        self.lock = threading.Lock()
        self.status, self.error = 'running', None
        self.created, self.finished = time.time(), None
        self.bars, self.warmup = bars, warmup
        self.results: list[dict] = []   # every tune, in finishing order
        self.best: dict | None = None
        self.events: list[dict] = []    # each time the best changed
        self.stop = threading.Event()

    def key(self, r: dict) -> tuple:
        m, obj = r['metrics'], self.req.objective
        if m['trades'] < self.req.min_trades:
            return (0, m['trades'], m[obj])  # not qualified: more trades first, so the search moves toward qualifying
        return (1, m[obj], m['win_rate'], -m['losses'])

    def planned(self) -> int:
        """Tunes this run will make: the budget, or every combination (+ tune #1 when the current settings are not
        one of them) when they all fit."""
        if not self.search.exhaustive:
            return self.req.tunes
        in_grid = all(self.base[d.id] in d.values for d in self.dims)
        return self.search.total + (0 if in_grid else 1)

    def ranked_idx(self) -> list[tuple]:
        good = sorted((r for r in self.results if r['idx'] is not None), key=self.key, reverse=True)
        return [r['idx'] for r in good[:5]]

    def add(self, n: int, idx: tuple | None, params: dict, metrics: dict) -> None:
        with self.lock:
            r = dict(n=n, idx=idx, params=params, metrics=metrics,
                     qualified=metrics['trades'] >= self.req.min_trades)
            self.results.append(r)
            if self.best is None or self.key(r) > self.key(self.best):
                prev = self.best
                self.best = r
                if r['qualified']:
                    obj = self.req.objective
                    self.events.append(dict(n=n, prev=prev['n'] if prev and prev['qualified'] else None,
                                            prev_score=prev['metrics'][obj] if prev and prev['qualified'] else None,
                                            score=metrics[obj], at=len(self.results)))

    def view(self) -> dict:
        with self.lock:
            obj = self.req.objective
            ranked = sorted(self.results, key=self.key, reverse=True)
            strip = lambda r: {k: v for k, v in r.items() if k != 'idx'}  # noqa: E731
            return dict(
                id=self.id, status=self.status, error=self.error, created=self.created, finished=self.finished,
                request=self.req.__dict__, objective=obj, objective_text=OBJECTIVES[obj],
                objectives=OBJECTIVES, done=len(self.results), budget=self.planned(),
                total_space=str(self.search.total), total_space_float=float(self.search.total),
                exhaustive=self.search.exhaustive, phase=self.search.phase(), bars=self.bars, warmup=self.warmup,
                dims=[dict(id=d.id, count=len(d.values), first=d.values[0], last=d.values[-1]) for d in self.dims],
                base=self.base,
                best=strip(self.best) if self.best else None,
                baseline=next((strip(r) for r in self.results if r['n'] == 1), None),
                top=[strip(r) for r in ranked[:12]],
                recent=[strip(r) for r in self.results[-30:]][::-1],
                points=[[r['n'], r['metrics'][obj], int(r['qualified'])] for r in self.results],
                events=self.events[-40:][::-1],
            )


class TuningJobs:
    def __init__(self, store: MarketStore, settings: Settings):
        self.store, self.settings = store, settings
        self.jobs: dict[str, TuningJob] = {}

    def start(self, request: dict) -> str:
        req = TuningRequest.from_dict(request)
        if any(j.status == 'running' for j in self.jobs.values()):
            raise TuningError('a tuning run is already in progress - stop it first')
        try:
            cls = get_script(req.script_id)
        except KeyError as exc:
            raise TuningError(str(exc)) from exc
        if not cls.meta.tuning:
            raise TuningError(f'{cls.meta.name} does not support tuning')
        try:
            base = cls(req.inputs).p  # validated current settings, tune #1
        except InputError as exc:
            raise TuningError(str(exc)) from exc
        dims = build_space(cls, req.ranges)
        warmup = cls(base).required_warmup()
        _, _, _, n_bars, n_pre = _load(self.store, req.feed_id, req.timeframe, req.start, req.end, warmup)
        search = Search(dims, req.tunes, req.seed if req.seed is not None else int(time.time()))
        job = TuningJob(req, dims, search, base, n_bars, n_pre)
        self.jobs[job.id] = job
        while len(self.jobs) > 10:  # forget old finished runs
            old = next(j for j in self.jobs.values() if j.status != 'running')
            del self.jobs[old.id]
        threading.Thread(target=self._run, args=(job, warmup), daemon=True, name=f'tuning-{job.id}').start()
        return job.id

    def get(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        return job.view() if job else None

    def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        job.stop.set()
        return True

    def shutdown(self) -> None:
        """Server stopping: stop every run."""
        for job in self.jobs.values():
            job.stop.set()

    def _run(self, job: TuningJob, warmup: int) -> None:
        req = job.req
        workers = max(1, self.settings.backtest_workers)
        pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn'), initializer=_init,
                                   initargs=(str(self.store.db_path), req.script_id, req.feed_id, req.timeframe,
                                             req.start, req.end, warmup))
        try:
            n = 0
            running: dict = {}

            def submit(idx: tuple | None, params: dict) -> None:
                nonlocal n
                n += 1
                running[pool.submit(_evaluate, params)] = (n, idx, params)

            submit(None, dict(job.base))  # tune #1 = the current settings
            # ... which the search must not test a second time when they are one of the grid's combinations
            here = [d.values.index(job.base[d.id]) if job.base[d.id] in d.values else None for d in job.dims]
            if None not in here:
                job.search.seen.add(tuple(here))
            while running:
                while not job.stop.is_set() and len(running) < workers * 2 and n < req.tunes:
                    idx = job.search.propose(job.ranked_idx())
                    if idx is None:
                        break
                    submit(idx, {**job.base, **job.search.values(idx)})
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    tn, idx, params = running.pop(fut)
                    metrics = fut.result()
                    tuned = {d.id: params[d.id] for d in job.dims}
                    job.add(tn, idx, tuned, metrics)
            job.status = 'stopped' if job.stop.is_set() else 'done'
        except Exception as exc:  # reported to the UI
            job.status, job.error = 'error', str(exc)
        finally:
            job.finished = time.time()
            pool.shutdown(wait=False, cancel_futures=True)


def _fmt(ts: int | None) -> str:
    if ts is None:
        return '—'
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))
