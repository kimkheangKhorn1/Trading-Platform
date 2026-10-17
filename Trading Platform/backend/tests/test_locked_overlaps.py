from copy import deepcopy
import pytest
from app.locked_overlaps import LockedOverlapRegistry, visible_band
from app.market.repository import MarketStore
from app.scripts.smc_master import SmcMaster
from tests.helpers import ts

T=ts('2026-10-05 00:00')

def band(side='demand',bottom=104.,top=108.,count=2,origin=T-3600):
    tfs=['M1','M5','M15'][:count]
    return dict(side=side,bottom=bottom,top=top,strength=count,timeframes=tfs,zone_count=count,
        badges=[dict(timeframe=tf,side=side,bottom=100.,top=110.,price=110.,origin=origin) for tf in tfs])

@pytest.fixture
def market(tmp_path):
    store=MarketStore(tmp_path/'locks.db')
    store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='Test')
    series=store.ensure_series('f','M1')
    # Wider source touched at 109 first; the locked 104-108 band is touched at 107 later.
    store.upsert_candles(series.id,[(T+i*60,112.,113.,109. if i==2 else 107. if i==5 else 111.,112.,1.,1) for i in range(12)])
    return store,series

def observe(r,clock,candidates=(),**kw):
    return r.observe(scope='scope',feed_id='f',as_of=T+clock,candidates=list(candidates),
        **dict(dict(low=110.,high=114.,distance=2.,close=112.),**kw))

def test_source_loss_or_new_strength_never_changes_locked_geometry(market):
    store,_=market;r=LockedOverlapRegistry(store)
    original=band();saved=deepcopy(original)
    first=observe(r,60,[original])['overlaps'][0]
    assert first['locked_at']==T+60
    assert observe(r,180)['overlaps'][0]==first
    assert observe(r,240,[band(bottom=105,top=107,count=3)])['overlaps']==[first]
    assert original==saved and first['strength']==2

def test_distance_hide_is_reversible_and_does_not_recreate_or_resize(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    hidden=observe(r,180,low=200,high=210,close=205,distance=.5)
    assert hidden['overlaps']==[] and hidden['hidden_locks']==1 and hidden['deleted_locks']==0
    assert observe(r,240)['overlaps']==[first]
    assert visible_band(first,200,210,0)

def test_touch_is_permanent_and_reduced_source_subset_cannot_resurrect(market):
    store,_=market;r=LockedOverlapRegistry(store)
    original=band(count=3);observe(r,60,[original])
    assert observe(r,300)['overlaps']
    deleted=observe(r,360,[original])
    assert deleted['overlaps']==[] and deleted['deleted_locks']==1
    assert observe(r,420,[band(count=2)])['overlaps']==[]
    assert observe(LockedOverlapRegistry(store),480,[original])['overlaps']==[]

def test_hidden_locks_still_consume_skipped_minute_touches(market):
    store,_=market;r=LockedOverlapRegistry(store)
    observe(r,60,[band()])
    assert observe(r,180,low=200,high=210,close=205)['hidden_locks']==1
    result=observe(r,660)
    assert result['overlaps']==[] and result['deleted_locks']==1

def test_rewind_uses_historical_birth_and_touch_without_future_events(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    assert not observe(r,660)['overlaps']
    rewind=observe(r,240)
    assert rewind['overlaps']==[first] and rewind['deleted_locks']==0
    assert 'touched_at' not in rewind['overlaps'][0]
    assert observe(r,30)['overlaps']==[]
    assert not observe(r,660,[band()])['overlaps']

def test_restart_preserves_locks_and_scope_ignores_distance_and_colors(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    assert observe(LockedOverlapRegistry(store),180)['overlaps']==[first]
    p=SmcMaster({}).p
    scope=LockedOverlapRegistry.scope('indicator','f',p,['M1','M5'],2)
    assert scope==LockedOverlapRegistry.scope('indicator','f',{**p,'aoi_max_dist':0,'supply_bord_col':'#123456FF'},['M5','M1'],2)
    assert scope!=LockedOverlapRegistry.scope('other','f',p,['M1','M5'],2)
    assert scope!=LockedOverlapRegistry.scope('indicator','f',{**p,'ds_min_atr':2.},['M1','M5'],2)

def test_supply_touch_and_gap_through_consume_locks(market):
    store,series=market;r=LockedOverlapRegistry(store)
    observe(r,60,[band(side='supply',bottom=118,top=122)])
    store.upsert_candles(series.id,[(T+120,112,119,111,112,1,1)])
    assert observe(r,180)['overlaps']==[]
    r.observe(scope='gap',feed_id='f',as_of=T+60,candidates=[band()],low=110,high=114,distance=2,close=112)
    store.upsert_candles(series.id,[(T+180,102,103,101,102,1,1)])
    assert r.observe(scope='gap',feed_id='f',as_of=T+240,candidates=[],low=100,high=114,distance=2,close=102)['deleted_locks']==1

def test_new_independent_origins_can_form_new_setup_after_consumption(market):
    store,_=market;r=LockedOverlapRegistry(store)
    old=observe(r,60,[band()])['overlaps'][0];observe(r,360)
    new=observe(r,600,[band(origin=T+480)])['overlaps'][0]
    assert new['lock_id']!=old['lock_id'] and new['locked_at']==T+600

def test_disjoint_locks_do_not_move_each_other(market):
    store,_=market;r=LockedOverlapRegistry(store)
    b=band(bottom=90,top=95,origin=T-7200)
    for z in b['badges']: z.update(bottom=88,top=96,price=96)
    first=observe(r,60,[band(),b],low=90,high=114,distance=2)
    assert len(first['overlaps'])==2
    assert observe(r,240,low=90,high=114,distance=2)['overlaps']==first['overlaps']

def test_far_candidate_locks_hidden_and_returns_with_original_bounds(market):
    store,_=market;r=LockedOverlapRegistry(store)
    far=observe(r,60,[band()],low=200,high=210,close=205,distance=.5)
    assert far['overlaps']==[] and far['hidden_locks']==1
    # Price approaches after a wider source was touched; the narrower later candidate must not replace it.
    near=observe(r,240,[band(bottom=105,top=107)])['overlaps']
    assert [(b['bottom'],b['top'],b['locked_at']) for b in near]==[(104,108,T+60)]
