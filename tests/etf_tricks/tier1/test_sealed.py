import pandas as pd

from etf_tricks.tier1.sealed import predict_sealed, split_training_and_sealed_frames


def test_sealed_split_keeps_only_selected_etf_after_boundary() -> None:
    frame = pd.DataFrame(
        {
            "event_id": ["a", "b", "c", "d"],
            "etf_id": ["momentum", "market_cap", "momentum", "market_cap"],
            "t0": pd.to_datetime(["2024-12-20", "2024-12-20", "2025-01-02", "2025-01-02"]),
            "t1": pd.to_datetime(["2024-12-30", "2024-12-30", "2025-01-08", "2025-01-08"]),
            "y_direction": [1, -1, 1, -1],
        }
    )

    train, sealed = split_training_and_sealed_frames(
        frame,
        research_t0_end="2024-12-31",
        sealed_start="2025-01-01",
        selected_etf_id="momentum",
    )

    assert set(train["event_id"]) == {"a", "b"}
    assert sealed["event_id"].tolist() == ["c"]
    assert sealed["etf_id"].eq("momentum").all()
    assert train["t1"].lt(pd.Timestamp("2025-01-01")).all()


def test_sealed_prediction_uses_only_historical_training_rows() -> None:
    train = pd.DataFrame(
        {
            "event_id": [f"train-{i}" for i in range(12)],
            "etf_id": ["momentum"] * 12,
            "t0_bar_id": range(12),
            "t0": pd.date_range("2024-01-01", periods=12),
            "t1": pd.date_range("2024-01-02", periods=12),
            "y_direction": [-1, 1] * 6,
            "net_log_return": [-0.01, 0.02] * 6,
            "decision_available_at": pd.date_range("2024-01-01 13:30", periods=12, tz="Asia/Taipei"),
            "f": [0.0, 1.0] * 6,
        }
    )
    sealed = pd.DataFrame(
        {
            "event_id": [f"sealed-{i}" for i in range(4)],
            "etf_id": ["momentum"] * 4,
            "t0_bar_id": range(12, 16),
            "t0": pd.date_range("2025-01-01", periods=4),
            "t1": pd.date_range("2025-01-02", periods=4),
            "y_direction": [-1, 1, -1, 1],
            "net_log_return": [-0.01, 0.02, -0.01, 0.02],
            "decision_available_at": pd.date_range("2025-01-01 13:30", periods=4, tz="Asia/Taipei"),
            "f": [0.2, 0.8, 0.3, 0.7],
        }
    )

    result = predict_sealed(train, sealed, ["f"])

    assert result["event_id"].tolist() == sealed["event_id"].tolist()
    assert result["prediction_kind"].eq("SEALED_CALIBRATED").all()
    assert "y_direction" not in result
