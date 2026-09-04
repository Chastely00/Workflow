"""Safe expansion of an ETF-local OOF hand-off into earlier untouched rows."""

from __future__ import annotations

import pandas as pd


_KEYS = {"event_id", "etf_id", "t0_bar_id", "p1", "prediction_kind", "decision_available_at"}


def prepend_earlier_oof(existing: pd.DataFrame, extension: pd.DataFrame) -> pd.DataFrame:
    """Prepend only rows strictly earlier than existing OOF, preserving it byte-for-byte by row."""
    for name, frame in (("existing", existing), ("extension", extension)):
        if missing := _KEYS.difference(frame.columns):
            raise ValueError(f"{name} OOF missing columns: {sorted(missing)}")
        if frame.empty or frame["event_id"].duplicated().any() or frame["t0_bar_id"].duplicated().any():
            raise ValueError(f"{name} OOF has duplicate or empty keys")
        if frame["etf_id"].nunique(dropna=False) != 1:
            raise ValueError(f"{name} OOF must contain one ETF")
    if existing["etf_id"].iloc[0] != extension["etf_id"].iloc[0]:
        raise ValueError("existing and extension must contain one ETF-local lineage")
    cutoff = int(existing["t0_bar_id"].min())
    earlier = extension.loc[extension["t0_bar_id"].lt(cutoff)].copy()
    if earlier.empty:
        raise ValueError("extension has no rows strictly earlier than existing OOF")
    if set(earlier["event_id"]).intersection(existing["event_id"]):
        raise ValueError("extension has duplicate event_id with existing OOF")
    if earlier["t0_bar_id"].duplicated().any():
        raise ValueError("extension has duplicate earlier t0_bar_id")
    result = pd.concat([earlier.sort_values("t0_bar_id", kind="stable"), existing.copy()], ignore_index=True)
    if not result["t0_bar_id"].is_monotonic_increasing:
        raise ValueError("combined OOF t0_bar_id must be strictly increasing")
    return result
