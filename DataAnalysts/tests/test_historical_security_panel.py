from data_analysts.security_panel import build_historical_security_panel, build_security_panel


def test_historical_security_panel_uses_next_trading_day_as_effective_date():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
                "data_cutoff_at": "2025-01-02T00:00:00Z",
            },
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 11,
                "traded_value": 1111,
                "market_cap": 10100,
                "data_cutoff_at": "2025-01-03T00:00:00Z",
            },
        ],
        security_master=[
            {
                "ticker": "2330",
                "stock_name": "TSMC",
                "market": "TWSE",
                "listed": True,
                "security_type": "common_stock",
                "data_cutoff_at": "2025-01-01T00:00:00Z",
            }
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["effective_date"] == "2025-01-03"
    assert by_date["2025-01-03"]["effective_date"] == "2025-01-06"
    assert by_date["2025-01-03"]["source_max_date"] == "2025-01-03"
    assert diag["as_of_date_count"] == 2
    assert diag["duplicate_as_of_ticker_count"] == 0


def test_historical_security_panel_adv20_uses_only_past_and_current_values():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 1,
                "traded_value": 10,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 1,
                "traded_value": 30,
                "market_cap": 10100,
            },
        ],
        security_master=[
            {"ticker": "2330", "market": "TWSE", "listed": True, "security_type": "common_stock"}
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["adv20"] == 10
    assert by_date["2025-01-03"]["adv20"] == 20


def test_historical_security_panel_uses_market_specific_next_trading_day():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-03",
                "ticker": "8088",
                "close": 51,
                "adj_close": 51,
                "volume": 10,
                "traded_value": 500,
                "market_cap": 5000,
            },
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True},
            {"ticker": "8088", "stock_name": "ABC", "market": "TPEX", "listed": True},
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-07", "market": "TPEX", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["2330"]["effective_date"] == "2025-01-06"
    assert by_ticker["8088"]["effective_date"] == "2025-01-07"


def test_historical_security_panel_known_market_does_not_fallback_to_global_calendar():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            }
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True}
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "effective_date": None,
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": "TSMC",
            "market": "TWSE",
            "security_type": None,
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 101,
            "traded_value": 1000.0,
            "market_cap": 10000,
            "adv20": 1000.0,
            "data_cutoff_at": None,
        }
    ]
    assert diag["effective_date_null_count"] == 1


def test_historical_security_panel_counts_duplicate_price_rows_in_diagnostics():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 999,
                "adj_close": 999,
                "volume": 99,
                "traded_value": 9999,
                "market_cap": 99999,
            },
        ],
        security_master=[{"ticker": "2330", "market": "TWSE", "listed": True}],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-02",
    )

    assert len(rows) == 1
    assert rows[0]["close"] == 100
    assert diag["duplicate_as_of_ticker_count"] == 1


def test_historical_security_panel_effective_date_null_count_only_counts_emitted_rows():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            }
        ],
        security_master=[
            {"ticker": "2330", "market": "TWSE", "listed": True},
            {"ticker": "2317", "market": "TWSE", "listed": True},
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "effective_date": None,
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": None,
            "market": "TWSE",
            "security_type": None,
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 101,
            "traded_value": 1000.0,
            "market_cap": 10000,
            "adv20": 1000.0,
            "data_cutoff_at": None,
        }
    ]
    assert diag["effective_date_null_count"] == 1


def test_build_security_panel_latest_helper_keeps_existing_latest_only_behavior():
    effective_date, rows = build_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 99, "volume": 10, "traded_value": 1000, "market_cap": 10000},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 100, "volume": 11, "traded_value": 1100, "market_cap": 10100},
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True, "security_type": "common_stock"}
        ],
    )

    assert effective_date == "2025-01-03"
    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": "TSMC",
            "market": "TWSE",
            "security_type": "common_stock",
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 100,
            "traded_value": 1100.0,
            "market_cap": 10100,
            "adv20": 1100.0,
            "data_cutoff_at": None,
        }
    ]
