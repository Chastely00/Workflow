import pandas as pd
import pytest

from etf_tricks.tier2.frame import build_meta_training_frame


def _inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2024-01-01", periods=3)
    oof = pd.DataFrame(
        {
            "event_id": ["a", "b", "c"],
            "etf_id": ["low_volatility"] * 3,
            "t0_bar_id": [10, 11, 12],
            "p1": [0.7, 0.6, 0.4],
            "candidate_indicator": [True, True, False],
            "prediction_kind": ["OOF_CALIBRATED"] * 3,
            "decision_available_at": dates + pd.Timedelta(hours=14),
        }
    )
    targets = pd.DataFrame(
        {
            "event_id": ["a", "b", "c"],
            "etf_id": ["low_volatility"] * 3,
            "t0_bar_id": [10, 11, 12],
            "t0_date": dates,
            "exit_date": dates + pd.Timedelta(days=5),
            "y_direction": [1, -1, 1],
            "net_log_return": [0.01, -0.01, 0.02],
        }
    )
    features = pd.DataFrame(
        {
            "etf_id": ["low_volatility"] * 3,
            "bar_id": [10, 11, 12],
            "feature_available_at": dates + pd.Timedelta(hours=13),
            "f": [1.0, 2.0, 3.0],
        }
    )
    return oof, targets, features


def test_meta_frame_uses_only_oof_candidates_and_removes_future_columns() -> None:
    oof, targets, features = _inputs()

    frame = build_meta_training_frame(oof, targets, features, ["f"])

    assert frame["event_id"].tolist() == ["a", "b"]
    assert frame["y_meta"].tolist() == [1, 0]
    assert frame["etf_id"].eq("low_volatility").all()
    assert frame["tier2_decision_available_at"].tolist() == oof.loc[:1, "decision_available_at"].tolist()
    assert frame["t1"].tolist() == (targets.loc[:1, "exit_date"].tolist())
    assert {"exit_date", "net_log_return", "y_direction"}.isdisjoint(frame.columns)


def test_meta_frame_rejects_a_candidate_without_oof_prediction() -> None:
    oof, targets, features = _inputs()
    oof.loc[0, "prediction_kind"] = "IN_SAMPLE"

    with pytest.raises(ValueError, match="OOF"):
        build_meta_training_frame(oof, targets, features, ["f"])


def test_meta_frame_rejects_cross_etf_candidate_pooling() -> None:
    oof, targets, features = _inputs()
    extra_oof = oof.iloc[[0]].assign(event_id="d", etf_id="volume_ratio", t0_bar_id=20)
    extra_target = targets.iloc[[0]].assign(event_id="d", etf_id="volume_ratio", t0_bar_id=20)
    extra_feature = features.iloc[[0]].assign(etf_id="volume_ratio", bar_id=20)

    with pytest.raises(ValueError, match="exactly one ETF"):
        build_meta_training_frame(
            pd.concat([oof, extra_oof], ignore_index=True),
            pd.concat([targets, extra_target], ignore_index=True),
            pd.concat([features, extra_feature], ignore_index=True),
            ["f"],
        )
