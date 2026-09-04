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


def test_lab_runs_oof_in_isolated_etf_partitions(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    dates = pd.date_range("2024-01-01", periods=20)
    etf_ids = ["a"] * 20 + ["b"] * 20
    bar_ids = list(range(20)) * 2
    event_dates = list(dates) * 2
    labels = ([-1, 1] * 10) * 2
    (afml / "metadata.json").write_text(
        json.dumps({"trading_sessions": [str(date.date()) for date in dates]}),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "etf_id": etf_ids,
            "bar_id": bar_ids,
            "feature_available_at": pd.to_datetime(event_dates).tz_localize("Asia/Taipei") + pd.Timedelta(hours=13, minutes=30),
            "f": list(range(20)) * 2,
        }
    ).to_parquet(afml / "tables" / "features.parquet", index=False)
    pd.DataFrame(
        {
            "event_id": [f"event-{etf_id}-{bar_id}" for etf_id, bar_id in zip(etf_ids, bar_ids)],
            "etf_id": etf_ids,
            "t0_bar_id": bar_ids,
            "t0_date": event_dates,
            "exit_date": pd.to_datetime(event_dates) + pd.Timedelta(days=1),
            "y_direction": labels,
            "net_log_return": [-0.01 if label == -1 else 0.02 for label in labels],
            "target_status": ["resolved_lower" if label == -1 else "resolved_upper" for label in labels],
        }
    ).to_parquet(targets / "targets.parquet", index=False)

    result = Tier1Lab.from_artifacts(afml, targets).run_oof_per_etf(["f"], outer_splits=1)

    assert set(result.by_etf) == {"a", "b"}
    assert result.by_etf["a"].training_frame["etf_id"].eq("a").all()
    assert result.by_etf["b"].training_frame["etf_id"].eq("b").all()
    assert result.by_etf["a"].handoff["etf_id"].eq("a").all()
    assert result.by_etf["b"].handoff["etf_id"].eq("b").all()

    changed = pd.read_parquet(targets / "targets.parquet")
    changed.loc[changed["etf_id"].eq("b"), "y_direction"] *= -1
    changed.loc[changed["etf_id"].eq("b"), "net_log_return"] *= -10
    changed.to_parquet(targets / "targets.parquet", index=False)
    rerun = Tier1Lab.from_artifacts(afml, targets).run_oof_per_etf(["f"], outer_splits=1)

    pd.testing.assert_frame_equal(
        result.by_etf["a"].predictions.reset_index(drop=True),
        rerun.by_etf["a"].predictions.reset_index(drop=True),
    )


def test_etf_local_oof_discards_only_pre_feature_availability_warmup(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    dates = pd.date_range("2024-01-01", periods=20)
    values = [None] * 4 + list(range(4, 20))
    (afml / "metadata.json").write_text(json.dumps({"trading_sessions": [str(date.date()) for date in dates]}), encoding="utf-8")
    pd.DataFrame({
        "etf_id": ["a"] * 20, "bar_id": range(20),
        "feature_available_at": dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=13, minutes=30),
        "f": values,
    }).to_parquet(afml / "tables" / "features.parquet", index=False)
    labels = [-1, 1] * 10
    pd.DataFrame({
        "event_id": [f"event-{i}" for i in range(20)], "etf_id": "a", "t0_bar_id": range(20),
        "t0_date": dates, "exit_date": dates + pd.Timedelta(days=1), "y_direction": labels,
        "net_log_return": [-0.01 if label == -1 else 0.02 for label in labels],
        "target_status": ["resolved_lower" if label == -1 else "resolved_upper" for label in labels],
    }).to_parquet(targets / "targets.parquet", index=False)

    run = Tier1Lab.from_artifacts(afml, targets).run_oof_per_etf(["f"], outer_splits=1).by_etf["a"]

    assert run.warmup_dropped_rows == 4
    assert run.training_frame["event_id"].iloc[0] == "event-4"
