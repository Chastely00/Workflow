from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_chip_extension_uses_current_holdings_and_past_current_twenty_sessions() -> None:
    from etf_tricks.tier1.chip_feature_extension import Tier1ChipFeatureExtensionBuilder

    dates = pd.bdate_range("2024-01-02", periods=20)
    bars = pd.DataFrame(
        {
            "etf_id": ["x"],
            "bar_id": [7],
            "bar_status": ["FINALIZED"],
            "bar_end_date": [dates[-1]],
            "feature_available_at": [
                dates[-1].tz_localize("Asia/Taipei") + pd.Timedelta(hours=23, minutes=59)
            ],
        }
    )
    holdings = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "etf_id": "x",
            "ticker": ["1101"] * len(dates) + ["2330"] * len(dates),
            "actual_weight": [0.25] * len(dates) + [0.75] * len(dates),
        }
    )
    chip = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "ticker": ["1101"] * len(dates) + ["2330"] * len(dates),
            "qfii_examt": list(range(1, 21)) + [0.0] * len(dates),
            "fund_examt": [0.0] * (2 * len(dates)),
            "dlrp_examt": [0.0] * (2 * len(dates)),
            "source_available_date": list(dates) * 2,
        }
    )

    result = Tier1ChipFeatureExtensionBuilder().build(bars, holdings, chip)

    expected_daily_flow = np.arange(1.0, 21.0) * 0.25
    expected_z = (expected_daily_flow[-1] - expected_daily_flow.mean()) / expected_daily_flow.std(ddof=1)
    row = result.iloc[0]
    assert row["chip_net_flow_20"] == pytest.approx(expected_daily_flow[-1])
    assert row["chip_net_flow_z_20"] == pytest.approx(expected_z)
    assert row["chip_observation_date"] == dates[-1]
    assert row["chip_availability_assumption"] == "AFTER_CLOSE_DATE_ONLY"
    assert row["chip_revision_status"] == "PIT_REVISION_UNVERIFIED"


def test_chip_extension_rejects_a_same_day_feature_before_conservative_after_close_cutoff() -> None:
    from etf_tricks.tier1.chip_feature_extension import Tier1ChipFeatureExtensionBuilder

    date = pd.Timestamp("2024-01-02")
    bars = pd.DataFrame(
        {
            "etf_id": ["x"], "bar_id": [1], "bar_status": ["FINALIZED"],
            "bar_end_date": [date],
            "feature_available_at": [date.tz_localize("Asia/Taipei") + pd.Timedelta(hours=17)],
        }
    )
    holdings = pd.DataFrame(
        {"date": [date], "etf_id": ["x"], "ticker": ["1101"], "actual_weight": [1.0]}
    )
    chip = pd.DataFrame(
        {
            "date": [date], "ticker": ["1101"], "qfii_examt": [1.0],
            "fund_examt": [0.0], "dlrp_examt": [0.0], "source_available_date": [date],
        }
    )

    with pytest.raises(ValueError, match="after-close"):
        Tier1ChipFeatureExtensionBuilder().build(bars, holdings, chip)
