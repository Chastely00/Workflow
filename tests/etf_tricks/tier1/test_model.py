import pandas as pd

from etf_tricks.tier1.model import oof_logistic_predictions


def test_oof_predictions_are_only_emitted_for_validation_rows() -> None:
    frame = pd.DataFrame({"f": [0.0, 0.2, 0.8, 1.0], "y_direction": [-1, -1, 1, 1], "t0": pd.date_range("2024-01-01", periods=4), "t1": pd.date_range("2024-01-01", periods=4)})

    result = oof_logistic_predictions(frame, [([0, 2], [1, 3])], ["f"])

    assert result["p1"].isna().tolist() == [True, False, True, False]
    assert set(result.loc[result["p1"].notna(), "prediction_kind"]) == {"OOF"}
