from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load project .env automatically on process start.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _as_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _as_optional_int(env_name: str) -> int | None:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _as_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    opinion_api_key: str
    opinion_http_base: str
    opinion_ws_url: str
    opinion_ws_channel: str
    opinion_ws_heartbeat_sec: int
    opinion_ws_resync_sec: int
    opinion_ws_enabled: bool
    opinion_api_rps: int
    opinion_retry_attempts: int
    refresh_interval_sec: int
    markets_refresh_interval_sec: int
    price_request_rps: int
    price_threshold: float
    expiry_days: int
    http_timeout_sec: int
    max_concurrent_price_requests: int
    opinion_market_page_size: int
    opinion_market_sort_order: int | None
    opinion_market_status: str | None
    markets_cache_file: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    api_key = os.getenv("OPINION_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPINION_API_KEY is required")

    return Settings(
        opinion_api_key=api_key,
        opinion_http_base=os.getenv("OPINION_HTTP_BASE", "https://openapi.opinion.trade/openapi").rstrip("/"),
        opinion_ws_url=os.getenv("OPINION_WS_URL", "wss://ws.opinion.trade").rstrip("/"),
        opinion_ws_channel=os.getenv("OPINION_WS_CHANNEL", "market.last.price").strip() or "market.last.price",
        opinion_ws_heartbeat_sec=_as_int("OPINION_WS_HEARTBEAT_SEC", 30),
        opinion_ws_resync_sec=_as_int("OPINION_WS_RESYNC_SEC", 5),
        opinion_ws_enabled=os.getenv("OPINION_WS_ENABLED", "1").strip().lower() not in {"0", "false", "no"},
        opinion_api_rps=_as_int("OPINION_API_RPS", 10),
        opinion_retry_attempts=_as_int("OPINION_RETRY_ATTEMPTS", 3),
        refresh_interval_sec=_as_int("REFRESH_INTERVAL_SEC", 30),
        markets_refresh_interval_sec=_as_int("MARKETS_REFRESH_INTERVAL_SEC", 1800),
        price_request_rps=_as_int("PRICE_REQUEST_RPS", 10),
        price_threshold=_as_float("PRICE_THRESHOLD", 0.90),
        expiry_days=_as_int("EXPIRY_DAYS", 10),
        http_timeout_sec=_as_int("HTTP_TIMEOUT_SEC", 10),
        max_concurrent_price_requests=max(1, _as_int("MAX_CONCURRENT_PRICE_REQUESTS", 10)),
        opinion_market_page_size=_as_int("OPINION_MARKET_PAGE_SIZE", 20),
        opinion_market_sort_order=_as_optional_int("OPINION_MARKET_SORT_ORDER"),
        opinion_market_status=(os.getenv("OPINION_MARKET_STATUS", "").strip() or None),
        markets_cache_file=os.getenv("MARKETS_CACHE_FILE", str(PROJECT_ROOT / "data" / "markets_cache.json")),
    )
