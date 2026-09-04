import pandas as pd
import pytest

from etf_tricks.tier2.artifact import write_tier2_oof_artifact


def test_tier2_oof_artifact_is_research_only_and_refuses_future_columns(tmp_path) -> None:
    handoff = pd.DataFrame(
        {
            "event_id": ["x-1"], "etf_id": ["low_volatility"], "t0_bar_id": [1],
            "p2": [0.7], "accepted": [True], "acceptance_threshold": [0.5],
            "acceptance_reason": ["p2_at_or_above_fold_threshold"],
            "prediction_kind": ["OOF_CALIBRATED"],
            "tier2_decision_available_at": pd.to_datetime(["2024-01-02 14:00+08:00"]),
        }
    )

    manifest = write_tier2_oof_artifact(handoff, tmp_path / "tier2", {"research_only": True, "sealed_status": "NOT_SEALED"})

    assert manifest["schema_version"] == "tier2-oof-v1"
    assert (tmp_path / "tier2" / "oof_handoff.parquet").exists()
    with pytest.raises(ValueError, match="future"):
        write_tier2_oof_artifact(handoff.assign(t1=pd.Timestamp("2024-01-03")), tmp_path / "forbidden", {"research_only": True, "sealed_status": "NOT_SEALED"})
