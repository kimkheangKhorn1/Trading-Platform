"""Live candle updates pushed to browsers over WebSocket.

For OANDA feeds the hub polls the candles endpoint for every (feed, timeframe) that has at least one
subscriber, stores changes and broadcasts them. Polling the candle endpoint (rather than building
candles from a tick stream) keeps the live candle identical to the provider's own aggregation, so
live and historical candles never disagree. Failures back off exponentially and are reported to the
UI as ``reconnecting``; imported CSV feeds are reported as ``static``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict

from fastapi import WebSocket

from .repository import MarketStore
from .sync import OandaSync

log = logging.getLogger("live")


class LiveHub:
    def __init__(self, store: MarketStore, sync: OandaSync | None, poll_seconds: float = 2.0):
        self.store = store
        self.sync = sync
        self.poll_seconds = poll_seconds
        self.subs: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)
        self.status: dict[tuple[str, str], dict] = {}
        self._failures: dict[tuple[str, str], int] = defaultdict(int)
        self._next_poll: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="live-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def subscribe(self, ws: WebSocket, feed_id: str, tf: str) -> None:
        key = (feed_id, tf)
        self.subs[key].add(ws)
        feed = self.store.get_feed(feed_id)
        if feed is None:
            await self._send(ws, {"type": "status", "feed": feed_id, "tf": tf, "state": "error",
                                  "message": "unknown feed"})
            return
        if feed.provider != "oanda":
            await self._send(ws, {"type": "status", "feed": feed_id, "tf": tf, "state": "static",
                                  "message": "Imported data - no live updates"})
        elif self.sync is None:
            await self._send(ws, {"type": "status", "feed": feed_id, "tf": tf, "state": "offline",
                                  "message": "OANDA credentials not configured"})
        else:
            self._next_poll[key] = 0.0  # poll immediately
            await self._send(ws, {"type": "status", "feed": feed_id, "tf": tf,
                                  **self.status.get(key, {"state": "connecting", "message": "Connecting to OANDA"})})

    def unsubscribe(self, ws: WebSocket) -> None:
        for key in list(self.subs):
            self.subs[key].discard(ws)
            if not self.subs[key]:
                del self.subs[key]

    async def _send(self, ws: WebSocket, msg: dict) -> bool:
        try:
            await ws.send_json(msg)
            return True
        except Exception:
            self.unsubscribe(ws)
            return False

    async def _broadcast(self, key: tuple[str, str], msg: dict) -> None:
        for ws in list(self.subs.get(key, ())):
            await self._send(ws, msg)

    async def _set_status(self, key, state: str, message: str) -> None:
        prev = self.status.get(key)
        self.status[key] = {"state": state, "message": message}
        if prev != self.status[key]:
            await self._broadcast(key, {"type": "status", "feed": key[0], "tf": key[1], **self.status[key]})

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the hub die
                log.exception("live hub tick failed")
            await asyncio.sleep(0.25)

    async def _tick(self) -> None:
        if self.sync is None:
            return
        now = time.monotonic()
        for key in list(self.subs):
            if now < self._next_poll.get(key, 0.0):
                continue
            feed = self.store.get_feed(key[0])
            if feed is None or feed.provider != "oanda":
                self._next_poll[key] = now + 3600
                continue
            try:
                # fail fast: the hub's own backoff + status reporting handles outages
                rows = await asyncio.to_thread(self.sync.sync_forward, feed, key[1], max_retries=0)
                self._failures[key] = 0
                self._next_poll[key] = time.monotonic() + self.poll_seconds
                await self._set_status(key, "live", "Live · OANDA")
                if rows:
                    await self._broadcast(key, {
                        "type": "bars", "feed": key[0], "tf": key[1],
                        "bars": [[int(r[0]), r[1], r[2], r[3], r[4], r[5], int(r[6])] for r in rows[-500:]],
                    })
            except Exception as exc:
                n = self._failures[key] = self._failures[key] + 1
                delay = min(60.0, self.poll_seconds * 2 ** min(n, 6))
                self._next_poll[key] = time.monotonic() + delay
                await self._set_status(key, "reconnecting", f"{exc} - retrying in {delay:.0f}s")
