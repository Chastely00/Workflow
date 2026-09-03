from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any, Iterable, Mapping, Sequence


ADJUSTMENT_POLICY_ID = "event_based_adjusted_ohlc_v1"
READY_ADJUSTMENT_STATUS = "adjusted_close_ready"
REQUIRED_ADJUSTED_OHLC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "adj_factor",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "price_adjustment_status",
)


@dataclass(frozen=True)
class AdjustmentSeed:
    adj_factor: float
    previous_close: float | None


@dataclass(frozen=True)
class ExpectedAdjustmentEvent:
    event_date: Any
    cash_dividend: float | None = None
    stock_event_factor: float | None = None


@dataclass(frozen=True)
class AdjustedOhlcValidationResult:
    status: str
    row_count: int
    date_range: tuple[Any, Any] | None
    ending_state_by_ticker: Mapping[str, AdjustmentSeed]
    violation_counts: Mapping[str, int]


@dataclass(frozen=True)
class AdjustmentFactorTransitionResult:
    state: AdjustmentSeed
    invalid_seed: bool
    invalid_raw_factor: bool
    factor_transition_violation: bool


class AdjustmentFactorStateMachine:
    """Derive canonical factor state from verified seeds and official events."""

    def __init__(
        self,
        *,
        initial_state_by_ticker: Mapping[str, AdjustmentSeed] | None = None,
        expected_events_by_ticker: Mapping[
            str, Sequence[ExpectedAdjustmentEvent]
        ]
        | None = None,
    ) -> None:
        self._initial_state_by_ticker = initial_state_by_ticker or {}
        self._expected_events_by_ticker = expected_events_by_ticker or {}
        self._states: dict[str, AdjustmentSeed] = {}
        self._event_cursors: dict[str, int] = {}
        for events in self._expected_events_by_ticker.values():
            _require_events_sorted(events)

    @property
    def states(self) -> Mapping[str, AdjustmentSeed]:
        return self._states

    def apply_row(
        self,
        *,
        ticker: str,
        row_date: str,
        raw_factor: Any,
        raw_close: Any,
    ) -> AdjustmentFactorTransitionResult:
        state = self._states.get(ticker)
        invalid_seed = False
        first_row_without_verified_seed = False
        if state is None:
            supplied_seed = self._initial_state_by_ticker.get(ticker)
            state, invalid_seed = _canonical_seed(supplied_seed)
            first_row_without_verified_seed = supplied_seed is None or invalid_seed

        expected_factor, transition_error = _expected_factor_for_row(
            ticker,
            row_date,
            state,
            self._expected_events_by_ticker.get(ticker, ()),
            self._event_cursors,
            reject_due_events_without_seed=first_row_without_verified_seed,
        )
        parsed_factor = _finite_float(raw_factor)
        invalid_raw_factor = parsed_factor is None or parsed_factor <= 0.0
        factor_transition_violation = (
            transition_error
            or invalid_raw_factor
            or not math.isclose(
                parsed_factor,
                expected_factor,
                rel_tol=1e-10,
                abs_tol=1e-8,
            )
        )
        close = _finite_float(raw_close)
        canonical_state = AdjustmentSeed(
            adj_factor=expected_factor,
            previous_close=close if close is not None else state.previous_close,
        )
        self._states[ticker] = canonical_state
        return AdjustmentFactorTransitionResult(
            state=canonical_state,
            invalid_seed=invalid_seed,
            invalid_raw_factor=invalid_raw_factor,
            factor_transition_violation=factor_transition_violation,
        )


_VALIDATION_REQUIRED_COLUMNS = ("ticker", "date", *REQUIRED_ADJUSTED_OHLC_COLUMNS)
_OHLC_COLUMNS = ("open", "high", "low", "close")


def empty_violation_counts() -> dict[str, int]:
    return {
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


def validate_adjusted_ohlc_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    initial_state_by_ticker: Mapping[str, AdjustmentSeed] | None = None,
    expected_events_by_ticker: Mapping[str, Sequence[ExpectedAdjustmentEvent]] | None = None,
) -> AdjustedOhlcValidationResult:
    """Validate sorted adjusted OHLC rows without materializing the input iterable."""
    initial_state_by_ticker = initial_state_by_ticker or {}
    expected_events_by_ticker = expected_events_by_ticker or {}
    violations = empty_violation_counts()
    seen_keys: set[tuple[str, str]] = set()
    factor_machine = AdjustmentFactorStateMachine(
        initial_state_by_ticker=initial_state_by_ticker,
        expected_events_by_ticker=expected_events_by_ticker,
    )
    previous_key: tuple[str, str] | None = None
    row_count = 0
    minimum_date: Any = None
    maximum_date: Any = None

    for row in rows:
        row_count += 1
        missing_columns = [column for column in _VALIDATION_REQUIRED_COLUMNS if column not in row]
        if missing_columns:
            violations["missing_required_column_count"] += len(missing_columns)

        ticker_key = _normalize_ticker(row.get("ticker"))
        date_key = _normalize_row_date(row.get("date"))
        invalid_identity_count = int(ticker_key is None) + int(date_key is None)
        if invalid_identity_count:
            violations["missing_required_column_count"] += invalid_identity_count
            continue
        key = (ticker_key, date_key)
        if previous_key is not None and key < previous_key:
            violations["row_order_violation_count"] += 1
        previous_key = key

        if key in seen_keys:
            violations["duplicate_key_count"] += 1
        else:
            seen_keys.add(key)

        if minimum_date is None or date_key < str(minimum_date):
            minimum_date = date_key
        if maximum_date is None or date_key > str(maximum_date):
            maximum_date = date_key

        if row.get("price_adjustment_status") != READY_ADJUSTMENT_STATUS:
            violations["unapproved_adjustment_status_count"] += 1

        factor = _finite_float(row.get("adj_factor"))
        if factor is None or factor <= 0.0:
            violations["invalid_adj_factor_count"] += 1

        _validate_adjusted_values(row, factor, violations)
        _validate_ohlc_ordering(row, "", "raw_ohlc_order_violation_count", violations)
        _validate_ohlc_ordering(row, "adj_", "adjusted_ohlc_order_violation_count", violations)

        transition = factor_machine.apply_row(
            ticker=ticker_key,
            row_date=date_key,
            raw_factor=factor,
            raw_close=row.get("close"),
        )
        if transition.invalid_seed:
            violations["invalid_adj_factor_count"] += 1
        if transition.factor_transition_violation:
            violations["factor_transition_violation_count"] += 1

    date_range = None if minimum_date is None else (minimum_date, maximum_date)
    status = "ready" if all(count == 0 for count in violations.values()) else "blocked"
    return AdjustedOhlcValidationResult(
        status=status,
        row_count=row_count,
        date_range=date_range,
        ending_state_by_ticker=factor_machine.states,
        violation_counts=violations,
    )


