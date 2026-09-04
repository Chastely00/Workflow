"""Post-label barrier diagnostics, isolated from Tier 1 policy inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _summarize(scope: str, events: pd.DataFrame, paths: pd.DataFrame) -> dict[str, object]:
    event_ids = set(events["event_id"])
    path = paths.loc[paths["event_id"].isin(event_ids)].copy()
    joined = path.merge(events[["event_id", "entry_price", "first_touch_type", "first_touch_bar_id", "t0_bar_id"]], on="event_id", how="inner", validate="many_to_one")
    joined["log_return"] = np.log(pd.to_numeric(joined["close_nav"], errors="coerce") / pd.to_numeric(joined["entry_price"], errors="coerce"))
    grouped = joined.groupby("event_id", sort=False)["log_return"]
    metrics = grouped.agg(mfe_log_return="max", mae_log_return="min").reset_index()
    touched = events.copy()
    touched["time_to_touch_bars"] = pd.to_numeric(touched["first_touch_bar_id"], errors="coerce") - pd.to_numeric(touched["t0_bar_id"], errors="coerce")
    upper = touched.loc[touched["first_touch_type"].eq("upper")].merge(metrics, on="event_id", how="left", validate="one_to_one")
    post_upper: list[float] = []
    for row in upper.itertuples(index=False):
        after = joined.loc[(joined["event_id"].eq(row.event_id)) & (joined["bar_id"] >= row.first_touch_bar_id), "log_return"]
        touch = joined.loc[(joined["event_id"].eq(row.event_id)) & (joined["bar_id"].eq(row.first_touch_bar_id)), "log_return"]
        if not after.empty and not touch.empty:
            post_upper.append(float(after.max() - touch.iloc[0]))
    merged = touched.merge(metrics, on="event_id", how="left", validate="one_to_one")
    return {
        "scope": scope,
        "event_count": int(len(events)),
        "upper_touch_count": int(touched["first_touch_type"].eq("upper").sum()),
        "lower_touch_count": int(touched["first_touch_type"].eq("lower").sum()),
        "vertical_touch_count": int(touched["first_touch_type"].eq("vertical").sum()),
        "mean_time_to_touch_bars": float(touched["time_to_touch_bars"].mean()),
        "mean_mfe_log_return": float(merged["mfe_log_return"].mean()),
        "mean_mae_log_return": float(merged["mae_log_return"].mean()),
        "mean_post_upper_continuation_log_return": float(np.nanmean(post_upper)) if post_upper else np.nan,
    }


def summarize_barriers(events: pd.DataFrame, candidates: pd.DataFrame, paths: pd.DataFrame) -> pd.DataFrame:
    """Return ALL_EVENTS and OOF-CANDIDATES barrier diagnostics.

    ``paths`` is outcome data and is intentionally accepted only here, never by
    the stateful signal policy.  All supplied events must be mature/resolved;
    an unresolved tail cannot diagnose a 60-bar barrier design.
    """
    required_events = {"event_id", "etf_id", "t0_bar_id", "t0_date", "entry_price", "first_touch_type", "first_touch_bar_id", "target_status"}
    required_candidates = {"event_id"}
    required_paths = {"event_id", "bar_id", "date", "close_nav"}
    if missing := required_events.difference(events.columns):
        raise ValueError(f"events missing columns: {sorted(missing)}")
    if missing := required_candidates.difference(candidates.columns):
        raise ValueError(f"candidates missing columns: {sorted(missing)}")
    if missing := required_paths.difference(paths.columns):
        raise ValueError(f"paths missing columns: {sorted(missing)}")
    if events.empty or paths.empty:
        raise ValueError("barrier diagnostics require events and paths")
    if events["event_id"].duplicated().any() or paths.duplicated(["event_id", "bar_id"]).any():
        raise ValueError("barrier diagnostic keys must be unique")
    if ~events["target_status"].astype(str).str.startswith("resolved").all():
        raise ValueError("barrier diagnostics reject unresolved events")
    if events[["entry_price", "first_touch_bar_id"]].isna().any().any() or pd.to_numeric(events["entry_price"], errors="coerce").le(0).any():
        raise ValueError("resolved events require positive entry price and touch bar")
    if ~events["first_touch_type"].isin({"upper", "lower", "vertical"}).all():
        raise ValueError("resolved events require supported first_touch_type")
    candidate_ids = candidates["event_id"].dropna().unique()
    unknown = set(candidate_ids).difference(events["event_id"])
    if unknown:
        raise ValueError(f"candidates reference unknown events: {sorted(unknown)}")
    all_events = events.copy()
    candidate_events = events.loc[events["event_id"].isin(candidate_ids)].copy()
    missing_paths = set(events["event_id"]).difference(paths["event_id"])
    if missing_paths:
        raise ValueError(f"paths missing events: {sorted(missing_paths)}")
    summaries = [_summarize("ALL_EVENTS", all_events, paths), _summarize("CANDIDATES", candidate_events, paths)]
    return pd.DataFrame(summaries)
