"""Causal overlap contact and reuse of the indicator's candle settings."""
from copy import deepcopy

import pytest

from app.engine.bars import Bars
from app.overlap_reactions import REACTION_WINDOW, overlap_reactions
from app.scripts.smc_master import SmcMaster
from tests.helpers import ts

T = ts('2026-10-05 00:00')
TOGGLES = ('d_', 'be_', 'hs_', 'dg_', 'tw_', 'st_', 'pc_', 'bh_', 'ls_')


def fixture(tf='M1', tail=((110,111.02,105,111),), side='demand', enabled='hs_'):
    step = 60 if tf=='M1' else 300
    base = [(112,113,111.5,112.5)]*30
    rows = [(T+i*step,*ohlc,1,True) for i,ohlc in enumerate([*base,*tail])]
    bars = Bars.from_rows(rows)
    p = SmcMaster({**dict.fromkeys(TOGGLES,False), enabled: True}).p
    z = dict(side=side,bottom=104,top=108,lock_id='lock',locked_at=T+29*step,
             touched_at=T+30*step+60)
    return bars,p,z,T+len(rows)*step


@pytest.mark.parametrize('tf',['M1','M5'])
def test_pin_bar_rejection_without_pivot_context(tf):
    b,p,z,clock=fixture(tf)
    got=overlap_reactions(b,[z],p,tf,clock)
    assert len(got)==1
    assert (got[0]['code'],got[0]['reaction'],got[0]['confirmed_at'])==('H','rejection',clock)


def test_shooting_star_rejection_from_supply():
    b,p,z,clock=fixture(tail=((102,107,100.98,101),),side='supply')
    got=overlap_reactions(b,[z],p,'M1',clock)
    assert [(r['code'],r['reaction'],r['bullish']) for r in got]==[('SS','rejection',False)]


@pytest.mark.parametrize('side,expected',[('demand','rejection'),('supply','continuation')])
def test_engulfing_classification_uses_zone_side(side,expected):
    b,p,z,clock=fixture(tail=((109,109.2,106,107),(106.9,111.2,106.8,111)),side=side,enabled='be_')
    got=overlap_reactions(b,[z],p,'M1',clock)
    assert len(got)==1 and got[0]['code']=='BE' and got[0]['reaction']==expected


def test_original_toggle_and_wick_atr_thresholds():
    b,p,z,clock=fixture()
    assert overlap_reactions(b,[z],{**p,'hs_':False},'M1',clock)==[]
    assert overlap_reactions(b,[z],{**p,'hsShadowPerc':0},'M1',clock)==[]
    assert overlap_reactions(b,[z],{**p,'atrMax':.1},'M1',clock)==[]


def test_forming_future_and_prebirth_candles_never_emit():
    b,p,z,clock=fixture('M5')
    assert overlap_reactions(b,[z],p,'M5',clock-1)==[]
    assert overlap_reactions(b,[{**z,'locked_at':clock-60}],p,'M5',clock)==[]
    b.complete[-1]=False
    assert overlap_reactions(b,[z],p,'M5',clock)==[]


def test_future_prices_do_not_change_earlier_confirmation_and_rewind_hides_it():
    b,p,z,clock=fixture()
    earlier=overlap_reactions(b,[z],p,'M1',clock)
    future=Bars.from_rows([*zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete),
                           (clock,111,999,0,888,1,True)])
    assert overlap_reactions(future,[z],p,'M1',clock)==earlier
    assert overlap_reactions(future,[z],p,'M1',clock-1)==[]


def test_inside_close_and_balanced_doji_are_unconfirmed():
    b,p,z,clock=fixture(tail=((106,107.02,103.9,107),))
    assert overlap_reactions(b,[z],p,'M1',clock)[0]['reaction']=='unconfirmed'
    b,p,z,clock=fixture(tail=((106,108,104,106),),enabled='d_')
    assert overlap_reactions(b,[z],p,'M1',clock)[0]['reaction']=='unconfirmed'


