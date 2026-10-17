"""Display-only badge confluence: exact source parity, freshness and causal snapshots."""
from datetime import datetime, timezone

import numpy as np
import pytest

from app.badge_overlap import Badge, BadgeOverlapService, overlap_bands, rendered_badges, strongest_bands
from app.engine.runner import ScriptRunner
from app.market.providers import StoreHtfProvider
from app.market.repository import MarketStore
from app.market.resample import resample
from app.market.timeframes import TIMEFRAMES
from app.scripts.smc_master import SmcMaster
from tests.helpers import random_walk_bars, ts

T = ts('2026-10-13 04:35')


def badge_label(*, side='demand', state='Fresh', origin=T-3600, bottom=100, top=105, group='demand_supply'):
    stamp=datetime.fromtimestamp(origin,timezone.utc).strftime('%Y/%m/%d %H:%M')
    price=top if side=='demand' else bottom
    return dict(kind='label',group=group,style='label_left',color='#17FF00FF',y=price,text=f'{price:.3f}',
                tooltip=f'{side.capitalize()} Zone · {state}\nRange: {bottom:.3f} – {top:.3f}\nOrigin UTC+0: {stamp}')


def test_only_actual_numeric_fresh_ds_badges_with_ranges_are_used():
    labels=[badge_label(),badge_label(side='supply'),badge_label(state='Tested'),badge_label(state='Invalidated'),
            badge_label(group='internal_ob'),badge_label(origin=T-32*86400),
            {**badge_label(),'kind':'box'}, {**badge_label(),'color':'#17FF0000'},
            {**badge_label(),'tooltip':'Range unknown'}, {**badge_label(),'text':'BUY'}]
    got=rendered_badges(labels,'M5',T)
    assert len(got)==3 and {z.side for z in got}=={'demand','supply'}
    assert all((z.bottom,z.top)==(100,105) for z in got)


def test_badge_origin_timezone_no_age_cutoff_and_no_future_origin():
    d=badge_label();d['tooltip']=d['tooltip'].replace('UTC+0: 2026/10/13 03:35','UTC+7: 2026/10/13 10:35')
    assert rendered_badges([d],'M5',T)[0].origin==T-3600
    assert rendered_badges([badge_label(origin=T-32*86400)],'H4',T)[0].origin==T-32*86400
    assert rendered_badges([badge_label(origin=T+60)],'H4',T)==[]


def z(tf,bottom,top,side='demand',origin=T-3600):
    return Badge(tf,side,bottom,top,top if side=='demand' else bottom,origin)


def test_common_range_and_distinct_timeframes_determine_strength():
    got=overlap_bands([z('M1',100,105),z('M5',103,107),z('M1',100,105)])
    assert len(got)==1 and (got[0]['bottom'],got[0]['top'],got[0]['strength'])==(103,105,2)
    assert overlap_bands([z('M1',100,105),z('M1',103,107)])==[]
    assert overlap_bands([z('M1',100,105),z('M5',103,107,'supply')])==[]
    assert overlap_bands([z('M1',100,105),z('M5',105,107)])==[]


def test_overlap_chain_is_never_claimed_as_three_way_common_range():
    got=overlap_bands([z('M1',0,2),z('M5',1,4),z('M15',3,5)])
    assert [(b['bottom'],b['top'],b['strength']) for b in got]==[(1,2,2),(3,4,2)]
    peak=strongest_bands(overlap_bands([z('M1',100,110),z('M5',102,108),z('M15',104,106)]))
    assert len(peak)==1 and (peak[0]['bottom'],peak[0]['top'],peak[0]['strength'])==(104,106,3)


@pytest.fixture
def market(tmp_path):
    store=MarketStore(tmp_path/'m.db')
    feed=store.ensure_feed('f',provider='csv',symbol='XAUUSD',instrument=None,price_type='bid',display_name='synthetic',alignment='utc')
    minutes=random_walk_bars(5000,start='2026-10-05 00:00',step=60,seed=72,skip_weekends=False)
    for code in ('M1','M5','M15','M30','H1','H4','D'):
        b=minutes if code=='M1' else resample(minutes,TIMEFRAMES['M1'],TIMEFRAMES[code],alignment='utc')
        series=store.ensure_series(feed.id,code)
        store.upsert_candles(series.id,zip(b.time.tolist(),b.open.tolist(),b.high.tolist(),b.low.tolist(),b.close.tolist(),b.volume.tolist(),b.complete.astype(int).tolist()))
    return store,feed,minutes


