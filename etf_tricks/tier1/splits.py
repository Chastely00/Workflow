from __future__ import annotations

import numpy as np
import pandas as pd


def average_uniqueness(
    events: pd.DataFrame,
    trading_sessions: pd.DatetimeIndex | list[pd.Timestamp] | None = None,
    entity_column: str | None = None,
) -> pd.Series:
    """Return each event's mean inverse concurrency across active trading sessions."""
    if not {"t0", "t1"}.issubset(events.columns):
        raise ValueError("events require t0 and t1")
    if entity_column is not None:
        if entity_column not in events.columns:
            raise ValueError(f"events missing uniqueness entity column: {entity_column}")
        if not events.index.is_unique or events[entity_column].isna().any():
            raise ValueError("entity-aware uniqueness requires unique rows and nonmissing entities")
        per_entity = [
            average_uniqueness(group, trading_sessions=trading_sessions)
            for _, group in events.groupby(entity_column, sort=False)
        ]
        return pd.concat(per_entity).reindex(events.index)
    starts = pd.to_datetime(events["t0"], errors="coerce").dt.normalize()
    ends = pd.to_datetime(events["t1"], errors="coerce").dt.normalize()
    if starts.isna().any() or ends.isna().any() or (ends < starts).any():
        raise ValueError("event intervals must be valid")
    if trading_sessions is None:
        timeline = pd.date_range(starts.min(), ends.max(), freq="D")
    else:
        timeline = pd.DatetimeIndex(pd.to_datetime(trading_sessions, errors="coerce")).normalize()
        if timeline.isna().any() or timeline.has_duplicates:
            raise ValueError("trading sessions must be valid and unique")
        timeline = timeline.sort_values()
        timeline = timeline[(timeline >= starts.min()) & (timeline <= ends.max())]
        if timeline.empty or not starts.isin(timeline).all() or not ends.isin(timeline).all():
            raise ValueError("trading sessions must cover every event endpoint")
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


def fold_audit_records(
    events: pd.DataFrame, folds: list[tuple[np.ndarray, np.ndarray]]
) -> pd.DataFrame:
    """Return the realized expanding-fold boundaries and event-end purge proof.

    These records are evidence, rather than a second splitter: every reported
    boolean is re-checked from the supplied row indices and source event times.
    A forward-only fold has no later observations in its training side, so a
    post-validation embargo is structurally not applicable.
    """
    if not {"t0", "t1"}.issubset(events.columns):
        raise ValueError("events require t0 and t1")
    t0 = pd.to_datetime(events["t0"], errors="coerce")
    t1 = pd.to_datetime(events["t1"], errors="coerce")
    if t0.isna().any() or t1.isna().any() or (t1 < t0).any():
        raise ValueError("event intervals must be valid")
    records: list[dict[str, object]] = []
    for fold_number, (train_rows, validation_rows) in enumerate(folds):
        train_index = np.asarray(train_rows, dtype=int)
        validation_index = np.asarray(validation_rows, dtype=int)
        if not len(train_index) or not len(validation_index):
            raise ValueError("fold audit requires nonempty train and validation rows")
        if set(train_index).intersection(validation_index):
            raise ValueError("fold audit rows overlap")
        train_t0 = t0.iloc[train_index]
        train_t1 = t1.iloc[train_index]
        validation_t0 = t0.iloc[validation_index]
        validation_t1 = t1.iloc[validation_index]
        event_end_purge_verified = bool(train_t1.lt(validation_t0.min()).all())
        if not event_end_purge_verified:
            raise ValueError("fold audit found training event unresolved at validation start")
        records.append(
            {
                "outer_fold": fold_number,
                "train_rows": int(len(train_index)),
                "validation_rows": int(len(validation_index)),
                "train_t0_min": train_t0.min(),
                "train_t0_max": train_t0.max(),
                "train_t1_max": train_t1.max(),
                "validation_t0_min": validation_t0.min(),
                "validation_t0_max": validation_t0.max(),
                "validation_t1_max": validation_t1.max(),
                "event_end_purge_verified": event_end_purge_verified,
                "embargo_policy": "NOT_APPLICABLE_FORWARD_ONLY",
            }
        )
    return pd.DataFrame(records)


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
