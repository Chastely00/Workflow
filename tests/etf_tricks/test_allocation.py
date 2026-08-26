from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from etf_tricks.allocation import AllocationPlanner


TARGETS = pd.DataFrame(
    {
        "ticker": ["1101", "1102"],
        "stock_name": ["Alpha", "Beta"],
        "target_weight": [0.5, 0.5],
    }
)
PRICES = pd.DataFrame(
    {"ticker": ["1101", "1102"], "raw_close": [100.0, 200.0]}
)
DATES = tuple(pd.to_datetime(["2025-02-03", "2025-02-04", "2025-02-05"]))


def test_allocate_accepts_arbitrary_capital_and_reconciles_integer_basket():
    planner = AllocationPlanner()
    plan = planner.allocate(
        etf_id="momentum",
        as_of_date="2025-01-31",
        targets=TARGETS,
        prices=PRICES,
        execution_dates=DATES,
        capital=Decimal("1000"),
    )

    assert plan.status == "ready"
    basket = plan.basket.set_index("ticker")
    assert basket["target_shares"].to_dict() == {"1101": 5, "1102": 2}
    assert basket["actual_allocated_notional"].to_dict() == pytest.approx(
        {"1101": 500.0, "1102": 400.0}
    )
    assert plan.total_cost == Decimal("5")
    assert plan.residual_cash == Decimal("95")
    assert sum(basket["actual_allocated_notional"]) + float(plan.total_cost + plan.residual_cash) == pytest.approx(1000.0)


def test_capital_is_not_locked_to_ten_million():
    planner = AllocationPlanner()
    small = planner.allocate("momentum", "2025-01-31", TARGETS, PRICES, DATES, Decimal("1000"))
    large = planner.allocate("momentum", "2025-01-31", TARGETS, PRICES, DATES, Decimal("2500"))
    assert small.basket["target_shares"].tolist() != large.basket["target_shares"].tolist()
    assert large.supplied_capital == Decimal("2500")


def test_infeasible_capital_returns_explicit_status_without_fractional_shares():
    plan = AllocationPlanner().allocate(
        "momentum", "2025-01-31", TARGETS, PRICES, DATES, Decimal("50")
    )
    assert plan.status == "infeasible_allocation"
    assert plan.basket["target_shares"].sum() == 0
    assert plan.residual_cash == Decimal("50")


def test_rebalance_sells_before_buys_and_reconciles_costs_and_cash():
    plan = AllocationPlanner().rebalance(
        etf_id="momentum",
        as_of_date="2025-01-31",
        targets=TARGETS,
        prices=PRICES,
        execution_dates=DATES,
        current_positions={"1101": 10},
        current_cash=Decimal("0"),
        capital_delta=Decimal("0"),
    )

    assert plan.status == "ready"
    orders = plan.orders.set_index("ticker")
    assert orders["net_order_shares"].to_dict() == {"1101": -5, "1102": 1}
    assert orders.loc["1101", "side"] == "sell"
    assert orders.loc["1102", "side"] == "buy"
    assert plan.total_cost == Decimal("6")
    assert plan.residual_cash == Decimal("294")
    assert plan.orders["execution_priority"].tolist() == [1, 2]
    assert plan.schedule["cash_after"].ge(0).all()
    assert plan.schedule["total_cost"].sum() == pytest.approx(float(plan.total_cost))


def test_schedule_uses_every_supplied_trading_day_and_cumulative_rounding():
    plan = AllocationPlanner().rebalance(
        "momentum",
        "2025-01-31",
        TARGETS,
        PRICES,
        DATES,
        current_positions={"1101": 10},
        current_cash=Decimal("0"),
        capital_delta=Decimal("0"),
    )
    schedule = plan.schedule
    assert schedule["execution_date"].nunique() == 3
    assert schedule[schedule["ticker"].eq("1101")]["scheduled_order_shares"].tolist() == [-2, -1, -2]
    assert schedule[schedule["ticker"].eq("1102")]["scheduled_order_shares"].tolist() == [0, 1, 0]
    assert schedule.groupby("ticker")["scheduled_order_shares"].sum().to_dict() == {
        "1101": -5,
        "1102": 1,
    }


def test_missing_price_duplicate_ticker_and_empty_schedule_fail_closed():
    planner = AllocationPlanner()
    with pytest.raises(ValueError, match="missing raw_close"):
        planner.allocate("momentum", "2025-01-31", TARGETS, PRICES.iloc[:1], DATES, Decimal("1000"))
    with pytest.raises(ValueError, match="duplicate"):
        planner.allocate(
            "momentum", "2025-01-31", TARGETS, pd.concat([PRICES, PRICES.iloc[[0]]]), DATES, Decimal("1000")
        )
    with pytest.raises(ValueError, match="execution_dates"):
        planner.allocate("momentum", "2025-01-31", TARGETS, PRICES, (), Decimal("1000"))
