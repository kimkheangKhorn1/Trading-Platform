"""Application settings, read from environment variables (optionally via backend/.env).

Credentials are never hard-coded: OANDA access is enabled only when OANDA_API_TOKEN is set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

OANDA_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_path: Path
    oanda_token: str | None
    oanda_account_id: str | None
    oanda_environment: str
    oanda_price_component: str  # "M" (mid), "B" (bid) or "A" (ask)
    oanda_instruments: tuple[str, ...]
    oanda_max_requests_per_second: float
    live_poll_seconds: float
    backtest_workers: int
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    oanda_base_url_override: str | None = None  # testing/proxy only; normally derived from the environment
    market_data_provider: str = "oanda"
    exness_api_key: str | None = field(default=None, repr=False)
    exness_private_key: str | None = field(default=None, repr=False)
    exness_private_key_file: Path | None = field(default=None, repr=False)
    exness_account_id: str | None = field(default=None, repr=False)
    exness_base_url: str | None = None
    exness_price_type: str = "bid"
    exness_alignment: str = "utc"
    exness_instruments: tuple[str, ...] = ("XAUUSD",)

    @property
    def exness_missing(self) -> list[str]:
        values = {"EXN_API_KEY": self.exness_api_key, "EXN_ACCOUNT_ID": self.exness_account_id,
                  "EXN_API_BASE_URL": self.exness_base_url,
                  "EXN_PRIVATE_KEY_FILE or EXN_PRIVATE_KEY": self.exness_private_key_file or self.exness_private_key}
        return [key for key, value in values.items() if not value]

    @property
    def exness_enabled(self) -> bool:
        return self.market_data_provider == "exness" and not self.exness_missing

    @property
    def oanda_enabled(self) -> bool:
        return self.market_data_provider == "oanda" and bool(self.oanda_token)

    @property
    def oanda_base_url(self) -> str:
        return self.oanda_base_url_override or OANDA_HOSTS[self.oanda_environment]


def load_settings() -> Settings:
    provider = os.getenv("MARKET_DATA_PROVIDER", "exness").strip().lower()
    if provider not in ("exness", "oanda"):
        raise ValueError("MARKET_DATA_PROVIDER must be exness or oanda")
    exness_price = os.getenv("EXNESS_PRICE_TYPE", "bid").strip().lower()
    if exness_price not in ("bid", "ask"):
        raise ValueError("EXNESS_PRICE_TYPE must be bid or ask (no synthetic midpoint)")
    alignment = os.getenv("EXNESS_ALIGNMENT", "utc").strip().lower()
    if alignment not in ("utc", "ny17"):
        raise ValueError("EXNESS_ALIGNMENT must match native candles: utc or ny17")
    key_file = os.getenv("EXN_PRIVATE_KEY_FILE")
    private_path = Path(key_file).expanduser() if key_file else None
    if private_path is not None and not private_path.is_absolute():
        private_path = BACKEND_DIR / private_path
    env = os.getenv("OANDA_ENVIRONMENT", "practice").strip().lower()
    if env not in OANDA_HOSTS:
        raise ValueError(f"OANDA_ENVIRONMENT must be one of {sorted(OANDA_HOSTS)}, got {env!r}")
    price = os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper()
    if price not in ("M", "B", "A"):
        raise ValueError("OANDA_PRICE_COMPONENT must be M, B or A")
    db_path = Path(os.getenv("DATABASE_PATH", str(BACKEND_DIR / "data" / "market.db")))
    if not db_path.is_absolute():
        db_path = BACKEND_DIR / db_path
    instruments = tuple(
        s.strip().upper() for s in os.getenv("OANDA_INSTRUMENTS", "XAU_USD").split(",") if s.strip()
    )
    origins = tuple(
        s.strip()
        for s in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if s.strip()
    )
    return Settings(
        database_path=db_path,
        oanda_token=os.getenv("OANDA_API_TOKEN") or None,
        oanda_account_id=os.getenv("OANDA_ACCOUNT_ID") or None,
        oanda_environment=env,
        oanda_price_component=price,
        oanda_instruments=instruments,
        oanda_max_requests_per_second=_env_float("OANDA_MAX_REQUESTS_PER_SECOND", 20.0),
        live_poll_seconds=_env_float("LIVE_POLL_SECONDS", 2.0),
        backtest_workers=_env_int("BACKTEST_WORKERS", 2),
        cors_origins=origins,
        oanda_base_url_override=os.getenv("OANDA_BASE_URL") or None,
        market_data_provider=provider,
        exness_api_key=os.getenv("EXN_API_KEY") or None,
        exness_private_key=os.getenv("EXN_PRIVATE_KEY") or None,
        exness_private_key_file=private_path,
        exness_account_id=os.getenv("EXN_ACCOUNT_ID") or None,
        exness_base_url=os.getenv("EXN_API_BASE_URL") or None,
        exness_price_type=exness_price,
        exness_alignment=alignment,
        exness_instruments=tuple(s.strip() for s in os.getenv("EXNESS_INSTRUMENTS", "XAUUSD").split(",") if s.strip()),
    )
