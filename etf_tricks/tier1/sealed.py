"""Strict temporal and universe separation for one Tier 1 sealed evaluation."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .model import oof_logistic_predictions
from .long_history import validate_fold_feature_coverage
from .splits import chronological_purged_folds


def validate_outcome_access_boundary(
    boundary: Mapping[str, object],
    *,
    sealed_start: str | pd.Timestamp,
) -> dict[str, str]:
    """Validate evidence that sealed outcomes were not observable at the boundary.

    Temporal partitioning alone does not make a holdout sealed.  The boundary
    must have been recorded before the held-out interval and must attest that
    outcomes were observable only through a strictly earlier date.
    """
    required = {
        "schema_version",
        "recorded_at",
        "observable_outcomes_through",
        "source_manifest_sha256",
    }
    if missing := required.difference(boundary):
        raise ValueError(f"outcome access boundary missing fields: {sorted(missing)}")
    if boundary["schema_version"] != "afml-outcome-access-boundary-v1":
        raise ValueError("unsupported outcome access boundary schema")
    source_hash = boundary["source_manifest_sha256"]
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(
        character not in "0123456789abcdef" for character in source_hash.lower()
    ):
        raise ValueError("outcome access boundary requires a SHA-256 source manifest hash")
    sealed = pd.Timestamp(sealed_start).normalize()
    observable = pd.Timestamp(boundary["observable_outcomes_through"]).normalize()
    recorded = pd.Timestamp(boundary["recorded_at"])
    if pd.isna(sealed) or pd.isna(observable) or pd.isna(recorded):
        raise ValueError("outcome access boundary requires valid timestamps")
    recorded_day = recorded.tz_convert("UTC").tz_localize(None).normalize() if recorded.tzinfo else recorded.normalize()
    if observable >= sealed:
        raise ValueError("sealed outcomes were already observable at sealed start")
    if recorded_day >= sealed:
        raise ValueError("outcome access boundary was not recorded before sealed start")
    return {
        "schema_version": "afml-outcome-access-boundary-v1",
        "recorded_at": recorded.isoformat(),
        "observable_outcomes_through": str(observable.date()),
        "source_manifest_sha256": source_hash.lower(),
    }


def split_training_and_sealed_frames(
    frame: pd.DataFrame,
    *,
    research_t0_end: str | pd.Timestamp,
    sealed_start: str | pd.Timestamp,
    selected_etf_id: str,
    outcome_access_boundary: Mapping[str, object],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate all pre-sealed mature training events from one ETF's test rows."""
    if missing := {"event_id", "etf_id", "t0", "t1"}.difference(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(missing)}")
    if not selected_etf_id:
        raise ValueError("selected_etf_id is required")
    end = pd.Timestamp(research_t0_end).normalize()
    sealed = pd.Timestamp(sealed_start).normalize()
    validate_outcome_access_boundary(outcome_access_boundary, sealed_start=sealed)
    if end >= sealed:
        raise ValueError("research t0 end must precede sealed start")
    copied = frame.copy()
    copied["t0"] = pd.to_datetime(copied["t0"], errors="coerce").dt.normalize()
    copied["t1"] = pd.to_datetime(copied["t1"], errors="coerce").dt.normalize()
    if copied[["t0", "t1"]].isna().any().any():
        raise ValueError("frame requires valid event times")
    train = copied.loc[copied["t0"].le(end) & copied["t1"].lt(sealed)].copy()
    test = copied.loc[
        copied["etf_id"].eq(selected_etf_id) & copied["t0"].ge(sealed)
    ].copy()
    if train.empty or test.empty:
        raise ValueError("sealed evaluation requires nonempty training and selected test events")
    if not test["etf_id"].eq(selected_etf_id).all() or not test["t0"].ge(sealed).all():
        raise ValueError("sealed test universe or time boundary was violated")
    return train.reset_index(drop=True), test.reset_index(drop=True)


def predict_sealed(
    training: pd.DataFrame,
    sealed: pd.DataFrame,
    feature_columns: list[str],
    *,
    model_family: str = "hist_gradient_boosting",
    categorical_columns: tuple[str, ...] = (),
    trading_sessions: pd.DatetimeIndex | None = None,
    outcome_access_boundary: Mapping[str, object],
) -> pd.DataFrame:
    """Fit from historical rows and return prediction-only selected test rows."""
    required = {
        "event_id", "etf_id", "t0_bar_id", "t0", "t1", "y_direction",
        "net_log_return", "decision_available_at", *feature_columns,
    }
    if missing := required.difference(training.columns) | required.difference(sealed.columns):
        raise ValueError(f"sealed evaluation missing columns: {sorted(missing)}")
    if training.empty or sealed.empty:
        raise ValueError("sealed evaluation requires nonempty training and test rows")
    training_end = pd.to_datetime(training["t1"]).max()
    sealed_start = pd.to_datetime(sealed["t0"]).min()
    validate_outcome_access_boundary(outcome_access_boundary, sealed_start=sealed_start)
    if training_end >= sealed_start:
        raise ValueError("sealed evaluation training outcomes overlap the test interval")
    calibration_folds = chronological_purged_folds(training[["t0", "t1"]], n_splits=2)
    validate_fold_feature_coverage(
        training,
        [(train.tolist(), valid.tolist()) for train, valid in calibration_folds],
        feature_columns,
    )
    combined = pd.concat([training, sealed], ignore_index=True)
    train_rows = list(range(len(training)))
    sealed_rows = list(range(len(training), len(combined)))
    predictions = oof_logistic_predictions(
        combined,
        [(train_rows, sealed_rows)],
        feature_columns,
        model_family=model_family,
        categorical_columns=categorical_columns,
        candidate_threshold_objective="economic_net_log_return",
        trading_sessions=trading_sessions,
        uniqueness_entity_column="etf_id",
    )
    output = combined.iloc[sealed_rows][
        ["event_id", "etf_id", "t0_bar_id", "decision_available_at"]
    ].join(
        predictions.iloc[sealed_rows][
            ["p1", "is_candidate", "candidate_threshold", "candidate_reason"]
        ]
    )
    if output["p1"].isna().any() or output["is_candidate"].isna().any():
        raise ValueError("sealed evaluation did not produce every prediction")
    output["side"] = 1
    output["candidate_indicator"] = output.pop("is_candidate").astype(bool)
    output["prediction_kind"] = "SEALED_CALIBRATED"
    return output[
        [
            "event_id", "etf_id", "t0_bar_id", "side", "p1",
            "candidate_indicator", "candidate_threshold", "candidate_reason",
            "prediction_kind", "decision_available_at",
        ]
    ].reset_index(drop=True)