@pytest.mark.parametrize('tf',['M1','M5'])
def test_gap_and_contact_after_the_window_do_not_emit(tf):
    b,p,z,clock=fixture(tf,tail=((110,111.02,109,111),))
    assert overlap_reactions(b,[z],p,tf,clock)==[]
    # First contact, then a hammer one candle after the window (1m: candle 11, 5m: candle 4).
    W=REACTION_WINDOW[tf]
    b,p,z,clock=fixture(tf,tail=((110,111,107,110),)+((110,111,109,110),)*(W-1)+((110,111.02,105,111),))
    assert overlap_reactions(b,[z],p,tf,clock)==[]


def test_only_small_timeframes_and_inputs_stay_unchanged():
    b,p,z,clock=fixture()
    original=deepcopy(z)
    assert overlap_reactions(b,[z],p,'M15',clock)==[]
    overlap_reactions(b,[z],p,'M1',clock)
    assert z==original


def test_service_keeps_reaction_when_first_touch_removes_box(tmp_path,monkeypatch):
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    from tests.test_badge_overlap import badge_label
    b,p,z,clock=fixture()
    p={**p,'aoi_max_dist':0}
    store=MarketStore(tmp_path/'reactions.db')
    store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='Test')
    series=store.ensure_series('f','M1')
    store.upsert_candles(series.id,zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete.astype(int)))
    svc=BadgeOverlapService(store)
    monkeypatch.setattr(svc,'source_drawings',lambda *args: ([badge_label(bottom=104,top=108,origin=T-3600)],T))
    request=dict(feed_id='f',inputs=p,timeframes=['M1','M5'],lock_id='test',chart_timeframe='M1')
    before=svc.run(**request,as_of=clock-60)
    assert len(before['overlaps'])==1 and before['reactions']==[]
    touched=svc.run(**request,as_of=clock)
    assert touched['overlaps']==[] and touched['deleted_locks']==1
    assert touched['reactions'][0]['reaction']=='rejection'
    assert 'reaction_zones' not in touched
    assert svc.run(**request,as_of=clock,candle_reactions=False)['reactions']==[]
    rewind=svc.run(**request,as_of=clock-60)
    assert len(rewind['overlaps'])==1 and rewind['reactions']==[]


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_first_touch_window_does_not_restart_for_later_engulfing(tf,side):
    # Later visits cannot restart the first-touch window.
    away=[(112,113,111.5,112.5)]*max(5,REACTION_WINDOW[tf])
    bullish=[(109,109.2,106,107),(106.9,111.2,106.8,111)]
    bearish=[(106,108.2,105.8,108),(108.1,108.3,102.8,103)]
    b,p,z,clock=fixture(tf,tail=tuple(bullish+away+bearish+away+bullish),side=side,enabled='be_')
    step=60 if tf=='M1' else 300
    got=overlap_reactions(b,[z],p,tf,clock)
    assert [r['time'] for r in got]==[T+31*step]
    assert [r['bullish'] for r in got]==[True]
    assert [r['name'] for r in got]==['Bullish Engulfing']
    assert [r['reaction'] for r in got]==(['rejection'] if side=='demand' else ['continuation'])
    # Replay must not reveal the later revisits before their closes.
    assert overlap_reactions(b,[z],p,tf,T+38*step)==got[:1]
    assert overlap_reactions(b,[z],{**p,'be_':False},tf,clock)==[]


@pytest.mark.parametrize('tf',['M1','M5'])
def test_continuous_contact_stops_after_exactly_the_window(tf):
    W=REACTION_WINDOW[tf]
    assert W==(10 if tf=='M1' else 3)
    b,p,z,clock=fixture(tf,tail=((106,107.02,103.9,107),)*(W+5))
    step=60 if tf=='M1' else 300
    got=overlap_reactions(b,[z],p,tf,clock)
    assert [r['time'] for r in got]==[T+i*step for i in range(30,30+W)]
    assert all(r['code']=='H' and r['reaction']=='unconfirmed' for r in got)
    assert overlap_reactions(b,[z],p,tf,T+(30+W)*step-1)==got[:-1]


