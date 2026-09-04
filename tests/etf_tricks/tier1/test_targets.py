import pandas as pd
import pytest

from etf_tricks.tier1.targets import Tier1TargetBuilder, Tier1TargetConfig


def test_target_cost_defaults_follow_authoritative_tier_one_rates() -> None:
    config = Tier1TargetConfig()

    assert config.buy_cost_rate == pytest.approx(0.001425)
    assert config.sell_cost_rate == pytest.approx(0.003)


def test_daily_close_inside_dollar_bar_triggers_before_bar_end() -> None:
    bars = pd.DataFrame(
        {
            "etf_id": ["x"] * 23,
            "bar_id": range(23),
            "bar_end_date": list(pd.bdate_range("2024-01-01", periods=21))
            + [pd.Timestamp("2024-02-05"), pd.Timestamp("2024-02-06")],
            "close_nav": [100.0 + (index % 3) for index in range(21)] + [130.0, 131.0],
            "feature_available_at": pd.date_range("2024-01-01", periods=23, tz="Asia/Taipei"),
        }
    )
    opens = pd.DataFrame(
        {
            "etf_id": ["x", "x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-06")],
            "raw_open_nav": [101.0, 102.0, 110.0],
            "available_at": pd.to_datetime(["2024-01-30 13:30+08:00", "2024-02-01 13:30+08:00", "2024-02-06 13:30+08:00"]),
            "is_legal_execution": [True, True, True],
        }
    )
    daily_closes = pd.DataFrame(
        {
            "etf_id": ["x", "x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01")],
            "nav": [101.0, 130.0, 129.0],
            "available_at": pd.to_datetime(["2024-01-30 18:00+08:00", "2024-01-31 18:00+08:00", "2024-02-01 18:00+08:00"]),
        }
    )

    result = Tier1TargetBuilder(
        Tier1TargetConfig(volatility_span=20, min_obs=20, pt_mult=2.0, sl_mult=2.0, vertical_bars=1)
    ).build(bars, opens, daily_closes)

    row = result.loc[result["t0_bar_id"].eq(20)].iloc[0]
    assert row["trigger_type"] == "upper"
    assert row["trigger_date"] == pd.Timestamp("2024-01-31")
    assert row["exit_date"] == pd.Timestamp("2024-02-01")
    assert row["exit_raw_open"] == pytest.approx(102.0)
    assert row["trigger_available_at"] == pd.Timestamp("2024-01-31 18:00:00+08:00")
    assert row["label_available_at"] == pd.Timestamp("2024-02-01 13:30:00+08:00")


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
    daily_closes = pd.DataFrame(
        {
            "etf_id": ["x"],
            "date": [pd.Timestamp("2024-01-30")],
            "nav": [102.0],
            "available_at": pd.to_datetime(["2024-01-30 18:00+08:00"]),
        }
    )

    result = Tier1TargetBuilder(
        Tier1TargetConfig(volatility_span=20, min_obs=20, pt_mult=2.0, sl_mult=2.0, vertical_bars=1, buy_cost_rate=0.001, sell_cost_rate=0.001)
    ).build(bars, opens, daily_closes)

    row = result.loc[result["t0_bar_id"].eq(20)].iloc[0]
    assert row["entry_date"] == pd.Timestamp("2024-01-30")
    assert row["entry_raw_open"] == pytest.approx(101.0)
    assert row["exit_date"] == pd.Timestamp("2024-01-31")
    assert row["exit_raw_open"] == pytest.approx(104.0)
    assert row["net_log_return"] == pytest.approx(__import__("math").log((104.0 * 0.999) / (101.0 * 1.001)))


def test_upper_close_trigger_exits_at_following_open() -> None:
    bars = pd.DataFrame(
        {
            "etf_id": ["x"] * 23,
            "bar_id": range(23),
            "bar_end_date": pd.bdate_range("2024-01-01", periods=23),
            "close_nav": [100.0 + (index % 3) for index in range(21)] + [130.0, 131.0],
            "feature_available_at": pd.date_range("2024-01-01", periods=23, tz="Asia/Taipei"),
        }
    )
    opens = pd.DataFrame(
        {
            "etf_id": ["x", "x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01")],
            "raw_open_nav": [101.0, 102.0, 110.0],
            "available_at": pd.to_datetime(["2024-01-30 13:30+08:00", "2024-01-31 13:30+08:00", "2024-02-01 13:30+08:00"]),
            "is_legal_execution": [True, True, True],
        }
    )
    daily_closes = pd.DataFrame(
        {
            "etf_id": ["x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31")],
            "nav": [101.0, 130.0],
            "available_at": pd.to_datetime(["2024-01-30 18:00+08:00", "2024-01-31 18:00+08:00"]),
        }
    )

    result = Tier1TargetBuilder(
        Tier1TargetConfig(volatility_span=20, min_obs=20, pt_mult=2.0, sl_mult=2.0, vertical_bars=2)
    ).build(bars, opens, daily_closes)

    row = result.loc[result["t0_bar_id"].eq(20)].iloc[0]
    assert row["trigger_type"] == "upper"
    assert row["trigger_date"] == pd.Timestamp("2024-01-31")
    assert row["exit_date"] == pd.Timestamp("2024-02-01")
    assert row["exit_raw_open"] == pytest.approx(110.0)


def test_target_scope_keeps_warmup_bars_but_emits_only_requested_events() -> None:
    bars = pd.DataFrame(
        {
            "etf_id": ["x"] * 24,
            "bar_id": range(24),
            "bar_end_date": pd.bdate_range("2024-01-01", periods=24),
            "close_nav": [100.0 + (index % 3) for index in range(24)],
            "feature_available_at": pd.date_range("2024-01-01", periods=24, tz="Asia/Taipei"),
        }
    )
    opens = pd.DataFrame(
        {
            "etf_id": ["x", "x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01")],
            "raw_open_nav": [101.0, 102.0, 103.0],
            "available_at": pd.to_datetime(["2024-01-30 13:30+08:00", "2024-01-31 13:30+08:00", "2024-02-01 13:30+08:00"]),
            "is_legal_execution": [True, True, True],
        }
    )
    daily_closes = pd.DataFrame(
        {
            "etf_id": ["x", "x"],
            "date": [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-01-31")],
            "nav": [102.0, 102.0],
            "available_at": pd.to_datetime(["2024-01-30 18:00+08:00", "2024-01-31 18:00+08:00"]),
        }
    )

    result = Tier1TargetBuilder(
        Tier1TargetConfig(volatility_span=20, min_obs=20, vertical_bars=1)
    ).build(bars, opens, daily_closes, event_start_date="2024-01-29")

    assert result["t0_date"].min() == pd.Timestamp("2024-01-29")
    assert result["t0_bar_id"].tolist() == [20, 21, 22, 23]
    assert result.loc[result["t0_bar_id"].eq(20), "target_volatility"].notna().all()
