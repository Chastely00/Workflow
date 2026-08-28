from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from .config import AFMLContractError, FFDConfig, config_sha256


class FFDContractError(AFMLContractError):
    """Raised when fixed-width FFD or its governed search cannot be applied."""


@dataclass(frozen=True)
class FFDSelection:
    d: float | None
    weights: np.ndarray
    width: int
    status: str
    calibration_version: str
    config_version: str
    search_evidence: pd.DataFrame


def fixed_width_weights(d: float, tolerance: float) -> np.ndarray:
    if not np.isfinite(d) or d < 0:
        raise FFDContractError("d must be finite and non-negative")
    if not np.isfinite(tolerance) or not 0 < tolerance < 1:
        raise FFDContractError("tolerance must satisfy 0 < tolerance < 1")
    return np.asarray(_cached_weights(float(d), float(tolerance)), dtype=float)


@lru_cache(maxsize=4096)
def _cached_weights(d: float, tolerance: float) -> tuple[float, ...]:
    weights = [1.0]
    for k in range(1, 1_000_001):
        next_weight = -weights[-1] * (d - k + 1) / k
        if not np.isfinite(next_weight):
            raise FFDContractError(f"FFD weight became non-finite at k={k}, d={d}")
        if abs(next_weight) < tolerance:
            return tuple(weights)
        weights.append(float(next_weight))
    raise FFDContractError(
        f"FFD weights did not reach tolerance={tolerance} by 1000000 terms"
    )


