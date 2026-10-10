"""Live candle hub: polling, broadcasting changed candles, backoff and recovery (fake OANDA)."""
import asyncio

import httpx

from app.market.live import LiveHub
from app.market.oanda import OandaClient
from app.market.repository import MarketStore
from app.market.sync import OandaSync
from tests.test_oanda import FakeOanda


class FakeWS:
    def __init__(self):
        self.msgs: list[dict] = []
        self.broken = False

    async def send_json(self, msg):
        if self.broken:
            raise RuntimeError("socket closed")
        self.msgs.append(msg)

    def of(self, kind):
        return [m for m in self.msgs if m["type"] == kind]


def _setup(tmp_path, n=300):
    fake = FakeOanda(n)
    client = OandaClient("https://example.test", "secret", transport=httpx.MockTransport(fake.handler),
                         max_requests_per_second=1000, max_retries=0, sleep=lambda s: None)
    store = MarketStore(tmp_path / "live.db")
    sync = OandaSync(store, client, price="M")
    feed = sync.ensure_feed("XAU_USD")
    return fake, store, sync, feed


def test_live_hub_streams_updates_and_recovers(tmp_path):
    fake, store, sync, feed = _setup(tmp_path)
    hub = LiveHub(store, sync, poll_seconds=0.0)
    key = (feed.id, "M1")

    async def scenario():
        ws = FakeWS()
        await hub.subscribe(ws, feed.id, "M1")
        assert ws.msgs[-1] == {"type": "status", "feed": feed.id, "tf": "M1", "state": "connecting",
                               "message": "Connecting to OANDA"}
        await hub._tick()  # first poll: history + the forming candle
        assert ws.of("status")[-1]["state"] == "live"
        first = ws.of("bars")[-1]["bars"]
        assert len(first) == 300 and first[-1][6] == 0  # last candle still forming

        # the forming candle closes and a new one starts
        t = fake.candles[-1]["t"]
        fake.candles[-1].update(c=2100.0, complete=True)
        fake.candles.append({"t": t + 60, "o": 2100, "h": 2101, "l": 2099, "c": 2100.5, "v": 3, "complete": False})
        hub._next_poll[key] = 0
        await hub._tick()
        update = ws.of("bars")[-1]["bars"]
        assert [r[0] for r in update] == [t, t + 60]
        assert update[0][4] == 2100.0 and update[0][6] == 1 and update[1][6] == 0

        # nothing changed -> nothing broadcast
        n_bars = len(ws.of("bars"))
        hub._next_poll[key] = 0
        await hub._tick()
        assert len(ws.of("bars")) == n_bars

        # provider failure -> reconnecting with backoff, then recovery
        fake.fail_next = [httpx.Response(503, json={})]
        hub._next_poll[key] = 0
        await hub._tick()
        st = ws.of("status")[-1]
        assert st["state"] == "reconnecting" and "retrying" in st["message"]
        assert hub._next_poll[key] > 0  # backed off
        hub._next_poll[key] = 0
        await hub._tick()
        assert ws.of("status")[-1]["state"] == "live"

        # a dead socket is dropped instead of breaking the hub
        ws.broken = True
        fake.candles[-1].update(c=2101.0)
        hub._next_poll[key] = 0
        await hub._tick()
        assert key not in hub.subs

    asyncio.run(scenario())
    # the store holds the provider's view, including the still-forming candle
    series = store.get_series(feed.id, "M1")
    last = store.load_bars(series.id, limit=2)
    assert last.complete.tolist() == [True, False] and last.close[-1] == 2101.0


def test_live_polling_fails_fast_without_client_retries(tmp_path):
    fake = FakeOanda(50)
    sleeps: list[float] = []
    client = OandaClient("https://example.test", "secret", transport=httpx.MockTransport(fake.handler),
                         max_requests_per_second=1000, max_retries=5, sleep=sleeps.append)
    store = MarketStore(tmp_path / "ff.db")
    sync = OandaSync(store, client, price="M")
    feed = sync.ensure_feed("XAU_USD")
    hub = LiveHub(store, sync, poll_seconds=0.0)

    async def scenario():
        ws = FakeWS()
        await hub.subscribe(ws, feed.id, "M1")
        fake.fail_next = [httpx.Response(503, json={})] * 3
        await hub._tick()
        assert ws.of("status")[-1]["state"] == "reconnecting"

    asyncio.run(scenario())
    assert sleeps == []  # no blocking client-side backoff during live polling
    assert len(fake.requests) == 1


def test_live_hub_reports_static_and_offline_feeds(tmp_path):
    fake, store, sync, feed = _setup(tmp_path, n=10)
    store.ensure_feed("csv-xauusd-test", provider="csv", symbol="XAUUSD", instrument=None, price_type="bid",
                      display_name="XAUUSD · CSV test")

    async def scenario():
        ws = FakeWS()
        await LiveHub(store, sync).subscribe(ws, "csv-xauusd-test", "M5")
        assert ws.msgs[-1]["state"] == "static"
        await LiveHub(store, None).subscribe(ws, feed.id, "M5")
        assert ws.msgs[-1]["state"] == "offline"
        await LiveHub(store, sync).subscribe(ws, "nope", "M5")
        assert ws.msgs[-1]["state"] == "error"

    asyncio.run(scenario())
