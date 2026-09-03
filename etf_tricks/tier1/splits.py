from __future__ import annotations

import numpy as np
import pandas as pd


def purged_train_indices(events: pd.DataFrame, validation_indices: list[int], embargo_rows: int = 0) -> np.ndarray:
    """Return rows whose closed event intervals do not overlap validation intervals."""
    if not {"t0", "t1"}.issubset(events.columns):
        raise ValueError("events require t0 and t1")
    t0 = pd.to_datetime(events["t0"])
    t1 = pd.to_datetime(events["t1"])
    validation = np.asarray(validation_indices, dtype=int)
    keep = np.ones(len(events), dtype=bool)
    keep[validation] = False
    for index in validation:
        overlaps = (t0 <= t1.iloc[index]) & (t1 >= t0.iloc[index])
        keep &= ~overlaps.to_numpy()
        if embargo_rows:
            keep[index + 1 : index + 1 + embargo_rows] = False
    return np.flatnonzero(keep)
