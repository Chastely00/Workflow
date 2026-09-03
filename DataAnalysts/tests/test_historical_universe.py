from data_analysts.universe import build_historical_universe_memberships


def test_historical_universe_uses_effective_date_and_year_safe_rows():
    specs = {
        "universes": [
            {
                "universe_id": "tw_equity_liquid_top2",
                "enabled": True,
                "source": "security_panel",
                "filters": [
                    {"field": "listed", "op": "eq", "value": True},
                    {"field": "tradable", "op": "eq", "value": True},
                    {"field": "market_cap", "op": "not_null"},
                ],
                "rank_by": [
                    {"field": "market_cap", "direction": "desc"},
                    {"field": "ticker", "direction": "asc"},
                ],
                "limit": 2,
            }
        ]
    }
    panel = [
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
            "market": "TWSE",
            "security_type": "common_stock",
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
            "market": "TWSE",
            "security_type": "common_stock",
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-03",
            "ticker": "9999",
            "listed": True,
            "tradable": True,
            "market_cap": 10,
            "market": "TPEX",
            "security_type": "common_stock",
        },
    ]

    memberships, diagnostics = build_historical_universe_memberships(panel, specs)

    rows = memberships["tw_equity_liquid_top2"]
    assert [(row["ticker"], row["rank"]) for row in rows] == [("2330", 1), ("2317", 2)]
    assert rows[0]["as_of_date"] == "2025-01-02"
    assert rows[0]["effective_date"] == "2025-01-03"
    assert rows[0]["included"] is True
    assert diagnostics["tw_equity_liquid_top2"]["top_n_limit"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["max_included_count"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_ticker_count"] == 0


def test_historical_universe_deduplicates_on_effective_date_keys():
    specs = {
        "universes": [
            {
                "universe_id": "tw_equity_liquid_top2",
                "enabled": True,
                "source": "security_panel",
                "filters": [
                    {"field": "listed", "op": "eq", "value": True},
                    {"field": "tradable", "op": "eq", "value": True},
                    {"field": "market_cap", "op": "not_null"},
                ],
                "rank_by": [
                    {"field": "market_cap", "direction": "desc"},
                    {"field": "ticker", "direction": "asc"},
                ],
                "limit": 2,
            }
        ]
    }
    panel = [
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-06",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
        },
        {
            "as_of_date": "2025-01-03",
            "effective_date": "2025-01-06",
            "ticker": "2330",
            "listed": True,
            "tradable": True,
            "market_cap": 30,
        },
        {
            "as_of_date": "2025-01-02",
            "effective_date": "2025-01-06",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
        },
        {
            "as_of_date": "2025-01-03",
            "effective_date": "2025-01-06",
            "ticker": "2317",
            "listed": True,
            "tradable": True,
            "market_cap": 20,
        },
    ]

    memberships, diagnostics = build_historical_universe_memberships(panel, specs)

    rows = memberships["tw_equity_liquid_top2"]
    assert [(row["effective_date"], row["ticker"], row["rank"]) for row in rows] == [
        ("2025-01-06", "2330", 1),
        ("2025-01-06", "2317", 2),
    ]
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_ticker_count"] == 2
    assert diagnostics["tw_equity_liquid_top2"]["duplicate_universe_effective_rank_count"] == 2
