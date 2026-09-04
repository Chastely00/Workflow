import pandas as pd
import pytest

from etf_tricks.tier1.long_history import (
    feature_columns_for,
    validate_fold_feature_coverage,
    validate_long_history_research_frame,
)


def test_long_history_research_frame_rejects_outcomes_in_sealed_interval() -> None:
    frame = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-12-20", "2024-12-30"]),
            "t1": pd.to_datetime(["2024-12-31", "2025-01-03"]),
        }
    )

    with pytest.raises(ValueError, match="sealed"):
        validate_long_history_research_frame(
            frame,
            research_t0_end="2024-12-31",
            sealed_start="2025-01-01",
        )


def test_long_history_research_frame_returns_auditable_boundaries() -> None:
    frame = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-12-20", "2024-12-30"]),
            "t1": pd.to_datetime(["2024-12-21", "2024-12-31"]),
        }
    )

    result = validate_long_history_research_frame(
        frame,
        research_t0_end="2024-12-31",
        sealed_start="2025-01-01",
    )

    assert result == {
        "research_t0_end": "2024-12-31",
        "sealed_start": "2025-01-01",
        "research_rows": 2,
    }


def test_fold_feature_coverage_rejects_declared_feature_absent_from_training_fold() -> None:
    frame = pd.DataFrame({"f": [float("nan"), float("nan"), 1.0]})

    with pytest.raises(ValueError, match="f"):
        validate_fold_feature_coverage(frame, [([0, 1], [2])], ["f"])


def test_fold_feature_coverage_reports_nonmissing_counts() -> None:
    frame = pd.DataFrame({"f": [1.0, float("nan"), 1.0]})

    result = validate_fold_feature_coverage(frame, [([0, 1], [2])], ["f"])

    assert result == [{"fold": 0, "training_rows": 2, "training_nonmissing": {"f": 1}}]


def test_long_history_feature_set_selection_is_explicit() -> None:
    base = feature_columns_for("hgb_base_15_v1")
    chip = feature_columns_for("hgb_chip_flow_16_v1")

    assert "chip_net_flow_z_20" not in base
    assert chip == [*base, "chip_net_flow_z_20"]
    with pytest.raises(ValueError, match="unknown"):
        feature_columns_for("not-a-feature-set")
