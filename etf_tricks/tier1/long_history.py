"""Hard boundaries for pre-registered long-history Tier 1 diagnostics."""

from __future__ import annotations

import pandas as pd


def validate_long_history_research_frame(
    frame: pd.DataFrame,
    *,
    research_t0_end: str | pd.Timestamp,
    sealed_start: str | pd.Timestamp,
) -> dict[str, object]:
    """Fail closed if a diagnostic frame could reveal a sealed outcome."""
    if missing := {"t0", "t1"}.difference(frame.columns):
        raise ValueError(f"research frame missing columns: {sorted(missing)}")
    t0_end = pd.Timestamp(research_t0_end).normalize()
    sealed = pd.Timestamp(sealed_start).normalize()
    if t0_end >= sealed:
        raise ValueError("research t0 end must precede sealed start")
    t0 = pd.to_datetime(frame["t0"], errors="coerce").dt.normalize()
    t1 = pd.to_datetime(frame["t1"], errors="coerce").dt.normalize()
    if frame.empty or t0.isna().any() or t1.isna().any():
        raise ValueError("research frame requires nonempty valid event times")
    if t0.gt(t0_end).any():
        raise ValueError("research frame includes a decision after research end")
    if t1.ge(sealed).any():
        raise ValueError("research frame includes an outcome in the sealed interval")
    return {
        "research_t0_end": str(t0_end.date()),
        "sealed_start": str(sealed.date()),
        "research_rows": int(len(frame)),
    }
