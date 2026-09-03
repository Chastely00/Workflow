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
        attribute_rows=[
            {"date": "2024-01-02", "ticker": "1101", "mkt": "TWSE", "stktp_e": "Common Stock"}
        ],
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


def test_build_daily_market_state_uses_attribute_identity_when_master_has_lifecycle_only() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2024-01-02", "market": "TWSE"},
            {"date": "2024-01-03", "market": "TWSE"},
        ],
        price_rows=[{"date": "2024-01-02", "ticker": "1101", "traded_value": 100.0}],
        security_master_rows=[
            {"ticker": "1101", "list_date": "2000-01-01", "delist_date": None},
            {"ticker": "0050", "list_date": "2003-01-01", "delist_date": None},
        ],
        attribute_rows=[
            {
                "date": "2024-01-02", "ticker": "1101", "mkt": "TSE",
                "stktp_e": "Common Stock", "susp_fg": "",
            },
            {
                "date": "2024-01-02", "ticker": "0050", "mkt": "TWSE",
                "stktp_e": "ETF", "susp_fg": "",
            },
        ],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2024-01-02", build_end="2024-01-02",
        data_cutoff_at="2024-01-02T14:00:00Z",
    )

    stock = next(row for row in rows if row["ticker"] == "1101")
    assert stock["market"] == "TWSE"
    assert stock["instrument_kind"] == "EQUITY"
    assert stock["identity_source"] == "SECURITY_MASTER_SNAPSHOT_APISTKATTR_IDENTITY"
    etf = next(row for row in rows if row["ticker"] == "0050")
    assert etf["instrument_kind"] == "ETF"


def test_build_daily_market_state_keeps_listing_day_without_future_attribute_identity() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2024-01-15", "market": "TWSE"},
            {"date": "2024-01-16", "market": "TWSE"},
        ],
        price_rows=[{"date": "2024-01-15", "ticker": "6906", "traded_value": 100.0}],
        security_master_rows=[{
            "ticker": "6906", "market": "TWSE", "list_date": "2024-01-15", "delist_date": None,
        }],
        attribute_rows=[],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2024-01-15", build_end="2024-01-15",
        data_cutoff_at="2024-01-15T14:00:00Z",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["market"] == "TWSE"
    assert row["market_state"] == "TRADING"
    assert row["instrument_kind"] == "OTHER"
    assert row["identity_source"] == "SECURITY_MASTER_SNAPSHOT"
    assert row["attr_row_present"] is False


def test_build_daily_market_state_authorizes_active_listed_dual_absence_from_master() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2010-10-01", "market": "TWSE"},
            {"date": "2010-10-04", "market": "TWSE"},
        ],
        price_rows=[],
        security_master_rows=[{
            "ticker": "9157", "market": "TWSE", "list_date": "2009-12-11", "delist_date": "2019-11-12",
        }],
        attribute_rows=[],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2010-10-01", build_end="2010-10-01",
        data_cutoff_at="2010-10-01T14:00:00Z",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["market_state"] == "HALTED"
    assert row["authoritative_traded_value"] == 0.0
    assert row["amount_zero_authorized"] is True
    assert row["identity_source"] == "SECURITY_MASTER_SNAPSHOT"


def test_build_daily_market_state_uses_master_market_when_attribute_market_is_blank() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2024-11-04", "market": "TWSE"},
            {"date": "2024-11-05", "market": "TWSE"},
        ],
        price_rows=[{"date": "2024-11-04", "ticker": "3616", "traded_value": 100.0}],
        security_master_rows=[{
            "ticker": "3616", "market": "TWSE", "list_date": "2000-01-01", "delist_date": None,
        }],
        attribute_rows=[{
            "date": "2024-11-04", "ticker": "3616", "mkt": "", "stktp_e": "Common Stock",
        }],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2024-11-04", build_end="2024-11-04",
        data_cutoff_at="2024-11-04T14:00:00Z",
    )

    assert len(rows) == 1
    assert rows[0]["market"] == "TWSE"
    assert rows[0]["instrument_kind"] == "EQUITY"
    assert rows[0]["identity_source"] == "SECURITY_MASTER_SNAPSHOT_APISTKATTR_IDENTITY"


def test_build_daily_market_state_marks_unidentified_lifecycle_market_missing_and_untradeable() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2026-06-17", "market": "TWSE"},
            {"date": "2026-06-18", "market": "TWSE"},
        ],
        price_rows=[{"date": "2026-06-17", "ticker": "7415", "traded_value": 100.0}],
        security_master_rows=[{
            "ticker": "7415", "market": "EMERGING", "list_date": "2026-06-17", "delist_date": None,
        }],
        attribute_rows=[{
            "date": "2026-06-17", "ticker": "7415", "mkt": "", "stktp_e": "Common Stock",
        }],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2026-06-17", build_end="2026-06-17",
        data_cutoff_at="2026-06-17T14:00:00Z",
    )

    assert len(rows) == 1
    assert rows[0]["market"] == "EMERGING"
    assert rows[0]["market_state"] == "MISSING"
    assert rows[0]["state_reason"] == "LIFECYCLE_EMERGING_BOARD"
    assert rows[0]["instrument_kind"] == "OTHER"
    assert rows[0]["exchange_tradable"] is None