def apply_fixed_width_ffd(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    kernel = np.asarray(weights, dtype=float)
    if array.ndim != 1 or kernel.ndim != 1:
        raise FFDContractError("values and weights must be one-dimensional")
    if kernel.size == 0:
        raise FFDContractError("weights cannot be empty")
    if array.size < kernel.size:
        return np.asarray([], dtype=float)
    return np.convolve(array, kernel, mode="valid")


class FFDSelector:
    def __init__(self, config: FFDConfig) -> None:
        self.config = config
        self.config_version = config_sha256(config)

    def fit(
        self,
        log_nav: pd.Series | np.ndarray,
        calibration_version: str,
    ) -> FFDSelection:
        series = _as_finite_series(log_nav)
        if not calibration_version:
            raise FFDContractError("calibration_version cannot be empty")
        evidence_rows: list[dict[str, object]] = []
        evaluated: dict[float, dict[str, object]] = {}

        def evaluate(d_value: float, phase: str) -> dict[str, object]:
            d_key = round(float(d_value), 10)
            if d_key in evaluated:
                return evaluated[d_key]
            weights = fixed_width_weights(d_key, self.config.weight_tolerance)
            transformed = apply_fixed_width_ffd(series.to_numpy(dtype=float), weights)
            width = len(weights) - 1
            aligned_raw = series.to_numpy(dtype=float)[width:]
            correlation = _safe_correlation(aligned_raw, transformed)
            row: dict[str, object] = {
                "phase": phase,
                "d": d_key,
                "weight_count": len(weights),
                "width": width,
                "post_ffd_observations": len(transformed),
                "nobs": np.nan,
                "lags": np.nan,
                "adf_stat": np.nan,
                "p_value": np.nan,
                "critical_value_1pct": np.nan,
                "critical_value_5pct": np.nan,
                "critical_value_10pct": np.nan,
                "correlation": correlation,
                "passed": False,
                "status": "insufficient_observations",
                "reason": (
                    f"post-FFD observations {len(transformed)} below minimum "
                    f"{self.config.min_adf_observations}"
                ),
                "calibration_version": calibration_version,
                "config_version": self.config_version,
            }
            if len(transformed) >= self.config.min_adf_observations:
                try:
                    result = _adf(transformed, d_key, self.config)
                except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
                    row["status"] = "adf_error"
                    row["reason"] = f"{type(exc).__name__}: {exc}"
                else:
                    critical = result["critical_values"]
                    statistic = float(result["adf_stat"])
                    p_value = float(result["p_value"])
                    critical_5 = float(critical["5%"])
                    passed = (
                        np.isfinite(statistic)
                        and np.isfinite(p_value)
                        and p_value < self.config.alpha
                        and statistic < critical_5
                    )
                    row.update(
                        {
                            "nobs": int(result["nobs"]),
                            "lags": int(result["lags"]),
                            "adf_stat": statistic,
                            "p_value": p_value,
                            "critical_value_1pct": float(critical["1%"]),
                            "critical_value_5pct": critical_5,
                            "critical_value_10pct": float(critical["10%"]),
                            "passed": bool(passed),
                            "status": "passed" if passed else "failed_stationarity",
                            "reason": (
                                "p-value and 5% critical-value gates passed"
                                if passed
                                else "ADF stationarity gate not reached"
                            ),
                        }
                    )
            evidence_rows.append(row)
            evaluated[d_key] = row
            return row

        selected_d = self._search_interval(
            self.config.d_initial_min,
            self.config.d_initial_max,
            include_lower=True,
            phase="INITIAL_GRID",
            evaluate=evaluate,
        )
        if selected_d is None:
            evidence_rows.append(
                _diagnostic_row(
                    phase="AUTONOMOUS_ESCALATION",
                    reason=(
                        "[0,1] did not pass; input, width, nobs, and ADF diagnostics "
                        "recorded before finite expansion"
                    ),
                    calibration_version=calibration_version,
                    config_version=self.config_version,
                )
            )
            lower = self.config.d_initial_max
            upper = min(
                self.config.d_first_escalation_max,
                self.config.autonomous_max_d,
            )
            while lower < self.config.autonomous_max_d + 1e-12:
                selected_d = self._search_interval(
                    lower,
                    upper,
                    include_lower=False,
                    phase="AUTONOMOUS_ESCALATION",
                    evaluate=evaluate,
                )
                if selected_d is not None or upper >= self.config.autonomous_max_d - 1e-12:
                    break
                lower = upper
                upper = min(
                    lower + self.config.d_expansion_span,
                    self.config.autonomous_max_d,
                )

        evidence = pd.DataFrame(evidence_rows)
        if not evidence.empty:
            evidence = evidence.sort_values(
                ["d", "phase"], kind="mergesort", na_position="last"
            ).reset_index(drop=True)
        if selected_d is None:
            attempted_adf = bool(
                not evidence.empty
                and evidence["status"].isin(["passed", "failed_stationarity"]).any()
            )
            status = (
                "stationarity_not_reached"
                if attempted_adf
                else "insufficient_observations"
            )
            return FFDSelection(
                d=None,
                weights=np.asarray([], dtype=float),
                width=0,
                status=status,
                calibration_version=calibration_version,
                config_version=self.config_version,
                search_evidence=evidence,
            )
        weights = fixed_width_weights(selected_d, self.config.weight_tolerance)
        return FFDSelection(
            d=selected_d,
            weights=weights,
            width=len(weights) - 1,
            status="stationarity_reached",
            calibration_version=calibration_version,
            config_version=self.config_version,
            search_evidence=evidence,
        )

    def _search_interval(
        self,
        lower: float,
        upper: float,
        *,
        include_lower: bool,
        phase: str,
        evaluate,
    ) -> float | None:
        coarse = _grid(lower, upper, self.config.coarse_step, include_lower)
        prior = lower
        for d_value in coarse:
            result = evaluate(d_value, phase)
            if bool(result["passed"]):
                refine_lower = max(lower, d_value - self.config.coarse_step)
                refine = _grid(
                    refine_lower,
                    d_value,
                    self.config.refine_step,
                    include_lower=True,
                )
                for refined_d in refine:
                    refined = evaluate(refined_d, f"{phase}_REFINE")
                    if bool(refined["passed"]):
                        return round(float(refined_d), 10)
                return round(float(d_value), 10)
            prior = d_value
        del prior
        return None

    def transform(
        self,
        log_nav: pd.Series | np.ndarray,
        selection: FFDSelection,
    ) -> pd.DataFrame:
        if selection.status != "stationarity_reached" or selection.d is None:
            raise FFDContractError(
                f"cannot transform selection with status {selection.status}"
            )
        if selection.config_version != self.config_version:
            raise FFDContractError("FFD selection config identity mismatch")
        series = _as_finite_series(log_nav)
        values = apply_fixed_width_ffd(
            series.to_numpy(dtype=float), selection.weights
        )
        index = series.index[selection.width :]
        return pd.DataFrame(
            {
                "ffd_level": values,
                "selected_d": selection.d,
                "ffd_width": selection.width,
                "calibration_version": selection.calibration_version,
                "config_version": selection.config_version,
            },
            index=index,
        )


def _adf(values: np.ndarray, d: float, config: FFDConfig) -> dict[str, object]:
    del d
    result = adfuller(
        values,
        maxlag=config.maxlag,
        regression=config.regression,
        autolag=config.autolag,
    )
    return {
        "adf_stat": result[0],
        "p_value": result[1],
        "lags": result[2],
        "nobs": result[3],
        "critical_values": result[4],
    }


def _grid(lower: float, upper: float, step: float, include_lower: bool) -> np.ndarray:
    start = lower if include_lower else lower + step
    if start > upper + 1e-12:
        return np.asarray([], dtype=float)
    count = int(np.floor((upper - start) / step + 1e-10)) + 1
    values = start + np.arange(count, dtype=float) * step
    if values.size == 0 or values[-1] < upper - 1e-10:
        values = np.r_[values, upper]
    return np.round(values, 10)


def _as_finite_series(values: pd.Series | np.ndarray) -> pd.Series:
    if isinstance(values, pd.Series):
        series = values.astype(float).copy()
    else:
        array = np.asarray(values, dtype=float)
        if array.ndim != 1:
            raise FFDContractError("log_nav must be one-dimensional")
        series = pd.Series(array, name="log_nav")
    if series.empty:
        raise FFDContractError("log_nav cannot be empty")
    if not np.isfinite(series.to_numpy(dtype=float)).all():
        raise FFDContractError("log_nav must contain only finite values")
    return series


def _safe_correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2 or len(right) < 2:
        return np.nan
    if np.std(left) == 0 or np.std(right) == 0:
        return np.nan
    return float(np.corrcoef(left, right)[0, 1])


def _diagnostic_row(
    *,
    phase: str,
    reason: str,
    calibration_version: str,
    config_version: str,
) -> dict[str, object]:
    return {
        "phase": phase,
        "d": np.nan,
        "weight_count": np.nan,
        "width": np.nan,
        "post_ffd_observations": np.nan,
        "nobs": np.nan,
        "lags": np.nan,
        "adf_stat": np.nan,
        "p_value": np.nan,
        "critical_value_1pct": np.nan,
        "critical_value_5pct": np.nan,
        "critical_value_10pct": np.nan,
        "correlation": np.nan,
        "passed": False,
        "status": "escalation_diagnostic",
        "reason": reason,
        "calibration_version": calibration_version,
        "config_version": config_version,
    }
