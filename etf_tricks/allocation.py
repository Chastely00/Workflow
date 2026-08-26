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

        starting_cash = Decimal(current_cash) + Decimal(capital_delta)
        if not starting_cash.is_finite():
            raise ValueError("current cash and capital delta must be finite")
        while True:
            net_orders = {
                ticker: target - positions.get(ticker, 0)
                for ticker, target in target_quantities.items()
                if target != positions.get(ticker, 0)
            }
            schedule, available_cash, feasible = self._simulate_schedule(
                etf_id=etf_id,
                as_of_date=pd.Timestamp(as_of_date),
                net_orders=net_orders,
                prices=prices_by_ticker,
                dates=dates,
                starting_cash=starting_cash,
            )
            if feasible:
                break
            reducible = [
                ticker
                for ticker, target in target_quantities.items()
                if target > positions.get(ticker, 0)
            ]
            if not reducible:
                raise ValueError("scheduled rebalance is not self-financing")
            ticker = max(
                reducible,
                key=lambda value: (
                    target_quantities[value] * prices_by_ticker[value] / capital,
                    value,
                ),
            )
            target_quantities[ticker] -= 1

        cost_by_ticker = (
            schedule.groupby("ticker", as_index=True)[["commission", "tax"]].sum()
            if not schedule.empty
            else pd.DataFrame(columns=["commission", "tax"])
        )

        merged["target_shares"] = merged["ticker"].map(target_quantities).astype(int)
        merged["actual_allocated_notional"] = [
            float(Decimal(row.target_shares) * row.raw_close)
            for row in merged.itertuples(index=False)
        ]
        merged["estimated_commission"] = merged["ticker"].map(
            lambda ticker: float(cost_by_ticker.loc[ticker, "commission"])
            if ticker in cost_by_ticker.index
            else 0.0
        )
        merged["estimated_sell_tax"] = merged["ticker"].map(
            lambda ticker: float(cost_by_ticker.loc[ticker, "tax"])
            if ticker in cost_by_ticker.index
            else 0.0
        )
        merged["unallocated_odd_lot_difference"] = (
            merged["theoretical_target_notional"] - merged["actual_allocated_notional"]
        )
        merged.insert(0, "as_of_date", pd.Timestamp(as_of_date))
        merged.insert(0, "etf_id", etf_id)

        order_rows = []
        for ticker, net in sorted(net_orders.items()):
            side = "sell" if net < 0 else "buy"
            ticker_schedule = schedule[schedule["ticker"].eq(ticker)]
            order_rows.append(
                {
                    "etf_id": etf_id,
                    "as_of_date": pd.Timestamp(as_of_date),
                    "ticker": ticker,
                    "side": side,
                    "net_order_shares": net,
                    "raw_close": float(prices_by_ticker[ticker]),
                    "notional": float(abs(net) * prices_by_ticker[ticker]),
                    "commission": float(ticker_schedule["commission"].sum()),
                    "tax": float(ticker_schedule["tax"].sum()),
                    "execution_priority": 1 if side == "sell" else 2,
                }
            )
        orders = pd.DataFrame(order_rows)
        if not orders.empty:
            orders = orders.sort_values(
                ["execution_priority", "ticker"], kind="stable"
            ).reset_index(drop=True)

        total_cost = Decimal(str(schedule["total_cost"].sum())) if not schedule.empty else Decimal("0")
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

    def _simulate_schedule(
        self,
        *,
        etf_id: str,
        as_of_date: pd.Timestamp,
        net_orders: Mapping[str, int],
        prices: Mapping[str, Decimal],
        dates: Sequence[pd.Timestamp],
        starting_cash: Decimal,
    ) -> tuple[pd.DataFrame, Decimal, bool]:
        planned: dict[tuple[pd.Timestamp, str], tuple[int, int]] = {}
        for ticker, net in sorted(net_orders.items()):
            previous = 0
            for k, date in enumerate(dates, start=1):
                cumulative = scheduled_position(0, int(net), k, len(dates))
                planned[(date, ticker)] = (cumulative - previous, cumulative)
                previous = cumulative

        cash = starting_cash
        rows: list[dict[str, object]] = []
        feasible = cash >= 0
        for date in dates:
            for side in ("sell", "buy"):
                for ticker in sorted(net_orders):
                    quantity, cumulative = planned[(date, ticker)]
                    if (side == "sell" and quantity >= 0) or (side == "buy" and quantity <= 0):
                        continue
                    absolute_quantity = abs(quantity)
                    cost = transaction_cost(
                        side, absolute_quantity, prices[ticker], self.cost_policy
                    )
                    if side == "sell":
                        cash += cost.notional - cost.total
                    else:
                        cash -= cost.notional + cost.total
                    feasible &= cash >= 0
                    rows.append(
                        {
                            "etf_id": etf_id,
                            "as_of_date": as_of_date,
                            "execution_date": date,
                            "ticker": ticker,
                            "scheduled_order_shares": quantity,
                            "cumulative_scheduled_shares": cumulative,
                            "raw_close": float(prices[ticker]),
                            "notional": float(cost.notional),
                            "commission": float(cost.commission),
                            "tax": float(cost.tax),
                            "total_cost": float(cost.total),
                            "cash_after": float(cash),
                            "execution_priority": 1 if side == "sell" else 2,
                        }
                    )
            zero_tickers = [
                ticker
                for ticker in sorted(net_orders)
                if planned[(date, ticker)][0] == 0
            ]
            for ticker in zero_tickers:
                _, cumulative = planned[(date, ticker)]
                rows.append(
                    {
                        "etf_id": etf_id,
                        "as_of_date": as_of_date,
                        "execution_date": date,
                        "ticker": ticker,
                        "scheduled_order_shares": 0,
                        "cumulative_scheduled_shares": cumulative,
                        "raw_close": float(prices[ticker]),
                        "notional": 0.0,
                        "commission": 0.0,
                        "tax": 0.0,
                        "total_cost": 0.0,
                        "cash_after": float(cash),
                        "execution_priority": 3,
                    }
                )
        schedule = pd.DataFrame(rows)
        if not schedule.empty:
            schedule = schedule.sort_values(
                ["execution_date", "execution_priority", "ticker"], kind="stable"
            ).reset_index(drop=True)
        return schedule, cash, bool(feasible and cash >= 0)
