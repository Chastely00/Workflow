from __future__ import annotations


def test_build_daily_market_state_marks_active_dual_source_absence_as_halted_and_excludes_delisted() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2024-01-02", "market": "TWSE"},
            {"date": "2024-01-03", "market": "TWSE"},
            {"date": "2024-01-04", "market": "TWSE"},
        ],
        price_rows=[
            {"date": "2024-01-02", "ticker": "1101", "traded_value": 100.0},
            {"date": "2024-01-02", "ticker": "IX0001", "traded_value": 1_000.0},
        ],
        security_master_rows=[
            {
                "ticker": "1101",
                "market": "TWSE",
                "list_date": "2000-01-01",
                "delist_date": "2024-01-04",
            }
        ],
        attribute_rows=[],
        manifest_hashes={
            "security_master": "a" * 64,
            "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64,
            "daily_tradability": "d" * 64,
        },
        build_start="2024-01-02",
        build_end="2024-01-04",
        data_cutoff_at="2024-01-04T14:00:00Z",
    )

    equity_rows = [row for row in rows if row["ticker"] == "1101"]
    assert [(row["date"], row["market_state"], row["amount_state"]) for row in equity_rows] == [
        ("2024-01-02", "TRADING", "OBSERVED"),
        ("2024-01-03", "HALTED", "ZERO_AUTHORIZED"),
    ]
    assert equity_rows[1]["state_reason"] == "ACTIVE_LIFECYCLE_DUAL_SOURCE_ABSENCE"
    assert equity_rows[1]["authoritative_traded_value"] == 0.0
    assert equity_rows[1]["earliest_execution_session"] == "2024-01-04"

    index_row = next(row for row in rows if row["ticker"] == "IX0001")
    assert index_row["instrument_kind"] == "INDEX"
    assert index_row["market_state"] == "TRADING"
    assert index_row["attr_row_present"] is False
