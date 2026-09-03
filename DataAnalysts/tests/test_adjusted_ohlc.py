import math

import pytest

from data_analysts.adjusted_ohlc import (
    AdjustmentSeed,
    ExpectedAdjustmentEvent,
    READY_ADJUSTMENT_STATUS,
    empty_violation_counts,
    validate_adjusted_ohlc_rows,
)


def ready_row(**overrides):
    row = {
        "date": "2026-01-02",
        "ticker": "2330",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "adj_factor": 1.0,
        "adj_open": 10.0,
        "adj_high": 12.0,
        "adj_low": 9.0,
        "adj_close": 11.0,
        "price_adjustment_status": READY_ADJUSTMENT_STATUS,
    }
    row.update(overrides)
    return row


def flat_row(*, date, close, adj_factor=1.0, ticker="2330"):
    adjusted_close = close * adj_factor
    return ready_row(
        date=date,
        ticker=ticker,
        open=close,
        high=close,
        low=close,
        close=close,
        adj_factor=adj_factor,
        adj_open=adjusted_close,
        adj_high=adjusted_close,
        adj_low=adjusted_close,
        adj_close=adjusted_close,
    )


def test_empty_violation_counts_has_ten_named_counters():
    assert empty_violation_counts() == {
        "missing_required_column_count": 0,
        "invalid_adj_factor_count": 0,
        "null_mismatch_count": 0,
        "adjusted_value_mismatch_count": 0,
        "raw_ohlc_order_violation_count": 0,
        "adjusted_ohlc_order_violation_count": 0,
        "duplicate_key_count": 0,
        "unapproved_adjustment_status_count": 0,
        "factor_transition_violation_count": 0,
        "row_order_violation_count": 0,
    }


def test_validator_counts_missing_atomic_column():
    row = ready_row()
    row.pop("adj_high")

    result = validate_adjusted_ohlc_rows([row])

    assert result.status == "blocked"
    assert result.violation_counts["missing_required_column_count"] == 1


def test_validator_counts_adjusted_value_mismatch():
    result = validate_adjusted_ohlc_rows([ready_row(adj_factor=2.0, adj_close=999.0)])

    assert result.violation_counts["adjusted_value_mismatch_count"] == 4


def test_validator_counts_invalid_adjusted_factor():
    result = validate_adjusted_ohlc_rows([ready_row(adj_factor=math.nan)])

    assert result.violation_counts["invalid_adj_factor_count"] == 1


def test_validator_requires_raw_and_adjusted_nulls_to_match():
    result = validate_adjusted_ohlc_rows([ready_row(open=None, adj_open=10.0)])

    assert result.violation_counts["null_mismatch_count"] == 1


def test_validator_counts_raw_ohlc_ordering_violation():
    result = validate_adjusted_ohlc_rows([ready_row(high=10.0, close=11.0)])

    assert result.violation_counts["raw_ohlc_order_violation_count"] == 1


def test_validator_counts_adjusted_ohlc_ordering_violation():
    result = validate_adjusted_ohlc_rows([ready_row(adj_high=10.0, adj_close=11.0)])

    assert result.violation_counts["adjusted_ohlc_order_violation_count"] == 1


def test_validator_accepts_adjusted_values_inside_absolute_tolerance_boundary():
    result = validate_adjusted_ohlc_rows(
        [
            ready_row(
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                adj_open=5e-9,
                adj_high=5e-9,
                adj_low=0.0,
                adj_close=0.0,
            )
        ]
    )

    assert result.status == "ready"


def test_validator_rejects_adjusted_values_outside_relative_tolerance_boundary():
    result = validate_adjusted_ohlc_rows(
        [
            ready_row(
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                adj_open=100.0,
                adj_high=100.0,
                adj_low=100.0,
                adj_close=100.0 + 5e-8,
            )
        ]
    )

    assert result.violation_counts["adjusted_value_mismatch_count"] == 1


def test_validator_blocks_duplicate_date_ticker():
    row = ready_row()

    result = validate_adjusted_ohlc_rows([row, dict(row)])

    assert result.violation_counts["duplicate_key_count"] == 1


def test_validator_requires_approved_status():
    result = validate_adjusted_ohlc_rows([ready_row(price_adjustment_status="unknown")])

    assert result.violation_counts["unapproved_adjustment_status_count"] == 1


def test_validator_counts_factor_change_without_event():
    rows = [
        ready_row(date="2026-01-02", close=10.0, high=10.0, low=10.0, adj_close=10.0, adj_high=10.0, adj_low=10.0),
        ready_row(date="2026-01-03", close=10.0, high=10.0, low=10.0, adj_factor=2.0, adj_open=20.0, adj_high=20.0, adj_low=20.0, adj_close=20.0),
    ]

    result = validate_adjusted_ohlc_rows(rows)

    assert result.violation_counts["factor_transition_violation_count"] == 1


def test_validator_counts_event_factor_mismatch():
    rows = [
        ready_row(date="2026-01-02", close=10.0, high=10.0, low=10.0, adj_close=10.0, adj_high=10.0, adj_low=10.0),
        ready_row(date="2026-01-03", close=9.0, high=9.0, low=9.0, adj_open=9.0, adj_high=9.0, adj_low=9.0, adj_close=9.0),
    ]

    result = validate_adjusted_ohlc_rows(
        rows,
        expected_events_by_ticker={
            "2330": [ExpectedAdjustmentEvent("2026-01-03", cash_dividend=1.0)]
        },
    )

    assert result.violation_counts["factor_transition_violation_count"] == 1


