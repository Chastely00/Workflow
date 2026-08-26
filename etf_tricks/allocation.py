from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .costs import transaction_cost
from .execution import scheduled_position
from .models import CostPolicy


@dataclass(frozen=True)
class AllocationPlan:
    etf_id: str
    as_of_date: pd.Timestamp
    status: str
    supplied_capital: Decimal
    basket: pd.DataFrame
    orders: pd.DataFrame
    schedule: pd.DataFrame
    total_cost: Decimal
    residual_cash: Decimal


class AllocationPlanner:
    def __init__(self, cost_policy: CostPolicy | None = None) -> None:
        self.cost_policy = cost_policy or CostPolicy()

    def allocate(
        self,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        targets: pd.DataFrame,
        prices: pd.DataFrame,
        execution_dates: Sequence[pd.Timestamp],
        capital: Decimal,
    ) -> AllocationPlan:
        return self._plan(
            etf_id=etf_id,
            as_of_date=as_of_date,
            targets=targets,
            prices=prices,
            execution_dates=execution_dates,
            current_positions={},
            current_cash=Decimal(capital),
            capital_delta=Decimal("0"),
            supplied_capital=Decimal(capital),
        )

    def rebalance(
        self,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        targets: pd.DataFrame,
        prices: pd.DataFrame,
        execution_dates: Sequence[pd.Timestamp],
        current_positions: Mapping[str, int],
        current_cash: Decimal,
        capital_delta: Decimal,
    ) -> AllocationPlan:
        normalized_prices = self._normalize_prices(prices)
        price_lookup = normalized_prices.set_index("ticker")["raw_close"].to_dict()
        positions = self._normalize_positions(current_positions)
        missing = sorted(set(positions).difference(price_lookup))
        if missing:
            raise ValueError(f"missing raw_close for current positions: {missing}")
        gross = Decimal(current_cash) + Decimal(capital_delta) + sum(
            Decimal(quantity) * price_lookup[ticker]
            for ticker, quantity in positions.items()
        )
        return self._plan(
            etf_id=etf_id,
            as_of_date=as_of_date,
            targets=targets,
            prices=normalized_prices,
            execution_dates=execution_dates,
            current_positions=positions,
            current_cash=Decimal(current_cash),
            capital_delta=Decimal(capital_delta),
            supplied_capital=gross,
        )

    def _plan(
        self,
        *,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        targets: pd.DataFrame,
        prices: pd.DataFrame,
        execution_dates: Sequence[pd.Timestamp],
        current_positions: Mapping[str, int],
        current_cash: Decimal,
        capital_delta: Decimal,
        supplied_capital: Decimal,
    ) -> AllocationPlan:
        capital = Decimal(supplied_capital)
        if not capital.is_finite() or capital <= 0:
            raise ValueError("supplied capital must be finite and positive")
        target_frame = self._normalize_targets(targets)
        price_frame = self._normalize_prices(prices)
        dates = tuple(pd.Timestamp(date) for date in execution_dates)
        if not dates:
            raise ValueError("execution_dates cannot be empty")
        if tuple(sorted(set(dates))) != dates:
            raise ValueError("execution_dates must be unique and increasing")

        merged = target_frame.merge(price_frame, on="ticker", how="left", validate="one_to_one")
        if merged["raw_close"].isna().any():
            missing = sorted(merged.loc[merged["raw_close"].isna(), "ticker"])
            raise ValueError(f"missing raw_close for target tickers: {missing}")
        prices_by_ticker = merged.set_index("ticker")["raw_close"].to_dict()
        positions = self._normalize_positions(current_positions)
        missing_positions = sorted(set(positions).difference(prices_by_ticker))
        if missing_positions:
            extra_prices = price_frame.set_index("ticker")["raw_close"].to_dict()
            missing_positions = sorted(set(positions).difference(extra_prices))
            if missing_positions:
                raise ValueError(f"missing raw_close for current positions: {missing_positions}")
            prices_by_ticker.update(extra_prices)

        merged["theoretical_target_notional"] = merged["target_weight"].map(
            lambda weight: float(capital * Decimal(str(weight)))
        )
        merged["target_shares"] = [
            int(
                (capital * Decimal(str(row.target_weight)) / row.raw_close).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            for row in merged.itertuples(index=False)
        ]
        target_quantities = dict(zip(merged["ticker"], merged["target_shares"], strict=True))
        for ticker in positions:
            target_quantities.setdefault(ticker, 0)

        available_cash = Decimal(current_cash) + Decimal(capital_delta)
        if not available_cash.is_finite():
            raise ValueError("current cash and capital delta must be finite")
        sell_orders = {
            ticker: positions.get(ticker, 0) - target
            for ticker, target in target_quantities.items()
            if positions.get(ticker, 0) > target
        }
        sell_costs = {}
        for ticker, quantity in sell_orders.items():
            cost = transaction_cost("sell", quantity, prices_by_ticker[ticker], self.cost_policy)
            sell_costs[ticker] = cost
            available_cash += cost.notional - cost.total

        buy_orders = {
            ticker: target - positions.get(ticker, 0)
            for ticker, target in target_quantities.items()
            if target > positions.get(ticker, 0)
        }
        while self._buy_cost(buy_orders, prices_by_ticker) > available_cash:
            reducible = [ticker for ticker, quantity in buy_orders.items() if quantity > 0]
            if not reducible:
                break
            ticker = max(
                reducible,
                key=lambda value: (
                    (positions.get(value, 0) + buy_orders[value])
                    * prices_by_ticker[value]
                    / capital,
                    value,
                ),
            )
            buy_orders[ticker] -= 1
            target_quantities[ticker] -= 1

        buy_costs = {
            ticker: transaction_cost("buy", quantity, prices_by_ticker[ticker], self.cost_policy)
            for ticker, quantity in buy_orders.items()
            if quantity > 0
        }
        available_cash -= sum(
            (cost.notional + cost.total for cost in buy_costs.values()), Decimal("0")
        )
        if available_cash < 0:
            raise ValueError("allocation produced negative residual cash")

        merged["target_shares"] = merged["ticker"].map(target_quantities).astype(int)
        merged["actual_allocated_notional"] = [
            float(Decimal(row.target_shares) * row.raw_close)
            for row in merged.itertuples(index=False)
        ]
        merged["estimated_commission"] = merged["ticker"].map(
            lambda ticker: float(
                (buy_costs.get(ticker) or sell_costs.get(ticker)).commission
                if ticker in buy_costs or ticker in sell_costs
                else Decimal("0")
            )
        )
        merged["estimated_sell_tax"] = merged["ticker"].map(
            lambda ticker: float(sell_costs[ticker].tax) if ticker in sell_costs else 0.0
        )
        merged["unallocated_odd_lot_difference"] = (
            merged["theoretical_target_notional"] - merged["actual_allocated_notional"]
        )
        merged.insert(0, "as_of_date", pd.Timestamp(as_of_date))
        merged.insert(0, "etf_id", etf_id)

        order_rows = []
        for ticker in sorted(set(sell_orders) | set(buy_orders)):
            sell_quantity = sell_orders.get(ticker, 0)
            buy_quantity = buy_orders.get(ticker, 0)
            net = -sell_quantity if sell_quantity else buy_quantity
            if net == 0:
                continue
            side = "sell" if net < 0 else "buy"
            cost = sell_costs[ticker] if side == "sell" else buy_costs[ticker]
            order_rows.append(
                {
                    "etf_id": etf_id,
                    "as_of_date": pd.Timestamp(as_of_date),
                    "ticker": ticker,
                    "side": side,
                    "net_order_shares": net,
                    "raw_close": float(prices_by_ticker[ticker]),
                    "notional": float(cost.notional),
                    "commission": float(cost.commission),
                    "tax": float(cost.tax),
                    "execution_priority": 1 if side == "sell" else 2,
                }
            )
        orders = pd.DataFrame(order_rows)
        if not orders.empty:
            orders = orders.sort_values(
                ["execution_priority", "ticker"], kind="stable"
            ).reset_index(drop=True)

        schedule_rows = []
        for ticker, net in (
            orders.set_index("ticker")["net_order_shares"].to_dict().items()
            if not orders.empty
            else []
        ):
            previous = 0
            for k, date in enumerate(dates, start=1):
                cumulative = scheduled_position(0, int(net), k, len(dates))
                schedule_rows.append(
                    {
                        "etf_id": etf_id,
                        "as_of_date": pd.Timestamp(as_of_date),
                        "execution_date": date,
                        "ticker": ticker,
                        "scheduled_order_shares": cumulative - previous,
                        "cumulative_scheduled_shares": cumulative,
                    }
                )
                previous = cumulative
        schedule = pd.DataFrame(schedule_rows)
        if not schedule.empty:
            schedule = schedule.sort_values(
                ["execution_date", "ticker"], kind="stable"
            ).reset_index(drop=True)

        total_cost = sum(
            (cost.total for cost in [*sell_costs.values(), *buy_costs.values()]),
            Decimal("0"),
        )
        status = "ready" if sum(target_quantities.values()) > 0 else "infeasible_allocation"
        return AllocationPlan(
            etf_id=etf_id,
            as_of_date=pd.Timestamp(as_of_date),
            status=status,
            supplied_capital=capital,
            basket=merged,
            orders=orders,
            schedule=schedule,
            total_cost=total_cost,
            residual_cash=available_cash,
        )

    @staticmethod
    def _normalize_targets(targets: pd.DataFrame) -> pd.DataFrame:
        required = {"ticker", "stock_name", "target_weight"}
        missing = sorted(required.difference(targets.columns))
        if missing:
            raise ValueError(f"targets missing columns: {missing}")
        frame = targets.loc[:, ["ticker", "stock_name", "target_weight"]].copy()
        frame["ticker"] = frame["ticker"].astype(str)
        if frame["ticker"].duplicated().any():
            raise ValueError("targets contain duplicate tickers")
        frame["target_weight"] = pd.to_numeric(frame["target_weight"], errors="coerce")
        if (
            (~np.isfinite(frame["target_weight"]) | frame["target_weight"].le(0)).any()
            or not math.isclose(frame["target_weight"].sum(), 1.0, abs_tol=1e-10)
        ):
            raise ValueError("target weights must be finite, positive, and sum to one")
        return frame.sort_values("ticker", kind="stable").reset_index(drop=True)

    @staticmethod
    def _normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
        required = {"ticker", "raw_close"}
        missing = sorted(required.difference(prices.columns))
        if missing:
            raise ValueError(f"prices missing columns: {missing}")
        frame = prices.loc[:, ["ticker", "raw_close"]].copy()
        frame["ticker"] = frame["ticker"].astype(str)
        if frame["ticker"].duplicated().any():
            raise ValueError("prices contain duplicate tickers")
        frame["raw_close"] = frame["raw_close"].map(lambda value: Decimal(str(value)))
        if frame["raw_close"].map(lambda value: not value.is_finite() or value <= 0).any():
            raise ValueError("raw_close must be finite and positive")
        return frame

    @staticmethod
    def _normalize_positions(current_positions: Mapping[str, int]) -> dict[str, int]:
        result = {}
        for ticker, quantity in current_positions.items():
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                raise ValueError("current positions must contain non-negative integer shares")
            if quantity:
                result[str(ticker)] = quantity
        return result

    def _buy_cost(
        self, orders: Mapping[str, int], prices: Mapping[str, Decimal]
    ) -> Decimal:
        return sum(
            (
                transaction_cost("buy", quantity, prices[ticker], self.cost_policy).notional
                + transaction_cost("buy", quantity, prices[ticker], self.cost_policy).total
                for ticker, quantity in orders.items()
                if quantity > 0
            ),
            Decimal("0"),
        )
