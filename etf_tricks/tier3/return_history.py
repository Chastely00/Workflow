"""PIT-safe common-calendar daily ETF Trick return history for Tier 3 risk models."""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_pit_daily_return_history(bar_daily_membership: pd.DataFrame) -> pd.DataFrame:
    """Derive one close-to-close return per ETF/date from immutable daily NAV rows.

    A return belongs to its current close date and becomes available only when
    that day's source NAV is available.  This preserves the causal clock used
    by inverse-volatility and HRP allocation decisions.
    """
    required = {"etf_id", "date", "nav", "source_available_at"}
    if missing := required.difference(bar_daily_membership.columns):
        raise ValueError(f"daily membership missing fields: {sorted(missing)}")
    frame = bar_daily_membership.loc[:, ["etf_id", "date", "nav", "source_available_at"]].copy()
    frame["etf_id"] = frame["etf_id"].astype(str)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame["source_available_at"] = pd.to_datetime(
        frame["source_available_at"], errors="coerce", utc=True
    )
    if frame[["date", "nav", "source_available_at"]].isna().any().any():
        raise ValueError("daily membership requires valid date, NAV and availability")
    if (~np.isfinite(frame["nav"]) | frame["nav"].le(0)).any():
        raise ValueError("daily membership requires positive finite NAV")
    if frame.duplicated(["etf_id", "date"]).any():
        raise ValueError("daily membership has duplicate ETF-date rows")
    frame = frame.sort_values(["etf_id", "date"], kind="stable")
    frame["daily_return"] = frame.groupby("etf_id", sort=False)["nav"].pct_change(fill_method=None)
    output = frame.loc[frame["daily_return"].notna(), ["date", "etf_id", "daily_return", "source_available_at"]]
    return output.rename(columns={"source_available_at": "available_at"}).sort_values(
        ["etf_id", "date"], kind="stable"
    ).reset_index(drop=True)
