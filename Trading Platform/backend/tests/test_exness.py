"""Contract tests use a fake provider and generated keys, never a user credential."""
import asyncio
import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.market.exness import ExnessClient, ExnessError, RequestBudget, iso, parse_candles, signed_headers, signing_key, timestamp
from app.market.exness_sync import ExnessSync
from app.market.exness_live import ExnessLiveHub
from app.market.repository import MarketStore
from tests.test_live import FakeWS


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def candle(t, close=101):
    return dict(time=iso(t), open=100, high=102, low=99, close=close, volume=12)


class Provider:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.now = 660.0
        self.rows = [candle(t) for t in range(0, 661, 60)]
        self.requests = []
        self.fail = None

    def handler(self, req):
        self.requests.append(req)
        payload = decode(req.headers['EXN-DATA'])
        self.key.public_key().verify(decode(req.headers['EXN-SIGN']), payload)
        signed = json.loads(payload)
        assert signed['path'] == req.url.raw_path.decode()
        assert signed['method'] == 'GET' and req.method == 'GET'
        assert signed['idempotency_key'] == req.headers['EXN-IDEMPOTENCY-KEY'] == ''
        assert signed['body_hash'] == '47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU'
        if self.fail:
            return self.fail
        path = req.url.path
        if path.endswith('/limits'):
            rate = dict(limit=10000, window_seconds=1)
            return httpx.Response(200, json={'limits': {'rest': {'global_account_rate':rate, 'methods':[
                dict(api_operation_id='getCandleHistory', rate_limit=rate, limits={'max_candles_per_request':4})]}}})
        if path.endswith('/instruments'):
            return httpx.Response(200, json={'instruments':['XAUUSD']})
        if path.endswith('/conditions'):
            return httpx.Response(200, json={'instrument':'XAUUSD','point_digits':3})
        if path.endswith('/candles'):
            start=timestamp(req.url.params['from']); count=int(req.url.params['count'])
            rows=[r for r in self.rows if timestamp(r['time']) >= start] if count>0 else [r for r in self.rows if timestamp(r['time']) <= start]
            rows=rows[:count] if count>0 else rows[count:]
            return httpx.Response(200,json={'candles':rows})
        raise AssertionError('unexpected endpoint '+path)

    def client(self, **kwargs):
        key=self.key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        client=ExnessClient('https://provider.test','test-public-key',key.hex().encode(),'123',
                            transport=httpx.MockTransport(self.handler),clock=lambda:self.now,**kwargs)
        client.budget.rules={'*':(10000,1)}
        return client


def setup(tmp_path):
    provider=Provider(); client=provider.client(); store=MarketStore(tmp_path/'market.db')
    sync=ExnessSync(store,client); feed=sync.ensure_feed('XAUUSD')
    return provider,client,store,sync,feed


def test_signed_exact_encoded_query_and_supported_key_formats():
    p=Provider(); c=p.client(); c.configure_limits(); c.candles('XAUUSD','M1',start=0,count=4)
    assert '%3A' in p.requests[-1].url.raw_path.decode()
    raw=p.key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
    pem=p.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    for encoded in (raw.hex().encode(),base64.urlsafe_b64encode(raw),pem):
        headers=signed_headers('test',signing_key(encoded),'/v1/test?a=1',1000)
        p.key.public_key().verify(decode(headers['EXN-SIGN']),decode(headers['EXN-DATA']))


def test_history_pages_backward_forward_and_reconciles_volume(tmp_path):
    p,c,store,sync,feed=setup(tmp_path)
    p.now=665; sync.sync_forward(feed,'M1'); sync.backfill(feed,'M1',before=660,min_bars=11)
    series=store.get_series(feed.id,'M1'); bars=store.load_bars(series.id)
    assert bars.time.tolist()==list(range(0,661,60))
    assert bars.complete.tolist()==[True]*11+[False]
    p.now=725; p.rows[-1]['close']=102; p.rows[-1]['volume']=90; p.rows.append(candle(720))
    sync.sync_forward(feed,'M1')
    bars=store.load_bars(series.id)
    assert bars.complete.tolist()==[True]*12+[False]
    assert bars.volume[-2]==90 and bars.close[-2]==102
    assert all(r.method=='GET' for r in p.requests)


def test_ticks_remain_provisional_and_rollover_reconciles_prior_bar(tmp_path):
    p,c,store,sync,feed=setup(tmp_path); p.now=665; sync.sync_forward(feed,'M1')
    tick=lambda t,price:dict(instrument='XAUUSD',timestamp=iso(t),bid=price,ask=price+.2)
    rows=sync.apply_tick(feed,'M1',tick(670,103))
    assert rows[-1][2]==103 and rows[-1][5:]==(12.,0)
    assert sync.apply_tick(feed,'M1',tick(669,104))==[]
    p.now=721; p.rows.append(candle(720))
    sync.apply_tick(feed,'M1',tick(721,104))
    bars=store.load_bars(store.get_series(feed.id,'M1').id)
    assert sum(~bars.complete)==1 and bars.time[-1]==660
    p.now=725
    rows=sync.apply_tick(feed,'M1',tick(725,104))
    assert rows[-1][0]==720 and rows[-1][-1]==0
    bars=store.load_bars(store.get_series(feed.id,'M1').id)
    assert bars.complete[-2] and not bars.complete[-1]


