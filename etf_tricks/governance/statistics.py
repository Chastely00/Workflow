"""AFML-style probabilistic and deflated Sharpe evidence for finalized returns."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.stats import kurtosis, norm, skew


@dataclass(frozen=True)
class SharpeEvidence:
    """Moment-adjusted Sharpe evidence on one fixed-period net-return path."""

    probability: float
    observations: int
    periodic_sharpe: float
    annualized_sharpe: float
    benchmark_annualized_sharpe: float
    skewness: float
    kurtosis: float
    sharpe_standard_error: float


@dataclass(frozen=True)
class DeflatedSharpeEvidence(SharpeEvidence):
    """PSR evaluated against the expected maximum Sharpe across registered trials."""

    effective_trial_count: float
    trial_annualized_sharpe_variance: float
    expected_max_annualized_sharpe: float


def _validated_returns(returns: np.ndarray | list[float]) -> np.ndarray:
    values = np.asarray(returns, dtype=float).reshape(-1)
    if len(values) < 4 or not np.isfinite(values).all():
        raise ValueError("PSR requires at least four finite returns")
    volatility = float(values.std(ddof=1))
    if not math.isfinite(volatility) or volatility <= 0:
        raise ValueError("PSR requires positive sample volatility")
    return values


def _moment_adjusted_sharpe_standard_error(
    values: np.ndarray,
) -> tuple[float, float, float, float]:
    periodic_sharpe = float(values.mean() / values.std(ddof=1))
    skewness = float(skew(values, bias=False))
    non_excess_kurtosis = float(kurtosis(values, fisher=False, bias=False))
    denominator = 1 - skewness * periodic_sharpe + ((non_excess_kurtosis - 1) / 4) * periodic_sharpe**2
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("PSR moment-adjusted Sharpe variance is not positive")
    standard_error = math.sqrt(denominator / (len(values) - 1))
    return periodic_sharpe, skewness, non_excess_kurtosis, standard_error


def probabilistic_sharpe_ratio(
    returns: np.ndarray | list[float],
    *,
    benchmark_annualized_sharpe: float = 0.0,
    annualization_factor: int = 252,
) -> SharpeEvidence:
    """Return AFML's moment-adjusted probability that Sharpe exceeds a benchmark.

    The calculation is performed at the raw return periodicity.  The benchmark
    supplied by callers is annualized and converted using the same factor only
    for the comparison, preventing accidental annualized/daily mixing.
    """
    values = _validated_returns(returns)
    if annualization_factor <= 0:
        raise ValueError("annualization factor must be positive")
    benchmark = float(benchmark_annualized_sharpe)
    if not math.isfinite(benchmark):
        raise ValueError("benchmark annualized Sharpe must be finite")
    periodic_sharpe, skewness, non_excess_kurtosis, standard_error = _moment_adjusted_sharpe_standard_error(values)
    benchmark_periodic = benchmark / math.sqrt(annualization_factor)
    probability = float(norm.cdf((periodic_sharpe - benchmark_periodic) / standard_error))
    return SharpeEvidence(
        probability=probability,
        observations=len(values),
        periodic_sharpe=periodic_sharpe,
        annualized_sharpe=periodic_sharpe * math.sqrt(annualization_factor),
        benchmark_annualized_sharpe=benchmark,
        skewness=skewness,
        kurtosis=non_excess_kurtosis,
        sharpe_standard_error=standard_error,
    )


def deflated_sharpe_ratio(
    returns: np.ndarray | list[float],
    *,
    effective_trial_count: float,
    trial_annualized_sharpe_variance: float,
    annualization_factor: int = 252,
) -> DeflatedSharpeEvidence:
    """Deflate PSR against the expected maximum Sharpe of registered trials.

    ``trial_annualized_sharpe_variance`` must come from observed, registered
    alternative return paths; it must not be guessed from this candidate's path.
    """
    trial_count = float(effective_trial_count)
    trial_variance = float(trial_annualized_sharpe_variance)
    if not math.isfinite(trial_count) or trial_count <= 1:
        raise ValueError("effective trial count must exceed one for DSR")
    if not math.isfinite(trial_variance) or trial_variance <= 0:
        raise ValueError("trial Sharpe variance must be finite and positive")
    expected_max = math.sqrt(trial_variance) * (
        (1 - np.euler_gamma) * norm.ppf(1 - 1 / trial_count)
        + np.euler_gamma * norm.ppf(1 - 1 / (trial_count * math.e))
    )
    psr = probabilistic_sharpe_ratio(
        returns,
        benchmark_annualized_sharpe=float(expected_max),
        annualization_factor=annualization_factor,
    )
    return DeflatedSharpeEvidence(
        **psr.__dict__,
        effective_trial_count=trial_count,
        trial_annualized_sharpe_variance=trial_variance,
        expected_max_annualized_sharpe=float(expected_max),
    )
