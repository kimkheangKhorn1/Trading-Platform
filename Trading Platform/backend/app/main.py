"""FastAPI application: REST API, live WebSocket and (optionally) the built frontend."""
from __future__ import annotations

import logging
import json
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
from .badge_overlap import BadgeOverlapService
from .market.csv_import import CsvImportError
from .market.importer import import_csv
from .market.live import LiveHub
from .market.exness_live import ExnessLiveHub
from .market.errors import MarketDataError
from .market.providers import make_market_sync
from .market.repository import MarketStore
from .market.sync import HistoryDownloadJobs
from .market.timeframes import TIMEFRAMES, get_timeframe

log = logging.getLogger("app")
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
EXNESS_ARCHIVE_ROOT = BACKEND_DIR / 'data/exness/XAUUSDm'


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    store = MarketStore(settings.database_path)
    store.list_feeds()  # creates the schema
    error = None
    sync = None
    try:
        sync = make_market_sync(settings, store)
    except MarketDataError as exc:
        error = str(exc)
    initialised = False
    if sync is not None:
        instruments = settings.exness_instruments if sync.provider == "exness" else settings.oanda_instruments
        for inst in instruments:
            try:
                sync.ensure_feed(inst)
                initialised = True
            except Exception as exc:  # network/auth problems are reported via /api/health
                error = str(exc)
                log.warning("could not initialise market-data feed %s: %s", inst, error)
    app.state.settings = settings
    app.state.store = store
    app.state.sync = sync
    app.state.indicators = IndicatorService(store, sync)
    app.state.badge_overlaps = BadgeOverlapService(store)
    app.state.backtests = BacktestJobs(store, settings)
    app.state.downloads = HistoryDownloadJobs(sync) if sync else None
    app.state.live = (ExnessLiveHub if sync is not None and sync.provider == "exness" else LiveHub)(store, sync, settings.live_poll_seconds)
    app.state.live.start()
    app.state.oanda_error = None
    app.state.market_error = error
    app.state.market_ready = initialised
    yield
    await app.state.live.stop()
    app.state.backtests.shutdown()
    if sync is not None:
        sync.client.close()


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
        "market_data_provider": s.market_data_provider,
        "exness": {"enabled": s.exness_enabled, "ready": app.state.market_ready if s.market_data_provider == "exness" else False,
                   "missing": s.exness_missing, "instruments": list(s.exness_instruments),
                   "price_type": s.exness_price_type, "alignment": s.exness_alignment,
                   "last_error": app.state.market_error if s.market_data_provider == "exness" else None},
        "oanda": {"enabled": s.oanda_enabled, "environment": s.oanda_environment,
                  "instruments": list(s.oanda_instruments), "price_component": s.oanda_price_component,
                  "last_error": app.state.oanda_error or (app.state.market_error if s.market_data_provider == "oanda" else None)},
        "timeframes": [{"code": tf.code, "label": tf.label, "seconds": tf.seconds} for tf in TIMEFRAMES.values()],
    }


# -------------------------------------------------------------------------- feeds
@app.get('/api/exness/history')
def exness_history_status():
    """Published archive progress is separate from authenticated/live API readiness."""
    path = BACKEND_DIR / 'data/exness/XAUUSDm/manifest.json'
    if not path.exists():
        return {'status':'not_downloaded','symbol':'XAUUSDm','archives_total':0,
                'archives_downloaded':0,'archives_processed':0,'ticks':0,'M1':0,'M5':0,
                'first_tick_utc':None,'last_tick_utc':None,'errors':[]}
    manifest = json.loads(path.read_text())
    entries = list(manifest['archives'].values())
    months = list(manifest.get('months',{}).values())
    total = len(manifest.get('inventory',[]))
    downloaded = sum(e.get('status') == 'downloaded' for e in entries)
    processed = sum('sha256' in e and e.get('processed_sha256') == e['sha256'] for e in entries)
    errors = [f'{e["period"]}: {e.get("processing_error",e.get("error"))}' for e in entries
              if e.get('processing_error') or e.get('error')]
    status = 'attention' if errors else 'downloading' if downloaded<total else 'processing' if processed<total else 'ready'
    verification_path = path.parent/'reports/verification.json'
    verification = json.loads(verification_path.read_text()) if verification_path.exists() else {}
    audit_matches = all(verification.get('archive_hashes',{}).get(e['filename']) == e.get('sha256')
                        and e.get('processed_sha256') == e.get('sha256')
                        for e in entries) and len(entries) == total
    repair_path = path.parent/'reports/source_order_repair.json'
    repair = json.loads(repair_path.read_text()) if repair_path.exists() else {}
    if repair.get('status') == 'running' or repair.get('finished_at','') > verification.get('verified_at',''):
        audit_matches = False
    accuracy_path = path.parent/'reports/accuracy_check.json'
    accuracy = json.loads(accuracy_path.read_text()) if accuracy_path.exists() else {}
    accuracy_current = accuracy.get('started_at','') >= repair.get('finished_at',repair.get('started_at',''))
    if accuracy_current and accuracy.get('status') in ('running','failed'):
        verification['status'] = 'pending' if accuracy['status'] == 'running' else 'failed'
    from datetime import datetime, timezone
    def stamp(ns):
        return datetime.fromtimestamp(ns/1e9,timezone.utc).isoformat() if ns is not None else None
    return {'status':status,'symbol':'XAUUSDm','archives_total':total,'archives_downloaded':downloaded,
            'archives_processed':processed,'ticks':sum(m['ticks'] for m in months),
            'M1':sum(m['M1'] for m in months),'M5':sum(m['M5'] for m in months),
            'first_tick_utc':stamp(min((m['first_timestamp_ns'] for m in months),default=None)),
            'last_tick_utc':stamp(max((m['last_timestamp_ns'] for m in months),default=None)),
            'errors':errors,'missing_weekdays':manifest.get('coverage',{}).get('missing_weekdays_requiring_calendar_review',[]),
            'verification':verification.get('status','pending') if audit_matches else 'pending'}


