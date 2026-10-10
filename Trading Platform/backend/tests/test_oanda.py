"""OANDA client + sync tests against an in-process fake of the v20 candles endpoint."""
import datetime as dt

import httpx
import pytest

from app.market.oanda import OandaAuthError, OandaClient, OandaError, RateLimiter
from app.market.repository import MarketStore
from app.market.sync import OandaSync
from tests.helpers import ts


def _parse_time(s: str) -> int:
    if s.replace(".", "", 1).isdigit():
        return int(float(s))
    return int(dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())


class FakeOanda:
    def __init__(self, n: int, start: int = ts("2026-01-05 00:00"), step: int = 60):
        self.candles = [
            {"t": start + i * step, "o": 2000 + i * 0.01, "h": 2001 + i * 0.01, "l": 1999 + i * 0.01,
             "c": 2000.5 + i * 0.01, "v": 10, "complete": True}
            for i in range(n)
        ]
        self.candles[-1]["complete"] = False
        self.requests: list[dict] = []
        self.fail_next: list[httpx.Response | Exception] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if self.fail_next:
            item = self.fail_next.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        assert request.headers["Authorization"] == "Bearer secret"
        assert params["dailyAlignment"] == "17" and params["alignmentTimezone"] == "America/New_York"
        count = int(params.get("count", 500))
        assert count <= 5000
        data = self.candles
        if "from" in params:
            f = _parse_time(params["from"])
            inc = params.get("includeFirst", "true") == "true"
            sel = [c for c in data if (c["t"] >= f if inc else c["t"] > f)][:count]
        elif "to" in params:
            t = _parse_time(params["to"])
            sel = [c for c in data if c["t"] <= t][-count:]  # inclusive on purpose: client must filter
        else:
            sel = data[-count:]
        return httpx.Response(200, json={
            "instrument": "XAU_USD", "granularity": params["granularity"],
            "candles": [{"time": f"{c['t']}.000000000", "volume": c["v"], "complete": c["complete"],
                         "mid": {"o": f"{c['o']:.3f}", "h": f"{c['h']:.3f}", "l": f"{c['l']:.3f}",
                                 "c": f"{c['c']:.3f}"}} for c in sel]})


@pytest.fixture
def env(tmp_path):
    fake = FakeOanda(12_345)
    sleeps: list[float] = []
    client = OandaClient("https://example.test", "secret", transport=httpx.MockTransport(fake.handler),
                         max_requests_per_second=1000, sleep=sleeps.append)
    store = MarketStore(tmp_path / "o.db")
    sync = OandaSync(store, client, price="M")
    feed = sync.ensure_feed("XAU_USD")
    return fake, client, store, sync, feed, sleeps


def test_feed_identity(env):
    _, _, _, _, feed, _ = env
    assert feed.id == "oanda-xau_usd-mid" and feed.symbol == "XAUUSD" and feed.alignment == "ny17"


def test_initial_sync_then_backfill_pages_backwards(env):
    fake, client, store, sync, feed, _ = env
    sync.sync_forward(feed, "M1")
    series = store.get_series(feed.id, "M1")
    assert store.series_stats(series.id)["count"] == 5000
    oldest = store.oldest_time(series.id)
    added = sync.backfill(feed, "M1", before=oldest, min_bars=6000)
    assert added >= 6000
    assert store.count_before(series.id, oldest) >= 6000
    # continue to the very beginning: provider runs out of data -> flagged, no further requests
    sync.backfill(feed, "M1", until=0)
    assert store.series_stats(series.id)["count"] == 12_345
    assert store.get_series(feed.id, "M1").oldest_reached
    n = len(fake.requests)
    sync.backfill(feed, "M1", until=0)
    assert len(fake.requests) == n
    bars = store.load_bars(series.id)
    assert (bars.time[1:] - bars.time[:-1] == 60).all()  # contiguous, no duplicates


def test_forward_sync_updates_incomplete_candle_and_appends(env):
    fake, client, store, sync, feed, _ = env
    sync.sync_forward(feed, "M1")
    series = store.get_series(feed.id, "M1")
    last = store.load_bars(series.id, limit=1)
    assert not last.complete[0]
    # the forming candle completes and two new ones arrive
    fake.candles[-1].update(c=2222.0, complete=True)
    t = fake.candles[-1]["t"]
    for k in (1, 2):
        fake.candles.append({"t": t + 60 * k, "o": 2222, "h": 2223, "l": 2221, "c": 2222.5, "v": 5,
                             "complete": k == 1})
    changed = sync.sync_forward(feed, "M1")
    assert [r[0] for r in changed] == [t, t + 60, t + 120]
    tail = store.load_bars(series.id, limit=3)
    assert tail.close[0] == 2222.0 and tail.complete.tolist() == [True, True, False]
    # nothing new -> nothing reported
    assert sync.sync_forward(feed, "M1") == []


def test_retry_on_rate_limit_and_server_errors(env):
    fake, client, store, sync, feed, sleeps = env
    fake.fail_next = [httpx.Response(429, headers={"Retry-After": "3"}, json={"errorMessage": "slow down"}),
                      httpx.Response(503, json={}),
                      httpx.ConnectError("boom")]
    rows = client.candles("XAU_USD", "M1", count=10)
    assert len(rows) == 10
    assert sleeps[:3] == [3.0, 2.0, 4.0]


def test_auth_error_is_not_retried(env):
    fake, client, *_ = env
    fake.fail_next = [httpx.Response(401, json={"errorMessage": "Insufficient authorization"})]
    with pytest.raises(OandaAuthError):
        client.candles("XAU_USD", "M1", count=10)
    assert len(fake.requests) == 1


def test_gives_up_after_max_retries(env):
    fake, client, *_ = env
    client.max_retries = 2
    fake.fail_next = [httpx.Response(500, json={})] * 3
    with pytest.raises(OandaError):
        client.candles("XAU_USD", "M1", count=10)


def test_count_with_from_and_to_rejected(env):
    _, client, *_ = env
    with pytest.raises(ValueError):
        client.candles("XAU_USD", "M1", start=1, end=2, count=5)


def test_rate_limiter_waits_when_bucket_empty():
    now = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(2.0, burst=2, clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        rl.acquire()
    assert sum(slept) == pytest.approx(1.0)
