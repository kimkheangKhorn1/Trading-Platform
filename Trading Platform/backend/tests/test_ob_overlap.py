"""Overlapped order blocks: the D/S overlap lifecycle applied to the chart's displayed order blocks."""
import hashlib
import json

import pytest

from app.badge_overlap import BadgeOverlapService, OrderBlockZone, displayed_order_blocks
from app.engine.runner import ScriptRunner
from app.locked_overlaps import DETECTOR_INPUTS, LockedOverlapRegistry
from app.market.providers import StoreHtfProvider
from app.scripts.smc_master import SmcMaster
from app.scripts.smc_master.indicator import Zone
from app.scripts.smc_master.ob_source import OrderBlockSource
from tests.test_badge_overlap import market  # noqa: F401  (fixture)

TFS = ['M1', 'M5']


@pytest.mark.parametrize('tf', ['M5', 'M15'])
@pytest.mark.parametrize('distance', [0., .2])
def test_order_block_source_matches_the_full_indicator(market, tf, distance):
    store, feed, _ = market
    series = store.get_series(feed.id, tf)
    bars = store.load_bars(series.id, limit=3000, complete_only=True)
    found = []
    for cls in (SmcMaster, OrderBlockSource):
        r = ScriptRunner(cls, SmcMaster({'aoi_max_dist': distance}).p, timeframe=tf, alignment=feed.alignment,
                         precision=feed.precision, htf_provider=StoreHtfProvider(store, feed))
        r.process(bars)
        found.append([(i, z.top, z.bottom, z.origin, z.born, z.side) for i, z in r.script.displayed_order_blocks()])
    assert found[0] == found[1] and found[0]


def test_bullish_blocks_use_the_demand_side_with_the_top_as_proximal_edge():
    class Script:
        def displayed_order_blocks(self):
            return [(False, Zone(105., 100., 1000, 1300, 1, 'swing_ob')), (True, Zone(95., 90., 1060, 1360, -1, 'internal_ob')),
                    (False, Zone(105., 100., 1000, 1300, 1, 'swing_ob'))]
    assert displayed_order_blocks(Script(), 'M5') == [
        OrderBlockZone('M5', 'demand', 100., 105., 105., 1000, 1300, 'swing'),
        OrderBlockZone('M5', 'supply', 90., 95., 90., 1060, 1360, 'internal')]


def _setup(market, monkeypatch):
    store, feed, m = market
    svc = BadgeOverlapService(store)
    clock = int(m.time[-100]) + 60
    floor = float(m.low[-100:-80].min())  # every block sits below the next 20 minutes of real lows
    blocks = {'M1': [OrderBlockZone('M1', 'demand', floor-9, floor-4, floor-4, clock-3600, clock-3000, 'internal')],
              'M5': [OrderBlockZone('M5', 'demand', floor-8, floor-3, floor-3, clock-7200, clock-6000, 'swing')]}
    monkeypatch.setattr(svc, 'source_order_blocks', lambda feed, tf, as_of, inputs: (blocks.get(tf, []), as_of - 60))
    base = dict(feed_id=feed.id, inputs={'aoi_max_dist': 0.}, timeframes=TFS, lock_id='chart', chart_timeframe='M1',
                kind='ob', fresh_only=False)
    return store, feed, svc, clock, floor, blocks, base


def test_overlapped_order_block_locks_and_is_deleted_by_its_own_first_touch(market, monkeypatch):
    store, feed, svc, clock, floor, blocks, base = _setup(market, monkeypatch)
    first = svc.run(**base, as_of=clock)
    [band] = first['overlaps']
    assert first['kind'] == 'ob' and (band['side'], band['bottom'], band['top'], band['strength']) == ('demand', floor-8, floor-4, 2)
    assert band['timeframes'] == ['M1', 'M5'] and band['lock_id']
    # The blocks disappear from the chart: the lock keeps its bounds and count.
    blocks.clear()
    assert svc.run(**base, as_of=clock+60)['overlaps'] == [band]
    # A 1m candle reaches the overlap's top: deleted, and it never comes back from the same blocks.
    minute = store.get_series(feed.id, 'M1')
    store.upsert_candles(minute.id, [(clock+60, floor, floor+1, floor-4.5, floor, 1, 1)])
    gone = svc.run(**base, as_of=clock+180)
    assert gone['overlaps'] == [] and gone['deleted_locks'] == 1


def test_order_block_and_demand_supply_locks_never_mix(market, monkeypatch):
    store, feed, svc, clock, *_, base = _setup(market, monkeypatch)
    monkeypatch.setattr(svc, 'source_drawings', lambda *args: ([], clock - 60))
    assert svc.run(**base, as_of=clock)['overlaps']
    ds = svc.run(**{**base, 'kind': 'ds'}, as_of=clock)
    assert ds['kind'] == 'ds' and ds['overlaps'] == [] and ds['deleted_locks'] == 0
    p = SmcMaster({}).p
    # Existing D/S locks keep their saved scope exactly.
    legacy = hashlib.sha256(json.dumps(['chart', feed.id, {k: p[k] for k in DETECTOR_INPUTS}, TFS, 2],
                                       sort_keys=True).encode()).hexdigest()
    assert svc.scope('ds', 'chart', feed, p, TFS, 2, ['swing'], True) == legacy == LockedOverlapRegistry.scope('chart', feed.id, p, TFS, 2)
    ob = svc.scope('ob', 'chart', feed, p, TFS, 2, ['swing', 'internal'], True)
    assert ob != legacy and ob != svc.scope('ob', 'chart', feed, p, TFS, 2, ['swing'], True)
    assert ob != svc.scope('ob', 'chart', feed, p, TFS, 2, ['swing', 'internal'], False)


def test_type_filter_and_untouched_filter(market, monkeypatch):
    store, feed, svc, clock, floor, blocks, base = _setup(market, monkeypatch)
    assert svc.run(**{**base, 'ob_types': ['swing']}, as_of=clock)['overlaps'] == []  # only the 5m block is swing
    assert len(svc.run(**{**base, 'ob_types': ['swing', 'internal']}, as_of=clock)['overlaps']) == 1
    # Untouched: no completed 1m candle after the block formed reached its proximal edge.
    _, _, m = market
    assert svc.untouched(feed, blocks['M1'][0], clock)
    high = float(m.high[-200:-100].max())
    touched = OrderBlockZone('M1', 'demand', high-1, high, high, int(m.time[-200]), int(m.time[-200]), 'swing')
    assert not svc.untouched(feed, touched, clock)
    assert svc.untouched(feed, touched, int(m.time[-200]) + 60)  # nothing observed yet at that clock


def test_invalid_kind_or_types_are_rejected(market, monkeypatch):
    *_, clock, _, _, base = _setup(market, monkeypatch)
    with pytest.raises(ValueError):
        BadgeOverlapService(market[0]).run(**{**base, 'kind': 'fvg'}, as_of=clock)
    with pytest.raises(ValueError):
        BadgeOverlapService(market[0]).run(**{**base, 'ob_types': ['breaker']}, as_of=clock)
