from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def evaluate_etf_local_gate(
    metrics: dict[str, float | int],
    etf_id: str,
    trial_id: str,
    effective_trial_count: float,
) -> dict[str, object]:
    """Apply the Tier 1 promotion rule to one ETF-local OOF result only."""
    if not etf_id:
        raise ValueError("ETF-local gate requires a nonempty etf_id")
    if effective_trial_count <= 0:
        raise ValueError("ETF-local gate requires a positive effective trial count")
    required = {
        "oof_rows", "auc", "candidate_count", "candidate_positive_rate",
        "base_positive_rate", "candidate_mean_net_log_return",
        "base_mean_net_log_return",
    }
    if missing := required.difference(metrics):
        raise ValueError(f"ETF-local gate metrics missing: {sorted(missing)}")
    if not int(metrics["oof_rows"]):
        return {
            "trial_id": trial_id,
            "etf_scope": etf_id,
            "model_scope": "ETF_LOCAL",
            "effective_independent_trial_count": effective_trial_count,
            "metrics": metrics,
            "reasons": ["insufficient_mature_events"],
            "status": "INSUFFICIENT_MATURE_EVENTS",
            "tier2_permitted": False,
            "tier3_permitted": False,
        }
    reasons: list[str] = []
    if not float(metrics["auc"]) > 0.5:
        reasons.append("oof_auc_not_above_0_5")
    if not int(metrics["candidate_count"]) > 0:
        reasons.append("no_oof_candidates")
    elif not float(metrics["candidate_positive_rate"]) > float(metrics["base_positive_rate"]):
        reasons.append("candidate_positive_rate_not_above_base")
    if not int(metrics["candidate_count"]) > 0 or not float(metrics["candidate_mean_net_log_return"]) > float(metrics["base_mean_net_log_return"]):
        reasons.append("candidate_net_return_not_above_base")
    passed = not reasons
    return {
        "trial_id": trial_id,
        "etf_scope": etf_id,
        "model_scope": "ETF_LOCAL",
        "effective_independent_trial_count": effective_trial_count,
        "metrics": metrics,
        "reasons": reasons,
        "status": "PASSED" if passed else "FAILED",
        "tier2_permitted": passed,
        # Tier 1 can admit only ETF-local meta-label research.  Tier 3 needs
        # independently admitted Tier 2 streams and cannot be bypassed.
        "tier3_permitted": False,
    }


def _metrics(group: pd.DataFrame) -> dict[str, float | int]:
    if group.empty:
        return {
            "oof_rows": 0,
            "positive_rate": np.nan,
            "auc": np.nan,
            "candidate_count": 0,
            "candidate_share": np.nan,
            "candidate_positive_rate": np.nan,
            "candidate_mean_net_log_return": np.nan,
            "base_mean_net_log_return": np.nan,
        }
    target = (group["y_direction"] == 1).astype(int)
    candidate = group["is_candidate"].astype(bool)
    candidate_rows = group.loc[candidate]
    return {
        "oof_rows": int(len(group)),
        "positive_rate": float(target.mean()),
        "auc": float(roc_auc_score(target, group["p1"])) if target.nunique() == 2 else np.nan,
        "candidate_count": int(candidate.sum()),
        "candidate_share": float(candidate.mean()),
        "candidate_positive_rate": float((candidate_rows["y_direction"] == 1).mean()) if not candidate_rows.empty else np.nan,
        "candidate_mean_net_log_return": float(candidate_rows["net_log_return"].mean()) if not candidate_rows.empty else np.nan,
        "base_mean_net_log_return": float(group["net_log_return"].mean()),
    }


