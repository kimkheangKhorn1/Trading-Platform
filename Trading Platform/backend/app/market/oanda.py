"""Minimal OANDA v20 REST client for instrument candles.

Reference: https://developer.oanda.com/rest-live-v20/instrument-ep/

* ``GET /v3/instruments/{instrument}/candles`` - at most 5000 candles per request
* ``count`` cannot be combined with both ``from`` and ``to``; we always page with ``from``+``count``
  (forward) or ``to``+``count`` (backward)
* the most recent candle is returned with ``complete: false`` while it is still forming
* daily/H4 candles are aligned with ``dailyAlignment=17`` / ``alignmentTimezone=America/New_York``
  (OANDA's defaults, made explicit here so stored candles never depend on a server default)
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable

import httpx

MAX_CANDLES_PER_REQUEST = 5000
RETRY_STATUS = {429, 500, 502, 503, 504}

PRICE_KEYS = {"M": "mid", "B": "bid", "A": "ask"}


class OandaError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class OandaAuthError(OandaError):
    pass


class RateLimiter:
    """Thread-safe token bucket."""

    def __init__(self, rate_per_second: float, burst: int | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.rate = max(rate_per_second, 0.1)
        self.capacity = burst or max(1, int(rate_per_second))
        self.tokens = float(self.capacity)
        self.clock = clock
        self.sleep = sleep
        self.updated = clock()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            self.sleep(wait)


def to_rfc3339(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def parse_candles(payload: dict, price: str) -> list[tuple[int, float, float, float, float, float, int]]:
    key = PRICE_KEYS[price]
    rows = []
    for cndl in payload.get("candles", []):
        px = cndl.get(key)
        if not px:
            continue
        t = int(float(cndl["time"]))
        rows.append((t, float(px["o"]), float(px["h"]), float(px["l"]), float(px["c"]),
                     float(cndl.get("volume", 0)), 1 if cndl.get("complete") else 0))
    rows.sort(key=lambda r: r[0])
    return rows


class OandaClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        max_requests_per_second: float = 20.0,
        timeout: float = 30.0,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept-Datetime-Format": "UNIX",
                "Content-Type": "application/json",
            },
        )
        self._limiter = RateLimiter(max_requests_per_second, sleep=sleep)
        self._sleep = sleep
        self.max_retries = max_retries
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict, max_retries: int | None = None) -> dict:
        retries = self.max_retries if max_retries is None else max_retries
        attempt = 0
        while True:
            self._limiter.acquire()
            self.request_count += 1
            try:
                resp = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt >= retries:
                    raise OandaError(f"network error after {attempt + 1} attempts: {exc}") from exc
                self._sleep(min(30.0, 2.0 ** attempt))
                attempt += 1
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403):
                raise OandaAuthError(_error_message(resp), resp.status_code)
            if resp.status_code in RETRY_STATUS and attempt < retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2.0 ** attempt
                self._sleep(min(60.0, delay))
                attempt += 1
                continue
            raise OandaError(_error_message(resp), resp.status_code)

    def candles(
        self,
        instrument: str,
        granularity: str,
        *,
        price: str = "M",
        start: int | None = None,
        end: int | None = None,
        count: int | None = None,
        include_first: bool | None = None,
        max_retries: int | None = None,
    ) -> list[tuple[int, float, float, float, float, float, int]]:
        if start is not None and end is not None and count is not None:
            raise ValueError("OANDA does not allow count together with both from and to")
        params: dict = {
            "price": price,
            "granularity": granularity,
            "dailyAlignment": 17,
            "alignmentTimezone": "America/New_York",
            "weeklyAlignment": "Friday",
        }
        if count is not None:
            params["count"] = min(int(count), MAX_CANDLES_PER_REQUEST)
        if start is not None:
            params["from"] = to_rfc3339(start)
            if include_first is not None:
                params["includeFirst"] = "true" if include_first else "false"
        if end is not None:
            params["to"] = to_rfc3339(end)
        payload = self._get(f"/v3/instruments/{instrument}/candles", params, max_retries)
        return parse_candles(payload, price)

    def instrument_details(self, account_id: str, instrument: str) -> dict | None:
        payload = self._get(f"/v3/accounts/{account_id}/instruments", {"instruments": instrument})
        items = payload.get("instruments", [])
        return items[0] if items else None


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("errorMessage") or body.get("message") or str(body)
    except Exception:
        msg = resp.text[:200]
    return f"OANDA HTTP {resp.status_code}: {msg}"
