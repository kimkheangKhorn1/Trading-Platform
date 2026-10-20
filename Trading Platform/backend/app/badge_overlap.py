"""Optional visual analysis of the *rendered* fresh Demand/Supply badges.

No detector is copied: source execution calls the original D/S methods with the same inputs. Only numeric
fresh Demand/Supply labels are eligible. Native candles beyond the observation clock are replaced
by an unfinished candle made from already-observed M1 bars. No orders or trade signals are emitted.
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import numpy as np

from .engine.bars import Bars, concat_bars
from .engine.runner import DataRevisionError, ScriptRunner
from .market.providers import StoreHtfProvider
from .market.repository import MarketStore
from .market.timeframes import TIMEFRAMES, bar_close_times, bucket_end, bucket_start
from .scripts.smc_master import SmcMaster
from .scripts.smc_master.badge_source import DemandSupplyBadgeSource
from .locked_overlaps import LockedOverlapRegistry
from .overlap_reactions import PatternTape, tape_reactions
from .overlap_signals import DEFAULT_BUFFER, DEFAULT_MAX_RISK, SIGNAL_TIMEFRAMES, overlap_signals
from .ob_signals import nearest_live, ob_signal_candidates
from .scripts.smc_master.ob_source import OrderBlockSource
from .locked_overlaps import visible_band

SOURCE_TFS = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4')
_RANGE = re.compile(r'\nRange: ([\d.]+) – ([\d.]+)\nOrigin UTC([+-][\d.]+): (\d{4}/\d{2}/\d{2} \d{2}:\d{2})$')


@dataclass(frozen=True)
class Badge:
    timeframe: str
    side: str
    bottom: float
    top: float
    price: float
    origin: int


def rendered_badges(drawings: list[dict], tf: str, as_of: int) -> list[Badge]:
    """Extract only numeric Fresh badge labels the original renderer actually produced."""
    out: dict[Badge, None] = {}
    for d in drawings:
        if d.get('group') != 'demand_supply' or d.get('kind') != 'label' or d.get('style') != 'label_left':
            continue
        tip = d.get('tooltip', '')
        side = 'demand' if tip.startswith('Demand Zone · Fresh\n') else 'supply' if tip.startswith('Supply Zone · Fresh\n') else None
        color = d.get('color', '')
        if side is None or (len(color) == 9 and color[-2:] == '00'):
            continue
        m = _RANGE.search(tip)
        if m is None:
            continue  # never guess a zone's range from the one price on its badge
        bottom, top, offset = map(float, m.group(1, 2, 3))
        origin = int(datetime.strptime(m[4], '%Y/%m/%d %H:%M').replace(tzinfo=timezone.utc).timestamp() - offset * 3600)
        price = float(d['y'])
        if not (math.isfinite(bottom) and math.isfinite(top) and bottom < top
                and abs(price - (top if side == 'demand' else bottom)) < 1e-7
                and origin <= as_of):
            continue
        try:
            if abs(float(d['text']) - price) > 1e-7:
                continue
        except (ValueError, TypeError):
            continue
        out[Badge(tf, side, bottom, top, price, origin)] = None
    return list(out)


def overlap_bands(badges: list[Badge], min_timeframes: int = 2) -> list[dict]:
    """Exact common bands, with distinct timeframe strength; never bridge a gap or mix sides."""
    out = []
    for side in ('demand', 'supply'):
        zs = list(dict.fromkeys(z for z in badges if z.side == side))
        edges = sorted({x for z in zs for x in (z.bottom, z.top)})
        last_cover = None
        for bottom, top in zip(edges, edges[1:]):
            cover = [z for z in zs if z.bottom <= bottom and z.top >= top]
            tfs = [tf for tf in SOURCE_TFS if any(z.timeframe == tf for z in cover)]
            if len(tfs) < min_timeframes:
                last_cover = None
                continue
            identity = frozenset(cover)
            if last_cover == identity and out and out[-1]['top'] == bottom:
                out[-1]['top'] = top
            else:
                out.append(dict(side=side, bottom=bottom, top=top, strength=len(tfs), timeframes=tfs,
                                zone_count=len(cover), badges=[asdict(z) for z in cover]))
            last_cover = identity
    return out


def strongest_bands(bands: list[dict]) -> list[dict]:
    """Keep the maximum distinct-TF coverage in each connected overlap area, not its weaker fringes."""
    out = []
    for side in ('demand', 'supply'):
        group = []
        end = -math.inf
        for b in sorted((x for x in bands if x['side'] == side), key=lambda x: x['bottom']):
            if group and b['bottom'] > end:
                strength = max(x['strength'] for x in group)
                out.extend(x for x in group if x['strength'] == strength)
                group = []
            group.append(b)
            end = max(end, b['top']) if len(group) > 1 else b['top']
        if group:
            strength = max(x['strength'] for x in group)
            out.extend(x for x in group if x['strength'] == strength)
    return out


class BadgeOverlapService:
    def __init__(self, store: MarketStore, max_sources: int = 24):
        self.store = store
        self.locks = LockedOverlapRegistry(store)
        self.max_sources = max_sources
        self._sources: OrderedDict[tuple, dict] = OrderedDict()
        self._tapes: OrderedDict[tuple, PatternTape] = OrderedDict()  # closed-candle patterns for reactions
        self._guard = threading.Lock()
        self._tape_lock = threading.Lock()

    def source_bars(self, feed, tf_code: str, as_of: int, count: int, since: int | None = None) -> Bars:
        """Native history plus one partial candle; no final HTF OHLC from after as_of.

        ``since``: only candles from this open time on (an incremental runner's last committed candle), instead of
        the latest ``count``: a replay step then reads a few rows rather than thousands."""
        series = self.store.get_series(feed.id, tf_code)
        if series is None:
            return Bars.empty()
        tf = TIMEFRAMES[tf_code]
        raw = (self.store.load_bars(series.id, start=since, before=as_of, complete_only=True) if since is not None
               else self.store.load_bars(series.id, before=as_of, limit=count, complete_only=True))
        if not len(raw):
            return raw
        keep = bar_close_times(raw.time, tf, feed.alignment) <= as_of
        closed = Bars(*(getattr(raw, k)[keep] for k in ('time', 'open', 'high', 'low', 'close', 'volume', 'complete')))
        start = bucket_start(as_of - 1, tf, feed.alignment)
        if bucket_end(start, tf, feed.alignment) <= as_of:
            return closed
        minute_series = self.store.get_series(feed.id, 'M1')
        if minute_series is None:
            raise ValueError(f'{tf.label}: M1 data is needed to reconstruct the current unfinished candle safely')
        mins = self.store.load_bars(minute_series.id, start=start, before=as_of, complete_only=True)
        k = mins.time + 60 <= as_of
        if not np.any(k):
            raise ValueError(f'{tf.label}: no observed M1 data in the current candle')
        part = Bars.from_rows([(start, mins.open[k][0], mins.high[k].max(), mins.low[k].min(),
                                mins.close[k][-1], mins.volume[k].sum(), False)])
        return concat_bars(closed, part)

    def _run_source(self, kind: str, script_cls, feed, tf: str, as_of: int, inputs: dict, read):
        # Same validated settings/history as the chart; one incremental runner per kind/feed/timeframe/settings.
        source = SmcMaster(dict(inputs))
        key = (kind, feed.id, tf, json.dumps(source.p, sort_keys=True))
        with self._guard:
            state = self._sources.get(key)
            if state is None:
                state = dict(runner=None, as_of=-1, result=None, lock=threading.Lock())
                self._sources[key] = state
            self._sources.move_to_end(key)
            while len(self._sources) > self.max_sources:
                self._sources.popitem(last=False)
        with state['lock']:
            if state['as_of'] == as_of and state['result'] is not None:
                return state['result'], state['last_bar']
            count = 3000 + source.required_warmup()
            def fresh():
                return ScriptRunner(script_cls, source.p, timeframe=tf, alignment=feed.alignment,
                                    precision=feed.precision, htf_provider=StoreHtfProvider(self.store, feed))
            r = state['runner']
            if r is None or as_of < state['as_of']:
                r = fresh()
            # Stepping forward: the runner only needs candles from its last committed one on (it checks that one
            # is unchanged and executes the rest), so read those instead of the whole history window.
            bars = self.source_bars(feed, tf, as_of, count, since=r.last_committed_time if r.n_committed else None)
            if not len(bars):
                return [], 0
            try:
                r.process(bars)
            except DataRevisionError:
                r = fresh()
                bars = self.source_bars(feed, tf, as_of, count)
                r.process(bars)
            result = read(r)
            state.update(runner=r, as_of=as_of, result=result, last_bar=int(bars.time[-1]))
            return result, int(bars.time[-1])

    def source_drawings(self, feed, tf: str, as_of: int, inputs: dict) -> tuple[list[dict], int]:
        # The original D/S methods run without unrelated independent SMC systems.
        # Invoke the shared original D/S renderer without serializing unrelated plots/history.
        return self._run_source('ds', DemandSupplyBadgeSource, feed, tf, as_of, inputs,
                                lambda r: r._last_bar_view()[0].to_json())

    def run(self, *, feed_id: str, inputs: dict, as_of: int, timeframes: list[str],
            min_timeframes: int = 2, max_ranges: int = 8, lock_id: str | None = None,
            chart_timeframe: str = 'M5', strongest_only: bool = False, candle_reactions: bool = True,
            reactions_only: bool = False, signals: bool = False, signal_buffer: float = DEFAULT_BUFFER,
            signal_max_risk: float = DEFAULT_MAX_RISK, replay_start: int | None = None) -> dict:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise ValueError('Unknown data feed')
        if not timeframes or any(tf not in SOURCE_TFS for tf in timeframes):
            raise ValueError('Choose source timeframes from 1m, 5m, 15m, 30m, 1H and 4H')
        if not 2 <= min_timeframes <= 6 or not 1 <= max_ranges <= 30:
            raise ValueError('Invalid overlap settings')
        # Display-only inputs (Easy Mode) cannot change sources, caches or lock scopes: drop them first
        inputs = {k: v for k, v in (inputs or {}).items() if k not in SmcMaster.meta.display_inputs}
        params = SmcMaster(inputs).p  # reject bad source settings before doing any work
        if lock_id is not None and (not isinstance(lock_id, str) or not 1 <= len(lock_id) <= 128):
            raise ValueError('Invalid overlap lock identity')
        if chart_timeframe not in TIMEFRAMES:
            raise ValueError('Invalid chart timeframe')
        if (not isinstance(signal_buffer, (int, float)) or not 0 <= signal_buffer <= 1000
                or not isinstance(signal_max_risk, (int, float)) or not 0 < signal_max_risk <= 1000):
            raise ValueError('Signal stop settings must be between 0 and 1000 price units')
        risk = (signal_buffer, signal_max_risk)
        if replay_start is not None and (not isinstance(replay_start, int) or isinstance(replay_start, bool)):
            raise ValueError('Invalid replay start')
        signals = bool(signals)
        # D/S sides follow the indicator's own D/S toggles.
        show = (params['showDemandZone'], params['showSupplyZone'])
        as_of = self.observed_clock(feed, as_of)
        if reactions_only:
            if lock_id is None or chart_timeframe not in ('M1','M5'):
                raise ValueError('Fast candle reactions require an indicator lock and a 1m/5m chart')
            # Independent close-confirmation lane: no source discovery, ranking or candidate creation.
            # This can finish while the ordinary multi-timeframe overlap scan is still running.
            chart = self.source_bars(feed,chart_timeframe,as_of,121)
            if not len(chart):
                raise ValueError('No observed chart candles for candle reactions')
            scope = self.locks.scope(lock_id,feed.id,params,timeframes,min_timeframes)
            observed = self.locks.observe(scope=scope,feed_id=feed.id,as_of=as_of,candidates=[],
                low=float(chart.low[-120:].min()),high=float(chart.high[-120:].max()),
                close=float(chart.close[-1]),distance=params['aoi_max_dist'],
                show_demand=show[0],show_supply=show[1])
            return dict(as_of=as_of,**self.reactions(feed,chart_timeframe,as_of,params,
                observed['reaction_zones'] if candle_reactions else [],
                observed['signal_zones'] if signals else [],risk,replay_start),warnings=[])
        badges, sources, warnings = [], [], []
        for tf in SOURCE_TFS:
            if tf not in timeframes:
                continue
            try:
                drawings, last = self.source_drawings(feed, tf, as_of, inputs)
                zs = rendered_badges(drawings, tf, as_of)
                badges.extend(zs)
                sources.append(dict(timeframe=tf, last_bar=last, badges=len(zs)))
                if last == 0:
                    warnings.append(f'{TIMEFRAMES[tf].label}: no source candles available')
            except ValueError as exc:
                warnings.append(str(exc))
        def ranked_bands(source_badges):
            found = strongest_bands(overlap_bands(source_badges, min_timeframes))
            found.sort(key=lambda b: (-b['strength'], -b['zone_count'], -(b['top'] - b['bottom']), b['bottom']))
            return found
        bands = ranked_bands(badges)
        result = dict(as_of=as_of, badges=[asdict(z) for z in badges], sources=sources,
                      overlaps=bands[:max_ranges], total_overlaps=len(bands), warnings=warnings)
        if lock_id is not None:
            chart = self.source_bars(feed, chart_timeframe, as_of, 121)
            if not len(chart):
                raise ValueError('No observed chart candles for overlap distance filtering')
            # Retire original sources BEFORE computing intersections/counts. Otherwise one
            # retired badge could either inflate a new group or block an all-new valid group.
            def candidates(retired):
                eligible = [z for z in badges if (z.timeframe,z.side,z.origin) not in retired]
                found = [] if warnings else ranked_bands(eligible)[:max_ranges]
                if strongest_only:
                    peaks = {side: max((b['strength'] for b in found if b['side'] == side), default=0)
                             for side in ('demand', 'supply')}
                    found = [b for b in found if b['strength'] == peaks[b['side']]]
                return found
            scope = self.locks.scope(lock_id, feed.id, params, timeframes, min_timeframes)
            result.update(self.locks.observe(scope=scope, feed_id=feed.id, as_of=as_of, candidates=candidates,
                low=float(chart.low[-120:].min()), high=float(chart.high[-120:].max()),
                close=float(chart.close[-1]), distance=params['aoi_max_dist'],
                show_demand=show[0], show_supply=show[1]))
        zones = result.pop('reaction_zones', [])
        signal_zones = result.pop('signal_zones', [])
        result.update(self.reactions(feed, chart_timeframe, as_of, params,
                                     zones if candle_reactions and chart_timeframe in ('M1', 'M5') else [],
                                     signal_zones if signals else [], risk, replay_start))
        return result

    def observed_clock(self, feed, as_of: int) -> int:
        """Archive snapshots can end inside a chart candle. Use only observed completed minutes,
        never its nominal future close or the workstation's wall clock as if prices were known there."""
        minute_series = self.store.get_series(feed.id, 'M1')
        if minute_series is not None:
            latest = self.store.load_bars(minute_series.id, before=as_of, limit=2, complete_only=True)
            observed = latest.time[latest.time + 60 <= as_of]
            if not len(observed):
                raise ValueError('No completed M1 data at this observation time')
            as_of = min(as_of, int(observed[-1]) + 60)
        return as_of

    def order_blocks(self, feed, tf: str, as_of: int, params: dict, anchor: int | None = None) -> list[dict]:
        """Every order block the indicator created on ``tf`` by ``as_of``: when it became known (close of the bar that
        created it) and when it was removed (close of the bar that closed through it), None while it is held.

        One incremental runner per feed / timeframe / settings / anchor. ``anchor`` (replay start bar): the runner's
        history starts a fixed number of candles before it, so a replay's blocks never depend on what was cached."""
        source = SmcMaster(dict(params))
        anchor = anchor if anchor is not None and anchor < as_of else None
        key = ('ob', feed.id, tf, json.dumps(source.p, sort_keys=True), anchor)
        with self._guard:
            state = self._sources.get(key)
            if state is None:
                state = dict(runner=None, as_of=-1, result=None, lock=threading.Lock())
                self._sources[key] = state
            self._sources.move_to_end(key)
            while len(self._sources) > self.max_sources:
                self._sources.popitem(last=False)
        with state['lock']:
            if state['as_of'] == as_of and state['result'] is not None:
                return state['result']
            count = 3000 + source.required_warmup()
            def fresh():
                return ScriptRunner(OrderBlockSource, source.p, timeframe=tf, alignment=feed.alignment,
                                    precision=feed.precision, htf_provider=StoreHtfProvider(self.store, feed))
            def bars_for(r):
                if r.n_committed:
                    return self.source_bars(feed, tf, as_of, count, since=r.last_committed_time)
                if anchor is not None:
                    series = self.store.get_series(feed.id, tf)
                    head = self.store.load_bars(series.id, before=anchor, limit=count, complete_only=True) if series else []
                    if len(head):
                        return self.source_bars(feed, tf, as_of, count, since=int(head.time[0]))
                return self.source_bars(feed, tf, as_of, count)
            r = state['runner']
            if r is None or as_of < state['as_of']:
                r = fresh()
            bars = bars_for(r)
            try:
                r.process(bars)
            except DataRevisionError:
                r = fresh()
                r.process(bars_for(r))
            frame = TIMEFRAMES[tf]
            script = r.script
            ends = {i: (t, why) for i, t, why in script.ob_ends}
            blocks = []
            for i, kind, side, top, bottom, origin, born in script.ob_births:
                end = ends.get(i)
                blocks.append(dict(id=i, timeframe=tf, kind='internal' if kind == 'internal_ob' else 'swing',
                                   side=side, top=top, bottom=bottom, origin=origin,
                                   known_at=int(bucket_end(born, frame, feed.alignment)),
                                   ended_at=None if end is None else int(bucket_end(end[0], frame, feed.alignment)),
                                   end_reason=None if end is None else end[1]))
            state.update(runner=r, as_of=as_of, result=blocks)
            return blocks

    def ob_signals(self, *, feed_id: str, inputs: dict, as_of: int, timeframes: list[str], confirmations: int = 1,
                   ob_types: list[str] = ('internal', 'swing'), chart_timeframe: str = 'M5',
                   signal_buffer: float = DEFAULT_BUFFER, signal_max_risk: float = DEFAULT_MAX_RISK,
                   replay_start: int | None = None, zones_per_side: int = 2) -> dict:
        """Order block trade signals on the 5m chart (``ob_signals.py``), with the D/S overlap trade rule.

        Each selected timeframe's order blocks are traded on their own (no overlaps). ``zones``: the waiting blocks
        nearest to price (``zones_per_side`` per timeframe and side), within the indicator's distance filter."""
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise ValueError('Unknown data feed')
        if not timeframes or any(tf not in SOURCE_TFS for tf in timeframes):
            raise ValueError('Choose order block timeframes from 1m, 5m, 15m, 30m, 1H and 4H')
        if not isinstance(confirmations, int) or isinstance(confirmations, bool) or not 1 <= confirmations <= 10:
            raise ValueError('Confirmation candles must be a whole number from 1 to 10')
        if not ob_types or any(k not in ('internal', 'swing') for k in ob_types):
            raise ValueError('Order block types are internal and / or swing')
        if chart_timeframe not in TIMEFRAMES:
            raise ValueError('Invalid chart timeframe')
        if (not isinstance(signal_buffer, (int, float)) or not 0 <= signal_buffer <= 1000
                or not isinstance(signal_max_risk, (int, float)) or not 0 < signal_max_risk <= 1000):
            raise ValueError('Signal stop settings must be between 0 and 1000 price units')
        if replay_start is not None and (not isinstance(replay_start, int) or isinstance(replay_start, bool)):
            raise ValueError('Invalid replay start')
        if not isinstance(zones_per_side, int) or not 0 <= zones_per_side <= 20:
            raise ValueError('Invalid zone count')
        inputs = {k: v for k, v in (inputs or {}).items() if k not in SmcMaster.meta.display_inputs}
        params = SmcMaster(inputs).p
        # Both block types are always built; the OB trade's own type choice filters them (not the chart's toggles)
        source_params = {**params, 'show_iob': True, 'show_ob': True}
        as_of = self.observed_clock(feed, as_of)
        if chart_timeframe not in SIGNAL_TIMEFRAMES:
            return dict(as_of=as_of, signals=[], zones=[], sources=[], warnings=[])
        blocks, sources, warnings = [], [], []
        for tf in SOURCE_TFS:
            if tf not in timeframes:
                continue
            try:
                found = [b for b in self.order_blocks(feed, tf, as_of, source_params, replay_start)
                         if b['kind'] in ob_types]
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            blocks += found
            sources.append(dict(timeframe=tf, blocks=len(found)))
        with self._tape_lock:
            # the D/S tape (same settings key): its closed 5m candles are shared
            tape = self.tape(feed, chart_timeframe, as_of, params, replay_start)
            rows = tape.rows[:tape.count(as_of)]
            floor = None
            if replay_start is not None and replay_start < as_of:
                k = tape.count(replay_start)
                floor = rows[max(0, k - 3000)][0] if k else replay_start
        if not rows:
            return dict(as_of=as_of, signals=[], zones=[], sources=sources,
                        warnings=warnings + ['No closed 5m candles for order block signals'])
        found, live = ob_signal_candidates(rows, blocks, confirmations, as_of, floor)
        risk = (signal_buffer, signal_max_risk)
        minutes = self.minutes_in(feed, chart_timeframe, as_of)
        if replay_start is None:
            signals = overlap_signals(rows, found, chart_timeframe, as_of, *risk, minutes=minutes)
        else:  # signals before the start bar keep their labels; the record runs on its own from the start bar
            signals = [t for t in overlap_signals(rows, found, chart_timeframe, as_of, *risk, minutes=minutes)
                       if t['time'] <= replay_start]
            signals += overlap_signals(rows, [r for r in found if r['time'] > replay_start], chart_timeframe, as_of,
                                       *risk, minutes=minutes)
        recent = rows[-120:]
        low, high = min(r[3] for r in recent), max(r[2] for r in recent)
        zones = [b for b in nearest_live(live, rows[-1][4], zones_per_side)
                 if visible_band(b, low, high, params['aoi_max_dist'])]
        return dict(as_of=as_of, signals=signals, zones=zones, sources=sources, warnings=warnings)

    def tape(self, feed, timeframe, as_of, params, since: int | None = None) -> PatternTape:
        """The closed-candle pattern tape of this feed / chart timeframe / settings, extended to as_of.

        Kept between requests: a replay or live step adds only the new candles. A rewind reads a prefix; it is
        rebuilt only when the clock goes back before the history it holds, or a stored candle was revised.
        ``since`` (replay start bar): the tape also holds every candle from it on, plus the usual history before it,
        so the replay's trade record never depends on where the cache happened to start (restart, settings change).
        Call with the tape's lock held (``self._tape_lock``)."""
        count = 3000 + SmcMaster(params).required_warmup()
        key = (feed.id, timeframe, json.dumps(params, sort_keys=True))
        series = self.store.get_series(feed.id, timeframe)
        if series is None:
            return PatternTape(params, timeframe)
        with self._guard:
            tape = self._tapes.get(key)
            if tape is not None:
                self._tapes.move_to_end(key)
        since = since if since is not None and since < as_of else None
        def build():
            t = PatternTape(params, timeframe)
            raw = self.store.load_bars(series.id, before=as_of if since is None else since, limit=count,
                                       complete_only=True)
            t.whole_history = len(raw) < count  # nothing older exists before its first candle
            if since is not None:
                raw = concat_bars(raw, self.store.load_bars(series.id, start=since, before=as_of, complete_only=True))
            t.extend(raw, as_of)
            return t
        def short(at):  # fewer than 3000 held candles closed by ``at``, while older ones exist
            return tape.count(at) < 3000 and not tape.whole_history
        if tape is None or short(as_of) or (since is not None and short(since)):
            tape = build()
        elif tape.times and as_of > tape.times[-1] + tape.step:
            new = self.store.load_bars(series.id, start=tape.times[-1], before=as_of, complete_only=True)
            last = tape.rows[-1]
            if not len(new) or int(new.time[0]) != last[0] or (float(new.open[0]), float(new.high[0]),
                    float(new.low[0]), float(new.close[0])) != last[1:5]:
                tape = build()  # the last held candle was revised (or removed)
            else:
                tape.extend(new, as_of)
                if len(tape.rows) > 3 * count:  # bound memory on long live sessions, never cutting into a replay
                    cut = len(tape.rows) - count if since is None else min(len(tape.rows) - count, tape.count(since) - 3000)
                    if cut > 0:
                        del tape.rows[:cut], tape.times[:cut], tape.formulas[:cut]
                        tape.whole_history = False
        with self._guard:
            self._tapes[key] = tape
            self._tapes.move_to_end(key)
            while len(self._tapes) > 8:
                self._tapes.popitem(last=False)
        return tape

    def minutes_in(self, feed, timeframe, as_of):
        """(high, low) of the closed 1m candles inside a chart candle, oldest first: which of SL / TP came first."""
        series = self.store.get_series(feed.id, 'M1')
        step = TIMEFRAMES[timeframe].seconds
        def minutes(t):
            if series is None:
                return []
            m = self.store.load_bars(series.id, start=t, before=min(t + step, as_of), complete_only=True)
            return [(float(h), float(l)) for mt, h, l in zip(m.time, m.high, m.low) if mt + 60 <= as_of]
        return minutes

    def reactions(self,feed,timeframe,as_of,params,zones,signal_zones=(),risk=(DEFAULT_BUFFER,DEFAULT_MAX_RISK),
                  replay_start=None):
        """Candle reactions at the shown locks and, on the 5m chart, the reversal trade signals of every touched lock.

        replay_start: the trade record restarts flat at the replay's start bar (one trade at a time from there), and is
        computed from the same candles whatever the cache held, so it never resets during the replay."""
        signal_zones = list(signal_zones) if timeframe in SIGNAL_TIMEFRAMES else []
        if not zones and not signal_zones:
            return dict(reactions=[],signals=[])
        shown = {z['lock_id'] for z in zones}
        with self._tape_lock:
            tape = self.tape(feed,timeframe,as_of,params,replay_start)
            # signal_zones holds every touched lock (a superset of the shown ones): one pass serves both
            found = tape_reactions(tape,signal_zones or zones,timeframe,as_of,limit=None)
            signals = []
            if signal_zones:
                rows, minutes = tape.rows[:tape.count(as_of)], self.minutes_in(feed,timeframe,as_of)
                if replay_start is None:
                    signals = overlap_signals(rows,found,timeframe,as_of,*risk,minutes=minutes)
                else:  # signals before the start bar keep their labels; the record runs on its own from the start bar
                    signals = [t for t in overlap_signals(rows,found,timeframe,as_of,*risk,minutes=minutes)
                               if t['time'] <= replay_start]
                    signals += overlap_signals(rows,[r for r in found if r['time'] > replay_start],timeframe,as_of,
                                               *risk,minutes=minutes)
        return dict(reactions=[r for r in found if r['lock_id'] in shown][-500:],signals=signals)