def summarize_oof_handoff_outcomes(
    handoff: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    etf_id: str,
) -> dict[str, float | int]:
    """Evaluate existing ETF-local OOF predictions against resolved targets only.

    This is a post-OOF reporting helper.  It is deliberately not usable by a
    model or candidate policy: target outcomes are joined only after the
    immutable hand-off has been written.
    """
    required_handoff = {"event_id", "etf_id", "p1", "candidate_indicator"}
    required_targets = {"event_id", "etf_id", "target_status", "y_direction", "net_log_return"}
    if missing := required_handoff.difference(handoff.columns):
        raise ValueError(f"OOF handoff missing columns: {sorted(missing)}")
    if missing := required_targets.difference(targets.columns):
        raise ValueError(f"targets missing columns: {sorted(missing)}")
    local_handoff = handoff.loc[handoff["etf_id"].eq(etf_id)].copy()
    if local_handoff.empty or not local_handoff["etf_id"].eq(etf_id).all():
        raise ValueError("OOF handoff must contain the requested ETF-local rows")
    if local_handoff["event_id"].duplicated().any():
        raise ValueError("OOF handoff event ids must be unique per ETF")
    local_targets = targets.loc[targets["etf_id"].eq(etf_id)].copy()
    if local_targets["event_id"].duplicated().any():
        raise ValueError("target event ids must be unique per ETF")
    joined = local_handoff.merge(
        local_targets[["event_id", "target_status", "y_direction", "net_log_return"]],
        on="event_id",
        how="left",
        validate="one_to_one",
    )
    if joined["target_status"].isna().any() or ~joined["target_status"].astype(str).str.startswith("resolved_").all():
        raise ValueError("OOF outcome reporting requires resolved matching ETF-local targets")
    if joined[["p1", "candidate_indicator", "y_direction", "net_log_return"]].isna().any().any():
        raise ValueError("OOF outcome reporting requires complete predictions and outcomes")
    metrics = _metrics(joined.rename(columns={"candidate_indicator": "is_candidate"}))
    metrics["base_positive_rate"] = metrics.pop("positive_rate")
    return metrics


def summarize_per_etf_oof(
    frame: pd.DataFrame,
    predictions: pd.DataFrame,
    folds: list[tuple[list[int], list[int]]],
    expected_etf_ids: list[str] | None = None,
    scope_label: str = "ALL",
) -> pd.DataFrame:
    """Summarize validation-only Tier 1 outcomes by ETF and outer OOF fold."""
    required_frame = {"etf_id", "y_direction", "net_log_return"}
    required_predictions = {"p1", "is_candidate"}
    if missing := required_frame.difference(frame.columns):
        raise ValueError(f"frame missing columns: {sorted(missing)}")
    if missing := required_predictions.difference(predictions.columns):
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if not frame.index.equals(predictions.index):
        raise ValueError("frame and predictions must have identical indexes")

    fold_assignment = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    for fold_number, (_, validation_rows) in enumerate(folds):
        labels = frame.index.take(validation_rows)
        if fold_assignment.loc[labels].notna().any():
            raise ValueError("row belongs to more than one outer validation fold")
        fold_assignment.loc[labels] = fold_number

    observed = predictions["p1"].notna()
    if observed.any() and fold_assignment.loc[observed].isna().any():
        raise ValueError("OOF prediction does not belong to an outer validation fold")
    if predictions.loc[observed, "is_candidate"].isna().any():
        raise ValueError("OOF prediction missing candidate indicator")

    oof = frame.loc[observed, ["etf_id", "y_direction", "net_log_return"]].join(
        predictions.loc[observed, ["p1", "is_candidate"]]
    )
    oof["outer_fold"] = fold_assignment.loc[observed].astype(int)
    summaries: list[dict[str, object]] = []
    universe = sorted(set(expected_etf_ids)) if expected_etf_ids is not None else sorted(frame["etf_id"].unique())
    for etf_id in universe:
        source_group = frame.loc[frame["etf_id"] == etf_id]
        group = oof.loc[oof["etf_id"] == etf_id]
        summaries.append(
            {"etf_id": etf_id, "scope": scope_label, "training_rows": int(len(source_group)), **_metrics(group)}
        )
        for fold_number, fold_group in group.groupby("outer_fold", sort=True):
            summaries.append(
                {
                    "etf_id": etf_id,
                    "scope": f"OUTER_FOLD_{fold_number}",
                    "training_rows": int(len(source_group)),
                    **_metrics(fold_group),
                }
            )
    return pd.DataFrame(summaries).sort_values(["etf_id", "scope"], kind="stable").reset_index(drop=True)
