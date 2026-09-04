"""Capital-aware raw-open execution ledger for Tier 1 state transitions."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


_TRANSITIONS = {"flat_to_long", "long_to_flat"}


def _commission(notional: float, rate: float, minimum: float) -> float:
    return max(notional * rate, minimum)


def _affordable_shares(cash: float, price: float, rate: float, minimum: float) -> int:
    """Largest whole-share buy that leaves non-negative cash including fee."""
    upper = int(math.floor(cash / price))
    while upper > 0 and upper * price + _commission(upper * price, rate, minimum) > cash + 1e-10:
        upper -= 1
    return upper


def execute_stateful_transitions(
    transitions: pd.DataFrame,
    opens: pd.DataFrame,
    *,
    initial_capital: float,
    buy_cost_rate: float = 0.001425,
    sell_cost_rate: float = 0.003,
    minimum_ticket_fee: float = 1.0,
) -> pd.DataFrame:
    """Execute only real flat/long state transitions at the next legal raw open.

    This is a capital-aware ETF-NAV proxy ledger.  It intentionally does not
    use target labels, future exits, adjusted prices, or a per-candidate
    round-trip charge.  Constituent-level order decomposition is a later
    layer; this ledger establishes whether the Tier 1 switching rule itself
    has executable, non-overlapping economics.
    """
    required_transitions = {"event_id", "etf_id", "t0_bar_id", "decision_available_at", "transition"}
    required_opens = {"etf_id", "date", "raw_open_nav", "is_legal_execution"}
    if missing := required_transitions.difference(transitions.columns):
        raise ValueError(f"transitions missing columns: {sorted(missing)}")
    if missing := required_opens.difference(opens.columns):
        raise ValueError(f"opens missing columns: {sorted(missing)}")
    if not np.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be finite and positive")
    if not (0 <= buy_cost_rate < 1 and 0 <= sell_cost_rate < 1 and minimum_ticket_fee > 0):
        raise ValueError("invalid execution cost configuration")
    if transitions.empty:
        return pd.DataFrame()
    if transitions["etf_id"].nunique(dropna=False) != 1:
        raise ValueError("execution ledger accepts one ETF-local transition stream only")
    if ~transitions["transition"].isin(_TRANSITIONS).all():
        raise ValueError("transition rows must be flat_to_long or long_to_flat")
    frame = transitions.copy()
    frame["decision_available_at"] = pd.to_datetime(frame["decision_available_at"], errors="raise")
    if frame.duplicated("event_id").any() or frame.duplicated("t0_bar_id").any():
        raise ValueError("transition event_id and t0_bar_id must be unique")
    if not frame["t0_bar_id"].is_monotonic_increasing:
        raise ValueError("transition t0_bar_id must be increasing")
    market = opens.copy()
    market["date"] = pd.to_datetime(market["date"], errors="raise").dt.normalize()
    market["raw_open_nav"] = pd.to_numeric(market["raw_open_nav"], errors="coerce")
    market = market.loc[market["is_legal_execution"].eq(True) & np.isfinite(market["raw_open_nav"]) & market["raw_open_nav"].gt(0)].copy()
    if market.duplicated(["etf_id", "date"]).any():
        raise ValueError("opens has duplicate etf_id-date keys")
    market = market.sort_values("date", kind="stable")

    cash = float(initial_capital)
    shares = 0
    last_execution_date: pd.Timestamp | None = None
    records: list[dict[str, object]] = []
    for signal in frame.itertuples(index=False):
        decision_date = pd.Timestamp(signal.decision_available_at).tz_localize(None).normalize()
        earliest_execution_date = decision_date if last_execution_date is None else max(decision_date, last_execution_date)
        fill = market.loc[(market["etf_id"].eq(signal.etf_id)) & market["date"].gt(earliest_execution_date)].head(1)
        if fill.empty:
            raise ValueError(f"no legal raw-open execution after decision for {signal.event_id}")
        execution_date = pd.Timestamp(fill.iloc[0].date)
        last_execution_date = execution_date
        price = float(fill.iloc[0].raw_open_nav)
        cash_before, shares_before = cash, shares
        if signal.transition == "flat_to_long":
            if shares != 0:
                raise ValueError("flat_to_long transition conflicts with actual long position")
            quantity = _affordable_shares(cash, price, buy_cost_rate, minimum_ticket_fee)
            if quantity < 1:
                raise ValueError(f"insufficient capital for one share at {signal.event_id}")
            notional = quantity * price
            commission = _commission(notional, buy_cost_rate, minimum_ticket_fee)
            cash -= notional + commission
            shares += quantity
            side = "buy"
        else:
            if shares < 1:
                raise ValueError("long_to_flat transition conflicts with actual flat position")
            quantity = shares
            notional = quantity * price
            commission = _commission(notional, sell_cost_rate, minimum_ticket_fee)
            cash += notional - commission
            shares = 0
            quantity = -quantity
            side = "sell"
        records.append(
            {
                "event_id": signal.event_id, "etf_id": signal.etf_id, "t0_bar_id": signal.t0_bar_id,
                "decision_available_at": signal.decision_available_at, "transition": signal.transition,
                "execution_date": execution_date, "raw_open_nav": price, "side": side,
                "shares": quantity, "shares_before": shares_before, "shares_after": shares,
                "trade_notional": notional, "commission": commission,
                "cash_before": cash_before, "cash_after": cash,
            }
        )
    return pd.DataFrame(records)