def test_build_daily_market_state_retains_price_key_outside_master_lifecycle() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2024-01-02", "market": "TWSE"},
            {"date": "2024-01-03", "market": "TWSE"},
        ],
        price_rows=[{"date": "2024-01-02", "ticker": "9999", "traded_value": 100.0}],
        security_master_rows=[{
            "ticker": "9999", "market": "TWSE", "list_date": "2024-01-03", "delist_date": None,
        }],
        attribute_rows=[],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2024-01-02", build_end="2024-01-02",
        data_cutoff_at="2024-01-02T14:00:00Z",
    )

    assert len(rows) == 1
    assert rows[0]["ticker"] == "9999"
    assert rows[0]["market_state"] == "MISSING"
    assert rows[0]["state_reason"] == "LIFECYCLE_OUTSIDE_ACTIVE_INTERVAL"
    assert rows[0]["exchange_tradable"] is None


def test_market_state_publication_schema_is_stable_when_attribute_fields_are_null() -> None:
    import pyarrow as pa

    from data_analysts.daily_market_state_publication import _DMS_SCHEMA

    table = pa.Table.from_pylist(
        [{"date": "2020-01-02", "ticker": "1101", "attr_row_present": False}],
        schema=_DMS_SCHEMA,
    )

    assert table.schema == _DMS_SCHEMA
    assert table.schema.field("atten_fg").type == pa.string()
    assert table.schema.field("authoritative_traded_value").type == pa.float64()


def test_build_daily_market_state_partition_scope_keeps_global_lifecycle_interval() -> None:
    from data_analysts.daily_market_state import build_daily_market_state_rows

    rows = build_daily_market_state_rows(
        trading_calendar_rows=[
            {"date": "2020-01-02", "market": "TWSE"},
            {"date": "2021-01-04", "market": "TWSE"},
            {"date": "2021-01-05", "market": "TWSE"},
        ],
        price_rows=[{"date": "2021-01-04", "ticker": "1101", "traded_value": 100.0}],
        security_master_rows=[{
            "ticker": "1101", "market": "TWSE", "list_date": "2000-01-01", "delist_date": None,
        }],
        attribute_rows=[],
        manifest_hashes={
            "security_master": "a" * 64, "trading_calendar": "b" * 64,
            "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
        },
        build_start="2020-01-02",
        build_end="2021-01-04",
        scope_start="2021-01-04",
        scope_end="2021-01-04",
        data_cutoff_at="2021-01-04T14:00:00Z",
    )

    assert [row["date"] for row in rows] == ["2021-01-04"]
    assert rows[0]["lifecycle_interval_start"] == "2020-01-02"


def test_partitioned_identity_carry_matches_single_pass_identity() -> None:
    from data_analysts.daily_market_state import (
        advance_attribute_identity,
        attribute_identity_asof,
        build_daily_market_state_rows,
    )

    hashes = {
        "security_master": "a" * 64, "trading_calendar": "b" * 64,
        "daily_price_volume": "c" * 64, "daily_tradability": "d" * 64,
    }
    calendar = [
        {"date": "2020-01-02", "market": "TWSE"},
        {"date": "2021-01-04", "market": "TWSE"},
        {"date": "2021-01-05", "market": "TWSE"},
    ]
    master = [{"ticker": "1101", "market": "", "list_date": "2000-01-01", "delist_date": None}]
    seed = [{"date": "2019-12-31", "ticker": "1101", "mkt": "TWSE", "stktp_e": "Common Stock"}]
    attrs_2020 = [{"date": "2020-01-02", "ticker": "1101", "mkt": "", "stktp_e": "Common Stock"}]
    attrs_2021 = [{"date": "2021-01-04", "ticker": "1101", "mkt": "", "stktp_e": "Common Stock"}]
    prices = [
        {"date": "2020-01-02", "ticker": "1101", "traded_value": 100.0},
        {"date": "2021-01-04", "ticker": "1101", "traded_value": 100.0},
    ]
    kwargs = dict(
        trading_calendar_rows=calendar, security_master_rows=master,
        manifest_hashes=hashes, build_start="2020-01-02", build_end="2021-01-04",
        data_cutoff_at="2021-01-04T14:00:00Z",
    )
    direct = build_daily_market_state_rows(
        **kwargs, price_rows=prices, attribute_rows=[*seed, *attrs_2020, *attrs_2021]
    )
    identity = attribute_identity_asof(seed, "2020-01-02")
    first = build_daily_market_state_rows(
        **kwargs, price_rows=[prices[0]], attribute_rows=attrs_2020,
        scope_start="2020-01-02", scope_end="2020-01-02",
        initial_attribute_identity=identity,
    )
    identity = advance_attribute_identity(identity, attrs_2020)
    second = build_daily_market_state_rows(
        **kwargs, price_rows=[prices[1]], attribute_rows=attrs_2021,
        scope_start="2021-01-04", scope_end="2021-01-04",
        initial_attribute_identity=identity,
    )

    assert first + second == direct
