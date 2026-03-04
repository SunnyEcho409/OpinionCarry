from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from typing import Any

import httpx

from app.config import Settings
from app.models import PriceSnapshot


class OpinionApiError(RuntimeError):
    """Raised when Opinion API responds with a non-success result."""


class AsyncRateLimiter:
    def __init__(self, rate: int, per_seconds: float = 1.0) -> None:
        self._rate = rate
        self._per_seconds = per_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self._per_seconds:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._rate:
                    self._timestamps.append(now)
                    return

                wait_for = self._per_seconds - (now - self._timestamps[0]) + 0.001
            await asyncio.sleep(wait_for)


class OpinionClient:
    def __init__(self, settings: Settings) -> None:
        self._retry_attempts = max(1, settings.opinion_retry_attempts)
        openapi_base = settings.opinion_http_base.rstrip("/")
        if not openapi_base.endswith("/openapi"):
            openapi_base = f"{openapi_base}/openapi"
        self._client = httpx.AsyncClient(
            base_url=openapi_base,
            headers={"apikey": settings.opinion_api_key},
            timeout=settings.http_timeout_sec,
        )
        self._limiter = AsyncRateLimiter(rate=max(1, settings.opinion_api_rps), per_seconds=1.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts):
            await self._limiter.acquire()
            try:
                response = await self._client.get(path, params=params)
                if response.status_code == 429 and attempt + 1 < self._retry_attempts:
                    await asyncio.sleep(_retry_delay_seconds(response, attempt))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not _is_success(payload):
                    code = payload.get("code")
                    errno = payload.get("errno")
                    message = payload.get("message") or payload.get("msg") or payload.get("errmsg") or "unknown"
                    raise OpinionApiError(f"Opinion API error code={code} errno={errno} message={message}")
                return payload
            except (httpx.HTTPError, OpinionApiError) as exc:
                last_error = exc
                if attempt + 1 >= self._retry_attempts:
                    raise
                await asyncio.sleep(_retry_delay_seconds(None, attempt))

        if last_error:
            raise last_error
        raise OpinionApiError("Unexpected error while calling Opinion API")

    async def list_markets(
        self,
        page: int,
        limit: int,
        status: str | None = None,
        market_type: int = 2,
        sort_order: int | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        params: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "marketType": market_type,
        }
        if status:
            params["status"] = status
        if sort_order is not None:
            params["sortOrder"] = sort_order

        payload = await self._get(
            "/market",
            params,
        )
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        inner = result.get("data")
        if isinstance(inner, dict):
            result = {**inner, **result}
        total = _safe_int(result.get("total"), 0)
        items = result.get("list") or result.get("items") or []
        if not isinstance(items, list):
            items = []
        return total, items

    async def get_latest_price(self, token_id: str) -> PriceSnapshot | None:
        payload = await self._get("/token/latest-price", {"token_id": token_id})
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        nested = result.get("data")
        if isinstance(nested, dict):
            result = {**nested, **result}

        raw_price = _safe_float(result.get("price"), None)
        if raw_price is None:
            raw_price = _safe_float(result.get("latestPrice"), None)
        bid_price = _safe_float(
            result.get("bidPrice")
            or result.get("bid")
            or result.get("bestBidPrice")
            or result.get("bestBid")
            or result.get("bid_price"),
            None,
        )
        ask_price = _safe_float(
            result.get("askPrice")
            or result.get("ask")
            or result.get("bestAskPrice")
            or result.get("bestAsk")
            or result.get("ask_price"),
            None,
        )
        effective_price = bid_price if bid_price is not None else ask_price
        if effective_price is None:
            effective_price = raw_price
        if effective_price is None:
            return None

        return PriceSnapshot(
            token_id=str(token_id),
            price=effective_price,
            timestamp=_safe_int(result.get("timestamp"), None),
            bid=bid_price,
            ask=ask_price,
        )


def _is_success(payload: dict[str, Any]) -> bool:
    code = payload.get("code")
    if code is not None:
        return _safe_int(code, None) == 0
    errno = payload.get("errno")
    if errno is not None:
        return _safe_int(errno, None) == 0
    return False


def _retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                parsed = float(retry_after)
                return max(0.2, min(parsed, 10.0))
            except (TypeError, ValueError):
                pass
    base = min(0.5 * (2**attempt), 8.0)
    return base + random.uniform(0.05, 0.2)


def _safe_int(value: Any, default: int | None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
