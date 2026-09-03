import pandas as pd
import pytest

from etf_tricks.tier1.targets import Tier1TargetBuilder, Tier1TargetConfig


def test_target_uses_next_open_and_proportional_costs() -> None:
    bars = pd.DataFrame(
        {
            "etf_id": ["x"] * 22,
            "bar_id": range(22),
            "bar_end_date": pd.bdate_range("2024-01-01", periods=22),
            "close_nav": [100.0 + (index % 3) for index in range(22)],
            "feature_available_at": pd.date_range("2024-01-01", periods=22, tz="Asia/Taipei"),
        }
    )
    opens = pd.DataFrame(
        {
            "etf_id": ["x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31")],
            "raw_open_nav": [101.0, 104.0],
            "available_at": pd.to_datetime(["2024-01-30 13:30+08:00", "2024-01-31 13:30+08:00"]),
            "is_legal_execution": [True, True],
        }
    )

    result = Tier1TargetBuilder(
        Tier1TargetConfig(volatility_span=20, min_obs=20, pt_mult=2.0, sl_mult=2.0, vertical_bars=1, buy_cost_rate=0.001, sell_cost_rate=0.001)
    ).build(bars, opens)

    row = result.loc[result["t0_bar_id"].eq(20)].iloc[0]
    assert row["entry_date"] == pd.Timestamp("2024-01-30")
    assert row["entry_raw_open"] == pytest.approx(101.0)
    assert row["exit_date"] == pd.Timestamp("2024-01-31")
    assert row["exit_raw_open"] == pytest.approx(104.0)
    assert row["net_log_return"] == pytest.approx(__import__("math").log((104.0 * 0.999) / (101.0 * 1.001)))
