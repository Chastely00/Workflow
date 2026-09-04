"""Descriptive diagnostics for a non-overlapping Tier 1 stateful ledger."""

from __future__ import annotations

import numpy as np
import pandas as pd


def summarize_stateful_ledger(daily_nav: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Summarize one ETF without confusing proxy marks with completed evidence."""
    required_daily = {"etf_id", "date", "strategy_nav", "mark_price_kind"}
    required_trades = {"side", "commission"}
    if missing := required_daily.difference(daily_nav.columns):
        raise ValueError(f"daily_nav missing columns: {sorted(missing)}")
    if missing := required_trades.difference(trades.columns):
        raise ValueError(f"trades missing columns: {sorted(missing)}")
    if daily_nav.empty or daily_nav["etf_id"].nunique(dropna=False) != 1:
        raise ValueError("daily_nav must contain one ETF-local nonempty series")
    if daily_nav.duplicated("date").any():
        raise ValueError("daily_nav dates must be unique")
    nav = daily_nav.copy().sort_values("date", kind="stable")
    value = pd.to_numeric(nav["strategy_nav"], errors="coerce")
    if not np.isfinite(value).all() or value.le(0).any():
        raise ValueError("strategy_nav must be finite and positive")
    if nav["mark_price_kind"].nunique(dropna=False) != 1:
        raise ValueError("daily_nav must use exactly one declared mark price kind")
    returns = np.log(value).diff().dropna()
    volatility = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else np.nan
    sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 and returns.std(ddof=1) > 0 else np.nan
    drawdown = float((value / value.cummax() - 1.0).min())
    completed = int(trades["side"].eq("sell").sum())
    open_position = int(trades["side"].eq("buy").sum()) > completed
    status = "MARK_TO_MARKET_ONLY" if open_position else "COMPLETED_TRADE_LEDGER"
    return pd.DataFrame(
        [
            {
                "etf_id": str(nav["etf_id"].iloc[0]),
                "daily_observation_count": int(len(nav)),
                "first_date": pd.Timestamp(nav["date"].iloc[0]),
                "last_date": pd.Timestamp(nav["date"].iloc[-1]),
                "transition_count": int(len(trades)),
                "completed_round_trip_count": completed,
                "open_position_at_end": open_position,
                "total_commission": float(pd.to_numeric(trades["commission"], errors="coerce").sum()),
                "initial_strategy_nav": float(value.iloc[0]),
                "final_strategy_nav": float(value.iloc[-1]),
                "annualized_volatility_proxy": volatility,
                "sharpe_proxy": sharpe,
                "max_drawdown_proxy": drawdown,
                "mark_price_kind": str(nav["mark_price_kind"].iloc[0]),
                "performance_status": status,
            }
        ]
    )
