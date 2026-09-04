import pandas as pd
import pytest

from etf_tricks.tier1.diagnostics import summarize_per_etf_oof


def test_per_etf_oof_summary_keeps_fold_and_all_metrics() -> None:
    frame = pd.DataFrame(
        {
            "etf_id": ["a", "a", "b", "b", "a", "a", "b", "b"],
            "y_direction": [1, -1, 1, -1, 1, -1, 1, -1],
            "net_log_return": [0.03, -0.02, 0.02, -0.01, 0.04, -0.03, 0.01, -0.02],
        }
    )
    predictions = pd.DataFrame(
        {
            "p1": [0.9, 0.2, 0.8, 0.1, 0.7, 0.3, 0.6, 0.4],
            "is_candidate": [True, False, True, False, True, False, True, False],
        }
    )

    result = summarize_per_etf_oof(
        frame,
        predictions,
        [([4, 5, 6, 7], [0, 1, 2, 3]), ([0, 1, 2, 3], [4, 5, 6, 7])],
    )

    all_a = result.loc[(result["etf_id"] == "a") & (result["scope"] == "ALL")].iloc[0]
    assert all_a["oof_rows"] == 4
    assert all_a["candidate_count"] == 2
    assert all_a["candidate_positive_rate"] == pytest.approx(1.0)
    assert all_a["candidate_mean_net_log_return"] == pytest.approx(0.035)
    assert all_a["base_mean_net_log_return"] == pytest.approx(0.005)
    assert all_a["auc"] == pytest.approx(1.0)

    fold_a = result.loc[(result["etf_id"] == "a") & (result["scope"] == "OUTER_FOLD_0")].iloc[0]
    assert fold_a["oof_rows"] == 2
    assert fold_a["candidate_count"] == 1


def test_per_etf_oof_summary_rejects_validation_index_reuse() -> None:
    frame = pd.DataFrame(
        {"etf_id": ["a", "a", "a"], "y_direction": [1, -1, 1], "net_log_return": [0.01, -0.01, 0.02]}
    )
    predictions = pd.DataFrame({"p1": [0.9, 0.1, 0.8], "is_candidate": [True, False, True]})

    with pytest.raises(ValueError, match="more than one outer validation fold"):
        summarize_per_etf_oof(frame, predictions, [([0], [1, 2]), ([0], [2])])


def test_per_etf_oof_summary_retains_etf_with_no_validation_predictions() -> None:
    frame = pd.DataFrame(
        {"etf_id": ["a", "a", "b"], "y_direction": [1, -1, 1], "net_log_return": [0.01, -0.01, 0.02]}
    )
    predictions = pd.DataFrame({"p1": [0.9, 0.1, None], "is_candidate": [True, False, None]})

    result = summarize_per_etf_oof(frame, predictions, [([2], [0, 1])])

    no_oof = result.loc[(result["etf_id"] == "b") & (result["scope"] == "ALL")].iloc[0]
    assert no_oof["oof_rows"] == 0
    assert pd.isna(no_oof["auc"])


def test_per_etf_oof_summary_retains_expected_etf_with_no_resolved_target() -> None:
    frame = pd.DataFrame(
        {"etf_id": ["a", "a"], "y_direction": [1, -1], "net_log_return": [0.01, -0.01]}
    )
    predictions = pd.DataFrame({"p1": [0.9, 0.1], "is_candidate": [True, False]})

    result = summarize_per_etf_oof(frame, predictions, [([], [0, 1])], expected_etf_ids=["a", "missing"])

    missing = result.loc[(result["etf_id"] == "missing") & (result["scope"] == "ALL")].iloc[0]
    assert missing["training_rows"] == 0
    assert missing["oof_rows"] == 0
