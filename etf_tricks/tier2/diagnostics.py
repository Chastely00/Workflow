"""Economic and classification diagnostics for Tier 2 OOF predictions."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score


def summarize_tier2_oof(
    frame: pd.DataFrame, predictions: pd.DataFrame, targets: pd.DataFrame
) -> dict[str, object]:
    """Compare accepted candidates to all OOF Tier 1 candidates without handoff leakage."""
    required_frame = {"event_id", "y_meta"}
    required_predictions = {"p2", "accepted"}
    required_targets = {"event_id", "net_log_return"}
    if missing := required_frame.difference(frame.columns):
        raise ValueError(f"Tier 2 frame missing columns: {sorted(missing)}")
    if missing := required_predictions.difference(predictions.columns):
        raise ValueError(f"Tier 2 predictions missing columns: {sorted(missing)}")
    if missing := required_targets.difference(targets.columns):
        raise ValueError(f"Tier 2 targets missing columns: {sorted(missing)}")
    if not frame.index.equals(predictions.index):
        raise ValueError("Tier 2 frame and predictions must have identical indexes")
    if targets["event_id"].duplicated().any():
        raise ValueError("Tier 2 targets require unique event_id")
    observed = predictions["p2"].notna()
    x = frame.loc[observed, ["event_id", "y_meta"]].join(predictions.loc[observed, ["p2", "accepted"]])
    x = x.merge(targets[["event_id", "net_log_return"]], on="event_id", how="left", validate="one_to_one")
    if x["net_log_return"].isna().any():
        raise ValueError("Tier 2 OOF event has no finite target return")
    returns = pd.to_numeric(x["net_log_return"], errors="coerce")
    if not np.isfinite(returns).all():
        raise ValueError("Tier 2 targets require finite net_log_return")
    target = x["y_meta"].astype(int)
    accepted = x.loc[x["accepted"].astype(bool)]
    return {
        "oof_rows": int(len(x)),
        "oof_auc": None if target.nunique() != 2 else float(roc_auc_score(target, x["p2"])),
        "oof_brier": None if x.empty else float(brier_score_loss(target, x["p2"])),
        "candidate_count": int(len(x)),
        "accepted_count": int(len(accepted)),
        "accepted_share": None if x.empty else float(len(accepted) / len(x)),
        "candidate_positive_rate": None if x.empty else float(target.mean()),
        "accepted_positive_rate": None if accepted.empty else float(accepted["y_meta"].mean()),
        "candidate_mean_net_log_return": None if x.empty else float(returns.mean()),
        "accepted_mean_net_log_return": None if accepted.empty else float(accepted["net_log_return"].mean()),
    }
