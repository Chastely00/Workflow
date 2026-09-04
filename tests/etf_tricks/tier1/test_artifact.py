import pandas as pd
import pytest

from etf_tricks.tier1.artifact import (
    write_feature_extension_artifact,
    write_oof_artifact,
    write_sealed_artifact,
    write_target_artifact,
)


def test_target_artifact_writes_manifest_and_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "tier1"
    result = write_target_artifact(pd.DataFrame({"event_id": ["x"], "y_direction": [1]}), output, {"input_hash": "abc"})
    assert (output / "targets.parquet").exists()
    assert result["tables"]["targets"]["sha256"]
    try:
        write_target_artifact(pd.DataFrame({"event_id": ["x"], "y_direction": [1]}), output, {})
    except FileExistsError:
        pass
    else:
        raise AssertionError("overwrite must fail")


def test_oof_artifact_refuses_future_target_columns(tmp_path) -> None:
    output = tmp_path / "tier1-oof"
    handoff = pd.DataFrame({"event_id": ["x-1"], "etf_id": ["x"], "t0_bar_id": [1], "side": [1], "p1": [0.6], "candidate_indicator": [True], "candidate_threshold": [0.5], "candidate_reason": ["p1_at_or_above_fold_threshold"], "prediction_kind": ["OOF_CALIBRATED"], "decision_available_at": pd.to_datetime(["2024-01-02 13:30+08:00"])})

    result = write_oof_artifact(handoff, output, {"input_hash": "abc"})

    assert (output / "oof_handoff.parquet").exists()
    assert result["schema_version"] == "tier1-oof-v1"
    try:
        write_oof_artifact(handoff.assign(t1=pd.Timestamp("2024-01-03")), tmp_path / "forbidden", {})
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("OOF hand-off must reject future target fields")


def test_feature_extension_artifact_requires_unique_pit_keys(tmp_path) -> None:
    extension = pd.DataFrame(
        {
            "etf_id": ["x"],
            "bar_id": [1],
            "feature_available_at": pd.to_datetime(["2024-01-02 18:00+08:00"]),
            "bar_log_return_std_14": [0.01],
        }
    )

    result = write_feature_extension_artifact(extension, tmp_path / "extension", {"input_hash": "abc"})

    assert (tmp_path / "extension" / "features.parquet").exists()
    assert result["schema_version"] == "tier1-feature-extension-v1"


def test_sealed_artifact_refuses_label_columns_and_unselected_etf(tmp_path) -> None:
    predictions = pd.DataFrame(
        {
            "event_id": ["momentum-1"],
            "etf_id": ["momentum"],
            "t0_bar_id": [1],
            "side": [1],
            "p1": [0.6],
            "candidate_indicator": [True],
            "candidate_threshold": [0.5],
            "candidate_reason": ["p1_at_or_above_fold_threshold"],
            "prediction_kind": ["SEALED_CALIBRATED"],
            "decision_available_at": pd.to_datetime(["2025-01-02 13:30+08:00"]),
        }
    )

    result = write_sealed_artifact(predictions, tmp_path / "sealed", {"selected_etf_id": "momentum"})

    assert result["schema_version"] == "tier1-sealed-v1"
    with pytest.raises(ValueError, match="future"):
        write_sealed_artifact(predictions.assign(y_direction=1), tmp_path / "label", {"selected_etf_id": "momentum"})
    with pytest.raises(ValueError, match="selected"):
        write_sealed_artifact(predictions.assign(etf_id="market_cap"), tmp_path / "other", {"selected_etf_id": "momentum"})
