"""Read-only client for the supplied Exness Public Trader API 1.0.2 contract.

No trading methods. Host must come from the client's Key Management/DNS widget; the
documentation host and MT5 server names are not API endpoints. TLS verification stays on.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import MarketDataError
from .timeframes import bucket_end, bucket_start, get_timeframe

EXNESS_TIMEFRAMES = {"D": "D1", "W": "W1", "M": "MN"}


class ExnessError(MarketDataError):
    def __init__(self, message: str, code: str = "EXNESS_ERROR", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def signing_key(raw: bytes) -> Ed25519PrivateKey:
    try:
        if raw.strip().startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("wrong key type")
            return key
        text = raw.strip().decode("ascii")
        seed = bytes.fromhex(text) if re.fullmatch(r"[0-9a-fA-F]{64}", text) else base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        return Ed25519PrivateKey.from_private_bytes(seed)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ExnessError("Signing key must be Ed25519 PEM or a 32-byte seed encoded as hex/base64url.", "LOCAL_KEY_FORMAT") from exc


def signed_headers(api_key: str, key: Ed25519PrivateKey, path: str, now_ms: int) -> dict[str, str]:
    payload = json.dumps({"api_key": api_key, "idempotency_key": "", "timestamp": now_ms,
                          "sign_version": 1, "method": "GET", "path": path,
                          "body_hash": b64url(hashlib.sha256(b"").digest())}, separators=(",", ":")).encode()
    return {"EXN-API-KEY": api_key, "EXN-IDEMPOTENCY-KEY": "", "EXN-TIMESTAMP": str(now_ms),
            "EXN-SIGN-VERSION": "1", "EXN-DATA": b64url(payload), "EXN-SIGN": b64url(key.sign(payload))}


def timestamp(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone missing")
        return dt.timestamp()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExnessError("Provider returned an invalid UTC timestamp.", "INVALID_PAYLOAD") from exc


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def numeric(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExnessError("Provider returned an invalid numeric price/volume.", "INVALID_PAYLOAD")
    return float(value)


def parse_candles(payload: dict, tf: str, alignment: str, now: float, price: str = "bid") -> list[tuple]:
    candles = payload.get("candles")
    if not isinstance(candles, list):
        raise ExnessError("Candle response is missing its candles array.", "INVALID_PAYLOAD")
    timeframe = get_timeframe(tf, reference=True)
    rows = []
    seen = set()
    for candle in candles:
        try:
            ts = timestamp(candle["time"])
            t = int(ts)
            if ts != t or bucket_start(t, timeframe, alignment) != t:
                raise ExnessError("Native candle boundaries do not match EXNESS_ALIGNMENT; adjust it before using this feed.", "SESSION_MISMATCH")
            px = candle.get(price, candle)
            o, h, lo, c = (numeric(px[k]) for k in ("open", "high", "low", "close"))
            v = numeric(candle["volume"])
            if min(o, h, lo, c) <= 0 or lo > min(o, c) or h < max(o, c) or lo > h or v < 0 or t in seen:
                raise ExnessError("Invalid OHLC, volume, or duplicate candle timestamp.", "INVALID_PAYLOAD")
            seen.add(t)
            # The contract has no `complete` flag. Wait two seconds beyond its configured boundary.
            complete = int(bucket_end(t, timeframe, alignment) <= now - 2)
            rows.append((t, o, h, lo, c, v, complete))
        except (KeyError, TypeError) as exc:
            raise ExnessError("Provider returned an incomplete candle record.", "INVALID_PAYLOAD") from exc
    rows.sort()
    # The runner permits one provisional tail only. During the two-second close grace,
    # hold the new candle until the preceding one can be reconciled as completed.
    for i, row in enumerate(rows):
        if row[6] == 0:
            return rows[:i + 1]
    return rows


class RequestBudget:
    """Track the server's rolling global + per-operation limits locally across threads."""
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.rules = {"*": (2, 1.0)}  # conservative bootstrap until getLimits returns actual quotas
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def configure(self, rest: dict) -> None:
        try:
            rules = {"*": (int(rest["global_account_rate"]["limit"]), float(rest["global_account_rate"]["window_seconds"]))}
            for method in rest["methods"]:
                rate = method["rate_limit"]
                rules[method["api_operation_id"]] = (int(rate["limit"]), float(rate["window_seconds"]))
            if any(n <= 0 or window <= 0 for n, window in rules.values()):
                raise ValueError("invalid rate")
        except (KeyError, ValueError, TypeError) as exc:
            raise ExnessError("API limits response is incomplete or invalid.", "INVALID_LIMITS") from exc
        with self.lock:
            self.rules = rules

    def acquire(self, operation: str) -> None:
        while True:
            with self.lock:
                now = self.clock()
                wait = 0.0
                for k in ("*", operation):
                    if k not in self.rules:
                        continue
                    n, window = self.rules[k]
                    q = self.events[k]
                    while q and q[0] <= now - window:
                        q.popleft()
                    if len(q) >= n:
                        wait = max(wait, q[0] + window - now)
                if wait == 0:
                    self.events["*"].append(now)
                    self.events[operation].append(now)
                    return
            if wait > 30:
                raise ExnessError("Local API quota is exhausted; wait for its window before requesting more history.", "LOCAL_RATE_LIMIT")
            self.sleep(wait)


