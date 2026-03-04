from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class OutcomeRef:
    market_id: int
    market_title: str
    market_type: int
    outcome_id: Optional[int]
    outcome_label: str
    token_id: str
    cutoff_at: int
    url_slug: Optional[str]


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    token_id: str
    price: float
    timestamp: Optional[int]
    bid: Optional[float] = None
    ask: Optional[float] = None


class HoldMarketItem(BaseModel):
    market_id: int
    market_title: str
    market_type: int
    outcome_id: Optional[int]
    outcome_label: str
    token_id: str
    price: float
    cutoff_at: datetime
    seconds_to_expiry: int
    url_slug: Optional[str]


class HoldMarketsResponse(BaseModel):
    updated_at: Optional[datetime]
    count: int
    items: list[HoldMarketItem]
    markets_scanned: int
    outcomes_scanned: int


class OutcomePriceItem(BaseModel):
    outcome_id: Optional[int]
    outcome_label: str
    yes_label: Optional[str]
    no_label: Optional[str]
    outcome_status: Optional[int]
    outcome_status_enum: Optional[str]
    is_resolved: bool
    resolved_label: Optional[str]
    token_id: str
    price: Optional[float]
    bid_price: Optional[float]
    ask_price: Optional[float]
    yes_price: Optional[float]
    no_price: Optional[float]


class MarketItem(BaseModel):
    market_id: int
    market_title: str
    market_type: Optional[int]
    status: Optional[int]
    status_enum: Optional[str]
    thumbnail_url: Optional[str]
    cover_url: Optional[str]
    cutoff_at: Optional[datetime]
    seconds_to_expiry: Optional[int]
    url_slug: Optional[str]
    has_incentive_factor: bool
    outcome_prices: list[OutcomePriceItem]
    child_markets: int
    child_titles: list[str]


class MarketsResponse(BaseModel):
    count: int
    items: list[MarketItem]


@dataclass(frozen=True, slots=True)
class HoldSnapshot:
    updated_at: Optional[datetime]
    items: list[HoldMarketItem]
    markets_scanned: int
    outcomes_scanned: int
