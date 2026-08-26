from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .costs import CostBreakdown, transaction_cost
from .models import CostPolicy, ETFSpec


class ExecutionInvariantError(ValueError):
    pass


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
        market: pd.DataFrame,
        calendar: TradingCalendar,
        initial_capital: Decimal,
        *,
        security_master: pd.DataFrame | None = None,
    ) -> EngineTables:
        capital = Decimal(initial_capital)
        if not capital.is_finite() or capital <= 0:
            raise ExecutionInvariantError("initial_capital must be finite and positive")
        market_frame = self._normalize_market(market)
        target_frame = self._normalize_targets(targets, spec)
        delist_dates = self._delist_dates(security_master)
        days = tuple(pd.Timestamp(day) for day in calendar.days)

        cash = capital
        shares: dict[str, int] = {}
        last_valid: dict[str, tuple[pd.Timestamp, Decimal, Decimal]] = {}
        stale_days: dict[str, int] = {}
        backlog: dict[str, int] = {}
        schedule_start: dict[str, int] = {}
        schedule_target: dict[str, int] = {}
        target_weights: dict[str, float] = {}
        schedule_month: pd.Period | None = None
        schedule_n = 0
        inception: pd.Timestamp | None = None
        previous_nav: float | None = None

        daily_records: list[dict[str, object]] = []
        holding_records: list[dict[str, object]] = []
        trade_records: list[dict[str, object]] = []
        diagnostic_records: list[dict[str, object]] = []

        indexed_market = market_frame.set_index(["date", "ticker"])
        tickers_by_date = market_frame.groupby("date")["ticker"].apply(list).to_dict()

        for date in days:
            month = date.to_period("M")
            month_days = calendar.month(date)
            k = month_days.index(date) + 1
            current_tickers = set(shares) | set(tickers_by_date.get(date, []))
            month_targets = target_frame[target_frame["target_month"].eq(month)]
            current_tickers |= set(month_targets["ticker"])

            ca_by_ticker: dict[str, CorporateActionConversion] = {}
            price_state: dict[str, tuple[Decimal | None, pd.Timestamp | None, int]] = {}
            for ticker in sorted(current_tickers):
                row = self._market_row(indexed_market, date, ticker)
                current_close, current_adj = self._valid_pair(row)
                previous = last_valid.get(ticker)
                if current_close is not None and current_adj is not None:
                    if previous is not None and shares.get(ticker, 0) > 0:
                        conversion = apply_synthetic_corporate_action(
                            shares=shares[ticker],
                            previous_close=previous[1],
                            previous_adj_close=previous[2],
                            current_close=current_close,
                            current_adj_close=current_adj,
                        )
                        shares[ticker] = conversion.shares
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

            forced_commission = Decimal("0")
            forced_tax = Decimal("0")
            for ticker in sorted(list(shares)):
                if shares.get(ticker, 0) <= 0 or delist_dates.get(ticker) != date:
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
                        0, price, cost, cash, True
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
                schedule_start = {ticker: quantity for ticker, quantity in shares.items() if quantity > 0}
                schedule_target = {ticker: 0 for ticker in schedule_start}
                target_weights = {}
                for target in month_targets.sort_values("ticker", kind="stable").itertuples(index=False):
                    ticker = str(target.ticker)
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
                schedule_n = len(month_days)
                backlog = {ticker: 0 for ticker in schedule_target}

            desired: dict[str, int] = {}
            if schedule_month == month:
                for ticker in sorted(set(schedule_start) | set(schedule_target)):
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
            for ticker in sorted(ticker for ticker, quantity in desired.items() if quantity < 0):
                wanted = -desired[ticker]
                price = self._current_trade_price(indexed_market, date, ticker)
                executed = 0 if price is None else min(wanted, shares.get(ticker, 0))
                if executed and sum(shares.values()) == executed and not any(
                    quantity > 0 and self._current_trade_price(indexed_market, date, buy_ticker) is not None
                    for buy_ticker, quantity in desired.items()
                ):
                    executed = max(0, executed - 1)
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
                        price, cost, cash, False
                    )
                )
                backlog[ticker] = unfilled

            buy_desired = {ticker: quantity for ticker, quantity in desired.items() if quantity > 0}
            buy_prices = {
                ticker: self._current_trade_price(indexed_market, date, ticker)
                for ticker in buy_desired
            }
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
                        price, cost, cash, False
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

    @staticmethod
    def _normalize_market(market: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "ticker", "close", "adj_close", "traded_value"}
        missing = sorted(required.difference(market.columns))
        if missing:
            raise ExecutionInvariantError(f"market missing columns: {missing}")
        frame = market.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["ticker"] = frame["ticker"].astype(str)
        if frame.duplicated(["date", "ticker"]).any():
            raise ExecutionInvariantError("market contains duplicate date-ticker keys")
        return frame.sort_values(["date", "ticker"], kind="stable")

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
        frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
        if frame["ticker"].duplicated().any():
            raise ExecutionInvariantError("security_master contains duplicate tickers")
        return dict(zip(frame["ticker"], frame["delist_date"], strict=True))

    @staticmethod
    def _market_row(indexed: pd.DataFrame, date: pd.Timestamp, ticker: str) -> pd.Series | None:
        try:
            return indexed.loc[(date, ticker)]
        except KeyError:
            return None

    @staticmethod
    def _valid_decimal(value: object) -> Decimal | None:
        try:
            result = Decimal(str(value))
        except Exception:
            return None
        return result if result.is_finite() and result > 0 else None

    @classmethod
    def _valid_pair(cls, row: pd.Series | None) -> tuple[Decimal | None, Decimal | None]:
        if row is None:
            return None, None
        close = cls._valid_decimal(row["close"])
        adj = cls._valid_decimal(row["adj_close"])
        return (close, adj) if close is not None and adj is not None else (None, None)

    @classmethod
    def _current_trade_price(
        cls, indexed: pd.DataFrame, date: pd.Timestamp, ticker: str
    ) -> Decimal | None:
        row = cls._market_row(indexed, date, ticker)
        return None if row is None else cls._valid_decimal(row["close"])

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
        }