class ExnessClient:
    def __init__(self, base_url: str, api_key: str, private_key: bytes, account_id: str, *,
                 alignment: str = "utc", price: str = "bid", transport=None, clock=time.time):
        host = urlsplit(base_url)
        if (host.scheme != "https" or not host.hostname or host.username or host.password or host.query
                or host.fragment or host.path not in ("", "/") or host.port not in (None, 443)):
            raise ExnessError("EXN_API_BASE_URL must be a verified HTTPS API origin from Key Management.", "LOCAL_HOST")
        if transport is None and not (host.hostname.endswith(".exness.com") or host.hostname in ("api.exness-api.com",)):
            raise ExnessError("Confirm the API host in Exness Key Management; documentation and MT5 hosts are not API origins.", "LOCAL_HOST")
        if not re.fullmatch(r"[0-9]+", account_id):
            raise ExnessError("EXN_ACCOUNT_ID must be the API-enabled numeric account ID.", "LOCAL_ACCOUNT")
        if price not in ("bid", "ask"):
            raise ExnessError("Only native bid or ask history is supported.", "LOCAL_PRICE_TYPE")
        self.base_url, self.api_key, self.account_id = base_url.rstrip("/"), api_key, account_id
        self.key, self.clock, self.alignment, self.price = signing_key(private_key), clock, alignment, price
        self.http = httpx.Client(base_url=self.base_url, timeout=20, transport=transport, follow_redirects=False)
        self.budget = RequestBudget()
        self.max_candles: int | None = None
        self.blocked = False

    def close(self) -> None:
        self.http.close()

    def _get(self, operation: str, path: str, params: dict | None = None) -> dict:
        if self.blocked:
            raise ExnessError("Provider rejected the API quota. Check its limits before restarting the connection.", "REQUEST_RATE_LIMIT")
        self.budget.acquire(operation)
        req = self.http.build_request("GET", path, params=params)
        req.headers.update(signed_headers(self.api_key, self.key, req.url.raw_path.decode("ascii"), int(self.clock() * 1000)))
        try:
            res = self.http.send(req)
        except httpx.TransportError as exc:
            raise ExnessError("Exness network request failed.", "NETWORK_ERROR") from exc
        if res.status_code != 200:
            code = f"HTTP_{res.status_code}"
            try:
                body = res.json()
                error = body.get("error", {})
                candidate = body.get("error_message") or (error.get("code") if isinstance(error, dict) else "")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", candidate):
                    code = candidate
            except (ValueError, AttributeError):
                pass
            if res.status_code == 429 or code == "REQUEST_RATE_LIMIT":
                self.blocked = True  # contract explicitly prohibits automatic retries on this response
            raise ExnessError(f"Exness HTTP {res.status_code} ({code}).", code, res.status_code)
        try:
            body = res.json()
            if not isinstance(body, dict):
                raise ValueError("not an object")
            return body
        except ValueError as exc:
            raise ExnessError("Exness returned an invalid JSON response.", "INVALID_PAYLOAD") from exc

    def path(self, suffix: str) -> str:
        return f"/v1/configuration/accounts/{self.account_id}/{suffix}"

    def configure_limits(self) -> None:
        payload = self._get("getLimits", self.path("limits"))
        try:
            rest = payload["limits"]["rest"]
            candles = next(m for m in rest["methods"] if m["api_operation_id"] == "getCandleHistory")
            maximum = int(candles["limits"]["max_candles_per_request"])
            if maximum < 1:
                raise ValueError("invalid candle limit")
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise ExnessError("Cannot determine the server's candle request limit.", "INVALID_LIMITS") from exc
        self.budget.configure(rest)
        self.max_candles = maximum

    def instruments(self) -> list[str]:
        items = self._get("getAvailableInstrumentList", self.path("instruments")).get("instruments")
        if not isinstance(items, list) or any(not isinstance(s, str) for s in items):
            raise ExnessError("Instrument list is invalid.", "INVALID_PAYLOAD")
        return items

    def conditions(self, instrument: str) -> dict:
        return self._get("getInstrumentCondition", self.path(f"instruments/{quote(instrument, safe='')}/conditions"))

    def candles(self, instrument: str, tf: str, *, start: int, count: int) -> list[tuple]:
        if self.max_candles is None:
            self.configure_limits()
        if count == 0 or abs(count) > self.max_candles:
            raise ExnessError("Candle count exceeds the server's configured limit.", "LOCAL_CANDLE_LIMIT")
        params = {"instrument": instrument, "price_type": self.price,
                  "timeframe": EXNESS_TIMEFRAMES.get(tf, tf), "from": iso(start), "count": count}
        body = self._get("getCandleHistory", f"/v1/market-data/accounts/{self.account_id}/candles", params)
        return parse_candles(body, tf, self.alignment, self.clock(), self.price)

    def ticks_connection(self):
        from websockets.asyncio.client import connect
        path = f"/v1/server-events/accounts/{self.account_id}/ws/ticks"
        headers = signed_headers(self.api_key, self.key, path, int(self.clock() * 1000))
        return connect(self.base_url.replace("https://", "wss://", 1) + path, additional_headers=headers,
                       subprotocols=["exness-ws-protocol"], open_timeout=15, ping_interval=20, ping_timeout=20,
                       max_size=256_000, compression=None, proxy=None)
