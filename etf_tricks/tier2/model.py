"""ETF-local Tier 2 meta-label OOF probability estimation."""

from __future__ import annotations

import pandas as pd

from etf_tricks.tier1.model import oof_logistic_predictions


def oof_meta_predictions(
    frame: pd.DataFrame,
    folds: list[tuple[list[int], list[int]]],
    feature_columns: list[str],
    calibration_splits: int = 2,
    acceptance_threshold_grid: tuple[float, ...] = (0.5, 0.55, 0.6, 0.65, 0.7),
    model_family: str = "logistic_regression",
    trading_sessions: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Fit fresh, fold-local Tier 2 models and emit validation-only ``p2``.

    The shared Tier 1 helper is used only for its audited purged-fold fitting
    mechanics; this invocation constructs distinct Tier 2 estimators from
    ``y_meta`` and never consumes a Tier 1 fitted object.
    """
    required = {"y_meta", "t0", "t1", *feature_columns}
    if missing := required.difference(frame.columns):
        raise ValueError(f"Tier 2 frame missing columns: {sorted(missing)}")
    if not feature_columns or len(set(feature_columns)) != len(feature_columns):
        raise ValueError("Tier 2 feature columns must be nonempty and unique")
    if frame["y_meta"].isna().any() or not frame["y_meta"].isin([0, 1]).all():
        raise ValueError("Tier 2 y_meta must be binary")
    model_frame = frame.copy()
    model_frame["y_direction"] = model_frame["y_meta"].astype(int).mul(2).sub(1)
    result = oof_logistic_predictions(
        model_frame,
        folds,
        feature_columns,
        calibration_splits=calibration_splits,
        candidate_threshold_grid=acceptance_threshold_grid,
        model_family=model_family,
        candidate_threshold_objective="f1",
        trading_sessions=trading_sessions,
        uniqueness_entity_column=None,
    )
    return result.rename(
        columns={
            "p1": "p2",
            "candidate_threshold": "acceptance_threshold",
            "is_candidate": "accepted",
            "candidate_reason": "acceptance_reason",
        }
    )
