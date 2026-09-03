from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score
from sklearn.preprocessing import StandardScaler

from .splits import chronological_purged_folds


def _fit_predict_probability(train: pd.DataFrame, valid: pd.DataFrame, feature_columns: list[str]) -> np.ndarray:
    y = (train["y_direction"] == 1).astype(int)
    if y.nunique() != 2:
        raise ValueError("training fold requires both classes")
    imputer = SimpleImputer(strategy="median").fit(train[feature_columns])
    train_features = imputer.transform(train[feature_columns])
    valid_features = imputer.transform(valid[feature_columns])
    scaler = StandardScaler().fit(train_features)
    model = LogisticRegression(random_state=0, max_iter=1000).fit(scaler.transform(train_features), y)
    return model.predict_proba(scaler.transform(valid_features))[:, 1]


def _fold_local_calibrator(train: pd.DataFrame, feature_columns: list[str], n_splits: int) -> tuple[LogisticRegression, np.ndarray, np.ndarray]:
    folds = chronological_purged_folds(train[["t0", "t1"]], n_splits=n_splits)
    probabilities = np.full(len(train), np.nan)
    for inner_train, inner_valid in folds:
        probabilities[inner_valid] = _fit_predict_probability(train.iloc[inner_train], train.iloc[inner_valid], feature_columns)
    usable = np.isfinite(probabilities)
    target = (train.loc[usable, "y_direction"] == 1).astype(int)
    if target.nunique() != 2:
        raise ValueError("calibration evidence requires both classes")
    logits = np.log(np.clip(probabilities[usable], 1e-6, 1 - 1e-6) / (1 - np.clip(probabilities[usable], 1e-6, 1 - 1e-6)))
    calibrator = LogisticRegression(random_state=0, max_iter=1000).fit(logits.reshape(-1, 1), target)
    calibrated = calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]
    return calibrator, calibrated, target.to_numpy()


def _select_candidate_threshold(probabilities: np.ndarray, target: np.ndarray, grid: tuple[float, ...]) -> float:
    if not grid or any(not 0 < value < 1 for value in grid):
        raise ValueError("candidate threshold grid must be within (0, 1)")
    return max((f1_score(target, probabilities >= value, zero_division=0), value) for value in grid)[1]


def oof_logistic_predictions(
    frame: pd.DataFrame,
    folds: list[tuple[list[int], list[int]]],
    feature_columns: list[str],
    calibration_splits: int = 2,
    candidate_threshold_grid: tuple[float, ...] = (0.5, 0.55, 0.6, 0.65, 0.7),
) -> pd.DataFrame:
    """Fit preprocessing/model on each supplied train fold and emit validation-only p1."""
    required = set(feature_columns) | {"y_direction", "t0", "t1"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(missing)}")
    if calibration_splits <= 0:
        raise ValueError("calibration_splits must be positive")
    result = pd.DataFrame(
        index=frame.index,
        data={
            "p1": np.nan,
            "prediction_kind": pd.Series(pd.NA, index=frame.index, dtype="string"),
            "candidate_threshold": np.nan,
            "is_candidate": pd.Series(pd.NA, index=frame.index, dtype="boolean"),
            "candidate_reason": pd.Series(pd.NA, index=frame.index, dtype="string"),
        },
    )
    for train_rows, validation_rows in folds:
        train = frame.iloc[train_rows]
        valid = frame.iloc[validation_rows]
        if set(train_rows).intersection(validation_rows):
            raise ValueError("train and validation rows overlap")
        if not (pd.to_datetime(train["t1"]) < pd.to_datetime(valid["t0"]).min()).all():
            raise ValueError("training events must resolve before validation begins")
        raw_probability = _fit_predict_probability(train, valid, feature_columns)
        calibrator, calibration_probability, calibration_target = _fold_local_calibrator(train.reset_index(drop=True), feature_columns, calibration_splits)
        threshold = _select_candidate_threshold(calibration_probability, calibration_target, candidate_threshold_grid)
        logits = np.log(np.clip(raw_probability, 1e-6, 1 - 1e-6) / (1 - np.clip(raw_probability, 1e-6, 1 - 1e-6)))
        probability = calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]
        candidate = probability >= threshold
        result.iloc[validation_rows, result.columns.get_loc("p1")] = probability
        result.iloc[validation_rows, result.columns.get_loc("prediction_kind")] = "OOF_CALIBRATED"
        result.iloc[validation_rows, result.columns.get_loc("candidate_threshold")] = threshold
        result.iloc[validation_rows, result.columns.get_loc("is_candidate")] = candidate
        result.iloc[validation_rows, result.columns.get_loc("candidate_reason")] = np.where(candidate, "p1_at_or_above_fold_threshold", "p1_below_fold_threshold")
    return result
