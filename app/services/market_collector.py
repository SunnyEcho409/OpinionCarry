from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from typing import Any

from app.clients.opinion_client import OpinionClient
from app.models import OutcomeRef, PriceSnapshot

logger = logging.getLogger(__name__)


class MarketCollector:
    def __init__(
        self,
        *,
        client: OpinionClient,
        market_page_size: int,
        market_sort_order: int | None,
        market_status: str | None,
        price_request_rps: int,
        max_concurrent_price_requests: int,
    ) -> None:
        self._client = client
        self._market_page_size = market_page_size
        self._market_sort_order = market_sort_order
        self._market_status = market_status
        self._price_request_rps = max(1, price_request_rps)
        self._max_concurrent_price_requests = max(1, max_concurrent_price_requests)
        self._price_limiter = AsyncRateLimiter(rate=self._price_request_rps, per_seconds=1.0)

    async def fetch_all_markets(self) -> list[dict[str, Any]]:
        page = 1
        total = 0
        markets: list[dict[str, Any]] = []

        while True:
            current_total, page_items = await self._client.list_markets(
                page=page,
                limit=self._market_page_size,
                status=self._market_status,
                market_type=2,
                sort_order=self._market_sort_order,
            )
            total = max(total, current_total)
            markets.extend(page_items)

            if not page_items:
                break
            if total and len(markets) >= total:
                break
            if len(page_items) < self._market_page_size:
                break

            page += 1
            # Guard against impossible pagination data from upstream.
            if total and page > math.ceil(total / self._market_page_size) + 2:
                break

        return markets

    def extract_outcomes(self, markets: list[dict[str, Any]]) -> list[OutcomeRef]:
        outcomes: list[OutcomeRef] = []

        for market in markets:
            market_id = _safe_int(market.get("marketId"), None)
            market_title = str(market.get("marketTitle", "")).strip()
            market_type = _safe_int(market.get("marketType"), 0)
            root_cutoff = _normalize_timestamp(market.get("cutoffAt"))
            url_slug = _extract_slug(market)

            if market_id is None or root_cutoff is None or not market_title:
                continue

            if market_type == 1:
                child_markets = market.get("childMarkets")
                if isinstance(child_markets, list):
                    outcomes.extend(
                        self._extract_categorical_outcomes(
                            parent_market_id=market_id,
                            parent_title=market_title,
                            parent_cutoff=root_cutoff,
                            parent_slug=url_slug,
                            child_markets=child_markets,
                        )
                    )
                continue

            outcomes.extend(
                self._extract_binary_outcomes(
                    market_id=market_id,
                    market_title=market_title,
                    market_type=market_type,
                    cutoff_at=root_cutoff,
                    url_slug=url_slug,
                    market=market,
                )
            )

        return outcomes

    def _extract_binary_outcomes(
        self,
        *,
        market_id: int,
        market_title: str,
        market_type: int,
        cutoff_at: int,
        url_slug: str | None,
        market: dict[str, Any],
    ) -> list[OutcomeRef]:
        rows: list[OutcomeRef] = []
        yes_token = _safe_str(market.get("yesTokenId"))
        no_token = _safe_str(market.get("noTokenId"))

        if yes_token:
            rows.append(
                OutcomeRef(
                    market_id=market_id,
                    market_title=market_title,
                    market_type=market_type,
                    outcome_id=None,
                    outcome_label=str(market.get("yesLabel", "YES")),
                    token_id=yes_token,
                    cutoff_at=cutoff_at,
                    url_slug=url_slug,
                )
            )
        if no_token:
            rows.append(
                OutcomeRef(
                    market_id=market_id,
                    market_title=market_title,
                    market_type=market_type,
                    outcome_id=None,
                    outcome_label=str(market.get("noLabel", "NO")),
                    token_id=no_token,
                    cutoff_at=cutoff_at,
                    url_slug=url_slug,
                )
            )

        return rows

    def _extract_categorical_outcomes(
        self,
        *,
        parent_market_id: int,
        parent_title: str,
        parent_cutoff: int,
        parent_slug: str | None,
        child_markets: list[dict[str, Any]],
    ) -> list[OutcomeRef]:
        rows: list[OutcomeRef] = []

        for child in child_markets:
            child_id = _safe_int(child.get("marketId"), None)
            if child_id is None:
                continue

            # Most categorical markets expose a YES token per child.
            token_id = (
                _safe_str(child.get("tokenId"))
                or _safe_str(child.get("yesTokenId"))
                or _safe_str(child.get("resultTokenId"))
            )
            if token_id is None:
                continue

            label = str(child.get("marketTitle") or child.get("yesLabel") or f"Outcome {child_id}")
            child_cutoff = _normalize_timestamp(child.get("cutoffAt")) or parent_cutoff

            rows.append(
                OutcomeRef(
                    market_id=parent_market_id,
                    market_title=parent_title,
                    market_type=1,
                    outcome_id=child_id,
                    outcome_label=label,
                    token_id=token_id,
                    cutoff_at=child_cutoff,
                    url_slug=parent_slug,
                )
            )

        return rows

    async def fetch_latest_prices(self, token_ids: set[str]) -> dict[str, PriceSnapshot]:
        if not token_ids:
            return {}

        prices: dict[str, PriceSnapshot] = {}
        sem = asyncio.Semaphore(self._max_concurrent_price_requests)

        async def _fetch(token_id: str) -> None:
            async with sem:
                await self._price_limiter.acquire()
                try:
                    price = await self._client.get_latest_price(token_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to fetch token price token_id=%s err=%s", token_id, exc)
                    return
                if price:
                    prices[token_id] = price

        tasks = [asyncio.create_task(_fetch(token)) for token in token_ids]
        await asyncio.gather(*tasks)
        return prices


def _safe_int(value: Any, default: int | None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_timestamp(value: Any) -> int | None:
    ts = _safe_int(value, None)
    if ts is None:
        return None
    # Handle millisecond timestamps from upstream variants.
    if ts > 10_000_000_000:
        ts = ts // 1000
    return ts


def _extract_slug(payload: dict[str, Any]) -> str | None:
    for key in ("slug", "marketSlug", "questionSlug", "questionId"):
        slug = _safe_str(payload.get(key))
        if slug:
            return slug.strip("/")

    question_obj = payload.get("question")
    if isinstance(question_obj, dict):
        for key in ("slug", "questionSlug", "id"):
            slug = _safe_str(question_obj.get(key))
            if slug:
                return slug.strip("/")

    return None


class AsyncRateLimiter:
    def __init__(self, rate: int, per_seconds: float = 1.0) -> None:
        self._rate = max(1, rate)
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
