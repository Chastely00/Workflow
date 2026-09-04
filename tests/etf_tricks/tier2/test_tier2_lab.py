import json

import pandas as pd

from etf_tricks.tier2.lab import Tier2Lab


def test_tier2_lab_emits_only_pit_safe_oof_handoff(tmp_path) -> None:
    afml = tmp_path / "afml"
    targets = tmp_path / "targets"
    (afml / "tables").mkdir(parents=True)
    targets.mkdir()
    dates = pd.date_range("2024-01-01", periods=24)
    (afml / "metadata.json").write_text(json.dumps({"trading_sessions": [str(day.date()) for day in dates]}), encoding="utf-8")
    pd.DataFrame({"etf_id": "low_volatility", "bar_id": range(24), "feature_available_at": dates + pd.Timedelta(hours=13), "f": [0.0, 1.0] * 12}).to_parquet(afml / "tables" / "features.parquet", index=False)
    pd.DataFrame({"event_id": [f"event-{i}" for i in range(24)], "etf_id": "low_volatility", "t0_bar_id": range(24), "t0_date": dates, "exit_date": dates + pd.Timedelta(days=1), "y_direction": [-1, 1] * 12}).to_parquet(targets / "targets.parquet", index=False)
    tier1_oof = pd.DataFrame({"event_id": [f"event-{i}" for i in range(24)], "etf_id": "low_volatility", "t0_bar_id": range(24), "p1": [0.4, 0.8] * 12, "candidate_indicator": [True] * 24, "prediction_kind": "OOF_CALIBRATED", "decision_available_at": dates + pd.Timedelta(hours=14)})

    run = Tier2Lab.from_artifacts(afml, targets).run_oof(tier1_oof, ["f", "p1"], outer_splits=1)

    assert run.handoff["p2"].notna().all()
    assert run.handoff["accepted"].notna().all()
    assert {"t1", "y_meta", "exit_date", "y_direction"}.isdisjoint(run.handoff.columns)
    assert run.handoff["etf_id"].eq("low_volatility").all()