def test_service_does_not_restart_window_after_sources_and_box_disappear(tmp_path,monkeypatch):
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    from tests.test_badge_overlap import badge_label
    tail=((110,111.02,105,111),)+((112,113,111.5,112.5),)*REACTION_WINDOW['M1']+((110,111.02,105,111),)
    b,p,z,clock=fixture(tail=tail)
    p={**p,'aoi_max_dist':0}
    store=MarketStore(tmp_path/'repeat.db')
    store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='Test')
    series=store.ensure_series('f','M1')
    store.upsert_candles(series.id,zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete.astype(int)))
    svc=BadgeOverlapService(store)
    monkeypatch.setattr(svc,'source_drawings',lambda *args: ([badge_label(bottom=104,top=108,origin=T-3600)],T))
    request=dict(feed_id='f',inputs=p,timeframes=['M1','M5'],lock_id='repeat',chart_timeframe='M1')
    svc.run(**request,as_of=T+30*60)
    first=svc.run(**request,as_of=T+31*60)
    assert len(first['reactions'])==1 and first['overlaps']==[]
    monkeypatch.setattr(svc,'source_drawings',lambda *args: ([],T))
    repeated=svc.run(**request,as_of=clock)
    assert repeated['overlaps']==[] and repeated['badges']==[]
    assert [r['time'] for r in repeated['reactions']]==[T+30*60]
    # Same ledger after service restart, with source badges still absent.
    restarted=BadgeOverlapService(store)
    monkeypatch.setattr(restarted,'source_drawings',lambda *args: ([],T))
    assert restarted.run(**request,as_of=clock)['reactions']==repeated['reactions']


@pytest.mark.parametrize('tf',['M1','M5'])
def test_bearish_engulfing_on_third_candle_but_not_after_the_window(tf):
    # First contact, bullish body, then bearish engulfing confirms on candle three.
    tail=((106,107,104.5,106),(106,108.2,105.8,108),(108.1,108.3,102.8,103))
    b,p,z,clock=fixture(tf,tail=tail,enabled='be_')
    got=overlap_reactions(b,[z],p,tf,clock)
    assert len(got)==1 and got[0]['name']=='Bearish Engulfing'
    step=60 if tf=='M1' else 300
    assert got[0]['time']==T+32*step
    assert overlap_reactions(b,[z],p,tf,clock-1)==[]
    # Shift the same engulfing to the first candle after the window without changing first touch.
    b,p,z,clock=fixture(tf,tail=(tail[0],)*(REACTION_WINDOW[tf]-2)+tail,enabled='be_')
    assert overlap_reactions(b,[z],p,tf,clock)==[]