def test_validator_uses_ending_state_for_a_cross_batch_event():
    first_batch = validate_adjusted_ohlc_rows(
        [ready_row(close=10.0, high=10.0, low=10.0, adj_factor=2.0, adj_open=20.0, adj_high=20.0, adj_low=20.0, adj_close=20.0)],
        initial_state_by_ticker={"2330": AdjustmentSeed(2.0, 9.0)},
    )
    second_batch = validate_adjusted_ohlc_rows(
        [ready_row(date="2026-01-03", close=9.0, high=10.0, low=9.0, adj_factor=20.0 / 9.0, adj_open=200.0 / 9.0, adj_high=200.0 / 9.0, adj_low=20.0, adj_close=20.0)],
        initial_state_by_ticker=first_batch.ending_state_by_ticker,
        expected_events_by_ticker={
            "2330": [ExpectedAdjustmentEvent("2026-01-03", cash_dividend=1.0)]
        },
    )

    assert first_batch.status == "ready"
    assert second_batch.status == "ready"
    assert second_batch.ending_state_by_ticker["2330"] == AdjustmentSeed(20.0 / 9.0, 9.0)


def test_validator_applies_event_on_first_row_after_non_trading_event_date():
    rows = [
        flat_row(date="2026-01-01", close=10.0),
        flat_row(date="2026-01-03", close=9.0, adj_factor=10.0 / 9.0),
    ]

    result = validate_adjusted_ohlc_rows(
        rows,
        expected_events_by_ticker={
            "2330": [ExpectedAdjustmentEvent("2026-01-02", cash_dividend=1.0)]
        },
    )

    assert result.status == "ready"
    assert result.ending_state_by_ticker["2330"] == AdjustmentSeed(10.0 / 9.0, 9.0)


def test_validator_accepts_stock_event_factor_transition():
    result = validate_adjusted_ohlc_rows(
        [
            flat_row(date="2026-01-01", close=10.0),
            flat_row(date="2026-01-02", close=10.0, adj_factor=1.1),
        ],
        expected_events_by_ticker={
            "2330": [ExpectedAdjustmentEvent("2026-01-02", stock_event_factor=1.1)]
        },
    )

    assert result.status == "ready"
    assert result.ending_state_by_ticker["2330"] == AdjustmentSeed(1.1, 10.0)


def test_validator_accepts_combined_cash_and_stock_event_factor_transition():
    combined_factor = 1.1 * 10.0 / 9.0
    result = validate_adjusted_ohlc_rows(
        [
            flat_row(date="2026-01-01", close=10.0),
            flat_row(date="2026-01-02", close=9.0, adj_factor=combined_factor),
        ],
        expected_events_by_ticker={
            "2330": [
                ExpectedAdjustmentEvent(
                    "2026-01-02", cash_dividend=1.0, stock_event_factor=1.1
                )
            ]
        },
    )

    assert result.status == "ready"
    assert result.ending_state_by_ticker["2330"] == AdjustmentSeed(combined_factor, 9.0)


def test_validator_counts_rows_out_of_ticker_date_order():
    rows = [
        ready_row(date="2026-01-03"),
        ready_row(date="2026-01-02"),
    ]

    result = validate_adjusted_ohlc_rows(rows)

    assert result.violation_counts["row_order_violation_count"] == 1


@pytest.mark.parametrize("rows", [[], [ready_row(ticker="2330")]])
def test_validator_rejects_unsorted_expected_events_without_matching_rows(rows):
    with pytest.raises(ValueError, match="sorted by event_date"):
        validate_adjusted_ohlc_rows(
            rows,
            expected_events_by_ticker={
                "9999": [
                    ExpectedAdjustmentEvent("2026-01-03"),
                    ExpectedAdjustmentEvent("2026-01-02"),
                ]
            },
        )


def test_validator_reads_one_shot_rows_iterable_once():
    class OneShotRows:
        def __init__(self):
            self.iterated = False

        def __iter__(self):
            if self.iterated:
                raise AssertionError("rows were iterated more than once")
            self.iterated = True
            yield ready_row()

    result = validate_adjusted_ohlc_rows(OneShotRows())

    assert result.status == "ready"


@pytest.mark.parametrize("identity_field", ["ticker", "date"])
def test_validator_blocks_null_identity_without_creating_boundary_state(identity_field):
    row = ready_row(**{identity_field: None})

    result = validate_adjusted_ohlc_rows([row])

    assert result.status == "blocked"
    assert result.violation_counts["missing_required_column_count"] == 1
    assert result.ending_state_by_ticker == {}


@pytest.mark.parametrize(
    ("identity_field", "invalid_value"),
    [("date", "not-a-date"), ("ticker", "23 30")],
)
def test_validator_blocks_malformed_nonempty_identity_without_creating_boundary_state(
    identity_field, invalid_value
):
    result = validate_adjusted_ohlc_rows(
        [ready_row(**{identity_field: invalid_value})]
    )

    assert result.status == "blocked"
    assert result.violation_counts["missing_required_column_count"] == 1
    assert result.ending_state_by_ticker == {}
