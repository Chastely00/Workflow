from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from numbers import Number
from types import MappingProxyType

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .costs import CostBreakdown, transaction_cost
from .models import CostPolicy, ETFSpec


class ExecutionInvariantError(ValueError):
    pass


_MARKET_STATE_TRADING = np.int8(1)
_MARKET_STATE_HALTED = np.int8(2)
_MARKET_STATE_MISSING = np.int8(3)
_EXCHANGE_TRADABLE_TRUE = np.int8(1)
_EXCHANGE_TRADABLE_FALSE = np.int8(0)
_EXCHANGE_TRADABLE_UNKNOWN = np.int8(-1)


def _is_boolean(value: object, expected: bool) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value) is expected


@dataclass(frozen=True)
class CorporateActionConversion:
    multiplier: Decimal
    shares: int
    cash: Decimal


@dataclass(frozen=True)
class EngineTables:
    daily_etf: pd.DataFrame
    daily_holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


@dataclass(frozen=True)
class PreparedExecutionMarket:
    date_positions: Mapping[pd.Timestamp, int]
    ticker_positions: Mapping[str, int]
    close: np.ndarray
    adj_close: np.ndarray
    traded_value: np.ndarray
    market_state: np.ndarray
    exchange_tradable: np.ndarray

    def lookup(
        self, date: pd.Timestamp, ticker: str
    ) -> tuple[np.float64, np.float64, np.float64, np.int8, np.int8] | None:
        date_position = self.date_positions.get(pd.Timestamp(date))
        ticker_position = self.ticker_positions.get(str(ticker))
        if date_position is None or ticker_position is None:
            return None
        return (
            self.close[date_position, ticker_position],
            self.adj_close[date_position, ticker_position],
            self.traded_value[date_position, ticker_position],
            self.market_state[date_position, ticker_position],
            self.exchange_tradable[date_position, ticker_position],
        )


