import pandas as pd

from etf_tricks.tier1.market_snapshot import ExecutionMarketSnapshot


def test_snapshot_uses_previous_holdings_and_raw_open() -> None:
    holdings = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02")],
            "etf_id": ["x", "x"],
            "ticker": ["A", "B"],
            "actual_weight": [0.6, 0.4],
        }
    )
    prices = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-03")],
            "ticker": ["A", "B"],
            "open": [110.0, 105.0],
            "previous_close": [100.0, 100.0],
            "source_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"] * 2),
            "is_legal_execution": [True, True],
        }
    )

    result = ExecutionMarketSnapshot.from_frames(holdings, prices)

    row = result.iloc[0]
    assert row["date"] == pd.Timestamp("2024-01-03")
    assert row["raw_open_nav"] == 108.0
    assert row["is_legal_execution"]


def test_snapshot_fails_closed_when_a_constituent_is_not_executable() -> None:
    holdings = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "ticker": ["A"], "actual_weight": [1.0]})
    prices = pd.DataFrame({"date": [pd.Timestamp("2024-01-03")], "ticker": ["A"], "open": [110.0], "previous_close": [100.0], "source_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"]), "is_legal_execution": [False]})

    result = ExecutionMarketSnapshot.from_frames(holdings, prices)

    assert not result.iloc[0]["is_legal_execution"]
    assert result.iloc[0]["raw_open_nav"] != result.iloc[0]["raw_open_nav"]
