from __future__ import annotations

from app.services.market_collector import MarketCollector


def test_extract_outcomes_binary_and_categorical() -> None:
    collector = MarketCollector(
        client=None,  # type: ignore[arg-type]
        market_page_size=20,
        max_concurrent_price_requests=10,
    )
    markets = [
        {
            "marketId": 1001,
            "marketTitle": "Binary question",
            "marketType": 0,
            "yesTokenId": "11",
            "noTokenId": "12",
            "yesLabel": "YES",
            "noLabel": "NO",
            "cutoffAt": 1_800_000_000,
            "questionId": "binary-q",
        },
        {
            "marketId": 2000,
            "marketTitle": "Categorical root",
            "marketType": 1,
            "cutoffAt": 1_800_000_000,
            "questionId": "cat-q",
            "childMarkets": [
                {
                    "marketId": 2001,
                    "marketTitle": "Option A",
                    "yesTokenId": "21",
                    "cutoffAt": 1_800_000_100,
                },
                {
                    "marketId": 2002,
                    "marketTitle": "Option B",
                    "yesTokenId": "22",
                    "cutoffAt": 1_800_000_200,
                },
            ],
        },
    ]

    outcomes = collector.extract_outcomes(markets)
    token_ids = {item.token_id for item in outcomes}

    assert token_ids == {"11", "12", "21", "22"}
    assert len(outcomes) == 4

