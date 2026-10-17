"""One signed market-data WebSocket per account; native REST history reconciles provisional bars."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

from .exness import ExnessError, timestamp
from .live import LiveHub


class ExnessLiveHub(LiveHub):
    def __init__(self, store, sync, poll_seconds=10.0):
        super().__init__(store, sync, max(2., poll_seconds))
        self._stream: asyncio.Task | None = None
        self._instruments: frozenset[str] = frozenset()
        self._retry_at = 0.
        self._stream_failures = 0
        self._stream_blocked = False
        self._last_received = 0.
        self._poll_blocked: set[tuple[str, str]] = set()

    async def stop(self):
        if self._stream:
            self._stream.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream
        await super().stop()

    async def _tick(self):
        keys = [k for k in self.subs if (f := self.store.get_feed(k[0])) is not None and self.sync.owns(f)]
        instruments = frozenset(self.store.get_feed(k[0]).instrument for k in keys)
        if instruments != self._instruments:
            if self._stream:
                self._stream.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._stream
            self._stream = None
            self._instruments = instruments
            self._last_received = 0.
        now = time.monotonic()
        if instruments and not self._stream_blocked and now >= self._retry_at and (self._stream is None or self._stream.done()):
            self._stream = asyncio.create_task(self._run_stream(instruments), name="exness-ticks")
        for key in keys:
            if now < self._next_poll.get(key, 0.) or key in self._poll_blocked:
                continue
            feed = self.store.get_feed(key[0])
            try:
                rows = await asyncio.to_thread(self.sync.sync_forward, feed, key[1], max_retries=0)
                await self._bars(key, rows)
                self._failures[key] = 0
                self._next_poll[key] = time.monotonic() + self.poll_seconds
                if self._stream_blocked:
                    await self._set_status(key, "error", "Exness tick stream rejected; check API access/limits and restart")
                elif self._last_received == 0:
                    await self._set_status(key, "connecting", "History available; waiting for Exness live ticks")
                elif time.monotonic() - self._last_received > 30:
                    await self._set_status(key, "offline", "No recent Exness ticks; waiting for new quotes")
            except Exception as exc:
                if isinstance(exc, ExnessError) and (exc.status in (401, 403, 429) or exc.code in ("REQUEST_RATE_LIMIT", "SESSION_MISMATCH")):
                    self._poll_blocked.add(key)
                    await self._set_status(key, "error", str(exc))
                else:
                    n = self._failures[key] = self._failures[key] + 1
                    delay = min(60., self.poll_seconds * 2 ** min(n, 5))
                    self._next_poll[key] = time.monotonic() + delay
                    await self._set_status(key, "reconnecting", f"History reconciliation failed; retrying in {delay:.0f}s")

    async def _bars(self, key, rows):
        if rows:
            await self._broadcast(key, {"type": "bars", "feed": key[0], "tf": key[1],
                                        "bars": [list(r) for r in rows[-500:]]})

    async def _run_stream(self, instruments):
        try:
            async with self.sync.client.ticks_connection() as ws:
                await ws.send(json.dumps({"id": "local-market-data", "subscribe": {"event": "ticks", "instruments": sorted(instruments)}}))
                async for raw in ws:
                    try:
                        tick = json.loads(raw)
                    except (ValueError, TypeError):
                        raise ExnessError("Invalid JSON from Exness tick stream.", "INVALID_PAYLOAD")
                    if not isinstance(tick, dict):
                        continue
                    if "error" in tick or "error_message" in tick:
                        raise ExnessError("Exness rejected the tick subscription; check market-data permissions and limits.", "SUBSCRIPTION_REJECTED")
                    if not all(k in tick for k in ("instrument", "bid", "ask", "timestamp")):
                        continue  # subscription acknowledgements are not ticks
                    if tick["instrument"] not in instruments:
                        continue
                    age = time.time() - timestamp(tick["timestamp"])
                    if age > 30 or age < -10:
                        continue  # initial snapshot may be from a closed market, not a fresh live quote
                    self._last_received = time.monotonic()
                    self._stream_failures = 0
                    for key in list(self.subs):
                        feed = self.store.get_feed(key[0])
                        if not feed or not self.sync.owns(feed) or feed.instrument != tick["instrument"]:
                            continue
                        rows = await asyncio.to_thread(self.sync.apply_tick, feed, key[1], tick)
                        await self._bars(key, rows)
                        if key not in self._poll_blocked:
                            await self._set_status(key, "live", "Live · Exness ticks")
                raise ExnessError("Exness tick stream closed.", "STREAM_CLOSED")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            fatal = status in (401, 403, 429) or isinstance(exc, ExnessError) and exc.code == "SUBSCRIPTION_REJECTED"
            self._stream_blocked = fatal
            self._stream_failures += 1
            delay = min(60., 2 ** min(self._stream_failures, 6))
            self._retry_at = time.monotonic() + delay
            for key in list(self.subs):
                feed = self.store.get_feed(key[0])
                if feed and feed.provider == "exness":
                    await self._set_status(key, "error" if fatal else "reconnecting",
                                           "Exness tick stream rejected; check API access/limits and restart" if fatal
                                           else f"Exness tick stream disconnected; retrying in {delay:.0f}s")