def test_partial_htf_candle_uses_observed_minutes_not_future_native_ohlc(market):
    store,feed,m=market;svc=BadgeOverlapService(store)
    as_of=int(m.time[1012])+60
    bars=svc.source_bars(feed,'M15',as_of,4000)
    start=int(bars.time[-1]);seen=(m.time>=start)&(m.time+60<=as_of)
    assert not bars.complete[-1] and bars.high[-1]==m.high[seen].max() and bars.low[-1]==m.low[seen].min()
    series=store.get_series(feed.id,'M15')
    store.conn.execute('UPDATE candles SET h=999999,l=0 WHERE series_id=? AND t=?',(series.id,start));store.conn.commit()
    later=svc.source_bars(feed,'M15',as_of,4000)
    assert np.array_equal(later.high,bars.high) and np.array_equal(later.low,bars.low)


def test_source_drawings_are_exact_original_indicator_drawings(market):
    store,feed,m=market;svc=BadgeOverlapService(store)
    clock=int(m.time[-1])+60
    p={'ds_line_atr':100.0,'aoi_max_dist':0.0,'ds_min_atr':.5}
    drawings,last=svc.source_drawings(feed,'M5',clock,p)
    bars=svc.source_bars(feed,'M5',clock,3000+SmcMaster(p).required_warmup())
    direct=ScriptRunner(SmcMaster,p,timeframe='M5',alignment='utc',precision=feed.precision,htf_provider=StoreHtfProvider(store,feed))
    direct.process(bars)
    baseline=direct.output()
    assert drawings==baseline['drawings'] and last==int(bars.time[-1])
    assert rendered_badges(drawings,'M5',clock)
    assert direct.output()==baseline and baseline['signals']==[]
    assert len(SmcMaster.meta.inputs)==210


def test_forward_and_backward_snapshot_matches_fresh_source(market):
    store,feed,m=market;svc=BadgeOverlapService(store)
    p={'ds_min_atr':.5,'ds_line_atr':100.,'aoi_max_dist':0.}
    for i in (2500,2513,2525,2100):
        clock=int(m.time[i])+60
        inc,_=svc.source_drawings(feed,'M15',clock,p)
        direct,_=BadgeOverlapService(store).source_drawings(feed,'M15',clock,p)
        assert rendered_badges(inc,'M15',clock)==rendered_badges(direct,'M15',clock)


def test_hidden_side_and_unbadged_zones_do_not_contribute(market):
    store,feed,m=market;svc=BadgeOverlapService(store)
    clock=int(m.time[-1])+60
    r=svc.run(feed_id=feed.id,inputs={'showDemandZone':False,'showSupplyZone':False},as_of=clock,timeframes=['M1','M5'])
    assert r['badges']==r['overlaps']==[] and not r['warnings']
    with pytest.raises(ValueError):svc.run(feed_id=feed.id,inputs={},as_of=clock,timeframes=['D'])
    with pytest.raises(ValueError):svc.run(feed_id=feed.id,inputs={},as_of=clock,timeframes=['M1'],min_timeframes=1)


def test_distance_setting_changes_badges_at_same_observation_time(market):
    store,feed,m=market;svc=BadgeOverlapService(store)
    clock=int(m.time[-1])+60
    base={'ds_min_atr':.5,'ds_line_atr':100.,'ds_line_max':5}
    unrestricted=svc.run(feed_id=feed.id,inputs={**base,'aoi_max_dist':0.},as_of=clock,timeframes=['M1','M5'])
    limited=svc.run(feed_id=feed.id,inputs={**base,'aoi_max_dist':.5},as_of=clock,timeframes=['M1','M5'])
    assert unrestricted['badges'] and len(limited['badges']) < len(unrestricted['badges'])
    assert all(z in unrestricted['badges'] for z in limited['badges'])
    for tf in ('M1','M5'):
        bars=svc.source_bars(feed,tf,clock,3000+SmcMaster(base).required_warmup())
        hi,lo=float(bars.high[-120:].max()),float(bars.low[-120:].min())
        pad=(hi-lo)*.5
        assert all(lo-pad-.001 <= z['price'] <= hi+pad+.001 for z in limited['badges'] if z['timeframe']==tf)
        # The overlay must receive exactly the badges the original renderer displays with this setting.
        drawings,_=svc.source_drawings(feed,tf,clock,{**base,'aoi_max_dist':.5})
        assert len(rendered_badges(drawings,tf,clock))==sum(z['timeframe']==tf for z in limited['badges'])
