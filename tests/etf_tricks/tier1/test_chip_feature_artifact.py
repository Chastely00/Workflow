from __future__ import annotations

import pandas as pd


def test_merge_chip_extension_preserves_complete_base_clock_and_columns() -> None:
    from etf_tricks.tier1.chip_feature_artifact import merge_chip_feature_extension

    availability = pd.to_datetime(["2024-01-02 15:00+08:00", "2024-01-03 15:00+08:00"])
    base = pd.DataFrame(
        {
            "etf_id": ["x", "x"], "bar_id": [1, 2],
            "feature_available_at": availability, "base_feature": [1.0, 2.0],
        }
    )
    chip = pd.DataFrame(
        {
            "etf_id": ["x", "x"], "bar_id": [1, 2],
            "feature_available_at": availability, "chip_net_flow_z_20": [0.5, 1.0],
        }
    )

    result = merge_chip_feature_extension(base, chip)

    assert result.columns.tolist() == [
        "etf_id", "bar_id", "feature_available_at", "base_feature", "chip_net_flow_z_20"
    ]
    assert result["chip_net_flow_z_20"].tolist() == [0.5, 1.0]