def round_half_away_from_zero(value: Decimal) -> int:
    magnitude = abs(Decimal(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(magnitude if value >= 0 else -magnitude)


def scheduled_position(start: int, target: int, k: int, n: int) -> int:
    if n <= 0 or not 1 <= k <= n:
        raise ValueError("schedule requires 1 <= k <= n")
    delta = target - start
    progress = Decimal(delta) * Decimal(k) / Decimal(n)
    return start + round_half_away_from_zero(progress)


def apply_synthetic_corporate_action(
    *,
    shares: int,
    previous_close: Decimal,
    previous_adj_close: Decimal,
    current_close: Decimal,
    current_adj_close: Decimal,
) -> CorporateActionConversion:
    values = (
        previous_close,
        previous_adj_close,
        current_close,
        current_adj_close,
    )
    if shares < 0 or any(not Decimal(value).is_finite() or value <= 0 for value in values):
        raise ExecutionInvariantError("corporate-action inputs must be finite and positive")
    multiplier = (current_adj_close / previous_adj_close) / (
        current_close / previous_close
    )
    exact = Decimal(shares) * multiplier
    converted = int(exact.to_integral_value(rounding=ROUND_FLOOR))
    cash = (exact - Decimal(converted)) * current_close
    return CorporateActionConversion(multiplier, converted, cash)


class PortfolioExecutionEngine:
    def __init__(self, cost_policy: CostPolicy | None = None) -> None:
        self.cost_policy = cost_policy or CostPolicy()

    def run(
        self,
        spec: ETFSpec,
        targets: pd.DataFrame,
        market: pd.DataFrame | PreparedExecutionMarket,
        calendar: TradingCalendar,
        initial_capital: Decimal,
        *,
        security_master: pd.DataFrame | None = None,
    ) -> EngineTables:
        capital = Decimal(initial_capital)
        if not capital.is_finite() or capital <= 0:
            raise ExecutionInvariantError("initial_capital must be finite and positive")
        prepared_market = (
            self._validate_prepared_market(market)
            if isinstance(market, PreparedExecutionMarket)
            else self.prepare_market(market)
        )
        target_frame = self._normalize_targets(targets, spec)
        days = tuple(pd.Timestamp(day) for day in calendar.days)
        effective_delist_sessions = self._effective_delist_sessions(
            security_master, days
        )
        days_by_month: dict[pd.Period, list[pd.Timestamp]] = {}
        for day in days:
            days_by_month.setdefault(day.to_period("M"), []).append(day)
        day_schedule = {
            day: (month, index, len(month_days))
            for month, month_days in days_by_month.items()
            for index, day in enumerate(month_days, start=1)
        }
        targets_by_month = {
            month: group
            for month, group in target_frame.groupby("target_month", sort=False)
        }
        empty_targets = target_frame.iloc[:0]

        cash = capital
        shares: dict[str, int] = {}
        last_valid: dict[str, tuple[pd.Timestamp, Decimal, Decimal]] = {}
        stale_days: dict[str, int] = {}
        backlog: dict[str, int] = {}
        schedule_start: dict[str, int] = {}
        schedule_target: dict[str, int] = {}
        target_weights: dict[str, float] = {}
        lifecycle_blocked: set[str] = set()
        schedule_month: pd.Period | None = None
        schedule_n = 0
        inception: pd.Timestamp | None = None
        previous_nav: float | None = None

        daily_records: list[dict[str, object]] = []
        holding_records: list[dict[str, object]] = []
        trade_records: list[dict[str, object]] = []
        diagnostic_records: list[dict[str, object]] = []

        for date in days:
            month, k, month_day_count = day_schedule[date]
            lifecycle_blocked.update(
                ticker
                for ticker, effective_session in effective_delist_sessions.items()
                if effective_session == date
            )
            for ticker in lifecycle_blocked:
                schedule_start.pop(ticker, None)
                schedule_target.pop(ticker, None)
                backlog.pop(ticker, None)
                target_weights.pop(ticker, None)
            current_tickers = {
                ticker for ticker, quantity in shares.items() if quantity > 0
            }
            if schedule_month == month:
                current_tickers.update(
                    ticker for ticker in schedule_start if ticker not in lifecycle_blocked
                )
                current_tickers.update(
                    ticker for ticker in schedule_target if ticker not in lifecycle_blocked
                )
            month_targets = targets_by_month.get(month, empty_targets)
            if lifecycle_blocked:
                month_targets = month_targets[
                    ~month_targets["ticker"].isin(lifecycle_blocked)
                ]
            current_tickers |= set(month_targets["ticker"])

            ca_by_ticker: dict[str, CorporateActionConversion] = {}
            ca_share_delta: dict[str, int] = {}
            price_state: dict[str, tuple[Decimal | None, pd.Timestamp | None, int]] = {}
            for ticker in sorted(current_tickers):
                row = self._market_row(prepared_market, date, ticker)
                current_close, current_adj = self._valid_pair(row)
                previous = last_valid.get(ticker)
                if current_close is not None and current_adj is not None:
                    if previous is not None and shares.get(ticker, 0) > 0:
                        opening_shares = shares[ticker]
                        conversion = apply_synthetic_corporate_action(
                            shares=shares[ticker],
                            previous_close=previous[1],
                            previous_adj_close=previous[2],
                            current_close=current_close,
                            current_adj_close=current_adj,
                        )
                        shares[ticker] = conversion.shares
                        ca_share_delta[ticker] = conversion.shares - opening_shares
                        cash += conversion.cash
                        ca_by_ticker[ticker] = conversion
                        if schedule_month == month:
                            schedule_start[ticker] = math.floor(
                                schedule_start.get(ticker, 0) * float(conversion.multiplier)
                            )
                            schedule_target[ticker] = math.floor(
                                schedule_target.get(ticker, 0) * float(conversion.multiplier)
                            )
                    last_valid[ticker] = (date, current_close, current_adj)
                    stale_days[ticker] = 0
                    price_state[ticker] = (current_close, date, 0)
                elif previous is not None:
                    stale_days[ticker] = stale_days.get(ticker, 0) + 1
                    price_state[ticker] = (previous[1], previous[0], stale_days[ticker])
                else:
                    price_state[ticker] = (None, None, 0)

            for ticker in sorted(current_tickers):
                if (
                    self._market_state_code(prepared_market, date, ticker)
                    == _MARKET_STATE_MISSING
                ):
                    diagnostic_records.append(
                        {
                            "date": date,
                            "etf_id": spec.etf_id,
                            "ticker": ticker,
                            "diagnostic": "missing_market_state",
                        }
                    )

            forced_commission = Decimal("0")
            forced_tax = Decimal("0")
            for ticker in sorted(list(shares)):
                if (
                    shares.get(ticker, 0) <= 0
                    or effective_delist_sessions.get(ticker) != date
                ):
                    continue
                price, _, _ = price_state.get(ticker, (None, None, 0))
                if price is None:
                    raise ExecutionInvariantError(
                        f"forced delisting lacks last valid close: {ticker} {date.date()}"
                    )
                quantity = shares[ticker]
                cost = transaction_cost("sell", quantity, price, self.cost_policy)
                cash += cost.notional - cost.total
                shares[ticker] = 0
                forced_commission += cost.commission
                forced_tax += cost.tax
                trade_records.append(
                    self._trade_record(
                        date, spec.etf_id, ticker, "sell", -quantity, 0, -quantity,
                        0, price, cost, cash, True,
                        ca_share_delta.get(ticker, 0),
                        ca_by_ticker.get(ticker, CorporateActionConversion(Decimal("1"), 0, Decimal("0"))).cash,
                    )
                )
                schedule_start.pop(ticker, None)
                schedule_target.pop(ticker, None)
                backlog.pop(ticker, None)

            if not month_targets.empty and schedule_month != month:
                assets = cash + sum(
                    Decimal(quantity) * price_state[ticker][0]
                    for ticker, quantity in shares.items()
                    if quantity > 0 and price_state.get(ticker, (None, None, 0))[0] is not None
                )
                schedule_start = {
                    ticker: quantity
                    for ticker, quantity in shares.items()
                    if quantity > 0 and ticker not in lifecycle_blocked
                }
                schedule_target = {ticker: 0 for ticker in schedule_start}
                target_weights = {}
                for target in month_targets.sort_values("ticker", kind="stable").itertuples(index=False):
                    ticker = str(target.ticker)
                    if ticker in lifecycle_blocked:
                        continue
                    weight = float(target.target_weight)
                    target_weights[ticker] = weight
                    price = price_state.get(ticker, (None, None, 0))[0]
                    if price is None:
                        schedule_target[ticker] = shares.get(ticker, 0)
                        diagnostic_records.append(
                            {"date": date, "etf_id": spec.etf_id, "ticker": ticker,
                             "diagnostic": "missing_target_formation_price"}
                        )
                    else:
                        schedule_target[ticker] = int(
                            (assets * Decimal(str(weight)) / price).to_integral_value(
                                rounding=ROUND_FLOOR
                            )
                        )
                schedule_month = month
                schedule_n = month_day_count
                backlog = {ticker: 0 for ticker in schedule_target}

            desired: dict[str, int] = {}
            if schedule_month == month:
                for ticker in sorted(
                    (set(schedule_start) | set(schedule_target)) - lifecycle_blocked
                ):
                    scheduled = scheduled_position(
                        schedule_start.get(ticker, 0),
                        schedule_target.get(ticker, 0),
                        k,
                        schedule_n,
                    )
                    desired[ticker] = scheduled - shares.get(ticker, 0)

            day_commission = forced_commission
            day_tax = forced_tax
            day_trades: list[dict[str, object]] = []
            sell_prices = {
                ticker: self._current_trade_price(prepared_market, date, ticker)
                for ticker, quantity in desired.items()
                if quantity < 0
            }
            sell_quantities = {
                ticker: (
                    0
                    if sell_prices[ticker] is None
                    else min(-desired[ticker], shares.get(ticker, 0))
                )
                for ticker in sell_prices
            }
            projected_shares = shares.copy()
            projected_cash = cash
            for ticker in sorted(sell_quantities):
                executed = sell_quantities[ticker]
                cost = self._cost_or_zero("sell", executed, sell_prices[ticker])
                projected_shares[ticker] = projected_shares.get(ticker, 0) - executed
                projected_cash += cost.notional - cost.total

            buy_desired = {ticker: quantity for ticker, quantity in desired.items() if quantity > 0}
            buy_prices = {
                ticker: self._current_trade_price(prepared_market, date, ticker)
                for ticker in buy_desired
            }
            buy_quantities = self._allocate_buys(
                buy_desired, buy_prices, projected_cash, projected_shares, schedule_target
            )
            if (
                sum(shares.values()) > 0
                and sum(projected_shares.values()) == 0
                and sum(buy_quantities.values()) == 0
            ):
                protected = next(
                    (ticker for ticker in sorted(sell_quantities) if sell_quantities[ticker] > 0),
                    None,
                )
                if protected is not None:
                    sell_quantities[protected] -= 1

            for ticker in sorted(ticker for ticker, quantity in desired.items() if quantity < 0):
                wanted = -desired[ticker]
                price = sell_prices[ticker]
                executed = sell_quantities[ticker]
                cost = self._cost_or_zero("sell", executed, price)
                shares[ticker] = shares.get(ticker, 0) - executed
                cash += cost.notional - cost.total
                day_commission += cost.commission
                day_tax += cost.tax
                unfilled = -(wanted - executed)
                day_trades.append(
                    self._trade_record(
                        date, spec.etf_id, ticker, "sell", desired[ticker],
                        backlog.get(ticker, 0), -executed, unfilled,
                        price, cost, cash, False,
                        ca_share_delta.get(ticker, 0),
                        ca_by_ticker.get(ticker, CorporateActionConversion(Decimal("1"), 0, Decimal("0"))).cash,
                    )
                )
                backlog[ticker] = unfilled

            buy_quantities = self._allocate_buys(
                buy_desired, buy_prices, cash, shares, schedule_target
            )
            for ticker in sorted(buy_desired):
                wanted = buy_desired[ticker]
                price = buy_prices[ticker]
                executed = buy_quantities.get(ticker, 0)
                cost = self._cost_or_zero("buy", executed, price)
                cash -= cost.notional + cost.total
                shares[ticker] = shares.get(ticker, 0) + executed
                day_commission += cost.commission
                unfilled = wanted - executed
                day_trades.append(
                    self._trade_record(
                        date, spec.etf_id, ticker, "buy", wanted,
                        backlog.get(ticker, 0), executed, unfilled,
                        price, cost, cash, False,
                        ca_share_delta.get(ticker, 0),
                        ca_by_ticker.get(ticker, CorporateActionConversion(Decimal("1"), 0, Decimal("0"))).cash,
                    )
                )
                backlog[ticker] = unfilled

            if cash < 0 or any(quantity < 0 for quantity in shares.values()):
                raise ExecutionInvariantError("trade produced negative cash or shares")
            trade_records.extend(day_trades)

            market_values: dict[str, Decimal] = {}
            for ticker, quantity in shares.items():
                if quantity <= 0:
                    continue
                price = price_state.get(ticker, (None, None, 0))[0]
                if price is None:
                    raise ExecutionInvariantError(f"holding cannot be valued: {ticker} {date.date()}")
                market_values[ticker] = Decimal(quantity) * price
            assets = cash + sum(market_values.values(), Decimal("0"))
            if assets <= 0:
                raise ExecutionInvariantError("portfolio assets must remain positive")
            if inception is None and market_values:
                inception = date
            if inception is None:
                continue

            nav = float(Decimal("100") * assets / capital)
            daily_return = np.nan if previous_nav is None else nav / previous_nav - 1.0
            previous_nav = nav
            completion = self._completion_ratio(schedule_start, schedule_target, shares)
            daily_records.append(
                {
                    "date": date,
                    "etf_id": spec.etf_id,
                    "nav": nav,
                    "daily_return": daily_return,
                    "total_assets": float(assets),
                    "cash": float(cash),
                    "invested_weight": float(sum(market_values.values()) / assets),
                    "cash_weight": float(cash / assets),
                    "holdings_count": len(market_values),
                    "commission": float(day_commission),
                    "tax": float(day_tax),
                    "total_cost": float(day_commission + day_tax),
                    "target_completion_ratio": completion,
                    "stale_holding_count": sum(
                        price_state[ticker][2] > 0 for ticker in market_values
                    ),
                    "has_data_quality_flag": any(
                        price_state[ticker][2] > 0 for ticker in market_values
                    ),
                }
            )
            for ticker, market_value in sorted(market_values.items()):
                price, source_date, stale = price_state[ticker]
                conversion = ca_by_ticker.get(
                    ticker,
                    CorporateActionConversion(Decimal("1"), shares[ticker], Decimal("0")),
                )
                holding_records.append(
                    {
                        "date": date,
                        "etf_id": spec.etf_id,
                        "ticker": ticker,
                        "shares": shares[ticker],
                        "raw_close": float(price),
                        "market_value": float(market_value),
                        "actual_weight": float(market_value / assets),
                        "target_weight": target_weights.get(ticker, 0.0),
                        "synthetic_ca_multiplier": float(conversion.multiplier),
                        "synthetic_ca_share_delta": ca_share_delta.get(ticker, 0),
                        "synthetic_ca_cash": float(conversion.cash),
                        "stale_price_days": stale,
                        "source_price_date": source_date,
                    }
                )

        return EngineTables(
            pd.DataFrame(daily_records),
            pd.DataFrame(holding_records),
            pd.DataFrame(trade_records),
            pd.DataFrame(diagnostic_records),
        )

    @classmethod
    def prepare_market(cls, market: pd.DataFrame) -> PreparedExecutionMarket:
        frame = cls._normalize_market(market)
        dates = pd.DatetimeIndex(frame["date"].drop_duplicates())
        tickers = pd.Index(frame["ticker"].drop_duplicates(), dtype=object)
        date_codes = dates.get_indexer(frame["date"])
        ticker_codes = tickers.get_indexer(frame["ticker"])
        shape = (len(dates), len(tickers))

        matrices = []
        for column in ("close", "adj_close", "traded_value"):
            matrix = np.full(shape, np.nan, dtype=np.float64)
            matrix[date_codes, ticker_codes] = pd.to_numeric(
                frame[column], errors="coerce"
            ).to_numpy(dtype=np.float64)
            matrix.setflags(write=False)
            matrices.append(matrix)
        state_matrix = np.full(shape, _MARKET_STATE_MISSING, dtype=np.int8)
        state_codes = frame["market_state"].map(
            {
                "TRADING": _MARKET_STATE_TRADING,
                "HALTED": _MARKET_STATE_HALTED,
                "MISSING": _MARKET_STATE_MISSING,
            }
        ).to_numpy(dtype=np.int8)
        state_matrix[date_codes, ticker_codes] = state_codes
        state_matrix.setflags(write=False)
        tradability_matrix = np.full(
            shape, _EXCHANGE_TRADABLE_UNKNOWN, dtype=np.int8
        )
        tradability_codes = np.array(
            [
                _EXCHANGE_TRADABLE_TRUE
                if _is_boolean(value, True)
                else _EXCHANGE_TRADABLE_FALSE
                if _is_boolean(value, False)
                else _EXCHANGE_TRADABLE_UNKNOWN
                for value in frame["exchange_tradable"]
            ],
            dtype=np.int8,
        )
        tradability_matrix[date_codes, ticker_codes] = tradability_codes
        tradability_matrix.setflags(write=False)
        return PreparedExecutionMarket(
            date_positions=MappingProxyType(
                {pd.Timestamp(date): index for index, date in enumerate(dates)}
            ),
            ticker_positions=MappingProxyType(
                {str(ticker): index for index, ticker in enumerate(tickers)}
            ),
            close=matrices[0],
            adj_close=matrices[1],
            traded_value=matrices[2],
            market_state=state_matrix,
            exchange_tradable=tradability_matrix,
        )

    @staticmethod
    def _normalize_market(market: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "ticker", "close", "adj_close", "traded_value"}
        missing = sorted(required.difference(market.columns))
        if missing:
            raise ExecutionInvariantError(f"market missing columns: {missing}")
        frame = market.copy()
        has_market_state = "market_state" in frame.columns
        has_exchange_tradable = "exchange_tradable" in frame.columns
        if not has_market_state or not has_exchange_tradable:
            raise ExecutionInvariantError(
                "market requires market_state and exchange_tradable"
            )
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["ticker"] = frame["ticker"].astype(str)
        if frame.duplicated(["date", "ticker"]).any():
            raise ExecutionInvariantError("market contains duplicate date-ticker keys")
        allowed_states = {"TRADING", "HALTED", "MISSING"}
        if frame["market_state"].isna().any() or not set(
            frame["market_state"]
        ).issubset(allowed_states):
            raise ExecutionInvariantError("market has invalid market_state")
        for row in frame[["market_state", "exchange_tradable"]].itertuples(index=False):
            if row.market_state == "TRADING" and not _is_boolean(
                row.exchange_tradable, True
            ):
                raise ExecutionInvariantError(
                    "TRADING market_state requires exchange_tradable=True"
                )
            if row.market_state == "HALTED" and not _is_boolean(
                row.exchange_tradable, False
            ):
                raise ExecutionInvariantError(
                    "HALTED market_state requires exchange_tradable=False"
                )
            if row.market_state == "MISSING" and not pd.isna(row.exchange_tradable):
                raise ExecutionInvariantError(
                    "MISSING market_state requires null exchange_tradable"
                )
        return frame.sort_values(["date", "ticker"], kind="stable")

    @staticmethod
    def _validate_prepared_market(
        market: PreparedExecutionMarket,
    ) -> PreparedExecutionMarket:
        matrices = {
            "close": market.close,
            "adj_close": market.adj_close,
            "traded_value": market.traded_value,
            "market_state": market.market_state,
            "exchange_tradable": market.exchange_tradable,
        }
        if not isinstance(market.close, np.ndarray) or market.close.ndim != 2:
            raise ExecutionInvariantError("prepared market matrix shape is invalid")
        shape = market.close.shape
        for name, matrix in matrices.items():
            if not isinstance(matrix, np.ndarray) or matrix.ndim != 2 or matrix.shape != shape:
                raise ExecutionInvariantError(
                    f"prepared market matrix shape is invalid for {name}"
                )
        PortfolioExecutionEngine._validate_prepared_axis(
            "date_positions", market.date_positions, shape[0], dates=True
        )
        PortfolioExecutionEngine._validate_prepared_axis(
            "ticker_positions", market.ticker_positions, shape[1], dates=False
        )
        for name, matrix in (
            ("market_state", market.market_state),
            ("exchange_tradable", market.exchange_tradable),
        ):
            if not np.issubdtype(matrix.dtype, np.integer) or np.issubdtype(
                matrix.dtype, np.bool_
            ):
                raise ExecutionInvariantError(
                    f"prepared {name} must have an integer dtype"
                )
        if not np.issubdtype(market.traded_value.dtype, np.number) or np.isinf(
            market.traded_value
        ).any():
            raise ExecutionInvariantError(
                "prepared traded_value must contain only finite values or nulls"
            )
        allowed_states = {
            _MARKET_STATE_TRADING,
            _MARKET_STATE_HALTED,
            _MARKET_STATE_MISSING,
        }
        allowed_tradability = {
            _EXCHANGE_TRADABLE_TRUE,
            _EXCHANGE_TRADABLE_FALSE,
            _EXCHANGE_TRADABLE_UNKNOWN,
        }
        if not set(np.unique(market.market_state)).issubset(allowed_states):
            raise ExecutionInvariantError("prepared market_state has invalid codes")
        if not set(np.unique(market.exchange_tradable)).issubset(allowed_tradability):
            raise ExecutionInvariantError("prepared exchange_tradable has invalid codes")
        valid_pairs = (
            ((market.market_state == _MARKET_STATE_TRADING) & (market.exchange_tradable == _EXCHANGE_TRADABLE_TRUE))
            | ((market.market_state == _MARKET_STATE_HALTED) & (market.exchange_tradable == _EXCHANGE_TRADABLE_FALSE))
            | ((market.market_state == _MARKET_STATE_MISSING) & (market.exchange_tradable == _EXCHANGE_TRADABLE_UNKNOWN))
        )
        if not valid_pairs.all():
            raise ExecutionInvariantError("prepared market state/tradability pair is invalid")
        return market

    @staticmethod
    def _validate_prepared_axis(
        name: str,
        positions: Mapping[object, object],
        size: int,
        *,
        dates: bool,
    ) -> None:
        if not isinstance(positions, Mapping) or len(positions) != size:
            raise ExecutionInvariantError(f"prepared {name} is invalid")
        values = list(positions.values())
        if any(
            isinstance(value, bool) or not isinstance(value, (int, np.integer))
            for value in values
        ) or set(values) != set(range(size)):
            raise ExecutionInvariantError(f"prepared {name} is invalid")
        keys = list(positions)
        if dates:
            try:
                normalized_keys = [pd.Timestamp(value) for value in keys]
            except (TypeError, ValueError) as exc:
                raise ExecutionInvariantError(f"prepared {name} is invalid") from exc
            if any(value is pd.NaT for value in normalized_keys) or len(
                set(normalized_keys)
            ) != size:
                raise ExecutionInvariantError(f"prepared {name} is invalid")
        elif any(not isinstance(value, str) or not value for value in keys):
            raise ExecutionInvariantError(f"prepared {name} is invalid")

    @staticmethod
    def _normalize_targets(targets: pd.DataFrame, spec: ETFSpec) -> pd.DataFrame:
        required = {"target_month", "ticker", "target_weight"}
        missing = sorted(required.difference(targets.columns))
        if missing:
            raise ExecutionInvariantError(f"targets missing columns: {missing}")
        frame = targets.copy()
        if "etf_id" in frame.columns:
            frame = frame[frame["etf_id"].eq(spec.etf_id)]
        frame["ticker"] = frame["ticker"].astype(str)
        frame["target_month"] = frame["target_month"].map(
            lambda value: value if isinstance(value, pd.Period) else pd.Period(value, freq="M")
        )
        if frame.duplicated(["target_month", "ticker"]).any():
            raise ExecutionInvariantError("targets contain duplicate month-ticker keys")
        weights = pd.to_numeric(frame["target_weight"], errors="coerce")
        if (~np.isfinite(weights) | weights.le(0)).any():
            raise ExecutionInvariantError("target weights must be finite and positive")
        for _, group in frame.groupby("target_month"):
            if not math.isclose(float(group["target_weight"].sum()), 1.0, abs_tol=1e-10):
                raise ExecutionInvariantError("target weights must sum to one by month")
        return frame

    @staticmethod
    def _delist_dates(security_master: pd.DataFrame | None) -> dict[str, pd.Timestamp]:
        if security_master is None or security_master.empty:
            return {}
        if not {"ticker", "delist_date"}.issubset(security_master.columns):
            raise ExecutionInvariantError("security_master requires ticker and delist_date")
        frame = security_master.copy()
        frame["ticker"] = frame["ticker"].astype(str)
        if frame["ticker"].duplicated().any():
            raise ExecutionInvariantError("security_master contains duplicate tickers")
        delist_dates: dict[str, pd.Timestamp] = {}
        for ticker, value in frame[["ticker", "delist_date"]].itertuples(
            index=False
        ):
            normalized = PortfolioExecutionEngine._normalize_delist_date(value)
            if normalized is not None:
                delist_dates[str(ticker)] = normalized
        return delist_dates

    @staticmethod
    def _normalize_delist_date(value: object) -> pd.Timestamp | None:
        if value is None or value is pd.NaT or value is pd.NA:
            return None
        if isinstance(value, (bool, np.bool_)):
            raise ExecutionInvariantError("security_master delist_date cannot be boolean")
        if isinstance(value, Number):
            raise ExecutionInvariantError("security_master delist_date cannot be numeric")
        if isinstance(value, str):
            try:
                parsed = pd.Timestamp(datetime.fromisoformat(value))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ExecutionInvariantError(
                    "security_master contains invalid delist_date"
                ) from exc
        elif isinstance(value, (pd.Timestamp, datetime, date)):
            parsed = pd.Timestamp(value)
        else:
            raise ExecutionInvariantError("security_master contains invalid delist_date")
        if parsed is pd.NaT or pd.isna(parsed):
            raise ExecutionInvariantError("security_master contains invalid delist_date")
        if parsed.tz is not None:
            parsed = parsed.tz_convert("Asia/Taipei").tz_localize(None)
        return parsed.normalize()

    @classmethod
    def _effective_delist_sessions(
        cls,
        security_master: pd.DataFrame | None,
        days: tuple[pd.Timestamp, ...],
    ) -> dict[str, pd.Timestamp]:
        delist_dates = cls._delist_dates(security_master)
        if not delist_dates:
            return {}
        sessions = pd.DatetimeIndex(days)
        if sessions.empty:
            raise ExecutionInvariantError("delist date is outside governed calendar")
        effective_sessions: dict[str, pd.Timestamp] = {}
        for ticker, delist_date in delist_dates.items():
            position = int(sessions.searchsorted(delist_date, side="left"))
            if position >= len(sessions):
                raise ExecutionInvariantError(
                    f"delist date is outside governed calendar: {ticker}"
                )
            effective_sessions[ticker] = pd.Timestamp(sessions[position])
        return effective_sessions

    @staticmethod
    def _market_row(
        market: PreparedExecutionMarket, date: pd.Timestamp, ticker: str
    ) -> tuple[np.float64, np.float64, np.float64, np.int8, np.int8] | None:
        return market.lookup(date, ticker)

    @staticmethod
    def _valid_decimal(value: object) -> Decimal | None:
        try:
            result = Decimal(str(value))
        except Exception:
            return None
        return result if result.is_finite() and result > 0 else None

    @classmethod
    def _valid_pair(
        cls, row: tuple[np.float64, np.float64, np.float64, np.int8, np.int8] | None
    ) -> tuple[Decimal | None, Decimal | None]:
        if row is None:
            return None, None
        close = cls._valid_decimal(row[0])
        adj = cls._valid_decimal(row[1])
        return (close, adj) if close is not None and adj is not None else (None, None)

    @classmethod
    def _current_trade_price(
        cls, market: PreparedExecutionMarket, date: pd.Timestamp, ticker: str
    ) -> Decimal | None:
        row = cls._market_row(market, date, ticker)
        if (
            row is None
            or row[3] != _MARKET_STATE_TRADING
            or row[4] != _EXCHANGE_TRADABLE_TRUE
        ):
            return None
        return cls._valid_decimal(row[0])

    @classmethod
    def _market_state_code(
        cls, market: PreparedExecutionMarket, date: pd.Timestamp, ticker: str
    ) -> np.int8 | None:
        row = cls._market_row(market, date, ticker)
        return None if row is None else row[3]

    def _cost_or_zero(
        self, side: str, quantity: int, price: Decimal | None
    ) -> CostBreakdown:
        if quantity == 0:
            return CostBreakdown(Decimal("0"), Decimal("0"), Decimal("0"))
        if price is None:
            raise ExecutionInvariantError("nonzero execution requires a valid raw close")
        return transaction_cost(side, quantity, price, self.cost_policy)

    def _allocate_buys(
        self,
        desired: dict[str, int],
        prices: dict[str, Decimal | None],
        cash: Decimal,
        shares: dict[str, int],
        targets: dict[str, int],
    ) -> dict[str, int]:
        executable = {ticker: quantity for ticker, quantity in desired.items() if prices[ticker] is not None}
        allocation = {ticker: 0 for ticker in desired}
        if not executable:
            return allocation

        def cost_for(ticker: str, quantity: int) -> Decimal:
            return self._cost_or_zero("buy", quantity, prices[ticker]).notional + self._cost_or_zero(
                "buy", quantity, prices[ticker]
            ).total

        full_cost = sum((cost_for(ticker, quantity) for ticker, quantity in executable.items()), Decimal("0"))
        if full_cost <= cash:
            allocation.update(executable)
            return allocation

        notional = sum(
            (Decimal(quantity) * prices[ticker] for ticker, quantity in executable.items()),
            Decimal("0"),
        )
        ratio = Decimal("0") if notional <= 0 else min(Decimal("1"), cash / notional)
        for ticker, quantity in executable.items():
            allocation[ticker] = math.floor(Decimal(quantity) * ratio)
        while sum((cost_for(t, q) for t, q in allocation.items()), Decimal("0")) > cash:
            reducible = [ticker for ticker, quantity in allocation.items() if quantity > 0]
            if not reducible:
                break
            ticker = sorted(reducible, reverse=True)[0]
            allocation[ticker] -= 1

        while True:
            spent = sum((cost_for(t, q) for t, q in allocation.items()), Decimal("0"))
            candidates = [ticker for ticker in executable if allocation[ticker] < executable[ticker]]
            candidates.sort(
                key=lambda ticker: (
                    (shares.get(ticker, 0) + allocation[ticker])
                    / max(1, targets.get(ticker, 0)),
                    ticker,
                )
            )
            added = False
            for ticker in candidates:
                increment = cost_for(ticker, allocation[ticker] + 1) - cost_for(
                    ticker, allocation[ticker]
                )
                if spent + increment <= cash:
                    allocation[ticker] += 1
                    added = True
                    break
            if not added:
                return allocation

    @staticmethod
    def _completion_ratio(
        start: dict[str, int], target: dict[str, int], actual: dict[str, int]
    ) -> float:
        tickers = set(start) | set(target)
        total = sum(abs(target.get(ticker, 0) - start.get(ticker, 0)) for ticker in tickers)
        if total == 0:
            return 1.0
        remaining = sum(abs(target.get(ticker, 0) - actual.get(ticker, 0)) for ticker in tickers)
        return max(0.0, min(1.0, 1.0 - remaining / total))

    @staticmethod
    def _trade_record(
        date: pd.Timestamp,
        etf_id: str,
        ticker: str,
        side: str,
        scheduled_shares: int,
        backlog_before: int,
        executed_shares: int,
        unfilled_shares: int,
        price: Decimal | None,
        cost: CostBreakdown,
        cash_after: Decimal,
        forced: bool,
        synthetic_ca_share_delta: int = 0,
        synthetic_ca_cash: Decimal = Decimal("0"),
    ) -> dict[str, object]:
        return {
            "date": date,
            "etf_id": etf_id,
            "ticker": ticker,
            "side": side,
            "scheduled_shares": scheduled_shares,
            "backlog_before": backlog_before,
            "executed_shares": executed_shares,
            "unfilled_shares": unfilled_shares,
            "raw_close": np.nan if price is None else float(price),
            "notional": float(cost.notional),
            "commission": float(cost.commission),
            "tax": float(cost.tax),
            "cash_after": float(cash_after),
            "is_forced_delist_liquidation": forced,
            "synthetic_ca_share_delta": synthetic_ca_share_delta,
            "synthetic_ca_cash": float(synthetic_ca_cash),
        }
