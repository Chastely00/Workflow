from __future__ import annotations

from dataclasses import replace

from fracdiff import fdiff
import numpy as np
import pandas as pd
import pytest

from etf_tricks.afml import AFMLConfig
from etf_tricks.afml.ffd import (
    FFDContractError,
    FFDSelector,
    apply_fixed_width_ffd,
    fixed_width_weights,
)


def _gate_at(threshold: float):
    def gate(values, d, config):
        del values, config
        passed = d >= threshold
        return {
            "adf_stat": -3.5 if passed else -1.0,
            "p_value": 0.01 if passed else 0.5,
            "lags": 1,
            "nobs": 200,
            "critical_values": {"1%": -3.4, "5%": -2.86, "10%": -2.57},
        }

    return gate


def _selector_config(**changes):
    values = {"min_adf_observations": 20, "weight_tolerance": 0.01}
    values.update(changes)
    return replace(AFMLConfig().ffd, **values)


def test_recursive_weights_and_d_boundaries():
    assert fixed_width_weights(0.0, 1e-5).tolist() == [1.0]
    assert fixed_width_weights(1.0, 1e-5).tolist() == pytest.approx([1.0, -1.0])
    weights = fixed_width_weights(0.5, 0.05)
    assert weights.tolist() == pytest.approx([1.0, -0.5, -0.125, -0.0625])


def test_valid_transform_uses_only_current_and_past():
    values = np.log(np.array([100, 101, 102, 104, 103], dtype=float))
    weights = np.array([1.0, -0.5])
    result = apply_fixed_width_ffd(values, weights)

    assert result.tolist() == pytest.approx(values[1:] - 0.5 * values[:-1])
    extended = apply_fixed_width_ffd(np.r_[values, np.log(999.0)], weights)
    assert extended[: len(result)].tolist() == pytest.approx(result)


def test_recursive_convolution_matches_fracdiff_modern():
    values = np.log(np.linspace(100.0, 120.0, 40))
    weights = fixed_width_weights(0.5, 0.01)

    expected = fdiff(values, n=0.5, window=len(weights), mode="valid")
    actual = apply_fixed_width_ffd(values, weights)

    assert actual.tolist() == pytest.approx(expected.tolist(), rel=1e-12, abs=1e-12)


def test_selector_chooses_minimum_passing_d(monkeypatch):
    monkeypatch.setattr("etf_tricks.afml.ffd._adf", _gate_at(0.37))
    values = pd.Series(np.cumsum(np.linspace(-0.2, 0.3, 240)), name="log_nav")

    selection = FFDSelector(_selector_config()).fit(values, "cal-1")

    assert selection.d == pytest.approx(0.37)
    assert selection.status == "stationarity_reached"
    first_pass = selection.search_evidence.query("passed").iloc[0]
    assert first_pass["d"] == pytest.approx(0.37)


def test_selector_autonomously_escalates_beyond_one(monkeypatch):
    monkeypatch.setattr("etf_tricks.afml.ffd._adf", _gate_at(1.17))
    values = pd.Series(np.linspace(1.0, 10.0, 300), name="log_nav")

    selection = FFDSelector(_selector_config()).fit(values, "cal-1")

    assert selection.d == pytest.approx(1.17)
    assert "AUTONOMOUS_ESCALATION" in set(selection.search_evidence["phase"])
    assert selection.search_evidence["d"].dropna().max() <= 2.0


def test_selector_reports_stationarity_not_reached_at_hard_stop(monkeypatch):
    monkeypatch.setattr("etf_tricks.afml.ffd._adf", _gate_at(99.0))
    values = pd.Series(np.linspace(1.0, 10.0, 300), name="log_nav")
    config = _selector_config(
        d_first_escalation_max=1.2,
        d_expansion_span=0.2,
        autonomous_max_d=1.4,
    )

    selection = FFDSelector(config).fit(values, "cal-1")

    assert selection.d is None
    assert selection.status == "stationarity_not_reached"
    assert selection.search_evidence["d"].dropna().max() <= 1.4
    with pytest.raises(FFDContractError, match="stationarity_not_reached"):
        FFDSelector(config).transform(values, selection)


def test_transform_preserves_index_and_future_append_prefix(monkeypatch):
    monkeypatch.setattr("etf_tricks.afml.ffd._adf", _gate_at(0.5))
    index = pd.Index(range(1, 241), name="bar_id")
    values = pd.Series(np.log(np.linspace(100.0, 140.0, len(index))), index=index)
    selector = FFDSelector(_selector_config())
    selection = selector.fit(values, "cal-1")

    prefix = selector.transform(values.iloc[:200], selection)
    extended = selector.transform(values, selection)

    pd.testing.assert_frame_equal(prefix, extended.iloc[: len(prefix)])
    assert prefix.index.name == "bar_id"
    assert prefix["ffd_level"].notna().all()


@pytest.mark.parametrize(
    ("d", "tolerance", "message"),
    [(-0.1, 1e-5, "non-negative"), (0.5, 0.0, "tolerance")],
)
def test_invalid_weight_contract_fails_closed(d, tolerance, message):
    with pytest.raises(FFDContractError, match=message):
        fixed_width_weights(d, tolerance)
