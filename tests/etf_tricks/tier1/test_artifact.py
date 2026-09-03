import pandas as pd

from etf_tricks.tier1.artifact import write_oof_artifact, write_target_artifact


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
