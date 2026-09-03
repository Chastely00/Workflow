import math

import pytest

from data_analysts.adjusted_ohlc import AdjustmentSeed, REQUIRED_ADJUSTED_OHLC_COLUMNS
from data_analysts.adjusted_prices import AdjustmentError, build_adjusted_daily_prices


def test_adjusted_ohlc_columns_are_atomic_and_share_one_factor():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1.0}]
    result = build_adjusted_daily_prices(rows)
    assert set(REQUIRED_ADJUSTED_OHLC_COLUMNS).issubset(result[0])
    assert result[0]["adj_factor"] == 1.0
    assert result[0]["adj_open"] == 10.0
    assert result[0]["adj_high"] == 12.0
    assert result[0]["adj_low"] == 9.0
    assert result[0]["adj_close"] == 11.0


def test_partial_adjustment_requires_seed_or_proven_new_series():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match="missing verified adjustment seed"):
        build_adjusted_daily_prices(rows, require_seed=True)


def test_partial_adjustment_uses_verified_seed():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    result = build_adjusted_daily_prices(
        rows,
        initial_state_by_ticker={"2330": AdjustmentSeed(2.0, 9.0)},
        require_seed=True,
    )
    assert result[0]["adj_close"] == 20.0


def test_partial_adjustment_allows_explicitly_proven_new_series():
    rows = [{"date": "2026-01-02", "ticker": "9999", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    result = build_adjusted_daily_prices(
        rows, proven_new_series_tickers={"9999"}, require_seed=True
    )
    assert result[0]["adj_factor"] == 1.0


def test_cash_dividend_adjusts_all_ohlc_from_ex_date():
    rows = [
        {"date": "2026-01-01", "ticker": "2330", "open": 10.0,
         "high": 12.0, "low": 9.0, "close": 10.0},
        {"date": "2026-01-02", "ticker": "2330", "open": 9.0,
         "high": 10.0, "low": 8.0, "close": 9.0},
    ]
    result = build_adjusted_daily_prices(
        rows,
        dividend_events=[{
            "event_date": "2026-01-02",
            "ticker": "2330",
            "cash_dividend_per_share": 1.0,
            "stock_dividend_ratio": 0.0,
        }],
    )
    assert result[1]["adj_factor"] == 10.0 / 9.0
    assert result[1]["adj_open"] == 10.0
    assert result[1]["adj_high"] == 100.0 / 9.0
    assert result[1]["adj_low"] == 80.0 / 9.0
    assert result[1]["adj_close"] == 10.0


def test_stock_events_share_one_combined_factor():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    result = build_adjusted_daily_prices(
        rows,
        dividend_events=[{
            "event_date": "2026-01-02",
            "ticker": "2330",
            "cash_dividend_per_share": 0.0,
            "stock_dividend_ratio": 0.1,
        }],
        capital_action_events=[{
            "event_date": "2026-01-02",
            "ticker": "2330",
            "action_type": "stock_price_adjustment",
            "price_adjustment_reference": 2.0,
        }],
    )
    assert result[0]["adj_factor"] == 2.2
    assert result[0]["adj_open"] == result[0]["adj_high"] == 22.0
    assert result[0]["adj_low"] == result[0]["adj_close"] == 22.0


def test_missing_stock_price_adjustment_reference_in_slice_fails_closed():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match="missing price_adjustment_reference"):
        build_adjusted_daily_prices(
            rows,
            capital_action_events=[{
                "event_date": "2026-01-02",
                "ticker": "2330",
                "action_type": "stock_price_adjustment",
            }],
        )


def test_missing_stock_price_adjustment_reference_before_slice_fails_closed():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match="missing price_adjustment_reference"):
        build_adjusted_daily_prices(
            rows,
            capital_action_events=[{
                "event_date": "2026-01-01",
                "ticker": "2330",
                "action_type": "stock_price_adjustment",
            }],
        )


@pytest.mark.parametrize(
    "reference", [0.0, -1.0, float("nan"), float("inf")]
)
def test_invalid_stock_price_adjustment_reference_before_slice_fails_closed(reference):
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError):
        build_adjusted_daily_prices(
            rows,
            capital_action_events=[{
                "event_date": "2026-01-01",
                "ticker": "2330",
                "action_type": "stock_price_adjustment",
                "price_adjustment_reference": reference,
            }],
        )


def test_raw_null_price_maps_to_adjusted_null():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": None,
             "high": 10.0, "low": None, "close": 10.0}]
    result = build_adjusted_daily_prices(rows)
    assert result[0]["adj_open"] is None
    assert result[0]["adj_high"] == 10.0
    assert result[0]["adj_low"] is None
    assert result[0]["adj_close"] == 10.0


@pytest.mark.parametrize(
    ("field", "value", "error_message"),
    [
        ("cash_dividend_per_share", -1.0, "invalid cash dividend"),
        ("cash_dividend_per_share", math.nan, "invalid cash dividend"),
        ("stock_dividend_ratio", -0.1, "invalid stock dividend ratio"),
    ],
)
def test_invalid_dividend_fields_before_price_slice_fail_closed(field, value, error_message):
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match=error_message):
        build_adjusted_daily_prices(
            rows,
            dividend_events=[{
                "event_date": "2026-01-01",
                "ticker": "2330",
                field: value,
            }],
        )


def test_cash_dividend_requires_previous_close():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match="missing_previous_close_for_cash_dividend"):
        build_adjusted_daily_prices(
            rows,
            dividend_events=[{
                "event_date": "2026-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 1.0,
            }],
        )


def test_cash_dividend_cannot_exceed_previous_close():
    rows = [
        {"date": "2026-01-01", "ticker": "2330", "open": 10.0,
         "high": 10.0, "low": 10.0, "close": 10.0},
        {"date": "2026-01-02", "ticker": "2330", "open": 9.0,
         "high": 9.0, "low": 9.0, "close": 9.0},
    ]
    with pytest.raises(AdjustmentError, match="cash_dividend_exceeds_previous_close"):
        build_adjusted_daily_prices(
            rows,
            dividend_events=[{
                "event_date": "2026-01-02",
                "ticker": "2330",
                "cash_dividend_per_share": 10.0,
            }],
        )


@pytest.mark.parametrize("seed_factor", [0.0, -1.0, math.inf, math.nan])
def test_adjustment_seed_factor_must_be_positive_and_finite(seed_factor):
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0}]
    with pytest.raises(AdjustmentError, match="invalid adjustment seed factor"):
        build_adjusted_daily_prices(
            rows,
            initial_state_by_ticker={"2330": AdjustmentSeed(seed_factor, 9.0)},
            require_seed=True,
        )


def test_incoming_row_adjustment_factor_is_not_an_implicit_seed():
    rows = [{"date": "2026-01-02", "ticker": "2330", "open": 10.0,
             "high": 10.0, "low": 10.0, "close": 10.0, "adj_factor": 9.0}]
    result = build_adjusted_daily_prices(rows)
    assert result[0]["adj_factor"] == 1.0