def test_mid_five_minute_touch_counts_its_containing_candle_as_first():
    b,p,z,clock=fixture('M5',tail=((106,107.02,103.9,107),)*6)
    z['touched_at']=T+30*300+240+60
    got=overlap_reactions(b,[z],p,'M5',clock)
    assert [r['time'] for r in got]==[T+i*300 for i in (30,31,32)]


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
@pytest.mark.parametrize('pattern',['pin','bull_eng','bear_eng'])
def test_http_window_boundary_rewind_and_restart(tf,side,pattern,tmp_path,monkeypatch):
    """Real endpoint + saved ledger + native/M1 bars + shared pattern formulas.

    Only source badge discovery is controlled to supply a known overlap.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    from tests.test_badge_overlap import badge_label
    W=REACTION_WINDOW[tf]
    tails={
        'pin': ((106,107.02,103.9,107),)*(W+5),
        'bull_eng': ((109,109.2,106,107),(106.9,111.2,106.8,111))+((112,113,111.5,112.5),)*6,
        'bear_eng': ((106,108.2,105.8,108),(108.1,108.3,102.8,103))+((101,102,100,101.5),)*6,
    }
    b,p,z,clock=fixture(tf,tail=tails[pattern],side=side,enabled='hs_' if pattern=='pin' else 'be_')
    if side=='supply':
        # Supply starts below the band. Engulfing direction is independent of the zone side.
        b=Bars.from_rows([(int(t),212-o,212-l,212-h,212-c,v,bool(complete))
                         if i<30 or pattern=='pin' else (int(t),o,h,l,c,v,bool(complete))
                         for i,(t,o,h,l,c,v,complete) in enumerate(zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete))])
    p={**p,'aoi_max_dist':0}
    store=MarketStore(tmp_path/'http-reactions.db')
    store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='Test')
    step=60 if tf=='M1' else 300
    native=store.ensure_series('f',tf)
    store.upsert_candles(native.id,zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete.astype(int)))
    if tf=='M5':
        minute=store.ensure_series('f','M1')
        rows=[]
        for i in range(len(b)):
            t,o,h,l,c=int(b.time[i]),float(b.open[i]),float(b.high[i]),float(b.low[i]),float(b.close[i])
            rows.append((t,o,h,l,c,1,1))
            rows.extend((t+j*60,c,c,c,c,1,1) for j in range(1,5))
        store.upsert_candles(minute.id,rows)
    def service():
        svc=BadgeOverlapService(store)
        monkeypatch.setattr(svc,'source_drawings',lambda *args: ([badge_label(side=side,bottom=104,top=108,origin=T-3600)],T))
        return svc
    monkeypatch.setattr(app.state,'badge_overlaps',service(),raising=False)
    client=TestClient(app)  # no lifespan: isolated temporary ledger, no live market services
    request=dict(feed_id='f',inputs=p,timeframes=['M1','M5'],lock_id='http',chart_timeframe=tf)
    def get(clock):
        response=client.post('/api/badge-overlaps',json={**request,'as_of':clock})
        assert response.status_code==200,response.text
        return response.json()
    assert get(T+30*step)['reactions']==[]
    for count in sorted({1,2,3,4,W,W+1,len(tails[pattern])}):
        result=get(T+(30+count)*step)
        expected=[T+i*step for i in range(30,30+min(count,W))] if pattern=='pin' else ([T+31*step] if count>=2 else [])
        assert [r['time'] for r in result['reactions']]==expected
        assert all(r['confirmed_at']<=result['as_of'] for r in result['reactions'])
        assert result['overlaps']==[]
        bullish=(side=='demand') if pattern=='pin' else pattern=='bull_eng'
        assert all(r['side']==side and r['bullish']==bullish for r in result['reactions'])
        if pattern!='pin' and result['reactions']:
            [r]=result['reactions']
            assert r['name']==('Bullish Engulfing' if bullish else 'Bearish Engulfing')
            assert r['reaction']==('rejection' if bullish==(side=='demand') else 'continuation')
    monkeypatch.setattr(app.state,'badge_overlaps',service())
    assert len(get(clock)['reactions'])==(W if pattern=='pin' else 1)  # restart cannot reset the window
    assert len(get(T+32*step)['reactions'])==(2 if pattern=='pin' else 1)  # rewind never reveals candle three
    assert len(get(clock)['reactions'])==(W if pattern=='pin' else 1)
    client.close()


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
@pytest.mark.parametrize('toggle,tail',[('d_',((106,108,104,106),)),('st_',((106,109,103,106.5),))])
def test_neutral_patterns_do_not_invent_bullish_direction(tf,side,toggle,tail):
    b,p,z,clock=fixture(tf,tail=tail,side=side,enabled=toggle)
    [r]=overlap_reactions(b,[z],p,tf,clock)
    assert r.get('neutral') is True
    assert r['reaction']=='unconfirmed'
    assert r['bullish']==(side=='demand')  # placement only: below demand, above supply


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
@pytest.mark.parametrize('bullish',[True,False])
@pytest.mark.parametrize('toggle,tail,bull_code,bear_code',[
    ('dg_',((106,106.01,103.9,106.01),),'DD','GD'),
    ('tw_',((109,109.2,106,107),(106.9,110.8,106,110.5)),'TB','TT'),
    ('pc_',((109,109.2,106,107),(107,108.6,106.9,108.5)),'P','DCC'),
    ('bh_',((109,109.2,104.8,105),(106,106.8,105.8,106.5)),'HW','HB'),
    ('ls_',((110,111.02,105,111),),'LLS','LUS'),
])
def test_other_pattern_families_detect_both_directions_at_both_sides(tf,side,bullish,toggle,tail,bull_code,bear_code):
    b,p,z,clock=fixture(tf,tail=tail,side=side,enabled=toggle)
    if not bullish:
        b=Bars.from_rows([(int(t),212-o,212-l,212-h,212-c,v,bool(complete))
                         for t,o,h,l,c,v,complete in zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete)])
    got=overlap_reactions(b,[z],p,tf,clock)
    if toggle=='ls_' and bullish:
        assert got==[]  # Explicitly excluded from overlap confirmations only.
        return
    assert got and got[-1]['code']==(bull_code if bullish else bear_code)
    assert got[-1]['bullish']==bullish and got[-1]['side']==side
    assert not got[-1]['neutral']
    assert overlap_reactions(b,[z],{**p,toggle:False},tf,clock)==[]


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_each_overlap_starts_its_own_pattern_window(tf,side):
    step=60 if tf=='M1' else 300
    W=REACTION_WINDOW[tf]  # the second touch lands just after the first lock's window
    tail=((110,111.02,105,111),)+((112,113,111.5,112.5),)*W+((110,111.02,100,111),)
    b,p,z,clock=fixture(tf,tail=tail)
    first={**z,'lock_id':'first'}
    second={**z,'lock_id':'second','bottom':99,'top':103,'touched_at':T+(31+W)*step+60}
    if side=='supply':
        b=Bars.from_rows([(int(t),212-o,212-l,212-h,212-c,v,bool(complete))
                         for t,o,h,l,c,v,complete in zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete)])
        for zone in (first,second): zone.update(side=side,bottom=212-zone['top'],top=212-zone['bottom'])
    got=overlap_reactions(b,[first,second],p,tf,clock)
    assert [(r['lock_id'],r['time'],r['window_index']) for r in got]==[
        ('first',T+30*step,1),('second',T+(31+W)*step,1)]
    assert all(r['name']==('Hammer' if side=='demand' else 'Shooting Star') for r in got)
    earlier=overlap_reactions(b,[first,second],p,tf,T+(30+W)*step)
    assert [r['lock_id'] for r in earlier]==['first']


@pytest.mark.parametrize('tf',['M1','M5'])
def test_window_candle_numbers_include_only_closed_candles(tf):
    W=REACTION_WINDOW[tf]
    b,p,z,clock=fixture(tf,tail=((106,107.02,103.9,107),)*(W+2))
    step=60 if tf=='M1' else 300
    if tf=='M5': z['touched_at']+=240  # touch occurs in the last minute of candle one
    got=overlap_reactions(b,[z],p,tf,clock)
    assert [r['window_index'] for r in got]==list(range(1,W+1))
    assert [r['window_index'] for r in overlap_reactions(b,[z],p,tf,T+(30+W)*step-1)]==list(range(1,W))


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_fast_close_endpoint_skips_discovery_and_matches_confirmed_patterns(tf,side,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.badge_overlap import BadgeOverlapService
    from app.market.repository import MarketStore
    W=REACTION_WINDOW[tf]
    b,p,z,clock=fixture(tf,tail=((110,111.02,105,111),)*(W+2),side=side)
    step=60 if tf=='M1' else 300
    if side=='supply':
        b=Bars.from_rows([(int(t),212-o,212-l,212-h,212-c,v,bool(n))
                         for t,o,h,l,c,v,n in zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete)])
    p={**p,'aoi_max_dist':0}
    store=MarketStore(tmp_path/'fast-close.db')
    store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='Test')
    native=store.ensure_series('f',tf)
    store.upsert_candles(native.id,zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete.astype(int)))
    if tf=='M5':
        minute=store.ensure_series('f','M1');mins=[]
        for t,o,h,l,c,v,n in zip(b.time,b.open,b.high,b.low,b.close,b.volume,b.complete):
            mins.append((int(t),float(o),float(h),float(l),float(c),1,1))
            mins.extend((int(t)+j*60,float(c),float(c),float(c),float(c),1,1) for j in range(1,5))
        store.upsert_candles(minute.id,mins)
    svc=BadgeOverlapService(store)
    scope=svc.locks.scope('fast','f',p,['M1','M5'],2)
    source=dict(side=side,bottom=104,top=108,strength=2,zone_count=2,timeframes=['M1','M5'],
                badges=[dict(timeframe=t,side=side,bottom=104,top=108,origin=T-3600,price=108 if side=='demand' else 104) for t in ['M1','M5']])
    birth=T+30*step
    svc.locks.observe(scope=scope,feed_id='f',as_of=birth,candidates=[source],low=90,high=120,distance=0,close=112 if side=='demand' else 100)
    def forbidden(*a,**kw):raise AssertionError('Fast candle confirmations must not scan source timeframes')
    monkeypatch.setattr(svc,'source_drawings',forbidden)
    monkeypatch.setattr(app.state,'badge_overlaps',svc,raising=False)
    request=dict(feed_id='f',inputs=p,timeframes=['M1','M5'],lock_id='fast',chart_timeframe=tf)
    client=TestClient(app)
    def get(at):
        r=client.post('/api/overlap-reactions',json={**request,'as_of':at})
        assert r.status_code==200,r.text
        return r.json()
    assert get(birth)['reactions']==[]
    for n in range(1,W+3):
        d=get(birth+n*step)
        assert len(d['reactions'])==min(n,W)
        assert all(r['confirmed_at']<=d['as_of'] for r in d['reactions'])
        assert all(r['name']==('Hammer' if side=='demand' else 'Shooting Star') for r in d['reactions'])
    assert len(get(birth+3*step-1)['reactions'])==2
    assert len(get(birth+2*step)['reactions'])==2
    assert len(get(clock)['reactions'])==W
    disabled=client.post('/api/overlap-reactions',json={**request,'as_of':clock,'candle_reactions':False})
    assert disabled.status_code==200 and disabled.json()['reactions']==[]
    assert store.conn.execute('SELECT COUNT(*) FROM overlap_locks_v1').fetchone()[0]==1
    # A slow full discovery request must not hold up a candle-close confirmation.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started,release=Event(),Event()
    def slow_source(*args):
        started.set()
        assert release.wait(5)
        return [],T
    monkeypatch.setattr(svc,'source_drawings',slow_source)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(svc.run,**request,as_of=clock,candle_reactions=False)
        try:
            assert started.wait(3)
            assert len(get(clock)['reactions'])==W
            assert not pending.done()  # confirmation returned while full scan is still blocked
        finally: release.set()
        pending.result(timeout=5)
    client.close()


@pytest.mark.parametrize('tf',['M1','M5'])
@pytest.mark.parametrize('side',['demand','supply'])
def test_lower_shadow_cannot_confirm_but_independent_hammer_can(tf,side):
    b,p,z,clock=fixture(tf,tail=((110,111.02,105,111),),side=side,enabled='ls_')
    assert overlap_reactions(b,[z],p,tf,clock)==[]
    got=overlap_reactions(b,[z],{**p,'hs_':True},tf,clock)
    assert len(got)==1 and got[0]['code']=='H'
    assert got[0]['patterns']==['Hammer']
    # The shared original geometry still recognizes LLS for its separate pivot-context display.
    from app.scripts.smc_master.patterns import pattern_formulas
    formulas=pattern_formulas(p,(0,110,111.02,105,111,1),(0,112,113,111.5,112.5,1),.5,.5,1)
    assert next(f for f in formulas if f[0]=='LLS')[3] is True
