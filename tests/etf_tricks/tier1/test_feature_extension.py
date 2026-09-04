from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_tricks.tier1.feature_extension import Tier1FeatureExtensionBuilder


def test_extension_builds_past_only_bar_and_ir0001_volatility() -> None:
    dates = pd.bdate_range("2024-01-02", periods=70)
    ir_close = 20_000.0 * np.exp(np.linspace(0.0, 0.14, len(dates)))
    ir = pd.DataFrame(
        {
            "date": dates,
            "close": ir_close,
            "available_at": dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18),
        }
    )
    bar_dates = dates[-15:]
    close_nav = 100.0 * np.exp(np.linspace(0.0, 0.06, len(bar_dates)))
    bars = pd.DataFrame(
        {
            "etf_id": "x",
            "bar_id": range(len(bar_dates)),
            "bar_status": "FINALIZED",
            "bar_end_date": bar_dates,
            "close_nav": close_nav,
            "feature_available_at": bar_dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18),
        }
    )

    result = Tier1FeatureExtensionBuilder().build(bars, ir)

    last = result.iloc[-1]
    expected_bar_std = np.diff(np.log(close_nav))[-14:].std(ddof=1)
    expected_ir_vol = np.diff(np.log(ir_close))[-20:].std(ddof=1) * np.sqrt(252)
    assert last["bar_log_return_std_14"] == pytest.approx(expected_bar_std)
    assert last["ir0001_realized_vol_20"] == pytest.approx(expected_ir_vol)
    assert last["ir0001_observation_date"] == dates[-1]
    assert last["ir0001_available_at"] <= last["feature_available_at"]
    assert last["ir0001_revision_status"] == "PIT_REVISION_UNVERIFIED"


def test_extension_does_not_use_ir0001_observation_available_after_decision() -> None:
    dates = pd.bdate_range("2024-01-02", periods=62)
    ir = pd.DataFrame(
        {
            "date": dates,
            "close": 20_000.0 * np.exp(np.linspace(0.0, 0.12, len(dates))),
            "available_at": dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18),
        }
    )
    bars = pd.DataFrame(
        {
            "etf_id": ["x"],
            "bar_id": [0],
            "bar_status": ["FINALIZED"],
            "bar_end_date": [dates[-1]],
            "close_nav": [100.0],
            "feature_available_at": [dates[-1].tz_localize("Asia/Taipei") + pd.Timedelta(hours=17)],
        }
    )

    result = Tier1FeatureExtensionBuilder().build(bars, ir)

    assert result.iloc[0]["ir0001_observation_date"] == dates[-2]
    assert result.iloc[0]["ir0001_available_at"] < result.iloc[0]["feature_available_at"]


def test_extension_normalizes_mixed_utc_timestamp_resolutions_for_asof_join() -> None:
    dates = pd.bdate_range("2024-01-02", periods=62)
    ir = pd.DataFrame(
        {
            "date": dates,
            "close": 20_000.0 * np.exp(np.linspace(0.0, 0.12, len(dates))),
            "available_at": pd.Series(
                dates.tz_localize("UTC") + pd.Timedelta(hours=10),
                dtype="datetime64[us, UTC]",
            ),
        }
    )
    bars = pd.DataFrame(
        {
            "etf_id": ["x"],
            "bar_id": [0],
            "bar_status": ["FINALIZED"],
            "bar_end_date": [dates[-1]],
            "close_nav": [100.0],
            "feature_available_at": pd.Series(
                [dates[-1].tz_localize("UTC") + pd.Timedelta(hours=11)],
                dtype="datetime64[ns, UTC]",
            ),
        }
    )

    result = Tier1FeatureExtensionBuilder().build(bars, ir)

    assert result.iloc[0]["ir0001_observation_date"] == dates[-1]
