from __future__ import annotations

import numpy as np
import pandas as pd


def average_uniqueness(events: pd.DataFrame) -> pd.Series:
    """Return each event's mean inverse concurrency across its active dates."""
    if not {"t0", "t1"}.issubset(events.columns):
        raise ValueError("events require t0 and t1")
    starts = pd.to_datetime(events["t0"], errors="coerce").dt.normalize()
    ends = pd.to_datetime(events["t1"], errors="coerce").dt.normalize()
    if starts.isna().any() or ends.isna().any() or (ends < starts).any():
        raise ValueError("event intervals must be valid")
    timeline = pd.date_range(starts.min(), ends.max(), freq="D")
    concurrency = np.zeros(len(timeline), dtype=np.int64)
    positions: list[tuple[int, int]] = []
    for start, end in zip(starts, ends, strict=True):
        left = int(timeline.searchsorted(start, side="left"))
        right = int(timeline.searchsorted(end, side="right"))
        concurrency[left:right] += 1
        positions.append((left, right))
    inverse = np.divide(
        1.0,
        concurrency,
        out=np.zeros(len(concurrency), dtype=float),
        where=concurrency > 0,
    )
    return pd.Series(
        [float(inverse[left:right].mean()) for left, right in positions],
        index=events.index,
        dtype=float,
    )


def chronological_purged_folds(events: pd.DataFrame, n_splits: int = 3) -> list[tuple[np.ndarray, np.ndarray]]:
    """Create expanding OOF folds whose training labels resolve before validation starts."""
    if not {"t0", "t1"}.issubset(events.columns):
        raise ValueError("events require t0 and t1")
    if n_splits <= 0:
        raise ValueError("n_splits must be positive")
    t0 = pd.to_datetime(events["t0"])
    t1 = pd.to_datetime(events["t1"])
    if t0.isna().any() or t1.isna().any() or (t1 < t0).any():
        raise ValueError("event intervals must be valid")
    dates = np.sort(t0.unique())
    if len(dates) < n_splits + 1:
        raise ValueError("insufficient distinct t0 dates for requested folds")
    blocks = np.array_split(dates, n_splits + 1)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for validation_dates in blocks[1:]:
        validation = np.flatnonzero(t0.isin(validation_dates).to_numpy())
        train = np.flatnonzero(t1.lt(pd.Timestamp(validation_dates[0])).to_numpy())
        if len(train) and len(validation):
            folds.append((train, validation))
    if not folds:
        raise ValueError("no fold has fully resolved historical training events")
    return folds


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
