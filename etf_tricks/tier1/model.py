from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


def oof_logistic_predictions(frame: pd.DataFrame, folds: list[tuple[list[int], list[int]]], feature_columns: list[str]) -> pd.DataFrame:
    """Fit preprocessing/model on each supplied train fold and emit validation-only p1."""
    required = set(feature_columns) | {"y_direction", "t0", "t1"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(missing)}")
    result = pd.DataFrame(index=frame.index, data={"p1": np.nan, "prediction_kind": pd.NA})
    for train_rows, validation_rows in folds:
        train = frame.iloc[train_rows]
        valid = frame.iloc[validation_rows]
        if set(train_rows).intersection(validation_rows):
            raise ValueError("train and validation rows overlap")
        y = (train["y_direction"] == 1).astype(int)
        if y.nunique() != 2:
            raise ValueError("training fold requires both classes")
        scaler = StandardScaler().fit(train[feature_columns])
        model = LogisticRegression(random_state=0, max_iter=1000).fit(scaler.transform(train[feature_columns]), y)
        result.iloc[validation_rows, result.columns.get_loc("p1")] = model.predict_proba(scaler.transform(valid[feature_columns]))[:, 1]
        result.iloc[validation_rows, result.columns.get_loc("prediction_kind")] = "OOF"
    return result
