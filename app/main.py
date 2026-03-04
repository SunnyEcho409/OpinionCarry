from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query

from app.clients.opinion_client import OpinionClient
from app.config import Settings, get_settings
from app.jobs.refresh_job import RefreshJob
from app.models import HoldMarketsResponse, MarketItem, MarketsResponse, OutcomePriceItem, PriceSnapshot
from app.services.cache import HoldCache
from app.services.market_collector import MarketCollector
from app.services.markets_cache import MarketsCache
from app.services.ws_price_stream import WsPriceStream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

MAX_REST_PRICE_BACKFILL_PER_REQUEST = 120


def create_app() -> FastAPI:
    settings = get_settings()
    cache = HoldCache()
    markets_cache = MarketsCache(settings.markets_cache_file)
    opinion_client = OpinionClient(settings)
    ws_price_stream = WsPriceStream(settings)
    collector = MarketCollector(
        client=opinion_client,
        market_page_size=settings.opinion_market_page_size,
        market_sort_order=settings.opinion_market_sort_order,
        market_status=settings.opinion_market_status,
        price_request_rps=settings.price_request_rps,
        max_concurrent_price_requests=settings.max_concurrent_price_requests,
    )
    refresh_job = RefreshJob(
        settings=settings,
        collector=collector,
        cache=cache,
        markets_cache=markets_cache,
        ws_price_stream=ws_price_stream,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.cache = cache
        app.state.markets_cache = markets_cache
        app.state.refresh_job = refresh_job
        app.state.opinion_client = opinion_client
        app.state.ws_price_stream = ws_price_stream
        app.state.collector = collector
        app.state.price_backfill_lock = asyncio.Lock()
        app.state.price_backfill_inflight_tokens = set()

        await markets_cache.load_from_disk()
        await ws_price_stream.start()
        await refresh_job.start()
        try:
            yield
        finally:
            await refresh_job.stop()
            await ws_price_stream.stop()
            await opinion_client.close()

    app = FastAPI(
        title="Opinion Hold Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/debug/refresh")
    async def debug_refresh() -> dict[str, object]:
        refresh = app.state.refresh_job.status()
        cache_state = await app.state.markets_cache.status()
        return {**refresh, **cache_state}

    @app.get("/markets", response_model=MarketsResponse)
    @app.get("/markets/all", response_model=MarketsResponse)
    async def get_all_markets(
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        only_active: bool = Query(default=True),
    ) -> MarketsResponse:
        markets, _updated_at = await app.state.markets_cache.read()
        now_ts = int(time.time())
        filtered_markets = [m for m in markets if _is_active_market(m)] if only_active else markets
        total = len(filtered_markets)
        page_markets = filtered_markets[offset : offset + limit]
        token_ids = _extract_market_token_ids(page_markets)
        prices_by_token = await app.state.ws_price_stream.get_prices(token_ids)
        missing_tokens = token_ids - set(prices_by_token.keys())
        if missing_tokens:
            backfill_tokens = set(sorted(missing_tokens)[:MAX_REST_PRICE_BACKFILL_PER_REQUEST])
            asyncio.create_task(_backfill_missing_prices(app, backfill_tokens))
        rows = [
            _to_market_item(raw, now_ts=now_ts, prices_by_token=prices_by_token)
            for raw in page_markets
        ]
        return MarketsResponse(count=total, items=rows)

    @app.get("/markets/hold", response_model=HoldMarketsResponse)
    async def get_hold_markets(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> HoldMarketsResponse:
        snapshot = await app.state.cache.read()
        page_items = snapshot.items[offset : offset + limit]
        return HoldMarketsResponse(
            updated_at=snapshot.updated_at,
            count=len(snapshot.items),
            items=page_items,
            markets_scanned=snapshot.markets_scanned,
            outcomes_scanned=snapshot.outcomes_scanned,
        )

    return app


app: FastAPI = create_app()


def get_app_settings(app_instance: FastAPI) -> Settings:
    return app_instance.state.settings


def _to_market_item(
    market: dict[str, Any],
    *,
    now_ts: int,
    prices_by_token: dict[str, PriceSnapshot],
) -> MarketItem:
    market_id = _safe_int(market.get("marketId"), 0) or 0
    cutoff_ts = _normalize_timestamp(market.get("cutoffAt"))
    cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc) if cutoff_ts else None
    seconds_to_expiry = cutoff_ts - now_ts if cutoff_ts else None
    if seconds_to_expiry is not None and seconds_to_expiry < 0:
        seconds_to_expiry = 0

    slug = _extract_slug(market)
    children = market.get("childMarkets")
    child_count = len(children) if isinstance(children, list) else 0
    child_titles = _extract_child_titles(market)
    has_incentive_factor = _has_incentive_factor(market)
    outcome_prices = _extract_outcome_prices(market, prices_by_token=prices_by_token)

    return MarketItem(
        market_id=market_id,
        market_title=str(market.get("marketTitle") or "").strip(),
        market_type=_safe_int(market.get("marketType"), None),
        status=_safe_int(market.get("status"), None),
        status_enum=_safe_str(market.get("statusEnum")),
        thumbnail_url=_extract_image_url(market.get("thumbnailUrl")),
        cover_url=_extract_image_url(market.get("coverUrl")),
        cutoff_at=cutoff_dt,
        seconds_to_expiry=seconds_to_expiry,
        url_slug=slug,
        has_incentive_factor=has_incentive_factor,
        outcome_prices=outcome_prices,
        child_markets=child_count,
        child_titles=child_titles,
    )


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


def _extract_image_url(value: Any) -> str | None:
    url = _safe_str(value)
    if not url:
        return None
    return url


def _extract_child_titles(payload: dict[str, Any]) -> list[str]:
    children = payload.get("childMarkets")
    if not isinstance(children, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for child in children:
        if not isinstance(child, dict):
            continue
        title = _safe_str(child.get("marketTitle")) or _safe_str(child.get("yesLabel"))
        if not title:
            child_id = _safe_int(child.get("marketId"), None)
            if child_id is None:
                continue
            title = f"Outcome {child_id}"
        if title in seen:
            continue
        seen.add(title)
        result.append(title)
    return result


def _has_incentive_factor(payload: dict[str, Any]) -> bool:
    if payload.get("incentiveFactor") is not None:
        return True

    children = payload.get("childMarkets")
    if not isinstance(children, list):
        return False

    for child in children:
        if isinstance(child, dict) and child.get("incentiveFactor") is not None:
            return True
    return False


def _extract_market_token_ids(markets: list[dict[str, Any]]) -> set[str]:
    token_ids: set[str] = set()
    for market in markets:
        for (
            _outcome_id,
            _label,
            yes_token_id,
            no_token_id,
            _yes_label,
            _no_label,
            _status,
            _status_enum,
            _is_resolved,
            _resolved_label,
        ) in _extract_market_outcomes(market):
            if yes_token_id:
                token_ids.add(yes_token_id)
            if no_token_id:
                token_ids.add(no_token_id)
    return token_ids


def _extract_outcome_prices(
    market: dict[str, Any],
    *,
    prices_by_token: dict[str, PriceSnapshot],
) -> list[OutcomePriceItem]:
    items: list[OutcomePriceItem] = []
    for (
        outcome_id,
        outcome_label,
        yes_token_id,
        no_token_id,
        yes_label,
        no_label,
        status,
        status_enum,
        is_resolved,
        resolved_label,
    ) in _extract_market_outcomes(market):
        yes_snapshot = prices_by_token.get(yes_token_id) if yes_token_id else None
        no_snapshot = prices_by_token.get(no_token_id) if no_token_id else None
        yes_price = _effective_price(yes_snapshot)
        no_price = _effective_price(no_snapshot)
        token_id = yes_token_id or no_token_id or ""

        fallback_last = yes_price if yes_price is not None else no_price
        items.append(
            OutcomePriceItem(
                outcome_id=outcome_id,
                outcome_label=outcome_label,
                yes_label=yes_label,
                no_label=no_label,
                outcome_status=status,
                outcome_status_enum=status_enum,
                is_resolved=is_resolved,
                resolved_label=resolved_label,
                token_id=token_id,
                price=fallback_last,
                bid_price=yes_price,
                ask_price=None,
                yes_price=yes_price,
                no_price=no_price,
            )
        )
    return items


def _extract_market_outcomes(
    payload: dict[str, Any],
) -> list[
    tuple[
        int | None,
        str,
        str | None,
        str | None,
        str | None,
        str | None,
        int | None,
        str | None,
        bool,
        str | None,
    ]
]:
    children = payload.get("childMarkets")
    market_type = _safe_int(payload.get("marketType"), None)
    is_categorical = isinstance(children, list) and ((market_type == 1) or len(children) > 0)

    if is_categorical:
        results: list[
            tuple[
                int | None,
                str,
                str | None,
                str | None,
                str | None,
                str | None,
                int | None,
                str | None,
                bool,
                str | None,
            ]
        ] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            yes_token_id = (
                _safe_str(child.get("yesTokenId"))
                or _safe_str(child.get("tokenId"))
                or _safe_str(child.get("resultTokenId"))
            )
            no_token_id = _safe_str(child.get("noTokenId"))
            if yes_token_id is None and no_token_id is None:
                continue
            outcome_id = _safe_int(child.get("marketId"), None)
            yes_label = _safe_str(child.get("yesLabel")) or "YES"
            no_label = _safe_str(child.get("noLabel")) or "NO"
            status = _safe_int(child.get("status"), None)
            status_enum = _safe_str(child.get("statusEnum"))
            resolved_label = _resolve_outcome_result(
                status=status,
                status_enum=status_enum,
                result_token_id=_safe_str(child.get("resultTokenId")),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                yes_label=yes_label,
                no_label=no_label,
            )
            outcome_label = (
                _safe_str(child.get("marketTitle"))
                or yes_label
                or (f"Outcome {outcome_id}" if outcome_id is not None else "Outcome")
            )
            results.append(
                (
                    outcome_id,
                    outcome_label,
                    yes_token_id,
                    no_token_id,
                    yes_label,
                    no_label,
                    status,
                    status_enum,
                    resolved_label is not None,
                    resolved_label,
                )
            )
        return results

    results: list[
        tuple[
            int | None,
            str,
            str | None,
            str | None,
            str | None,
            str | None,
            int | None,
            str | None,
            bool,
            str | None,
        ]
    ] = []
    yes_token = _safe_str(payload.get("yesTokenId"))
    no_token = _safe_str(payload.get("noTokenId"))
    yes_label = _safe_str(payload.get("yesLabel"))
    no_label = _safe_str(payload.get("noLabel"))
    status = _safe_int(payload.get("status"), None)
    status_enum = _safe_str(payload.get("statusEnum"))

    # Binary markets (team A vs team B, up vs down, etc):
    # one row with explicit team/side labels for chips.
    if yes_token is not None or no_token is not None:
        resolved_yes_label = yes_label or "YES"
        resolved_no_label = no_label or "NO"
        resolved_label = _resolve_outcome_result(
            status=status,
            status_enum=status_enum,
            result_token_id=_safe_str(payload.get("resultTokenId")),
            yes_token_id=yes_token,
            no_token_id=no_token,
            yes_label=resolved_yes_label,
            no_label=resolved_no_label,
        )
        outcome_label = _safe_str(payload.get("marketTitle")) or f"{resolved_yes_label} vs {resolved_no_label}"
        results.append(
            (
                None,
                outcome_label,
                yes_token,
                no_token,
                resolved_yes_label,
                resolved_no_label,
                status,
                status_enum,
                resolved_label is not None,
                resolved_label,
            )
        )
    return results


async def _backfill_missing_prices(app: FastAPI, token_ids: set[str]) -> None:
    if not token_ids:
        return

    lock: asyncio.Lock = app.state.price_backfill_lock
    inflight: set[str] = app.state.price_backfill_inflight_tokens
    async with lock:
        targets = {token for token in token_ids if token not in inflight}
        if not targets:
            return
        inflight.update(targets)

    try:
        rest_prices = await app.state.collector.fetch_latest_prices(targets)
        if rest_prices:
            await app.state.ws_price_stream.upsert_prices(rest_prices)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Price backfill failed err=%s", exc)
    finally:
        async with lock:
            inflight.difference_update(targets)


def _is_active_market(payload: dict[str, Any]) -> bool:
    status_enum = _safe_str(payload.get("statusEnum"))
    if status_enum is not None:
        return status_enum.casefold() == "activated"
    status = _safe_int(payload.get("status"), None)
    return status == 2


def _effective_price(snapshot: PriceSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    if snapshot.bid is not None:
        return snapshot.bid
    if snapshot.ask is not None:
        return snapshot.ask
    return snapshot.price


def _resolve_outcome_result(
    *,
    status: int | None,
    status_enum: str | None,
    result_token_id: str | None,
    yes_token_id: str | None,
    no_token_id: str | None,
    yes_label: str,
    no_label: str,
) -> str | None:
    is_resolved = (status_enum is not None and status_enum.casefold() == "resolved") or status == 4
    if not is_resolved:
        return None

    if result_token_id is not None:
        if yes_token_id is not None and result_token_id == yes_token_id:
            return yes_label
        if no_token_id is not None and result_token_id == no_token_id:
            return no_label
    return "Resolved"
