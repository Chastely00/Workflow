import pandas as pd

from etf_tricks.tier1.stateful_gate import evaluate_stateful_gate


def _summary(round_trips: int, *, open_position: bool = False, sharpe: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "etf_id": ["x"],
            "completed_round_trip_count": [round_trips],
            "open_position_at_end": [open_position],
            "sharpe_proxy": [sharpe],
            "mark_price_kind": ["ETF_TRICK_DAILY_NAV_PROXY"],
        }
    )


def test_stateful_summary_classifies_insufficient_completed_round_trips_without_blocking_tier2() -> None:
    result = evaluate_stateful_gate(_summary(6), minimum_completed_round_trips=20)

    assert result["status"] == "INSUFFICIENT_EXECUTED_TRADES"
    assert result["tier2_permitted"] is False


def test_stateful_summary_never_uses_proxy_sharpe_as_tier2_admission() -> None:
    open_result = evaluate_stateful_gate(_summary(20, open_position=True), minimum_completed_round_trips=20)
    failed_result = evaluate_stateful_gate(_summary(20, sharpe=0.0), minimum_completed_round_trips=20)
    passed = evaluate_stateful_gate(_summary(20), minimum_completed_round_trips=20)

    assert open_result["status"] == "MARK_TO_MARKET_ONLY"
    assert failed_result["status"] == "COMPLETED_TRADE_LEDGER"
    assert passed["status"] == "COMPLETED_TRADE_LEDGER"
    assert passed["tier2_permitted"] is False
