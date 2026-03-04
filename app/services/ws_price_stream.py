from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import websockets

from app.config import Settings
from app.models import PriceSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Subscription:
    key: str
    value: int

    def payload(self, *, action: str, channel: str) -> dict[str, Any]:
        return {"action": action, "channel": channel, self.key: self.value}


class WsPriceStream:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.opinion_ws_enabled
        self._api_key = settings.opinion_api_key
        self._ws_url = settings.opinion_ws_url
        self._channel = settings.opinion_ws_channel
        self._heartbeat_sec = max(10, settings.opinion_ws_heartbeat_sec)
        self._resync_sec = max(1, settings.opinion_ws_resync_sec)

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

        self._desired_lock = asyncio.Lock()
        self._desired_subscriptions: set[Subscription] = set()
        self._active_subscriptions: set[Subscription] = set()

        self._prices_lock = asyncio.Lock()
        self._prices: dict[str, PriceSnapshot] = {}

        self._connected = False
        self._last_error: str | None = None
        self._last_message_at: datetime | None = None
        self._message_count = 0
        self._connect_count = 0

    async def start(self) -> None:
        if not self._enabled:
            logger.info("WS price stream is disabled by config")
            return
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="opinion-ws-price-stream")

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
            self._connected = False

    async def set_desired_from_markets(self, markets: list[dict[str, Any]]) -> None:
        desired = _build_subscriptions_from_markets(markets)
        async with self._desired_lock:
            self._desired_subscriptions = desired

    async def get_prices(self, token_ids: set[str]) -> dict[str, PriceSnapshot]:
        if not token_ids:
            return {}
        async with self._prices_lock:
            return {
                token_id: snapshot
                for token_id, snapshot in self._prices.items()
                if token_id in token_ids
            }

    async def upsert_prices(self, snapshots: dict[str, PriceSnapshot]) -> None:
        if not snapshots:
            return
        async with self._prices_lock:
            self._prices.update(snapshots)

    def status(self) -> dict[str, str | int | bool | None]:
        return {
            "ws_enabled": self._enabled,
            "ws_connected": self._connected,
            "ws_channel": self._channel,
            "ws_connect_count": self._connect_count,
            "ws_messages": self._message_count,
            "ws_last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "ws_last_error": self._last_error,
            "ws_active_subscriptions": len(self._active_subscriptions),
            "ws_desired_subscriptions": len(self._desired_subscriptions),
            "ws_cached_tokens": len(self._prices),
        }

    async def _run_loop(self) -> None:
        reconnect_delay = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_stream()
                reconnect_delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self._last_error = str(exc)
                logger.warning("WS stream disconnected err=%s", exc)
                await _wait_or_stop(self._stop_event, reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0) + random.uniform(0.05, 0.2)

    async def _connect_and_stream(self) -> None:
        uri = _ws_uri(self._ws_url, self._api_key)
        async with websockets.connect(uri, ping_interval=None) as ws:
            self._connect_count += 1
            self._connected = True
            self._last_error = None
            self._active_subscriptions = set()
            await self._sync_subscriptions(ws)
            last_sync = time.monotonic()
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
            try:
                while not self._stop_event.is_set():
                    now = time.monotonic()
                    if now - last_sync >= self._resync_sec:
                        await self._sync_subscriptions(ws)
                        last_sync = now

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    self._message_count += 1
                    self._last_message_at = datetime.now(timezone.utc)
                    await self._handle_raw_message(raw)
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._connected = False

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._heartbeat_sec)
            await ws.send(json.dumps({"action": "HEARTBEAT"}))

    async def _sync_subscriptions(self, ws: websockets.WebSocketClientProtocol) -> None:
        async with self._desired_lock:
            desired = set(self._desired_subscriptions)
        to_subscribe = sorted(desired - self._active_subscriptions, key=lambda x: (x.key, x.value))
        to_unsubscribe = sorted(self._active_subscriptions - desired, key=lambda x: (x.key, x.value))

        for sub in to_unsubscribe:
            await ws.send(json.dumps(sub.payload(action="UNSUBSCRIBE", channel=self._channel)))
            self._active_subscriptions.discard(sub)

        sent = 0
        for sub in to_subscribe:
            await ws.send(json.dumps(sub.payload(action="SUBSCRIBE", channel=self._channel)))
            self._active_subscriptions.add(sub)
            sent += 1
            if sent % 200 == 0:
                await asyncio.sleep(0.05)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = raw
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return

        for event in _extract_events(payload):
            msg_type = str(event.get("msgType") or event.get("channel") or "").strip()
            if msg_type and msg_type != self._channel:
                continue

            data = event.get("data")
            if isinstance(data, dict):
                source = {**event, **data}
            else:
                source = event

            token_id = source.get("tokenId") or source.get("token_id")
            if token_id is None:
                continue

            raw_price = _safe_float(source.get("price"), None)
            if raw_price is None:
                raw_price = _safe_float(source.get("latestPrice"), None)
            raw_bid = (
                source.get("bidPrice")
                or source.get("bid")
                or source.get("bestBidPrice")
                or source.get("bestBid")
                or source.get("bid_price")
            )
            raw_ask = (
                source.get("askPrice")
                or source.get("ask")
                or source.get("bestAskPrice")
                or source.get("bestAsk")
                or source.get("ask_price")
            )
            bid = _safe_float(raw_bid, None)
            ask = _safe_float(raw_ask, None)
            effective_price = bid if bid is not None else ask
            if effective_price is None:
                effective_price = raw_price
            if effective_price is None:
                continue

            try:
                snapshot = PriceSnapshot(
                    token_id=str(token_id),
                    price=effective_price,
                    timestamp=_safe_int(source.get("timestamp"), None),
                    bid=bid,
                    ask=ask,
                )
            except (TypeError, ValueError):
                continue

            async with self._prices_lock:
                self._prices[snapshot.token_id] = snapshot


def _build_subscriptions_from_markets(markets: list[dict[str, Any]]) -> set[Subscription]:
    subscriptions: set[Subscription] = set()
    for market in markets:
        market_id = _safe_int(market.get("marketId"), None)
        if market_id is None:
            continue

        children = market.get("childMarkets")
        market_type = _safe_int(market.get("marketType"), None)
        is_categorical_root = (isinstance(children, list) and len(children) > 0) or market_type == 1
        if is_categorical_root:
            subscriptions.add(Subscription(key="rootMarketId", value=market_id))
        else:
            subscriptions.add(Subscription(key="marketId", value=market_id))
    return subscriptions


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


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


def _ws_uri(base_url: str, api_key: str) -> str:
    query = urlencode({"apikey": api_key})
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{query}"


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
