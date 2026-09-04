"""Capital-aware raw-open execution ledger for Tier 1 state transitions."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


_TRANSITIONS = {"flat_to_long", "long_to_flat"}


@dataclass(frozen=True)
class StatefulLedgerTables:
    """Daily mark-to-market and the only executable transition tickets."""

    daily_nav: pd.DataFrame
    trades: pd.DataFrame


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


def materialize_etf_ledger(
    transitions: pd.DataFrame,
    opens: pd.DataFrame,
    daily_nav: pd.DataFrame,
    *,
    initial_capital: float,
    buy_cost_rate: float = 0.001425,
    sell_cost_rate: float = 0.003,
    minimum_ticket_fee: float = 1.0,
) -> StatefulLedgerTables:
    """Build a daily ETF-NAV proxy ledger from raw-open state-transition fills.

    The execution price is the constituent-derived ``raw_open_nav``. Daily
    marking is the already-produced ETF Trick NAV, explicitly a proxy pending
    the constituent ticket ledger; it never supplies a fill price or a signal.
    """
    required = {"etf_id", "date", "nav"}
    if missing := required.difference(daily_nav.columns):
        raise ValueError(f"daily_nav missing columns: {sorted(missing)}")
    if daily_nav.empty:
        raise ValueError("daily_nav is empty")
    daily = daily_nav.loc[:, ["etf_id", "date", "nav"]].copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="raise").dt.normalize()
    daily["nav"] = pd.to_numeric(daily["nav"], errors="coerce")
    if daily["etf_id"].nunique(dropna=False) != 1 or daily.duplicated(["etf_id", "date"]).any():
        raise ValueError("daily_nav must be a unique ETF-local daily series")
    if ~np.isfinite(daily["nav"]).all() or daily["nav"].le(0).any():
        raise ValueError("daily_nav requires finite positive nav")
    daily = daily.sort_values("date", kind="stable").reset_index(drop=True)
    trades = execute_stateful_transitions(
        transitions,
        opens,
        initial_capital=initial_capital,
        buy_cost_rate=buy_cost_rate,
        sell_cost_rate=sell_cost_rate,
        minimum_ticket_fee=minimum_ticket_fee,
    )
    if not trades.empty and set(trades["execution_date"]).difference(set(daily["date"])):
        raise ValueError("daily_nav does not cover every execution date")
    trade_by_date = {pd.Timestamp(row.execution_date): row for row in trades.itertuples(index=False)}
    cash = float(initial_capital)
    shares = 0
    rows: list[dict[str, object]] = []
    for row in daily.itertuples(index=False):
        trade = trade_by_date.get(pd.Timestamp(row.date))
        commission = 0.0
        transition: str | None = None
        if trade is not None:
            cash = float(trade.cash_after)
            shares = int(trade.shares_after)
            commission = float(trade.commission)
            transition = str(trade.transition)
        total_assets = cash + shares * float(row.nav)
        rows.append(
            {
                "etf_id": row.etf_id,
                "date": row.date,
                "mark_nav": float(row.nav),
                "cash": cash,
                "shares": shares,
                "total_assets": total_assets,
                "strategy_nav": 100.0 * total_assets / float(initial_capital),
                "transition": transition,
                "commission": commission,
                "mark_price_kind": "ETF_TRICK_DAILY_NAV_PROXY",
            }
        )
    marked = pd.DataFrame(rows)
    marked["daily_log_return"] = np.log(marked["strategy_nav"]).diff()
    return StatefulLedgerTables(daily_nav=marked, trades=trades)
