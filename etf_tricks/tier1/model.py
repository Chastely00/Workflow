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
    trading_sessions: pd.DatetimeIndex | None,
    uniqueness_entity_column: str | None,
) -> tuple[LogisticRegression, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    folds = chronological_purged_folds(train[["t0", "t1"]], n_splits=n_splits)
    probabilities = np.full(len(train), np.nan)
    calibration_weights = average_uniqueness(
        train, trading_sessions, uniqueness_entity_column
    ).to_numpy()
    for inner_train, inner_valid in folds:
        inner = train.iloc[inner_train]
        probabilities[inner_valid] = _fit_predict_probability(
            inner,
            train.iloc[inner_valid],
            feature_columns,
            model_family,
            categorical_columns,
            average_uniqueness(inner, trading_sessions, uniqueness_entity_column).to_numpy(),
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
    return calibrator, calibrated, target.to_numpy(), calibration_weights[usable], usable


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


def _select_economic_candidate_threshold(
    probabilities: np.ndarray | list[float],
    net_log_returns: np.ndarray | list[float],
    grid: tuple[float, ...],
    sample_weight: np.ndarray | list[float],
    minimum_candidate_weight_share: float,
) -> float:
    """Select a training-only probability threshold by supported net return."""
    probabilities_array = np.asarray(probabilities, dtype=float)
    returns_array = np.asarray(net_log_returns, dtype=float)
    weights_array = np.asarray(sample_weight, dtype=float)
    if not grid or any(not 0 < value < 1 for value in grid):
        raise ValueError("candidate threshold grid must be within (0, 1)")
    if not 0 < minimum_candidate_weight_share <= 1:
        raise ValueError("minimum candidate weight share must be within (0, 1]")
    if not (
        len(probabilities_array) == len(returns_array) == len(weights_array)
    ) or not len(probabilities_array):
        raise ValueError("economic threshold inputs must be nonempty and aligned")
    if not (
        np.isfinite(probabilities_array).all()
        and np.isfinite(returns_array).all()
        and np.isfinite(weights_array).all()
    ) or (weights_array < 0).any() or weights_array.sum() <= 0:
        raise ValueError("economic threshold inputs must be finite with positive total weight")
    total_weight = weights_array.sum()
    candidates: list[tuple[float, float]] = []
    for value in grid:
        selected = probabilities_array >= value
        selected_weight = weights_array[selected].sum()
        if selected_weight / total_weight < minimum_candidate_weight_share:
            continue
        candidates.append(
            (
                float(np.average(returns_array[selected], weights=weights_array[selected])),
                value,
            )
        )
    if not candidates:
        raise ValueError("no candidate threshold meets minimum weighted support")
    return max(candidates)[1]


def oof_logistic_predictions(
    frame: pd.DataFrame,
    folds: list[tuple[list[int], list[int]]],
    feature_columns: list[str],
    calibration_splits: int = 2,
    candidate_threshold_grid: tuple[float, ...] = (0.5, 0.55, 0.6, 0.65, 0.7),
    model_family: str = "logistic_regression",
    categorical_columns: tuple[str, ...] = (),
    candidate_threshold_objective: str = "f1",
    minimum_candidate_weight_share: float = 0.10,
    trading_sessions: pd.DatetimeIndex | None = None,
    uniqueness_entity_column: str | None = None,
) -> pd.DataFrame:
    """Fit preprocessing/model on each supplied train fold and emit validation-only p1."""
    required = set(feature_columns) | set(categorical_columns) | {"y_direction", "t0", "t1"}
    if candidate_threshold_objective == "economic_net_log_return":
        required.add("net_log_return")
    elif candidate_threshold_objective != "f1":
        raise ValueError("unsupported candidate threshold objective")
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
        train_weights = average_uniqueness(
            train, trading_sessions, uniqueness_entity_column
        ).to_numpy()
        try:
            raw_probability = _fit_predict_probability(
                train,
                valid,
                feature_columns,
                model_family,
                categorical_columns,
                train_weights,
            )
            calibrator, calibration_probability, calibration_target, calibration_weights, calibration_usable = _fold_local_calibrator(
                train.reset_index(drop=True),
                feature_columns,
                calibration_splits,
                model_family,
                categorical_columns,
                trading_sessions,
                uniqueness_entity_column,
            )
        except ValueError as error:
            if str(error) not in {"training fold requires both classes", "calibration evidence requires both classes"}:
                raise
            result.iloc[validation_rows, result.columns.get_loc("prediction_kind")] = "INSUFFICIENT_TRAINING_CLASSES"
            result.iloc[validation_rows, result.columns.get_loc("is_candidate")] = False
            result.iloc[validation_rows, result.columns.get_loc("candidate_reason")] = "insufficient_training_classes"
            continue
        if candidate_threshold_objective == "economic_net_log_return":
            calibration_returns = train.loc[calibration_usable, "net_log_return"]
            try:
                threshold = _select_economic_candidate_threshold(
                    calibration_probability,
                    calibration_returns.to_numpy(dtype=float),
                    candidate_threshold_grid,
                    calibration_weights,
                    minimum_candidate_weight_share,
                )
            except ValueError as error:
                if str(error) != "no candidate threshold meets minimum weighted support":
                    raise
                threshold = None
        else:
            threshold = _select_candidate_threshold(
                calibration_probability,
                calibration_target,
                candidate_threshold_grid,
                calibration_weights,
            )
        logits = np.log(np.clip(raw_probability, 1e-6, 1 - 1e-6) / (1 - np.clip(raw_probability, 1e-6, 1 - 1e-6)))
        probability = calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]
        candidate = np.zeros(len(probability), dtype=bool) if threshold is None else probability >= threshold
        result.iloc[validation_rows, result.columns.get_loc("p1")] = probability
        result.iloc[validation_rows, result.columns.get_loc("prediction_kind")] = "OOF_CALIBRATED"
        if threshold is not None:
            result.iloc[validation_rows, result.columns.get_loc("candidate_threshold")] = threshold
        result.iloc[validation_rows, result.columns.get_loc("is_candidate")] = candidate
        result.iloc[validation_rows, result.columns.get_loc("candidate_reason")] = (
            "no_supported_training_threshold" if threshold is None else np.where(candidate, "p1_at_or_above_fold_threshold", "p1_below_fold_threshold")
        )
    return result