@app.get('/api/exness/history/coverage')
def exness_history_coverage():
    path = BACKEND_DIR/'data/exness/XAUUSDm/reports/coverage.json'
    if not path.exists():
        raise HTTPException(404,'The archive coverage report is not available yet')
    return FileResponse(path,media_type='application/json',filename='exness-xauusdm-coverage.json')


@app.get('/api/exness/history/confirmed-snapshot')
def exness_confirmed_snapshot(as_of: str, timeframes: str = 'M5,H1'):
    from .market.confirmed_data import SnapshotError, archive_snapshot
    try:
        return archive_snapshot(EXNESS_ARCHIVE_ROOT,as_of,timeframes.split(','),store=_store())
    except SnapshotError as exc:
        raise HTTPException(exc.status_code,str(exc)) from exc


@app.get('/api/exness/history/intrabar')
def exness_intrabar(start: str, end: str, as_of: str):
    from .market.confirmed_data import SnapshotError, archive_intrabar
    try:
        return archive_intrabar(EXNESS_ARCHIVE_ROOT,start,end,as_of)
    except SnapshotError as exc:
        raise HTTPException(exc.status_code,str(exc)) from exc


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
        sync = app.state.sync
        connected = sync is not None and sync.owns(feed) and app.state.market_ready
        if connected:
            available = list(TIMEFRAMES)
        out.append({**feed.to_dict(), "series": series, "timeframes": available,
                    "live": connected})
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
    after: int | None = None,
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
    if sync is not None and sync.owns(feed):
        try:
            if before is None:
                sync.sync_forward(feed, tf.code)
                series = store.get_series(feed.id, tf.code)
                last = store.latest_time(series.id) if series else None
                if last is not None:
                    sync.backfill(feed, tf.code, before=last + 1, min_bars=limit)
            else:
                sync.backfill(feed, tf.code, before=before, min_bars=limit)
            app.state.market_error = None
        except MarketDataError as exc:
            warning = f"{feed.provider.upper()} request failed ({exc}); showing locally stored candles"
            app.state.market_error = str(exc)
            if feed.provider == "oanda":
                app.state.oanda_error = str(exc)
    series = store.get_series(feed.id, tf.code)
    if series is None:
        raise HTTPException(404, f"{tf.label} candles are not available for {feed.display_name}")
    bars = store.load_bars(series.id, before=before, after=after, limit=limit)
    has_more = False
    if len(bars):
        has_more = store.count_before(series.id, int(bars.time[0])) > 0 or (
            sync is not None and sync.owns(feed) and not store.get_series(feed.id, tf.code).oldest_reached)
    return {"feed_id": feed.id, "timeframe": tf.code, "precision": feed.precision, "bars": bars.to_json_rows(),
            "has_more": has_more,
            "has_newer": bool(len(bars) and (store.latest_time(series.id) or 0) > int(bars.time[-1])),
            "warning": warning}


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
    if feed is None or feed.provider not in ("exness", "oanda"):
        raise HTTPException(400, "history downloads require a broker data feed")
    if app.state.downloads is None or not app.state.sync.owns(feed):
        raise HTTPException(400, "The selected provider's API credentials are not configured")
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
    except MarketDataError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------------- backtests
@app.post("/api/badge-overlaps")
def badge_overlaps(request: dict):
    """Optional display-only analysis; uses the original indicator's rendered fresh badges."""
    try:
        return app.state.badge_overlaps.run(**request)
    except (ValueError, TypeError, InputError) as exc:
        raise HTTPException(400, str(exc)) from exc


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
