# Exness market-data connection

**For historical ML data, use the credential-free [Exness archive pipeline](EXNESS_HISTORY.md).**
Read [the beginner ML data guide](ML_DATA_GUIDE.md) for folder organization, column
meanings, known gaps, chronological training windows and the checks required before training.
It downloads available XAUUSDm tick history from 2015 onward, preserves original
ZIPs, builds Bid/Ask M1/M5 Parquet data and imports separate historical chart feeds.
The manifest and coverage report record actual timestamps and gaps. The API setup
below is a separate optional path for account-specific history/live quotes.

The activation statements below refer to the authenticated API, independently of
the historical archive feeds. See the archive report for actual downloaded data.

The project now selects Exness as its preferred API provider. It implements signed read-only candle history, isolated SQLite caching, native bid/ask price selection, higher-timeframe references, and a signed live tick subscription. OANDA remains an optional provider and the existing imported CSV is preserved.

**Authenticated API activation: account eligibility must be confirmed before credentials can be obtained.** The API connector has been tested against a fake provider using generated test keys; it has not been authenticated against your Exness account. Historical archive feeds are available independently as downloads are processed. Existing CSV candles retain their original source identity.

## Obtain access

As checked on 13 October 2026, the [official Exness Help Center](https://get.exness.help/hc/en-us/articles/27866287512476-Exness-API) says the Public Trader API is currently available only in Vietnam and Thailand, and that standard MetaTrader accounts cannot connect. Verification alone does not establish eligibility. For accounts registered elsewhere, ask Exness support to confirm availability before attempting key setup. An Exness password, MT5 password, or different IP address does not establish API eligibility.

If direct API access is unavailable, an independently implemented read-only MetaTrader data bridge is a possible alternative to investigate. It is not part of this connector and is not currently implemented. It would require a compatible running terminal and separate history/live-data validation.

The [API setup documentation](https://www.exness-api.com/documentation/create-api-key) describes Key Management, an account-scoped API key, its private signing key, and the current host from the DNS widget. Account verification and an MT5 login/server name do not substitute for those values. We have not confirmed that Key Management is available in your Personal Area. If it is absent, ask Exness support whether your account can access this Public Trader API and which portal provides Key Management. Do not guess a hidden endpoint or reuse browser session cookies.

Request market-data/configuration access for the intended account if scope selection is offered. The connector does not place, change, close, or cancel orders. It keeps TLS verification enabled and does not forward signed headers through redirects.

## Configure locally

Edit **`backend/.env`**, not `.env.example`. The local file is ignored by Git; the signing-key folder is ignored too. Do not paste secrets into chat.

```dotenv
MARKET_DATA_PROVIDER=exness
EXN_API_KEY=<API key from Key Management>
EXN_ACCOUNT_ID=<API-enabled account ID>
EXN_API_BASE_URL=<HTTPS origin from Exness DNS widget>
EXN_PRIVATE_KEY_FILE=secrets/exness-signing-key.pem
EXNESS_INSTRUMENTS=XAUUSD
EXNESS_PRICE_TYPE=bid
EXNESS_ALIGNMENT=utc
```

Put the supplied Ed25519 signing key into the indicated local file. Supported encodings are unencrypted Ed25519 PEM, a 32-byte seed encoded as 64 hex characters, or a 32-byte seed encoded as base64/base64url. Do not generate a new unrelated key and expect it to match an existing issued API key. A different provider secret format requires an explicit documented decoder before activation.

`EXNESS_INSTRUMENTS` must match the account's instrument list exactly, including any suffix. The code verifies membership and obtains `point_digits`; it does not assume that every account uses the same gold symbol or precision.

`EXNESS_ALIGNMENT=utc` is a configuration default, not a confirmed fact about your account's sessions. The parser checks native candle timestamps against it and rejects mismatches without shifting candles. Supported alternatives currently are UTC and 17:00 New York. If the account requires another session convention, extend the alignment model before using its H4/D1/W1/MN data. Weekly/monthly Exness references use native history instead of resampling another provider's daily prices.

## Verify and activate

From the project directory:

```bash
cd backend
.venv/bin/python tools/check_exness.py
```

This probe uses read-only configuration/history requests and a tick subscription, prints candle counts and tick age, and avoids printing keys, signed headers, or account balances. Without credentials it exits with `needs_setup`. A tick receipt alone is not proof the tick is current: inspect its reported age, especially outside market hours.

After a successful probe, restart the existing `./start.sh` process so `.env` is reloaded. Open **Data** to check Exness status, then select the Exness feed from the symbol menu. If an Exness live-capable feed initializes, the app prefers it at the next page initialization. Watch the live status while the market is open. Check 1m, 5m, 1H, 4H and 1D against the broker terminal using the same symbol and price side; confirm timestamps and previous-period levels before relying on parity.

API startup verification covers limits, instrument availability and price precision. Candle-history retrieval and live quote receipt are separate steps; configuration readiness does not itself prove either one.

## Live candles and recovery

The [tick contract](https://www.exness-api.com/reference/get-ws-ticks) can throttle delivery. Therefore sampled ticks update only provisional OHLC and do not invent tick volume. Native candle history periodically replaces those estimates and supplies authoritative volume. At rollover the previous provisional candle is reconciled before another is appended; completion is inferred from the configured boundary with a two-second grace because this schema has no explicit completion flag.

Unexpected disconnects trigger a fresh signed connection and resubscription with backoff. Reconciliation fetches history missed during outages. Authentication/permission/quota rejection stops automatic retry in the affected path; fix the cause and restart. HTTP 429 is not automatically retried. Local rate accounting honors reported global and per-operation quotas, though other clients using the account can consume quotas outside this process.

Exness feed IDs separate account, instrument and bid/ask choice from CSV/OANDA data. Cached feeds belonging to a different account/side are not treated as connected by the current credentials. Native history errors are surfaced; a cached chart does not imply current live connectivity.

## Validation boundary

Contract tests cover Ed25519 verification against the exact transmitted encoded query, key formats, server candle limits, backward/forward history paging, OHLC validation, bid/ask separation, account isolation, provisional updates, rollover reconciliation, rate-limit stopping, and live subscription/disconnect behavior. UI verification confirms missing-credential state and continued CSV access.

Still required from the real account: issued credentials and host, supported account access, authenticated REST and WebSocket responses, actual negative-count paging behavior, session alignment, and terminal/chart parity. No genuine Exness history or live feed is claimed until these checks succeed.

Final local checks on 13 October 2026: **124 backend tests passed**, including 13 Exness contract tests;
**32 frontend tests passed**; frontend lint and production build passed. Browser Data manager showed
Exness selected with the four missing credential settings and the existing CSV feed intact.
The activation probe returned `needs_setup`, as expected. The build retains its existing bundle-size
advisory; test output includes the existing Starlette/httpx deprecation warning.
