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
