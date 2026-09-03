import pandas as pd

from etf_tricks.tier1.lab import Tier1Lab


def test_lab_reads_immutable_artifacts_and_produces_oof_handoff(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    dates = pd.date_range("2024-01-01", periods=24)
    pd.DataFrame(
        {
            "etf_id": ["x", "y"] * 12,
            "bar_id": range(24),
            "feature_available_at": pd.date_range("2024-01-01 13:30", periods=24, tz="Asia/Taipei"),
            "f": [0.0, 1.0] * 12,
        }
    ).to_parquet(afml / "tables" / "features.parquet", index=False)
    pd.DataFrame(
        {
            "event_id": [f"event-{i}" for i in range(24)],
            "etf_id": ["x", "y"] * 12,
            "t0_bar_id": range(24),
            "t0_date": dates,
            "exit_date": dates + pd.Timedelta(days=1),
            "y_direction": [-1, 1] * 12,
            "target_status": ["resolved_upper", "resolved_lower"] * 12,
        }
    ).to_parquet(targets / "targets.parquet", index=False)

    result = Tier1Lab.from_artifacts(afml, targets).run_oof(["f"], outer_splits=1)

    assert len(result.training_frame) == 24
    assert result.handoff["prediction_kind"].eq("OOF_CALIBRATED").all()
    assert "t1" not in result.handoff