def test_ask_side_and_account_isolation(tmp_path):
    p,c,store,sync,feed=setup(tmp_path)
    other=p.client(price='ask'); ask_sync=ExnessSync(store,other); ask=ask_sync.ensure_feed('XAUUSD')
    assert ask.id != feed.id and not ask_sync.owns(feed)
    with pytest.raises(ExnessError,match='another account'):
        ask_sync.sync_forward(feed,'M1')
    rows=ask_sync.apply_tick(ask,'M1',dict(instrument='XAUUSD',timestamp=iso(670),bid=100,ask=100.5))
    assert rows[0][4]==100.5 and rows[0][5]==0


@pytest.mark.parametrize('update,code',[
    ({'high':98},'INVALID_PAYLOAD'),({'volume':-1},'INVALID_PAYLOAD'),
    ({'time':iso(61)},'SESSION_MISMATCH'),({'close':float('nan')},'INVALID_PAYLOAD')])
def test_reject_bad_candles(update,code):
    with pytest.raises(ExnessError) as e:
        parse_candles({'candles':[{**candle(60),**update}]},'M1','utc',1000)
    assert e.value.code==code


def test_monthly_mapping_and_no_retries_on_provider_quota():
    p=Provider(); c=p.client(); c.configure_limits()
    p.fail=httpx.Response(429,json={'error_message':'REQUEST_RATE_LIMIT','secret':'never expose'})
    with pytest.raises(ExnessError) as e: c.candles('XAUUSD','M',start=0,count=1)
    assert p.requests[-1].url.params['timeframe']=='MN'
    count=len(p.requests)
    with pytest.raises(ExnessError): c.instruments()
    assert len(p.requests)==count and 'secret' not in str(e.value)


def test_budget_waits_for_per_method_quota():
    clock=[0.]; waits=[]
    def sleep(n): waits.append(n); clock[0]+=n
    b=RequestBudget(clock=lambda:clock[0],sleep=sleep)
    b.rules={'*':(10,1),'candles':(1,3)}
    b.acquire('candles'); b.acquire('candles')
    assert waits==[3.]


def test_signed_websocket_handshake_uses_get_and_verified_tls(monkeypatch):
    p=Provider(); c=p.client(); captured={}
    def connect(url,**kwargs):captured.update(url=url,**kwargs);return 'connection'
    monkeypatch.setattr('websockets.asyncio.client.connect',connect)
    assert c.ticks_connection()=='connection'
    headers=captured['additional_headers']; payload=decode(headers['EXN-DATA'])
    p.key.public_key().verify(decode(headers['EXN-SIGN']),payload)
    signed=json.loads(payload)
    assert captured['url']=='wss://provider.test/v1/server-events/accounts/123/ws/ticks'
    assert signed['method']=='GET' and signed['path']=='/v1/server-events/accounts/123/ws/ticks'
    assert captured['subprotocols']==['exness-ws-protocol'] and 'ssl' not in captured


def test_subscription_rejection_is_not_automatically_reconnected(tmp_path):
    p,c,store,sync,feed=setup(tmp_path)
    class Rejected:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,message):pass
        def __aiter__(self):return self
        async def __anext__(self):return json.dumps({'error_message':'AUTH_PERMISSION_DENIED'})
    c.ticks_connection=lambda:Rejected()
    async def scenario():
        hub=ExnessLiveHub(store,sync);ws=FakeWS()
        await hub.subscribe(ws,feed.id,'M1')
        await hub._run_stream(frozenset(['XAUUSD']))
        assert hub._stream_blocked and ws.of('status')[-1]['state']=='error'
        await hub._tick()
        assert hub._stream is None and ws.of('status')[-1]['state']=='error'
    asyncio.run(scenario())


def test_live_subscription_stream_and_disconnect(tmp_path, monkeypatch):
    p,c,store,sync,feed=setup(tmp_path)
    p.now=665; sync.sync_forward(feed,'M1')
    monkeypatch.setattr('app.market.exness_live.time.time',lambda:670.)
    sent=[]
    class Stream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,message):sent.append(json.loads(message))
        def __aiter__(self):return self
        async def __anext__(self):
            if sent[-1].get('consumed'):raise StopAsyncIteration
            sent[-1]['consumed']=True
            return json.dumps(dict(instrument='XAUUSD',timestamp=iso(time.time()),bid=103,ask=103.2))
    c.ticks_connection=lambda:Stream()
    async def scenario():
        hub=ExnessLiveHub(store,sync); ws=FakeWS()
        await hub.subscribe(ws,feed.id,'M1')
        await hub._run_stream(frozenset(['XAUUSD']))
        assert ws.of('bars') and any(s['state']=='live' for s in ws.of('status'))
        assert ws.of('status')[-1]['state']=='reconnecting'
        assert hub._retry_at>0
    asyncio.run(scenario())
    assert sent[0]['subscribe']=={'event':'ticks','instruments':['XAUUSD']}
