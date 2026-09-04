from __future__ import annotations

import numpy as np
import pandas as pd


def build_directional_training_frame(
    targets: pd.DataFrame,
    features: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    """Join resolved Tier 1 targets to the matching PIT feature row."""
    target_required = {"event_id", "etf_id", "t0_bar_id", "t0_date", "exit_date", "y_direction", "net_log_return", "target_status"}
    feature_required = {"etf_id", "bar_id", "feature_available_at", *feature_columns}
    if missing := target_required.difference(targets.columns):
        raise ValueError(f"targets missing columns: {sorted(missing)}")
    if missing := feature_required.difference(features.columns):
        raise ValueError(f"features missing columns: {sorted(missing)}")
    resolved = targets.loc[targets["target_status"].astype(str).str.startswith("resolved_")].copy()
    if resolved.empty:
        raise ValueError("no resolved directional targets")
    frame = resolved.merge(
        features[["etf_id", "bar_id", "feature_available_at", *feature_columns]],
        left_on=["etf_id", "t0_bar_id"],
        right_on=["etf_id", "bar_id"],
        how="inner",
        validate="one_to_one",
    )
    if len(frame) != len(resolved):
        raise ValueError("resolved targets missing matching feature rows")
    frame["t0"] = pd.to_datetime(frame["t0_date"]).dt.normalize()
    frame["t1"] = pd.to_datetime(frame["exit_date"]).dt.normalize()
    frame["decision_available_at"] = pd.to_datetime(frame["feature_available_at"])
    availability_date = frame["decision_available_at"].dt.tz_localize(None).dt.normalize()
    if (availability_date > frame["t0"]).any():
        raise ValueError("feature availability after t0 is not PIT-safe")
    if frame["t1"].isna().any() or (frame["t1"] <= frame["t0"]).any():
        raise ValueError("resolved targets require an executable future exit")
    if frame["y_direction"].isna().any() or ~frame["y_direction"].isin([-1, 1]).all():
        raise ValueError("resolved targets require y_direction in {-1, +1}")
    if ~np.isfinite(pd.to_numeric(frame["net_log_return"], errors="coerce")).all():
        raise ValueError("resolved targets require finite net_log_return")
    return frame[["event_id", "etf_id", "t0_bar_id", "t0", "t1", "y_direction", "net_log_return", "decision_available_at", *feature_columns]].sort_values(
        ["t0", "etf_id", "t0_bar_id"], kind="stable"
    ).reset_index(drop=True)


def build_tier1_handoff(frame: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Return the Tier 1 OOF-only trading hand-off without labels or horizons."""
    required_frame = {"event_id", "etf_id", "t0_bar_id", "decision_available_at"}
    required_predictions = {"p1", "prediction_kind", "candidate_threshold", "is_candidate", "candidate_reason"}
    if missing := required_frame.difference(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(missing)}")
    if missing := required_predictions.difference(predictions.columns):
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if not frame.index.equals(predictions.index):
        raise ValueError("frame and predictions must have identical indexes")
    joined = frame[["event_id", "etf_id", "t0_bar_id", "decision_available_at"]].join(predictions)
    oof = joined.loc[joined["p1"].notna()].copy()
    if not oof["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("hand-off accepts calibrated OOF predictions only")
    if oof[["candidate_threshold", "is_candidate", "candidate_reason"]].isna().any().any():
        raise ValueError("OOF predictions require candidate metadata")
    oof["side"] = 1
    oof["candidate_indicator"] = oof["is_candidate"].astype(bool)
    return oof[["event_id", "etf_id", "t0_bar_id", "side", "p1", "candidate_indicator", "candidate_threshold", "candidate_reason", "prediction_kind", "decision_available_at"]].sort_values(
        ["decision_available_at", "etf_id", "t0_bar_id"], kind="stable"
    ).reset_index(drop=True)
