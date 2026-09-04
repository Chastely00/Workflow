import numpy as np
import pytest
from scipy.stats import norm

from etf_tricks.governance.statistics import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)


def test_probabilistic_sharpe_matches_afml_moment_adjusted_formula() -> None:
    returns = np.array([0.01, -0.005, 0.012, 0.002, -0.001, 0.009, 0.004, -0.003])

    result = probabilistic_sharpe_ratio(returns, annualization_factor=252)

    sr = returns.mean() / returns.std(ddof=1)
    skew = ((returns - returns.mean()) ** 3).mean() / returns.std(ddof=0) ** 3
    kurtosis = ((returns - returns.mean()) ** 4).mean() / returns.std(ddof=0) ** 4
    denominator = np.sqrt(1 - skew * sr + ((kurtosis - 1) / 4) * sr**2)
    expected = norm.cdf(sr * np.sqrt(len(returns) - 1) / denominator)
    assert result.probability == pytest.approx(expected, abs=0.03)
    assert result.annualized_sharpe == pytest.approx(sr * np.sqrt(252))


def test_psr_uses_same_periodicity_for_benchmark_and_is_scale_invariant() -> None:
    returns = np.array([0.012, -0.004, 0.008, 0.001, 0.004, -0.002, 0.006, 0.003])
    annualized_benchmark = 0.5

    annualized = probabilistic_sharpe_ratio(
        returns, benchmark_annualized_sharpe=annualized_benchmark, annualization_factor=252
    )
    periodic = probabilistic_sharpe_ratio(
        returns, benchmark_annualized_sharpe=annualized_benchmark / np.sqrt(252), annualization_factor=1
    )

    assert annualized.probability == pytest.approx(periodic.probability)


def test_dsr_uses_effective_trial_count_and_cross_trial_variance() -> None:
    returns = np.array([0.01, -0.004, 0.012, 0.002, -0.001, 0.009, 0.004, -0.003])
    psr = probabilistic_sharpe_ratio(returns, annualization_factor=252)
    dsr = deflated_sharpe_ratio(
        returns,
        effective_trial_count=20,
        trial_annualized_sharpe_variance=0.04,
        annualization_factor=252,
    )

    assert dsr.expected_max_annualized_sharpe > 0
    assert 0 <= dsr.probability < psr.probability
    assert dsr.effective_trial_count == 20


@pytest.mark.parametrize("returns", [np.array([0.01, 0.01, 0.01, 0.01]), np.array([0.01, -0.01, 0.0])])
def test_psr_refuses_insufficient_or_zero_variance_returns(returns: np.ndarray) -> None:
    with pytest.raises(ValueError, match="at least four finite returns|positive sample volatility"):
        probabilistic_sharpe_ratio(returns)


def test_dsr_refuses_missing_trial_variance_or_invalid_trial_count() -> None:
    returns = np.array([0.01, -0.004, 0.012, 0.002, -0.001, 0.009, 0.004, -0.003])
    with pytest.raises(ValueError, match="effective trial count"):
        deflated_sharpe_ratio(returns, effective_trial_count=1, trial_annualized_sharpe_variance=0.04)
    with pytest.raises(ValueError, match="trial Sharpe variance"):
        deflated_sharpe_ratio(returns, effective_trial_count=2, trial_annualized_sharpe_variance=0)
