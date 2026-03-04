from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.models import OutcomeRef
from app.services.cache import HoldCache
from app.services.hold_filter import build_hold_items
from app.services.market_collector import MarketCollector
from app.services.markets_cache import MarketsCache
from app.services.ws_price_stream import WsPriceStream

logger = logging.getLogger(__name__)


class RefreshJob:
    def __init__(
        self,
        *,
        settings: Settings,
        collector: MarketCollector,
        cache: HoldCache,
        markets_cache: MarketsCache,
        ws_price_stream: WsPriceStream,
    ) -> None:
        self._settings = settings
        self._collector = collector
        self._cache = cache
        self._markets_cache = markets_cache
        self._ws_price_stream = ws_price_stream
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_markets_refresh_at: datetime | None = None
        self._last_error: str | None = None
        self._last_stats: dict[str, Any] = {
            "markets_scanned": 0,
            "markets_refreshed": 0,
            "outcomes_scanned": 0,
            "future_outcomes": 0,
            "future_min_seconds": None,
            "future_max_seconds": None,
            "candidate_outcomes": 0,
            "candidate_min_seconds": None,
            "candidate_max_seconds": None,
            "tokens_requested": 0,
            "prices_received": 0,
            "ws_prices": 0,
            "rest_fallback_prices": 0,
            "hold_items": 0,
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="opinion-refresh-job")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        failures = 0
        while not self._stop_event.is_set():
            try:
                await self.refresh_once()
                failures = 0
                self._last_error = None
                sleep_for = self._settings.refresh_interval_sec
            except Exception as exc:  # noqa: BLE001
                failures += 1
                sleep_for = min(self._settings.refresh_interval_sec * (2**failures), 300)
                self._last_error = str(exc)
                logger.exception("Refresh loop failed err=%s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def refresh_once(self) -> None:
        async with self._run_lock:
            self._last_attempt_at = datetime.now(timezone.utc)
            markets, markets_cache_updated_at = await self._markets_cache.read()
            markets_refreshed = False
            if _is_markets_refresh_due(
                markets,
                updated_at=markets_cache_updated_at,
                now_dt=self._last_attempt_at,
                refresh_interval_sec=self._settings.markets_refresh_interval_sec,
            ):
                markets = await self._collector.fetch_all_markets()
                await self._markets_cache.update(markets)
                markets_refreshed = True
                self._last_markets_refresh_at = datetime.now(timezone.utc)
            elif markets_cache_updated_at is not None:
                self._last_markets_refresh_at = markets_cache_updated_at

            outcomes = self._collector.extract_outcomes(markets)
            now_ts = int(time.time())
            future_outcomes = [outcome for outcome in outcomes if outcome.cutoff_at > now_ts]
            candidate_outcomes = _prefilter_by_expiry(
                outcomes,
                now_ts=now_ts,
                expiry_days=self._settings.expiry_days,
            )
            tokens = {outcome.token_id for outcome in candidate_outcomes}
            await self._ws_price_stream.set_desired_from_markets(markets)
            prices = await self._ws_price_stream.get_prices(tokens)
            missing_tokens = tokens - set(prices.keys())
            rest_prices: dict[str, Any] = {}
            if missing_tokens:
                rest_prices = await self._collector.fetch_latest_prices(missing_tokens)
                prices.update(rest_prices)
                await self._ws_price_stream.upsert_prices(rest_prices)

            items = build_hold_items(
                candidate_outcomes,
                prices,
                now_ts=now_ts,
                expiry_days=self._settings.expiry_days,
                price_threshold=self._settings.price_threshold,
            )
            now_dt = datetime.now(timezone.utc)
            self._last_success_at = now_dt
            self._last_stats = {
                "markets_scanned": len(markets),
                "markets_refreshed": 1 if markets_refreshed else 0,
                "outcomes_scanned": len(outcomes),
                "future_outcomes": len(future_outcomes),
                "future_min_seconds": _min_seconds_to_expiry(future_outcomes, now_ts=now_ts),
                "future_max_seconds": _max_seconds_to_expiry(future_outcomes, now_ts=now_ts),
                "candidate_outcomes": len(candidate_outcomes),
                "candidate_min_seconds": _min_seconds_to_expiry(candidate_outcomes, now_ts=now_ts),
                "candidate_max_seconds": _max_seconds_to_expiry(candidate_outcomes, now_ts=now_ts),
                "tokens_requested": len(tokens),
                "prices_received": len(prices),
                "ws_prices": len(prices) - len(rest_prices) if missing_tokens else len(prices),
                "rest_fallback_prices": len(rest_prices) if missing_tokens else 0,
                "hold_items": len(items),
            }
            await self._cache.update(
                updated_at=now_dt,
                items=items,
                markets_scanned=len(markets),
                outcomes_scanned=len(outcomes),
            )
            logger.info(
                "Snapshot refreshed markets=%s refreshed=%s outcomes=%s future=%s candidates=%s token_req=%s ws_prices=%s rest_prices=%s hold_items=%s",
                len(markets),
                markets_refreshed,
                len(outcomes),
                len(future_outcomes),
                len(candidate_outcomes),
                len(tokens),
                self._last_stats["ws_prices"],
                self._last_stats["rest_fallback_prices"],
                len(items),
            )

    def status(self) -> dict[str, Any]:
        # markets_cache.status() is async; keep status cheap/sync here.
        return {
            "last_attempt_at": self._last_attempt_at.isoformat() if self._last_attempt_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_markets_refresh_at": self._last_markets_refresh_at.isoformat() if self._last_markets_refresh_at else None,
            "last_error": self._last_error,
            **self._last_stats,
            **self._ws_price_stream.status(),
        }


def _prefilter_by_expiry(
    outcomes: list[OutcomeRef],
    *,
    now_ts: int,
    expiry_days: int,
) -> list[OutcomeRef]:
    max_age_seconds = expiry_days * 24 * 60 * 60
    deadline_ts = now_ts + max_age_seconds
    return [
        outcome
        for outcome in outcomes
        if outcome.cutoff_at > now_ts and outcome.cutoff_at <= deadline_ts
    ]


def _min_seconds_to_expiry(outcomes: list[OutcomeRef], *, now_ts: int) -> int | None:
    if not outcomes:
        return None
    return min(outcome.cutoff_at - now_ts for outcome in outcomes)


def _max_seconds_to_expiry(outcomes: list[OutcomeRef], *, now_ts: int) -> int | None:
    if not outcomes:
        return None
    return max(outcome.cutoff_at - now_ts for outcome in outcomes)


def _is_markets_refresh_due(
    markets: list[dict[str, Any]],
    *,
    updated_at: datetime | None,
    now_dt: datetime,
    refresh_interval_sec: int,
) -> bool:
    if not markets:
        return True
    if updated_at is None:
        return True

    updated_at_ts = int(updated_at.timestamp())
    now_ts = int(now_dt.timestamp())
    return (now_ts - updated_at_ts) >= max(1, refresh_interval_sec)
