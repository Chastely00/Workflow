import pandas as pd

from etf_tricks.tier1.model import oof_logistic_predictions


def test_oof_predictions_are_only_emitted_for_validation_rows() -> None:
    frame = pd.DataFrame({"f": [0.0, 0.2, 0.8, 1.0, 0.4, 0.6], "y_direction": [-1, 1, -1, 1, -1, 1], "t0": pd.date_range("2024-01-01", periods=6), "t1": pd.date_range("2024-01-01", periods=6)})

    result = oof_logistic_predictions(frame, [([0, 1, 2, 3], [4, 5])], ["f"])

    assert result["p1"].isna().tolist() == [True, True, True, True, False, False]
    assert set(result.loc[result["p1"].notna(), "prediction_kind"]) == {"OOF_CALIBRATED"}


def test_oof_rejects_a_training_event_that_resolves_during_validation() -> None:
    frame = pd.DataFrame({"f": [0.0, 1.0, 0.2, 0.8], "y_direction": [-1, 1, -1, 1], "t0": pd.date_range("2024-01-01", periods=4), "t1": pd.date_range("2024-01-02", periods=4)})

    try:
        oof_logistic_predictions(frame, [([0, 1, 2], [3])], ["f"])
    except ValueError as exc:
        assert "resolve" in str(exc)
    else:
        raise AssertionError("expected chronological leakage rejection")


def test_oof_imputes_missing_features_from_the_training_fold() -> None:
    frame = pd.DataFrame({"f": [0.0, 1.0, None, 1.0, None, 0.8], "y_direction": [-1, 1, -1, 1, -1, 1], "t0": pd.date_range("2024-01-01", periods=6), "t1": pd.date_range("2024-01-01", periods=6)})

    result = oof_logistic_predictions(frame, [([0, 1, 2, 3], [4, 5])], ["f"])

    assert result.loc[[4, 5], "p1"].notna().all()


def test_oof_predictions_are_fold_locally_calibrated() -> None:
    frame = pd.DataFrame({"f": [0.0, 1.0] * 6, "y_direction": [-1, 1] * 6, "t0": pd.date_range("2024-01-01", periods=12), "t1": pd.date_range("2024-01-01", periods=12)})

    result = oof_logistic_predictions(frame, [([*range(8)], [8, 9, 10, 11])], ["f"])

    assert set(result.loc[[8, 9, 10, 11], "prediction_kind"]) == {"OOF_CALIBRATED"}


def test_oof_candidate_threshold_is_selected_inside_the_training_fold() -> None:
    frame = pd.DataFrame({"f": [0.0, 1.0] * 6, "y_direction": [-1, 1] * 6, "t0": pd.date_range("2024-01-01", periods=12), "t1": pd.date_range("2024-01-01", periods=12)})

    result = oof_logistic_predictions(frame, [([*range(8)], [8, 9, 10, 11])], ["f"])

    oof = result.loc[[8, 9, 10, 11]]
    assert oof["candidate_threshold"].notna().all()
    assert (oof["is_candidate"] == (oof["p1"] >= oof["candidate_threshold"])).all()
    assert set(oof["candidate_reason"]) <= {"p1_at_or_above_fold_threshold", "p1_below_fold_threshold"}
