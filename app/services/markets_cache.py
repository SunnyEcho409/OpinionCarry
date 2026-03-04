from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MarketsCache:
    def __init__(self, file_path: str) -> None:
        self._lock = asyncio.Lock()
        self._path = Path(file_path)
        self._markets: list[dict[str, Any]] = []
        self._updated_at: datetime | None = None
        self._loaded_from_disk = False

    async def load_from_disk(self) -> None:
        if not self._path.exists():
            return

        payload = await asyncio.to_thread(self._read_json, self._path)
        if not isinstance(payload, dict):
            return

        markets = payload.get("markets")
        if not isinstance(markets, list):
            return

        updated_at_raw = payload.get("updated_at")
        updated_at: datetime | None = None
        if isinstance(updated_at_raw, str):
            try:
                updated_at = datetime.fromisoformat(updated_at_raw)
            except ValueError:
                updated_at = None

        async with self._lock:
            self._markets = _ensure_markets_list(markets)
            self._updated_at = updated_at
            self._loaded_from_disk = True

    async def update(self, markets: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        normalized = _ensure_markets_list(markets)
        async with self._lock:
            self._markets = normalized
            self._updated_at = now
        await self._persist_to_disk(normalized, now)

    async def read(self) -> tuple[list[dict[str, Any]], datetime | None]:
        async with self._lock:
            return _ensure_markets_list(self._markets), self._updated_at

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "markets_cache_count": len(self._markets),
                "markets_cache_updated_at": self._updated_at.isoformat() if self._updated_at else None,
                "markets_cache_loaded_from_disk": self._loaded_from_disk,
                "markets_cache_file": str(self._path),
            }

    async def _persist_to_disk(self, markets: list[dict[str, Any]], updated_at: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": updated_at.isoformat(),
            "markets": markets,
        }
        await asyncio.to_thread(self._write_json_atomic, self._path, payload)

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=True, separators=(",", ":"))
        tmp.replace(path)


def _ensure_markets_list(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # JSON round-trip guarantees detached, serializable payload.
    return json.loads(json.dumps(markets, ensure_ascii=True))