def normalize_adjusted_ohlc_identity(
    ticker: Any, row_date: Any
) -> tuple[str, str] | None:
    ticker_key = _normalize_ticker(ticker)
    date_key = _normalize_row_date(row_date)
    if ticker_key is None or date_key is None:
        return None
    return ticker_key, date_key


def normalize_adjusted_ohlc_ticker(ticker: Any) -> str | None:
    """Normalize a ticker without inventing an unrelated probe date."""
    return _normalize_ticker(ticker)


def _normalize_ticker(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value.isascii()
        or not value.isalnum()
    ):
        return None
    return value


def _normalize_row_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed_date.isoformat() != value:
        return None
    return value


def _validate_adjusted_values(
    row: Mapping[str, Any],
    factor: float | None,
    violations: dict[str, int],
) -> None:
    for column in _OHLC_COLUMNS:
        raw_value = row.get(column)
        adjusted_value = row.get(f"adj_{column}")
        if (raw_value is None) != (adjusted_value is None):
            violations["null_mismatch_count"] += 1
            continue
        if raw_value is None or factor is None or factor <= 0.0:
            continue
        raw_number = _finite_float(raw_value)
        adjusted_number = _finite_float(adjusted_value)
        if (
            raw_number is None
            or adjusted_number is None
            or not math.isclose(
                adjusted_number,
                raw_number * factor,
                rel_tol=1e-10,
                abs_tol=1e-8,
            )
        ):
            violations["adjusted_value_mismatch_count"] += 1


def _validate_ohlc_ordering(
    row: Mapping[str, Any],
    prefix: str,
    counter: str,
    violations: dict[str, int],
) -> None:
    values = [_finite_float(row.get(f"{prefix}{column}")) for column in _OHLC_COLUMNS]
    if any(value is None for value in values):
        return
    open_price, high, low, close = values
    if low > min(open_price, close) or high < max(open_price, close):
        violations[counter] += 1


def _canonical_seed(seed: Any) -> tuple[AdjustmentSeed, bool]:
    if seed is None:
        return AdjustmentSeed(1.0, None), False
    if not isinstance(seed, AdjustmentSeed):
        return AdjustmentSeed(1.0, None), True
    factor = _finite_float(seed.adj_factor)
    if factor is None or factor <= 0.0:
        return AdjustmentSeed(1.0, None), True
    previous_close = _finite_float(seed.previous_close)
    return AdjustmentSeed(factor, previous_close), False


def _expected_factor_for_row(
    ticker: str,
    date: str,
    state: AdjustmentSeed,
    events: Sequence[ExpectedAdjustmentEvent],
    cursors: dict[str, int],
    *,
    reject_due_events_without_seed: bool = False,
) -> tuple[float, bool]:
    cursor = cursors.get(ticker, 0)
    if reject_due_events_without_seed and (
        cursor < len(events) and str(events[cursor].event_date) <= date
    ):
        while cursor < len(events) and str(events[cursor].event_date) <= date:
            cursor += 1
        cursors[ticker] = cursor
        return state.adj_factor, True
    cash_dividend = 0.0
    stock_factor = 1.0
    event_due = False

    while cursor < len(events) and str(events[cursor].event_date) <= date:
        event_due = True
        event = events[cursor]
        parsed_cash = _finite_float(event.cash_dividend)
        if event.cash_dividend is not None and (parsed_cash is None or parsed_cash < 0.0):
            cursors[ticker] = cursor + 1
            return state.adj_factor, True
        cash_dividend += parsed_cash or 0.0
        parsed_stock = _finite_float(event.stock_event_factor)
        if event.stock_event_factor is not None and (parsed_stock is None or parsed_stock <= 0.0):
            cursors[ticker] = cursor + 1
            return state.adj_factor, True
        stock_factor *= parsed_stock or 1.0
        cursor += 1

    cursors[ticker] = cursor
    if not event_due:
        return state.adj_factor, False
    if cash_dividend > 0.0:
        previous_close = state.previous_close
        if previous_close is None or previous_close <= cash_dividend:
            return state.adj_factor, True
        stock_factor *= previous_close / (previous_close - cash_dividend)
    return state.adj_factor * stock_factor, False


def _require_events_sorted(events: Sequence[ExpectedAdjustmentEvent]) -> None:
    previous_date: str | None = None
    for event in events:
        event_date = str(event.event_date)
        if previous_date is not None and event_date < previous_date:
            raise ValueError("expected events must be sorted by event_date")
        previous_date = event_date


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
