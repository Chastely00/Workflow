from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .splits import average_uniqueness, chronological_purged_folds


def _make_model(model_family: str):
    if model_family == "logistic_regression":
        return LogisticRegression(random_state=0, max_iter=1000)
    if model_family == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(learning_rate=0.05, max_iter=100, max_leaf_nodes=7, l2_regularization=1.0, random_state=0)
    raise ValueError(f"unsupported model_family: {model_family}")


def _fit_predict_probability(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    feature_columns: list[str],
    model_family: str,
    categorical_columns: tuple[str, ...],
    sample_weight: np.ndarray,
) -> np.ndarray:
    y = (train["y_direction"] == 1).astype(int)
    if y.nunique() != 2:
        raise ValueError("training fold requires both classes")
    imputer = SimpleImputer(strategy="median").fit(train[feature_columns])
    train_features = imputer.transform(train[feature_columns])
    valid_features = imputer.transform(valid[feature_columns])
    scaler = StandardScaler().fit(train_features)
    train_features = scaler.transform(train_features)
    valid_features = scaler.transform(valid_features)
    if categorical_columns:
        encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(train.loc[:, categorical_columns].astype("string").fillna("<MISSING>"))
        train_features = np.hstack((train_features, encoder.transform(train.loc[:, categorical_columns].astype("string").fillna("<MISSING>"))))
        valid_features = np.hstack((valid_features, encoder.transform(valid.loc[:, categorical_columns].astype("string").fillna("<MISSING>"))))
    model = _make_model(model_family).fit(train_features, y, sample_weight=sample_weight)
    return model.predict_proba(valid_features)[:, 1]


def _fold_local_calibrator(
    train: pd.DataFrame,
    feature_columns: list[str],
    n_splits: int,
    model_family: str,
    categorical_columns: tuple[str, ...],
) -> tuple[LogisticRegression, np.ndarray, np.ndarray, np.ndarray]:
    folds = chronological_purged_folds(train[["t0", "t1"]], n_splits=n_splits)
    probabilities = np.full(len(train), np.nan)
    calibration_weights = average_uniqueness(train[["t0", "t1"]]).to_numpy()
    for inner_train, inner_valid in folds:
        inner = train.iloc[inner_train]
        probabilities[inner_valid] = _fit_predict_probability(
            inner,
            train.iloc[inner_valid],
            feature_columns,
            model_family,
            categorical_columns,
            average_uniqueness(inner[["t0", "t1"]]).to_numpy(),
        )
    usable = np.isfinite(probabilities)
    target = (train.loc[usable, "y_direction"] == 1).astype(int)
    if target.nunique() != 2:
        raise ValueError("calibration evidence requires both classes")
    logits = np.log(np.clip(probabilities[usable], 1e-6, 1 - 1e-6) / (1 - np.clip(probabilities[usable], 1e-6, 1 - 1e-6)))
    calibrator = LogisticRegression(random_state=0, max_iter=1000).fit(
        logits.reshape(-1, 1), target, sample_weight=calibration_weights[usable]
    )
    calibrated = calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]
    return calibrator, calibrated, target.to_numpy(), calibration_weights[usable]


def _select_candidate_threshold(
    probabilities: np.ndarray,
    target: np.ndarray,
    grid: tuple[float, ...],
    sample_weight: np.ndarray,
) -> float:
    if not grid or any(not 0 < value < 1 for value in grid):
        raise ValueError("candidate threshold grid must be within (0, 1)")
    return max(
        (
            f1_score(target, probabilities >= value, sample_weight=sample_weight, zero_division=0),
            value,
        )
        for value in grid
    )[1]


def oof_logistic_predictions(
    frame: pd.DataFrame,
    folds: list[tuple[list[int], list[int]]],
    feature_columns: list[str],
    calibration_splits: int = 2,
    candidate_threshold_grid: tuple[float, ...] = (0.5, 0.55, 0.6, 0.65, 0.7),
    model_family: str = "logistic_regression",
    categorical_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Fit preprocessing/model on each supplied train fold and emit validation-only p1."""
    required = set(feature_columns) | set(categorical_columns) | {"y_direction", "t0", "t1"}
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
        train_weights = average_uniqueness(train[["t0", "t1"]]).to_numpy()
        raw_probability = _fit_predict_probability(
            train,
            valid,
            feature_columns,
            model_family,
            categorical_columns,
            train_weights,
        )
        calibrator, calibration_probability, calibration_target, calibration_weights = _fold_local_calibrator(
            train.reset_index(drop=True),
            feature_columns,
            calibration_splits,
            model_family,
            categorical_columns,
        )
        threshold = _select_candidate_threshold(
            calibration_probability,
            calibration_target,
            candidate_threshold_grid,
            calibration_weights,
        )
        logits = np.log(np.clip(raw_probability, 1e-6, 1 - 1e-6) / (1 - np.clip(raw_probability, 1e-6, 1 - 1e-6)))
        probability = calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]
        candidate = probability >= threshold
        result.iloc[validation_rows, result.columns.get_loc("p1")] = probability
        result.iloc[validation_rows, result.columns.get_loc("prediction_kind")] = "OOF_CALIBRATED"
        result.iloc[validation_rows, result.columns.get_loc("candidate_threshold")] = threshold
        result.iloc[validation_rows, result.columns.get_loc("is_candidate")] = candidate
        result.iloc[validation_rows, result.columns.get_loc("candidate_reason")] = np.where(candidate, "p1_at_or_above_fold_threshold", "p1_below_fold_threshold")
    return result
