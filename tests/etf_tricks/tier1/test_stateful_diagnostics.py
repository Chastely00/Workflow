import pandas as pd
import pytest

from etf_tricks.tier1.stateful_diagnostics import summarize_stateful_ledger


def test_stateful_summary_keeps_mark_to_market_separate_from_completed_trades() -> None:
    daily = pd.DataFrame(
        {
            "etf_id": "x",
            "date": pd.date_range("2024-01-02", periods=4, freq="B"),
            "strategy_nav": [100.0, 101.0, 102.0, 103.0],
            "mark_price_kind": "ETF_TRICK_DAILY_NAV_PROXY",
        }
    )
    trades = pd.DataFrame({"side": ["buy"], "commission": [1.0]})

    result = summarize_stateful_ledger(daily, trades)

    assert result.loc[0, "completed_round_trip_count"] == 0
    assert result.loc[0, "open_position_at_end"] == True
    assert result.loc[0, "performance_status"] == "MARK_TO_MARKET_ONLY"
    assert result.loc[0, "final_strategy_nav"] == pytest.approx(103.0)


def test_stateful_summary_rejects_multiple_etfs_or_non_proxy_mixing() -> None:
    daily = pd.DataFrame({"etf_id": ["x", "y"], "date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "strategy_nav": [100.0, 101.0], "mark_price_kind": "ETF_TRICK_DAILY_NAV_PROXY"})
    with pytest.raises(ValueError, match="one ETF"):
        summarize_stateful_ledger(daily, pd.DataFrame(columns=["side", "commission"]))
