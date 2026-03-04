from __future__ import annotations

from datetime import datetime, timezone

from app.models import HoldMarketItem, OutcomeRef, PriceSnapshot


def build_hold_items(
    outcomes: list[OutcomeRef],
    prices_by_token: dict[str, PriceSnapshot],
    *,
    now_ts: int,
    expiry_days: int,
    price_threshold: float,
) -> list[HoldMarketItem]:
    max_age_seconds = expiry_days * 24 * 60 * 60
    deadline_ts = now_ts + max_age_seconds

    items: list[HoldMarketItem] = []
    for outcome in outcomes:
        if outcome.cutoff_at <= now_ts or outcome.cutoff_at > deadline_ts:
            continue

        price = prices_by_token.get(outcome.token_id)
        if price is None:
            continue
        price_value = _effective_price(price)
        if price_value is None:
            continue
        if price_value < price_threshold:
            continue

        items.append(
            HoldMarketItem(
                market_id=outcome.market_id,
                market_title=outcome.market_title,
                market_type=outcome.market_type,
                outcome_id=outcome.outcome_id,
                outcome_label=outcome.outcome_label,
                token_id=outcome.token_id,
                price=price_value,
                cutoff_at=datetime.fromtimestamp(outcome.cutoff_at, tz=timezone.utc),
                seconds_to_expiry=outcome.cutoff_at - now_ts,
                url_slug=outcome.url_slug,
            )
        )

    items.sort(key=lambda item: (item.seconds_to_expiry, -item.price, item.market_id))
    return items


def _effective_price(snapshot: PriceSnapshot) -> float | None:
    if snapshot.bid is not None:
        return snapshot.bid
    if snapshot.ask is not None:
        return snapshot.ask
    return snapshot.price
