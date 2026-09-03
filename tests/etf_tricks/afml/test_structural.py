from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from statsmodels.tsa.stattools import adfuller

from etf_tricks.afml import (
    StructuralConfig,
    StructuralFeatureEngine,
    adf_start_vector,
    structural_statistics,
)


def test_structural_statistics_keep_qadf_and_cadf_dispersion_distinct():
    values = np.array([-2.0, -1.0, 0.0, 1.0, 8.0])

    result = structural_statistics(values, q=0.8, v=0.1, quantile_method="linear")

    assert result["sadf"] == 8.0
    assert result["qadf"] == pytest.approx(
        np.quantile(values, 0.8, method="linear")
    )
    assert result["qadf_dispersion"] == pytest.approx(
        np.quantile(values, 0.9, method="linear")
        - np.quantile(values, 0.7, method="linear")
    )
    tail = values[values >= result["qadf"]]
    assert result["cadf"] == pytest.approx(tail.mean())
    assert result["cadf_dispersion"] == pytest.approx(tail.std(ddof=0))


def test_structural_statistics_leave_zero_dispersion_z_missing_with_reason():
    result = structural_statistics(
        np.array([1.0, 1.0, 1.0]),
        q=0.8,
        v=0.1,
        quantile_method="linear",
    )

    assert np.isnan(result["sadf_cadf_z"])
    assert result["structural_quality_reason"] == "ZERO_CADF_DISPERSION"


def test_adf_start_vector_matches_statsmodels_for_every_governed_start():
    rng = np.random.default_rng(9127)
    log_prices = np.cumsum(rng.normal(0.001, 0.02, size=90))
    end = 72
    minimum = 35

    starts, actual = adf_start_vector(
        log_prices,
        end=end,
        min_sample_length=minimum,
        lags=1,
    )

    assert starts.tolist() == list(range(end - minimum + 2))
    expected = np.array(
        [
            adfuller(
                log_prices[start : end + 1],
                maxlag=1,
                regression="c",
                autolag=None,
            )[0]
            for start in starts
        ]
    )
    np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-9)


def test_adf_start_vector_handles_rank_deficient_history():
    starts, statistics = adf_start_vector(
        np.ones(80), end=79, min_sample_length=30, lags=1
    )

    assert len(starts) == 51
    assert np.isnan(statistics).all()


def _structural_frame(size: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(41)
    dates = pd.bdate_range("2024-01-02", periods=size)
    return pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": np.arange(size),
            "log_close": np.cumsum(rng.normal(0.0005, 0.018, size=size)),
            "available_at": dates.tz_localize("Asia/Taipei")
            + pd.Timedelta(hours=14),
        }
    )


def test_structural_prefix_is_unchanged_by_future_append():
    config = StructuralConfig(min_sample_length=30, lags=1, q=0.9, v=0.05)
    series = _structural_frame()

    prefix = StructuralFeatureEngine(config).transform(
        series.iloc[:110], "log_close", "available_at"
    )
    extended = StructuralFeatureEngine(config).transform(
        series, "log_close", "available_at"
    )

    pd.testing.assert_frame_equal(
        prefix.reset_index(drop=True),
        extended.iloc[: len(prefix)].reset_index(drop=True),
    )


def test_structural_engine_preserves_availability_and_audit_metadata():
    config = StructuralConfig(min_sample_length=30, lags=1, q=0.9, v=0.05)
    frame = _structural_frame(45)

    result = StructuralFeatureEngine(config).transform(
        frame, "log_close", "available_at"
    )

    assert result["feature_available_at"].equals(frame["available_at"])
    assert result.loc[:28, "structural_quality_reason"].eq(
        "INSUFFICIENT_OBSERVATIONS"
    ).all()
    row = result.iloc[-1]
    assert row["adf_window_count"] == 16
    assert row["adf_min_start"] == 0
    assert 0 <= row["adf_maximizing_start"] <= 15
    assert row["structural_q"] == 0.9
    assert row["structural_v"] == 0.05
    assert row["structural_quantile_method"] == "linear"
    assert row["source_observation_count"] == 45
    assert row["source_value_column"] == "log_close"


def test_structural_engine_groups_etfs_without_cross_contamination():
    base = _structural_frame(55)
    other = base.assign(
        etf_id="low_volatility",
        log_close=base["log_close"] * -0.7 + 3.0,
    )
    combined = pd.concat([base, other], ignore_index=True)
    config = StructuralConfig(min_sample_length=30, lags=1, q=0.9, v=0.05)

    grouped = StructuralFeatureEngine(config).transform(
        combined, "log_close", "available_at"
    )
    isolated = StructuralFeatureEngine(config).transform(
        other, "log_close", "available_at"
    )

    columns = [
        "sadf",
        "qadf",
        "qadf_dispersion",
        "cadf",
        "cadf_dispersion",
        "sadf_cadf_z",
        "adf_window_count",
    ]
    pd.testing.assert_frame_equal(
        grouped[grouped["etf_id"].eq("low_volatility")][columns].reset_index(
            drop=True
        ),
        isolated[columns].reset_index(drop=True),
    )


@pytest.mark.parametrize(
    ("prices", "end", "minimum", "lags", "message"),
    [
        ([1.0, 2.0, np.nan, 4.0], 3, 3, 0, "finite"),
        ([1.0, 2.0, 3.0], 3, 3, 0, "end"),
        ([1.0, 2.0, 3.0], 2, 1, 0, "min_sample_length"),
        ([1.0, 2.0, 3.0], 2, 3, -1, "lags"),
    ],
)
def test_adf_start_vector_rejects_invalid_contract(
    prices, end, minimum, lags, message
):
    with pytest.raises(ValueError, match=message):
        adf_start_vector(
            prices,
            end=end,
            min_sample_length=minimum,
            lags=lags,
        )
