import json

import pandas as pd

from etf_tricks.tier1.lab import Tier1Lab


def test_lab_reads_immutable_artifacts_and_produces_oof_handoff(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    extension = tmp_path / "extension"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    extension.mkdir()
    dates = pd.date_range("2024-01-01", periods=24)
    (afml / "metadata.json").write_text(
        json.dumps({"trading_sessions": [str(date.date()) for date in dates]}),
        encoding="utf-8",
    )
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
            "net_log_return": [-0.01, 0.02] * 12,
            "target_status": ["resolved_upper", "resolved_lower"] * 12,
        }
    ).to_parquet(targets / "targets.parquet", index=False)
    pd.DataFrame(
        {
            "etf_id": ["x", "y"] * 12,
            "bar_id": range(24),
            "feature_available_at": pd.date_range("2024-01-01 13:30", periods=24, tz="Asia/Taipei"),
            "f_extension": [1.0, 0.0] * 12,
        }
    ).to_parquet(extension / "features.parquet", index=False)

    result = Tier1Lab.from_artifacts(afml, targets, extension).run_oof(["f", "f_extension"], outer_splits=1)

    assert len(result.training_frame) == 24
    assert result.handoff["prediction_kind"].eq("OOF_CALIBRATED").all()
    assert "t1" not in result.handoff


def test_lab_excludes_events_that_resolve_in_the_sealed_interval(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    dates = pd.date_range("2024-01-01", periods=32)
    (afml / "metadata.json").write_text(
        json.dumps({"trading_sessions": [str(date.date()) for date in dates]}),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "etf_id": ["x", "y"] * 16,
            "bar_id": range(32),
            "feature_available_at": pd.date_range("2024-01-01 13:30", periods=32, tz="Asia/Taipei"),
            "f": [0.0, 1.0] * 16,
        }
    ).to_parquet(afml / "tables" / "features.parquet", index=False)
    pd.DataFrame(
        {
            "event_id": [f"event-{i}" for i in range(32)],
            "etf_id": ["x", "y"] * 16,
            "t0_bar_id": range(32),
            "t0_date": dates,
            "exit_date": dates + pd.Timedelta(days=1),
            "y_direction": [-1, 1] * 16,
            "net_log_return": [-0.01, 0.02] * 16,
            "target_status": ["resolved_upper", "resolved_lower"] * 16,
        }
    ).to_parquet(targets / "targets.parquet", index=False)

    result = Tier1Lab.from_artifacts(afml, targets).run_oof(
        ["f"],
        outer_splits=1,
        research_t0_end="2024-01-20",
        research_outcome_before="2024-01-21",
    )

    assert result.training_frame["t0"].max() <= pd.Timestamp("2024-01-20")
    assert result.training_frame["t1"].max() < pd.Timestamp("2024-01-21")
    assert set(result.training_frame["event_id"]) == {f"event-{i}" for i in range(19)}
