from __future__ import annotations

from app.models import OutcomeRef, PriceSnapshot
from app.services.hold_filter import build_hold_items


def test_build_hold_items_filters_by_time_and_price() -> None:
    now_ts = 1_700_000_000

    outcomes = [
        OutcomeRef(
            market_id=101,
            market_title="A",
            market_type=0,
            outcome_id=None,
            outcome_label="YES",
            token_id="1",
            cutoff_at=now_ts + 3 * 24 * 60 * 60,
            url_slug="a",
        ),
        OutcomeRef(
            market_id=102,
            market_title="B",
            market_type=0,
            outcome_id=None,
            outcome_label="YES",
            token_id="2",
            cutoff_at=now_ts + 5 * 24 * 60 * 60,
            url_slug="b",
        ),
        OutcomeRef(
            market_id=103,
            market_title="C",
            market_type=0,
            outcome_id=None,
            outcome_label="YES",
            token_id="3",
            cutoff_at=now_ts + 2 * 24 * 60 * 60,
            url_slug="c",
        ),
    ]
    prices = {
        "1": PriceSnapshot(token_id="1", price=0.95, timestamp=now_ts),
        "2": PriceSnapshot(token_id="2", price=0.99, timestamp=now_ts),
        "3": PriceSnapshot(token_id="3", price=0.94, timestamp=now_ts),
    }

    result = build_hold_items(
        outcomes,
        prices,
        now_ts=now_ts,
        expiry_days=4,
        price_threshold=0.95,
    )

    assert len(result) == 1
    assert result[0].market_id == 101
    assert result[0].token_id == "1"

