"""FastAPI application: REST API, live WebSocket and (optionally) the built frontend."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .backtest.engine import BacktestError
from .backtest.service import BacktestJobs
from .config import BACKEND_DIR, load_settings
from .engine.inputs import InputError
from .engine.registry import list_scripts
from .indicator_service import IndicatorRequestError, IndicatorService
from .market.csv_import import CsvImportError
from .market.importer import import_csv
from .market.live import LiveHub
from .market.oanda import OandaError
from .market.providers import make_oanda_sync
from .market.repository import MarketStore
from .market.sync import HistoryDownloadJobs
from .market.timeframes import TIMEFRAMES, get_timeframe

log = logging.getLogger("app")
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    store = MarketStore(settings.database_path)
    store.list_feeds()  # creates the schema
    sync = make_oanda_sync(settings, store)
    if sync is not None:
        for inst in settings.oanda_instruments:
            try:
                sync.ensure_feed(inst)
            except Exception as exc:  # network/auth problems are reported via /api/health
                log.warning("could not initialise OANDA feed %s: %s", inst, exc)
    app.state.settings = settings
    app.state.store = store
    app.state.sync = sync
    app.state.indicators = IndicatorService(store, sync)
    app.state.backtests = BacktestJobs(store, settings)
    app.state.downloads = HistoryDownloadJobs(sync) if sync else None
    app.state.live = LiveHub(store, sync, settings.live_poll_seconds)
    app.state.live.start()
    app.state.oanda_error = None
    yield
    await app.state.live.stop()
    app.state.backtests.shutdown()


app = FastAPI(title="XAUUSD Trading Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_settings().cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store() -> MarketStore:
    return app.state.store


# ------------------------------------------------------------------------- system
@app.get("/api/health")
def health():
    s = app.state.settings
    return {
        "status": "ok",
        "time": int(time.time()),
        "oanda": {"enabled": s.oanda_enabled, "environment": s.oanda_environment,
                  "instruments": list(s.oanda_instruments), "price_component": s.oanda_price_component,
                  "last_error": app.state.oanda_error},
        "timeframes": [{"code": tf.code, "label": tf.label, "seconds": tf.seconds} for tf in TIMEFRAMES.values()],
    }


# -------------------------------------------------------------------------- feeds
@app.get("/api/feeds")
def feeds():
    store = _store()
    out = []
    for feed in store.list_feeds():
        series = []
        for s in store.list_series(feed.id):
            st = store.series_stats(s.id)
            series.append({"timeframe": s.timeframe, "origin": s.origin, "oldest_reached": s.oldest_reached,
                           **st})
        available = [s["timeframe"] for s in series]
        if feed.provider == "oanda":
            available = list(TIMEFRAMES)
        out.append({**feed.to_dict(), "series": series, "timeframes": available,
                    "live": feed.provider == "oanda" and app.state.sync is not None})
    return out


@app.delete("/api/feeds/{feed_id}")
def delete_feed(feed_id: str):
    store = _store()
    if store.get_feed(feed_id) is None:
        raise HTTPException(404, "unknown feed")
    store.delete_feed(feed_id)
    return {"deleted": feed_id}


@app.get("/api/candles")
def candles(
    feed_id: str,
    timeframe: str,
    before: int | None = None,
    limit: int = Query(2000, ge=1, le=20000),
):
    store = _store()
    feed = store.get_feed(feed_id)
    if feed is None:
        raise HTTPException(404, "unknown feed")
    try:
        tf = get_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    warning = None
    sync = app.state.sync
    if feed.provider == "oanda" and sync is not None:
        try:
            if before is None:
                sync.sync_forward(feed, tf.code)
                series = store.get_series(feed.id, tf.code)
                last = store.latest_time(series.id) if series else None
                if last is not None:
                    sync.backfill(feed, tf.code, before=last + 1, min_bars=limit)
            else:
                sync.backfill(feed, tf.code, before=before, min_bars=limit)
            app.state.oanda_error = None
        except OandaError as exc:
            warning = f"OANDA request failed ({exc}); showing locally stored candles"
            app.state.oanda_error = str(exc)
    series = store.get_series(feed.id, tf.code)
    if series is None:
        raise HTTPException(404, f"{tf.label} candles are not available for {feed.display_name}")
    bars = store.load_bars(series.id, before=before, limit=limit)
    has_more = False
    if len(bars):
        has_more = store.count_before(series.id, int(bars.time[0])) > 0 or (
            feed.provider == "oanda" and sync is not None and not store.get_series(feed.id, tf.code).oldest_reached)
    return {"feed_id": feed.id, "timeframe": tf.code, "precision": feed.precision, "bars": bars.to_json_rows(),
            "has_more": has_more, "warning": warning}


@app.post("/api/import/csv")
def import_csv_endpoint(
    file: UploadFile = File(...),
    symbol: str = Form("XAUUSD"),
    dataset: str = Form("import"),
    price_type: str = Form("bid"),
    timezone: str = Form("UTC"),
    timeframe: str = Form(""),
    alignment: str = Form("ny17"),
):
    content = file.file.read()
    try:
        feed, report = import_csv(_store(), content, symbol=symbol, dataset=dataset, price_type=price_type,
                                  timezone=timezone, timeframe=timeframe or None, alignment=alignment,
                                  filename=file.filename)
    except CsvImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"feed": feed.to_dict(), "report": report.to_dict()}


class DownloadRequest(BaseModel):
    timeframe: str
    until: int = Field(..., description="download history back to this UTC timestamp")


@app.post("/api/feeds/{feed_id}/download")
def start_download(feed_id: str, req: DownloadRequest):
    feed = _store().get_feed(feed_id)
    if feed is None or feed.provider != "oanda":
        raise HTTPException(400, "history downloads are only available for OANDA feeds")
    if app.state.downloads is None:
        raise HTTPException(400, "OANDA is not configured")
    get_timeframe(req.timeframe)
    return app.state.downloads.start(feed, req.timeframe, req.until)


@app.get("/api/downloads")
def downloads():
    return app.state.downloads.list() if app.state.downloads else []


# --------------------------------------------------------------------- indicators
@app.get("/api/scripts")
def scripts():
    out = []
    for cls in list_scripts():
        d = cls.meta.to_dict()
        d["default_warmup"] = cls().required_warmup()
        out.append(d)
    return out


class IndicatorRunRequest(BaseModel):
    feed_id: str
    timeframe: str
    script_id: str
    inputs: dict = Field(default_factory=dict)
    start_time: int
    end_time: int | None = None


@app.post("/api/indicators/run")
def run_indicator(req: IndicatorRunRequest):
    try:
        return app.state.indicators.run(feed_id=req.feed_id, timeframe=req.timeframe, script_id=req.script_id,
                                        inputs=req.inputs, start_time=req.start_time, end_time=req.end_time)
    except (InputError, IndicatorRequestError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------- backtests
@app.post("/api/backtests")
def submit_backtest(request: dict):
    try:
        job_id = app.state.backtests.submit(request)
    except (BacktestError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": job_id}


@app.get("/api/backtests")
def list_backtests(limit: int = Query(30, ge=1, le=200)):
    return app.state.backtests.list(limit)


@app.get("/api/backtests/{job_id}")
def get_backtest(job_id: str):
    job = app.state.backtests.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown backtest")
    return job


# -------------------------------------------------------------------------- live
@app.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    await ws.accept()
    hub: LiveHub = app.state.live
    try:
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")
            if action == "subscribe":
                hub.unsubscribe(ws)
                await hub.subscribe(ws, str(msg.get("feed_id")), str(msg.get("timeframe")))
            elif action == "unsubscribe":
                hub.unsubscribe(ws)
            elif action == "ping":
                await ws.send_json({"type": "pong", "time": int(time.time())})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(ws)


# ------------------------------------------------------------- built frontend (optional)
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = FRONTEND_DIST / path
        if path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
