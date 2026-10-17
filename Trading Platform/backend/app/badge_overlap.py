"""Optional visual analysis of the *rendered* fresh Demand/Supply badges.

No detector is copied: each source is the original SmcMaster with the same inputs. Only its numeric
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
    def __init__(self, store: MarketStore, max_sources: int = 12):
        self.store = store
        self.max_sources = max_sources
        self._sources: OrderedDict[tuple, dict] = OrderedDict()
        self._guard = threading.Lock()

    def source_bars(self, feed, tf_code: str, as_of: int, count: int) -> Bars:
        """Native history plus one partial candle; no final HTF OHLC from after as_of."""
        series = self.store.get_series(feed.id, tf_code)
        if series is None:
            return Bars.empty()
        tf = TIMEFRAMES[tf_code]
        raw = self.store.load_bars(series.id, before=as_of, limit=count, complete_only=True)
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

    def source_drawings(self, feed, tf: str, as_of: int, inputs: dict) -> tuple[list[dict], int]:
        # same validated settings; the original indicator is not mutated. Only its D/S badges are read.
        source = SmcMaster(dict(inputs))
        key = (feed.id, tf, json.dumps(source.p, sort_keys=True))
        with self._guard:
            state = self._sources.get(key)
            if state is None:
                state = dict(runner=None, as_of=-1, drawings=None, lock=threading.Lock())
                self._sources[key] = state
            self._sources.move_to_end(key)
            while len(self._sources) > self.max_sources:
                self._sources.popitem(last=False)
        with state['lock']:
            if state['as_of'] == as_of and state['drawings'] is not None:
                return state['drawings'], state['last_bar']
            bars = self.source_bars(feed, tf, as_of, 3000 + source.required_warmup())
            if not len(bars):
                return [], 0
            def fresh():
                return ScriptRunner(SmcMaster, source.p, timeframe=tf, alignment=feed.alignment,
                                    precision=feed.precision, htf_provider=StoreHtfProvider(self.store, feed))
            r = state['runner']
            if r is None or as_of < state['as_of']:
                r = fresh()
            try:
                r.process(bars)
            except DataRevisionError:
                r = fresh()
                r.process(bars)
            # This invokes the exact same last-bar renderer as the chart, without serializing plots/history.
            drawings, _ = r._last_bar_view()
            result = drawings.to_json()
            state.update(runner=r, as_of=as_of, drawings=result, last_bar=int(bars.time[-1]))
            return result, int(bars.time[-1])

    def run(self, *, feed_id: str, inputs: dict, as_of: int, timeframes: list[str],
            min_timeframes: int = 2, max_ranges: int = 8) -> dict:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise ValueError('Unknown data feed')
        if not timeframes or any(tf not in SOURCE_TFS for tf in timeframes):
            raise ValueError('Choose source timeframes from 1m, 5m, 15m, 30m, 1H and 4H')
        if not 2 <= min_timeframes <= 6 or not 1 <= max_ranges <= 30:
            raise ValueError('Invalid overlap settings')
        SmcMaster(inputs)  # reject bad source settings before doing any work
        # Archive snapshots can end inside a chart candle. Use only observed completed minutes,
        # never its nominal future close or the workstation's wall clock as if prices were known there.
        minute_series = self.store.get_series(feed.id, 'M1')
        if minute_series is not None:
            latest = self.store.load_bars(minute_series.id, before=as_of, limit=2, complete_only=True)
            observed = latest.time[latest.time + 60 <= as_of]
            if not len(observed):
                raise ValueError('No completed M1 data at this observation time')
            as_of = min(as_of, int(observed[-1]) + 60)
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
        bands = strongest_bands(overlap_bands(badges, min_timeframes))
        bands.sort(key=lambda b: (-b['strength'], -b['zone_count'], -(b['top'] - b['bottom']), b['bottom']))
        return dict(as_of=as_of, badges=[asdict(z) for z in badges], sources=sources,
                    overlaps=bands[:max_ranges], total_overlaps=len(bands), warnings=warnings)
