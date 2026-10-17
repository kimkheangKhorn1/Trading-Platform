"""Backend integration tests through the HTTP/WebSocket API (CSV feed; OANDA disabled)."""
import pytest
from fastapi.testclient import TestClient

from tests.helpers import random_walk_bars


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    db = tmp_path_factory.mktemp("api") / "api.db"
    mp.setenv("DATABASE_PATH", str(db))
    mp.delenv("OANDA_API_TOKEN", raising=False)
    mp.setenv("BACKTEST_WORKERS", "1")
    from app.main import app

    with TestClient(app) as c:
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def feed_id(client):
    bars = random_walk_bars(3000, seed=33)
    lines = ["time,open,high,low,close,volume"] + [
        f"{t},{o:.3f},{h:.3f},{lo:.3f},{c:.3f},{v:.0f}"
        for t, o, h, lo, c, v in zip(bars.time, bars.open, bars.high, bars.low, bars.close, bars.volume)
    ]
    r = client.post("/api/import/csv", files={"file": ("synthetic.csv", "\n".join(lines), "text/csv")},
                    data={"symbol": "XAUUSD", "dataset": "synthetic-test", "price_type": "mid", "timezone": "UTC"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["rows_imported"] == 3000 and body["report"]["timeframe"] == "M5"
    return body["feed"]["id"]


def test_health_reports_oanda_disabled(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["oanda"]["enabled"] is False
    assert [tf["label"] for tf in r["timeframes"]] == ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]


def test_feeds_and_paged_candles(client, feed_id):
    feeds = client.get("/api/feeds").json()
    feed = next(f for f in feeds if f["id"] == feed_id)
    assert "M1" not in feed["timeframes"] and {"M5", "H1", "D"} <= set(feed["timeframes"])
    first = client.get("/api/candles", params={"feed_id": feed_id, "timeframe": "M5", "limit": 1000}).json()
    assert len(first["bars"]) == 1000 and first["has_more"]
    seen = [b[0] for b in first["bars"]]
    before = seen[0]
    while True:
        page = client.get("/api/candles", params={"feed_id": feed_id, "timeframe": "M5", "limit": 1000,
                                                  "before": before}).json()
        seen = [b[0] for b in page["bars"]] + seen
        if not page["has_more"]:
            break
        before = page["bars"][0][0]
    assert len(seen) == 3000 and seen == sorted(set(seen))
    r = client.get("/api/candles", params={"feed_id": feed_id, "timeframe": "M1"})
    assert r.status_code == 404


def test_forward_candle_paging_starts_at_the_next_bar_without_skipping_to_the_present(client, feed_id):
    query = {"feed_id": feed_id, "timeframe": "M5", "limit": 3000}
    all_bars = client.get("/api/candles", params=query).json()["bars"]
    cutoff = all_bars[199][0]
    first = client.get("/api/candles", params={**query, "after": cutoff, "limit": 50}).json()
    assert first["bars"] == all_bars[200:250]
    assert first["has_newer"]
    second = client.get("/api/candles", params={**query, "after": first["bars"][-1][0], "limit": 50}).json()
    assert second["bars"] == all_bars[250:300]
    tail = client.get("/api/candles", params={**query, "after": all_bars[-6][0], "limit": 50}).json()
    assert tail["bars"] == all_bars[-5:]
    assert not tail["has_newer"]


def test_smc_master_runs_through_api(client, feed_id):
    assert any(s["id"] == "smc_ict_scalping_master" for s in client.get("/api/scripts").json())
    candles = client.get("/api/candles", params={"feed_id": feed_id, "timeframe": "M5", "limit": 1500}).json()
    req = {"feed_id": feed_id, "timeframe": "M5", "script_id": "smc_ict_scalping_master", "inputs": {},
           "start_time": candles["bars"][0][0]}
    r = client.post("/api/indicators/run", json=req)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["times"]) == 1500 and len(out["candle_colors"]) == 1500
    assert out["tables"][0]["id"] == "directions" and out["drawings"]
    # the synthetic feed starts in March: no completed previous month to draw
    assert any(w.startswith("Previous M levels unavailable") for w in out["warnings"])
    bad = client.post("/api/indicators/run", json={**req, "inputs": {"fvg_tf": "W"}})
    assert bad.status_code == 400


def test_live_websocket_reports_static_feed(client, feed_id):
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"action": "subscribe", "feed_id": feed_id, "timeframe": "M5"})
        msg = ws.receive_json()
        assert msg["type"] == "status" and msg["state"] == "static"
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_csv_errors_are_400(client):
    r = client.post("/api/import/csv", files={"file": ("x.csv", "nothing useful here", "text/csv")})
    assert r.status_code == 400
