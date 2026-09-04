"""Descriptive status classifier for an ETF-local stateful Tier 1 proxy ledger."""

from __future__ import annotations

import pandas as pd


def evaluate_stateful_gate(summary: pd.DataFrame, *, minimum_completed_round_trips: int) -> dict[str, object]:
    """Classify execution evidence without using it to admit Tier 2."""
    required = {"etf_id", "completed_round_trip_count", "open_position_at_end", "sharpe_proxy", "mark_price_kind"}
    if missing := required.difference(summary.columns):
        raise ValueError(f"stateful summary missing columns: {sorted(missing)}")
    if len(summary) != 1 or summary["etf_id"].isna().any():
        raise ValueError("stateful gate requires exactly one ETF-local summary row")
    if minimum_completed_round_trips < 1:
        raise ValueError("minimum_completed_round_trips must be positive")
    row = summary.iloc[0]
    completed = int(row["completed_round_trip_count"])
    result: dict[str, object] = {
        "etf_id": str(row["etf_id"]),
        "minimum_completed_round_trips": minimum_completed_round_trips,
        "completed_round_trip_count": completed,
        "mark_price_kind": str(row["mark_price_kind"]),
        "tier2_permitted": False,
        "tier3_permitted": False,
        "paper_trade_permitted": False,
    }
    if bool(row["open_position_at_end"]):
        return {**result, "status": "MARK_TO_MARKET_ONLY", "reason": "proxy ledger ends with an open position; it is not an admission decision"}
    if completed < minimum_completed_round_trips:
        return {**result, "status": "INSUFFICIENT_EXECUTED_TRADES", "reason": "completed trades are reported for uncertainty, not used to block Tier 2"}
    return {**result, "status": "COMPLETED_TRADE_LEDGER", "reason": "closed proxy ledger is descriptive only; Tier 2 uses the ETF-local model/economic gate"}
