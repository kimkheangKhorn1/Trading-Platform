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
        badges=[dict(timeframe=tf,side=side,bottom=bottom-4 if side=='demand' else bottom-2,top=top+2,
                     price=top+2 if side=='demand' else bottom-2,origin=origin) for tf in tfs])

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
    assert observe(r,120)['overlaps'][0]==first
    assert observe(r,120,[band(bottom=105,top=107,count=3)])['overlaps']==[first]
    assert original==saved and first['strength']==2

def test_distance_hide_is_reversible_and_does_not_recreate_or_resize(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    hidden=observe(r,120,low=200,high=210,close=205,distance=.5)
    assert hidden['overlaps']==[] and hidden['hidden_locks']==1 and hidden['deleted_locks']==0
    assert observe(r,120)['overlaps']==[first]
    assert visible_band(first,200,210,0)

def test_touch_is_permanent_and_reduced_source_subset_cannot_resurrect(market):
    store,_=market;r=LockedOverlapRegistry(store)
    original=band(count=3);observe(r,60,[original])
    assert observe(r,120)['overlaps']
    deleted=observe(r,360,[original])
    assert deleted['overlaps']==[] and deleted['deleted_locks']==1
    assert observe(r,420,[band(count=2)])['overlaps']==[]
    assert observe(LockedOverlapRegistry(store),480,[original])['overlaps']==[]

def test_hidden_locks_still_consume_skipped_minute_touches(market):
    store,_=market;r=LockedOverlapRegistry(store)
    observe(r,60,[band()])
    assert observe(r,120,low=200,high=210,close=205)['hidden_locks']==1
    result=observe(r,660)
    assert result['overlaps']==[] and result['deleted_locks']==1

def test_rewind_uses_historical_birth_and_touch_without_future_events(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    assert not observe(r,660)['overlaps']
    rewind=observe(r,120)
    assert rewind['overlaps']==[first] and rewind['deleted_locks']==0
    assert 'touched_at' not in rewind['overlaps'][0]
    assert observe(r,30)['overlaps']==[]
    assert not observe(r,660,[band()])['overlaps']

def test_restart_preserves_locks_and_scope_ignores_distance_and_colors(market):
    store,_=market;r=LockedOverlapRegistry(store)
    first=observe(r,60,[band()])['overlaps'][0]
    assert observe(LockedOverlapRegistry(store),120)['overlaps']==[first]
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
    assert observe(r,120,low=90,high=114,distance=2)['overlaps']==first['overlaps']

def test_far_candidate_locks_hidden_and_returns_with_original_bounds(market):
    store,_=market;r=LockedOverlapRegistry(store)
    far=observe(r,60,[band()],low=200,high=210,close=205,distance=.5)
    assert far['overlaps']==[] and far['hidden_locks']==1
    # Before contact, distance display and a smaller candidate cannot resize the lock.
    near=observe(r,120,[band(bottom=105,top=107)])['overlaps']
    assert [(b['bottom'],b['top'],b['locked_at']) for b in near]==[(104,108,T+60)]


@pytest.mark.parametrize('side',['demand','supply'])
def test_outer_source_touch_does_not_delete_shared_band(market,side):
    store,series=market;r=LockedOverlapRegistry(store)
    original=band(count=3)
    for z,(bottom,top) in zip(original['badges'],[(100,110),(102,109),(104,108)]):
        z.update(bottom=bottom,top=top,price=top)
    if side=='supply':
        original['side']=side
        for z in original['badges']:
            z.update(side=side,bottom=212-z['top'],top=212-z['bottom'],price=212-z['top'])
        rows=[(T+i*60,100,102.5 if i==2 else 101,99,100,1,1) for i in range(12)]
        rows[10]=(T+600,100,105,99,100,1,1)
    else:
        rows=[(T+i*60,112,113,109.5 if i==2 else 111,112,1,1) for i in range(12)]
        rows[10]=(T+600,112,113,107,112,1,1)
    store.upsert_candles(series.id,rows)
    close=100 if side=='supply' else 112
    first=observe(r,60,[original],close=close)['overlaps'][0]
    result=observe(r,180,close=close)
    assert result['overlaps']==[first] and result['deleted_locks']==0
    assert result['reaction_zones']==[]
    remaining=deepcopy(original);remaining['badges']=remaining['badges'][1:]
    remaining.update(strength=2,timeframes=['M5','M15'],zone_count=2)
    assert observe(r,600,[remaining],close=close)['overlaps']==[first]
    deleted=observe(r,660,[remaining],close=close)
    assert deleted['overlaps']==[] and deleted['deleted_locks']==1
    assert observe(LockedOverlapRegistry(store),720,[remaining],close=close)['overlaps']==[]
    assert observe(r,180,close=close)['overlaps']==[first]


def test_existing_ledger_preserves_band_until_its_own_touch(market):
    import json
    store,_=market
    store.conn.execute('''CREATE TABLE overlap_locks_v1 (
        scope TEXT NOT NULL,id TEXT NOT NULL,locked_at INTEGER NOT NULL,touched_at INTEGER,
        checked_until INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,id))''')
    original=band(count=3)
    store.conn.execute('INSERT INTO overlap_locks_v1 VALUES (?,?,?,?,?,?)',
                       ('scope','old-lock',T+60,None,T+300,json.dumps(original)))
    store.conn.commit()
    r=LockedOverlapRegistry(store)
    result=observe(r,300)
    assert len(result['overlaps'])==1 and result['deleted_locks']==0
    assert observe(r,360)['overlaps']==[]
    row=dict(store.conn.execute('SELECT * FROM overlap_locks_v1').fetchone())
    assert row['id']=='old-lock' and row['locked_at']==T+60 and row['deleted_at']==T+360
    assert observe(r,120)['overlaps'][0]['lock_id']=='old-lock'


@pytest.mark.parametrize('side',['demand','supply'])
def test_service_does_not_replace_three_source_group_with_two_source_group(market,side,monkeypatch):
    from app.badge_overlap import BadgeOverlapService
    from tests.test_badge_overlap import badge_label
    store,series=market
    if side=='demand':
        rows=[(T+i*60,112,113,109.5 if i==2 else 107 if i==5 else 111,112,1,1) for i in range(12)]
    else:
        rows=[(T+i*60,100,102.5 if i==2 else 105 if i==5 else 101,99,100,1,1) for i in range(12)]
    store.upsert_candles(series.id,rows)
    svc=BadgeOverlapService(store)
    def sources(feed,tf,clock,inputs):
        bounds={'M1':(100,110),'M5':(102,109),'M15':(104,108)}
        if clock>=T+180 and tf=='M1':
            return [],T  # only the outermost original source lost freshness
        bottom,top=bounds[tf]
        if side=='supply': bottom,top=212-top,212-bottom
        return [badge_label(side=side,bottom=bottom,top=top,origin=T-3600)],T
    monkeypatch.setattr(svc,'source_drawings',sources)
    request=dict(feed_id='f',inputs={'aoi_max_dist':0},timeframes=['M1','M5','M15'],lock_id='combined',chart_timeframe='M1')
    original=svc.run(**request,as_of=T+60)['overlaps'][0]
    assert original['strength']==3
    result=svc.run(**request,as_of=T+180)
    assert len(result['badges'])==2 and result['overlaps']==[original] and result['deleted_locks']==0
    assert svc.run(**request,as_of=T+600)['overlaps']==[]
    restarted=BadgeOverlapService(store)
    monkeypatch.setattr(restarted,'source_drawings',sources)
    assert restarted.run(**request,as_of=T+600)['overlaps']==[]
    assert restarted.run(**request,as_of=T+120)['overlaps']==[original]


@pytest.mark.parametrize('tf',['M1','M5','M15','M30','H1','H4'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_any_retired_source_cannot_mix_with_new_source_on_any_timeframe(market,tf,side):
    store,series=market
    if side=='supply':
        store.upsert_candles(series.id,[(T+i*60,98,103 if i==2 else 105 if i==5 else 99,97,98,1,1) for i in range(12)])
    original=band(side=side,count=3)
    # Use the same bounds for both sides, approached from the appropriate direction.
    for z in original['badges']:
        z.update(bottom=100,top=110,price=110 if side=='demand' else 100)
    original['badges'][1]['timeframe']=tf
    others=[t for t in ('M1','M5','M15','M30','H1','H4') if t!=tf]
    original['badges'][0]['timeframe']=others[0]
    original['badges'][2]['timeframe']=others[1]
    original['timeframes']=[z['timeframe'] for z in original['badges']]
    r=LockedOverlapRegistry(store)
    close=112 if side=='demand' else 98
    saved=observe(r,60,[original],close=close)['overlaps'][0]
    assert observe(r,360,close=close)['overlaps']==[]
    mixed=deepcopy(original)
    reused=deepcopy(original['badges'][1])
    # Geometry changes cannot disguise the original source identity.
    reused.update(bottom=101,top=109,price=109 if side=='demand' else 101)
    new=deepcopy(original['badges'][2]);new.update(origin=T+300)
    mixed.update(badges=[reused,new],strength=2,zone_count=2,timeframes=[reused['timeframe'],new['timeframe']])
    for registry in (r,LockedOverlapRegistry(store)):
        assert observe(registry,600,[mixed],close=close)['overlaps']==[]
    # An entirely new set is eligible; retiring a TF does not ban new zones on that TF.
    independent=deepcopy(mixed)
    for z in independent['badges']: z['origin']=T+420
    next_group=observe(r,660,[independent],close=close)['overlaps'][0]
    assert next_group['lock_id']!=saved['lock_id']
    assert observe(r,120,close=close)['overlaps']==[saved]
    assert observe(r,600,[mixed],close=close)['overlaps']==[]


@pytest.mark.parametrize('tf',['M1','M5','M15','M30','H1','H4'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_service_filters_retired_source_before_counting_new_groups(market,tf,side,monkeypatch):
    from app.badge_overlap import BadgeOverlapService, SOURCE_TFS
    from tests.test_badge_overlap import badge_label
    store,series=market
    if side=='supply':
        store.upsert_candles(series.id,[(T+i*60,100,102.5 if i==2 else 105 if i==5 else 101,99,100,1,1) for i in range(12)])
    else:
        store.upsert_candles(series.id,[(T+i*60,112,113,109.5 if i==2 else 107 if i==5 else 111,112,1,1) for i in range(12)])
    other=[t for t in SOURCE_TFS if t!=tf][:2]
    def sources(feed,code,clock,inputs):
        if clock<T+180:
            bounds={other[0]:(100,110),tf:(102,109),other[1]:(104,108)}
            bottom,top=bounds[code];origin=T-3600
        elif code==tf:
            bottom,top,origin=102,109,T-3600  # untouched, but retired with the old group
        else:
            if code==other[1] and clock<T+600: return [],T
            bottom,top,origin=104,108,T+240 if code==other[0] else T+480
        if side=='supply':bottom,top=212-top,212-bottom
        return [badge_label(side=side,bottom=bottom,top=top,origin=origin)],T
    svc=BadgeOverlapService(store)
    monkeypatch.setattr(svc,'source_drawings',sources)
    request=dict(feed_id='f',inputs={'aoi_max_dist':0},timeframes=[tf,*other],lock_id='no-reuse',chart_timeframe='M1')
    original=svc.run(**request,as_of=T+60)['overlaps'][0]
    assert original['strength']==3
    assert svc.run(**request,as_of=T+180)['overlaps']==[original]
    # A surviving retired source + one new zone is not a valid two-TF overlap.
    assert svc.run(**request,as_of=T+300)['overlaps']==[original]
    assert svc.run(**request,as_of=T+360)['overlaps']==[]
    # Two new zones can form a new overlap; the retired third source cannot inflate its count.
    [new]=svc.run(**request,as_of=T+600)['overlaps']
    assert new['strength']==2 and set(new['timeframes'])==set(other)
    assert all(z['origin']>=T+240 for z in new['badges'])
    restarted=BadgeOverlapService(store)
    monkeypatch.setattr(restarted,'source_drawings',sources)
    assert restarted.run(**request,as_of=T+600)['overlaps']==[new]
    assert restarted.run(**request,as_of=T+120)['overlaps']==[original]


def test_saved_mixed_group_from_old_policy_is_not_restored_after_restart(market):
    import json
    store,_=market;r=LockedOverlapRegistry(store)
    original=band(count=3)
    observe(r,60,[original]);observe(r,360)
    mixed=band(count=2)
    mixed['badges'][1]['origin']=T+240
    store.conn.execute('''INSERT INTO overlap_locks_v1
        (scope,id,locked_at,touched_at,checked_until,payload,deleted_at,source_checked_until)
        VALUES (?,?,?,NULL,?,?,NULL,?)''',('scope','legacy-mixed',T+420,T+420,json.dumps(mixed),T+420))
    store.conn.commit()
    assert observe(LockedOverlapRegistry(store),600)['overlaps']==[]


def test_touching_separate_supply_group_never_deletes_untouched_group(market):
    store,series=market
    store.upsert_candles(series.id,[(T+i*60,98,103 if i==2 else 99,97,98,1,1) for i in range(12)])
    first=band(side='supply',bottom=102,top=108,origin=T-3600)
    second=band(side='supply',bottom=118,top=122,origin=T-7200)
    r=LockedOverlapRegistry(store)
    before=observe(r,60,[first,second],low=98,high=130,close=98,distance=0)
    assert len(before['overlaps'])==2
    kept=next(b for b in before['overlaps'] if b['bottom']==118)
    after=observe(r,180,low=98,high=130,close=98,distance=0)
    assert after['overlaps']==[kept] and after['deleted_locks']==1
    assert observe(LockedOverlapRegistry(store),600,low=98,high=130,close=98,distance=0)['overlaps']==[kept]


@pytest.mark.parametrize('shared_tf',['M1','M5','M15','M30','H1','H4'])
@pytest.mark.parametrize('side',['supply','demand'])
def test_four_existing_overlaps_survive_shared_source_retirement(market,shared_tf,side):
    store,series=market
    other=[tf for tf in ('M1','M5','M15','M30','H1','H4') if tf!=shared_tf][:2]
    groups=[]
    for i in range(4):
        bottom,top=100+i*10,102+i*10
        sources=[dict(timeframe=shared_tf,side='supply',origin=T-7200,bottom=100,top=140,price=100)]
        sources.extend(dict(timeframe=tf,side='supply',origin=T-3600+i*60,bottom=bottom,top=top,price=bottom)
                       for tf in other[:1 if i==0 else 2])
        groups.append(dict(side='supply',bottom=bottom,top=top,strength=len(sources),zone_count=len(sources),
                           timeframes=[z['timeframe'] for z in sources],badges=sources))
    rows=[(T+i*60,98,101 if i==2 else 111 if i==5 else 121 if i==8 else 99,97,98,1,1) for i in range(12)]
    close=98
    if side=='demand':
        close=142
        for g in groups:
            g.update(side=side,bottom=240-g['top'],top=240-g['bottom'])
            for z in g['badges']:z.update(side=side,bottom=240-z['top'],top=240-z['bottom'],price=240-z['bottom'])
        rows=[(t,240-o,240-l,240-h,240-c,v,n) for t,o,h,l,c,v,n in rows]
    store.upsert_candles(series.id,rows)
    r=LockedOverlapRegistry(store)
    original=observe(r,60,groups,low=90,high=150,close=close,distance=0)['overlaps']
    assert len(original)==4
    first=next(g for g in original if g['strength']==2)
    survivors=[g for g in original if g['lock_id']!=first['lock_id']]
    result=observe(r,180,low=90,high=150,close=close,distance=0)
    assert result['overlaps']==survivors and result['deleted_locks']==1
    # Retiring the common source forbids a NEW combination, not these existing three.
    new=deepcopy(groups[-1])
    for z in new['badges']:
        if z['timeframe']!=shared_tf:z['origin']=T+120
    assert observe(r,240,[new],low=90,high=150,close=close,distance=0)['overlaps']==survivors
    restarted=LockedOverlapRegistry(store)
    assert observe(restarted,240,low=90,high=150,close=close,distance=0)['overlaps']==survivors
    result=observe(restarted,360,low=90,high=150,close=close,distance=0)
    assert len(result['overlaps'])==2 and result['deleted_locks']==2
    assert all(g in survivors for g in result['overlaps'])  # exact range/count/provenance retained
    assert observe(r,120,low=90,high=150,close=close,distance=0)['overlaps']==original
    assert observe(r,240,low=90,high=150,close=close,distance=0)['overlaps']==survivors


def test_old_source_edge_deletions_are_repaired_without_resetting_locks(market):
    store,_=market;r=LockedOverlapRegistry(store)
    saved=observe(r,60,[band()])['overlaps'][0]
    # Mimic the old policy: source touched at 180; shared band untouched until 360.
    store.conn.execute('UPDATE overlap_locks_v1 SET deleted_at=?,source_checked_until=? WHERE scope=?',
                       (T+180,T+300,'scope'))
    store.conn.commit()
    repaired=observe(LockedOverlapRegistry(store),300)
    assert repaired['overlaps']==[saved] and repaired['deleted_locks']==0
    assert observe(r,360)['overlaps']==[]
