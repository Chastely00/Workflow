import pandas as pd

from etf_tricks.tier1 import research


def test_training_frame_joins_only_resolved_targets_to_pit_features() -> None:
    targets = pd.DataFrame(
        {
            "event_id": ["x-1", "x-2"],
            "etf_id": ["x", "x"],
            "t0_bar_id": [1, 2],
            "t0_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "exit_date": pd.to_datetime(["2024-01-04", "2024-01-05"]),
            "y_direction": [1, -1],
            "net_log_return": [0.02, -0.01],
            "target_status": ["resolved_upper", "unresolved_tail"],
        }
    )
    features = pd.DataFrame(
        {
            "etf_id": ["x", "x"],
            "bar_id": [1, 2],
            "feature_available_at": pd.to_datetime(["2024-01-02 13:30+08:00", "2024-01-03 13:30+08:00"]),
            "f": [0.1, 0.2],
        }
    )

    result = research.build_directional_training_frame(targets, features, ["f"])

    assert result.columns.tolist() == ["event_id", "etf_id", "t0_bar_id", "t0", "t1", "y_direction", "net_log_return", "decision_available_at", "f"]
    assert result[["event_id", "y_direction", "f"]].to_dict("records") == [{"event_id": "x-1", "y_direction": 1, "f": 0.1}]


def test_training_frame_rejects_features_available_after_decision_date() -> None:
    targets = pd.DataFrame({"event_id": ["x-1"], "etf_id": ["x"], "t0_bar_id": [1], "t0_date": pd.to_datetime(["2024-01-02"]), "exit_date": pd.to_datetime(["2024-01-03"]), "y_direction": [1], "net_log_return": [0.02], "target_status": ["resolved_upper"]})
    features = pd.DataFrame({"etf_id": ["x"], "bar_id": [1], "feature_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"]), "f": [0.1]})

    try:
        research.build_directional_training_frame(targets, features, ["f"])
    except ValueError as exc:
        assert "after t0" in str(exc)
    else:
        raise AssertionError("expected PIT availability rejection")


def test_handoff_keeps_only_oof_trading_fields() -> None:
    frame = pd.DataFrame(
        {
            "event_id": ["x-1", "x-2"],
            "etf_id": ["x", "x"],
            "t0_bar_id": [1, 2],
            "t0": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "t1": pd.to_datetime(["2024-01-04", "2024-01-05"]),
            "y_direction": [1, -1],
            "decision_available_at": pd.to_datetime(["2024-01-02 13:30+08:00", "2024-01-03 13:30+08:00"]),
        }
    )
    predictions = pd.DataFrame(
        {
            "p1": [0.7, None],
            "prediction_kind": ["OOF_CALIBRATED", None],
            "candidate_threshold": [0.6, None],
            "is_candidate": [True, None],
            "candidate_reason": ["p1_at_or_above_fold_threshold", None],
        }
    )

    result = research.build_tier1_handoff(frame, predictions)

    assert result.columns.tolist() == ["event_id", "etf_id", "t0_bar_id", "side", "p1", "candidate_indicator", "candidate_threshold", "candidate_reason", "prediction_kind", "decision_available_at"]
    assert result.to_dict("records")[0]["side"] == 1
    assert "t1" not in result and "y_direction" not in result
