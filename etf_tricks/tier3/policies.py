"""Pure, research-only Tier 3 policy weights from past-available returns."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform


@dataclass(frozen=True)
class AllocationWeights:
    policy: str
    status: str
    weights: pd.Series
    covariance_asof: pd.Timestamp | None
    observations: int


def allocate_policy_weights(
    accepted_etf_ids: list[str],
    return_history: pd.DataFrame,
    decision_time: str | pd.Timestamp,
    policy: str,
    min_observations: int = 20,
) -> AllocationWeights:
    """Return allocation weights using only return observations available before decision."""
    if policy not in {"equal_capital", "inverse_vol", "hrp"}:
        raise ValueError("unsupported Tier 3 policy")
    ids = tuple(sorted(set(map(str, accepted_etf_ids))))
    if len(ids) < 2:
        return AllocationWeights(policy, "INSUFFICIENT_CROSS_ETF_UNIVERSE", pd.Series(dtype=float), None, 0)
    required = {"date", "etf_id", "daily_return", "available_at"}
    if missing := required.difference(return_history.columns):
        raise ValueError(f"Tier 3 return history missing columns: {sorted(missing)}")
    if min_observations < 2:
        raise ValueError("Tier 3 min_observations must be at least two")
    asof = pd.Timestamp(decision_time)
    asof_utc = asof.tz_localize("UTC") if asof.tzinfo is None else asof.tz_convert("UTC")
    history = return_history.copy()
    history["date"] = pd.to_datetime(history["date"], errors="coerce").dt.normalize()
    history["available_at"] = pd.to_datetime(history["available_at"], errors="coerce", utc=True)
    history["daily_return"] = pd.to_numeric(history["daily_return"], errors="coerce")
    if history[["date", "available_at", "daily_return"]].isna().any().any():
        raise ValueError("Tier 3 return history requires valid dates, availability and returns")
    eligible = history.loc[
        history["etf_id"].astype(str).isin(ids)
        & history["date"].lt(asof.normalize())
        & history["available_at"].le(asof_utc),
        ["date", "etf_id", "daily_return"],
    ]
    if eligible.duplicated(["date", "etf_id"]).any():
        raise ValueError("Tier 3 return history has duplicate date-ETF observations")
    matrix = eligible.pivot(index="date", columns="etf_id", values="daily_return").reindex(columns=ids).dropna()
    if len(matrix) < min_observations:
        return AllocationWeights(policy, "INSUFFICIENT_COMMON_HISTORY", pd.Series(dtype=float), None, len(matrix))
    if policy == "equal_capital":
        weights = pd.Series(1.0 / len(ids), index=ids, dtype=float)
    elif policy == "inverse_vol":
        volatility = matrix.std(ddof=1)
        if (~np.isfinite(volatility) | volatility.le(0)).any():
            return AllocationWeights(policy, "INVALID_PAST_VOLATILITY", pd.Series(dtype=float), matrix.index.max(), len(matrix))
        inverse = 1.0 / volatility
        weights = inverse / inverse.sum()
    else:
        weights = _hrp_weights(matrix)
    return AllocationWeights(policy, "READY", weights.sort_index(), matrix.index.max(), len(matrix))


def _hrp_weights(returns: pd.DataFrame) -> pd.Series:
    covariance = returns.cov()
    correlation = returns.corr()
    if correlation.isna().any().any():
        raise ValueError("HRP requires finite past-only correlation")
    distance = np.sqrt(np.clip((1.0 - correlation.to_numpy(dtype=float)) / 2.0, 0.0, 1.0))
    clustered = linkage(squareform(distance, checks=False), method="single")
    ordered = list(correlation.index[leaves_list(clustered)])
    weights = pd.Series(1.0, index=ordered, dtype=float)
    clusters = [ordered]
    while clusters:
        cluster = clusters.pop(0)
        if len(cluster) <= 1:
            continue
        split = len(cluster) // 2
        left, right = cluster[:split], cluster[split:]
        left_variance = _cluster_variance(covariance, left)
        right_variance = _cluster_variance(covariance, right)
        alpha = 1.0 - left_variance / (left_variance + right_variance)
        weights.loc[left] *= alpha
        weights.loc[right] *= 1.0 - alpha
        clusters.extend((left, right))
    return weights / weights.sum()


def _cluster_variance(covariance: pd.DataFrame, members: list[str]) -> float:
    diagonal = np.diag(covariance.loc[members, members])
    if (~np.isfinite(diagonal) | (diagonal <= 0)).any():
        raise ValueError("HRP requires positive finite variances")
    inverse = 1.0 / diagonal
    ivp = inverse / inverse.sum()
    return float(ivp @ covariance.loc[members, members].to_numpy() @ ivp)
