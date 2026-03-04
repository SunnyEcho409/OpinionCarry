from __future__ import annotations

import asyncio
from datetime import datetime

from app.models import HoldMarketItem, HoldSnapshot


class HoldCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot = HoldSnapshot(
            updated_at=None,
            items=[],
            markets_scanned=0,
            outcomes_scanned=0,
        )

    async def update(
        self,
        *,
        updated_at: datetime,
        items: list[HoldMarketItem],
        markets_scanned: int,
        outcomes_scanned: int,
    ) -> None:
        async with self._lock:
            self._snapshot = HoldSnapshot(
                updated_at=updated_at,
                items=items,
                markets_scanned=markets_scanned,
                outcomes_scanned=outcomes_scanned,
            )

    async def read(self) -> HoldSnapshot:
        async with self._lock:
            # Return detached copies so request handlers cannot mutate cached state.
            return HoldSnapshot(
                updated_at=self._snapshot.updated_at,
                items=[item.model_copy(deep=True) for item in self._snapshot.items],
                markets_scanned=self._snapshot.markets_scanned,
                outcomes_scanned=self._snapshot.outcomes_scanned,
            )

