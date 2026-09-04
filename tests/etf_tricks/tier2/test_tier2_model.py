import pandas as pd

from etf_tricks.tier2.model import oof_meta_predictions


def test_meta_oof_emits_only_validation_probabilities_and_acceptance() -> None:
    frame = pd.DataFrame(
        {
            "f": [0.0, 1.0] * 6,
            "p1": [0.4, 0.8] * 6,
            "y_meta": [0, 1] * 6,
            "t0": pd.date_range("2024-01-01", periods=12),
            "t1": pd.date_range("2024-01-01", periods=12),
        }
    )

    result = oof_meta_predictions(frame, [([*range(8)], [8, 9, 10, 11])], ["f", "p1"])

    assert result.loc[:7, "p2"].isna().all()
    assert result.loc[8:, "p2"].notna().all()
    assert result.loc[8:, "prediction_kind"].eq("OOF_CALIBRATED").all()
    assert result.loc[8:, "accepted"].notna().all()


def test_meta_oof_marks_one_class_training_as_insufficient_without_fabricating_p2() -> None:
    frame = pd.DataFrame(
        {
            "f": list(range(8)), "p1": [0.8] * 8,
            "y_meta": [1] * 4 + [0, 1, 0, 1],
            "t0": pd.date_range("2024-01-01", periods=8),
            "t1": pd.date_range("2024-01-01", periods=8),
        }
    )

    result = oof_meta_predictions(frame, [([0, 1, 2, 3], [4, 5, 6, 7])], ["f", "p1"])

    assert result.loc[4:, "p2"].isna().all()
    assert result.loc[4:, "prediction_kind"].eq("INSUFFICIENT_TRAINING_CLASSES").all()
    assert result.loc[4:, "accepted"].eq(False).all()
