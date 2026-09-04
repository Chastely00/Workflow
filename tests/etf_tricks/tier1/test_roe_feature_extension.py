from __future__ import annotations

import pandas as pd
import pytest

from etf_tricks.tier1.roe_feature_extension import Tier1RoeFeatureExtensionBuilder


def test_roe_extension_uses_only_latest_after_close_available_r103_and_current_holdings():
    bars = pd.DataFrame(
        {
            "etf_id": ["quality"], "bar_id": [1], "bar_status": ["FINALIZED"],
            "bar_end_date": ["2024-01-03"],
            "feature_available_at": ["2024-01-03 23:59:59+08:00"],
        }
    )
    holdings = pd.DataFrame(
        {
            "date": ["2024-01-03", "2024-01-03"], "etf_id": ["quality", "quality"],
            "ticker": ["1101", "2330"], "actual_weight": [0.4, 0.6],
        }
    )
    roe = pd.DataFrame(
        {
            "ticker": ["1101", "2330", "2330"],
            "source_available_date": ["2024-01-02", "2024-01-02", "2024-01-04"],
            "revision_date": ["2023-12-31", "2023-12-31", "2023-12-31"],
            "no": ["TTM", "TTM", "TTM"], "merg": ["Y", "Y", "Y"], "curr": ["NTD", "NTD", "NTD"],
            "r103": [10.0, 20.0, 99.0],
            "r103_conflict": [False, False, False],
        }
    )

    result = Tier1RoeFeatureExtensionBuilder().build(bars, holdings, roe)

    assert result.loc[0, "roe_weighted_r103"] == pytest.approx(16.0)
    assert result.loc[0, "roe_coverage_count"] == 2
    assert result.loc[0, "roe_source_available_date"] == pd.Timestamp("2024-01-02")


def test_roe_extension_does_not_use_same_day_value_before_after_close_cutoff():
    bars = pd.DataFrame(
        {
            "etf_id": ["quality"], "bar_id": [1], "bar_status": ["FINALIZED"],
            "bar_end_date": ["2024-01-03"],
            "feature_available_at": ["2024-01-03 17:00:00+08:00"],
        }
    )
    holdings = pd.DataFrame(
        {"date": ["2024-01-03"], "etf_id": ["quality"], "ticker": ["2330"], "actual_weight": [1.0]}
    )
    roe = pd.DataFrame(
        {
            "ticker": ["2330"], "source_available_date": ["2024-01-03"],
            "revision_date": ["2023-12-31"], "no": ["TTM"], "merg": ["Y"], "curr": ["NTD"], "r103": [20.0], "r103_conflict": [False],
        }
    )

    result = Tier1RoeFeatureExtensionBuilder().build(bars, holdings, roe)

    assert pd.isna(result.loc[0, "roe_weighted_r103"])
    assert result.loc[0, "roe_coverage_count"] == 0
