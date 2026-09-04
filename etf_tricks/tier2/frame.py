"""PIT-safe Tier 2 meta-label training-frame construction."""

from __future__ import annotations

import pandas as pd


_OOF_PREDICTION_KIND = "OOF_CALIBRATED"
_OOF_KEYS = ["event_id", "etf_id", "t0_bar_id"]


def _require_columns(frame: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = columns.difference(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _require_unique(frame: pd.DataFrame, keys: list[str], name: str) -> None:
    if frame.duplicated(keys).any():
        raise ValueError(f"{name} has duplicate keys: {keys}")


def build_meta_training_frame(
    tier1_oof: pd.DataFrame,
    targets: pd.DataFrame,
    features: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    """Return one ETF-local, candidate-only meta-label frame for purged OOF fitting.

    ``t1`` is retained solely for event-end purging.  It is not a model
    feature and must be removed from every Tier 2 prediction hand-off.
    """
    if not feature_columns or len(set(feature_columns)) != len(feature_columns):
        raise ValueError("Tier 2 feature columns must be nonempty and unique")
    _require_columns(
        tier1_oof,
        {"event_id", "etf_id", "t0_bar_id", "p1", "candidate_indicator", "prediction_kind", "decision_available_at"},
        "Tier 1 OOF handoff",
    )
    _require_columns(
        targets,
        {"event_id", "etf_id", "t0_bar_id", "t0_date", "exit_date", "y_direction"},
        "Tier 1 targets",
    )
    _require_columns(
        features,
        {"etf_id", "bar_id", "feature_available_at", *feature_columns},
        "AFML features",
    )
    _require_unique(tier1_oof, _OOF_KEYS, "Tier 1 OOF handoff")
    _require_unique(targets, _OOF_KEYS, "Tier 1 targets")
    _require_unique(features, ["etf_id", "bar_id"], "AFML features")

    candidates = tier1_oof.loc[tier1_oof["candidate_indicator"].astype(bool)].copy()
    if candidates.empty:
        return pd.DataFrame(
            columns=["event_id", "etf_id", "t0_bar_id", "t0", "t1", "p1", *feature_columns, "y_meta", "tier2_decision_available_at"]
        )
    if not candidates["prediction_kind"].eq(_OOF_PREDICTION_KIND).all():
        raise ValueError("Tier 2 accepts only OOF_CALIBRATED Tier 1 candidates")
    if candidates["p1"].isna().any():
        raise ValueError("Tier 1 OOF candidate has null p1")

    source = candidates.loc[:, [* _OOF_KEYS, "p1", "decision_available_at"]].merge(
        targets.loc[:, [*_OOF_KEYS, "t0_date", "exit_date", "y_direction"]],
        on=_OOF_KEYS,
        how="left",
        validate="one_to_one",
    )
    if source[["t0_date", "exit_date", "y_direction"]].isna().any().any():
        raise ValueError("Tier 1 OOF candidate has no resolved target")
    source = source.merge(
        features.loc[:, ["etf_id", "bar_id", "feature_available_at", *feature_columns]],
        left_on=["etf_id", "t0_bar_id"],
        right_on=["etf_id", "bar_id"],
        how="left",
        validate="one_to_one",
    )
    if source["feature_available_at"].isna().any():
        raise ValueError("Tier 1 OOF candidate has no PIT feature row")
    source["decision_available_at"] = pd.to_datetime(source["decision_available_at"], errors="coerce")
    source["feature_available_at"] = pd.to_datetime(source["feature_available_at"], errors="coerce")
    if source[["decision_available_at", "feature_available_at"]].isna().any().any():
        raise ValueError("Tier 2 requires parseable availability timestamps")
    source["tier2_decision_available_at"] = source[["decision_available_at", "feature_available_at"]].max(axis=1)
    source["t0"] = pd.to_datetime(source.pop("t0_date"), errors="coerce")
    source["t1"] = pd.to_datetime(source.pop("exit_date"), errors="coerce")
    if source[["t0", "t1"]].isna().any().any() or not source["t1"].ge(source["t0"]).all():
        raise ValueError("Tier 2 requires valid nondecreasing event intervals")
    source["y_meta"] = (source.pop("y_direction") == 1).astype("int8")
    return source.loc[:, ["event_id", "etf_id", "t0_bar_id", "t0", "t1", "p1", *feature_columns, "y_meta", "tier2_decision_available_at"]].sort_values(
        ["etf_id", "t0", "event_id"], kind="stable"
    ).reset_index(drop=True)
