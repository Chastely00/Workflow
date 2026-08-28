from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from etf_tricks.afml import AFMLBoundaries, AFMLConfig
from etf_tricks.afml.dollar_bars import (
    DollarBarBuilder,
    DollarBarCalibrator,
    DollarBarContractError,
    QCalibration,
)


def _available_at(dates: pd.Series) -> pd.Series:
    index = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize("Asia/Taipei")
    return pd.Series(
        index.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1),
        index=dates.index,
    )


def _daily_frame(dates: list[str], amounts: list[float], etf_id: str = "momentum"):
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "etf_id": etf_id,
            "nav": [100.0 + index for index in range(len(dates))],
            "etf_amount": amounts,
            "has_data_quality_flag": False,
            "missing_traded_value_count": 0,
            "source_revision_status": "PIT_REVISION_UNVERIFIED",
            "source_manifest_hash": "daily-hash",
        }
    )
    frame["source_available_at"] = _available_at(frame["date"])
    return frame


def _ix_frame(dates: list[str], amounts: list[float]):
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": "IX0001",
            "close": 20_000.0,
            "traded_value": amounts,
            "source_revision_status": "PIT_REVISION_UNVERIFIED",
            "source_manifest_hash": "ix-hash",
        }
    )
    frame["source_available_at"] = _available_at(frame["date"])
    return frame


def _calendar(dates: list[str]):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "market": "TWSE",
            "is_trading_day": True,
        }
    )


def _manual_calibration(fit_end: str, q_star: float = 0.1) -> QCalibration:
    frozen = pd.Timestamp(fit_end, tz="Asia/Taipei") + pd.Timedelta(days=1) - pd.Timedelta(
        nanoseconds=1
    )
    return QCalibration(
        q_star=q_star,
        calibration_version="cal-1",
        calibration_scope="TEST_ONLY_SUBSET",
        etf_ids=("momentum",),
        fit_start=pd.Timestamp("2024-01-02"),
        fit_end=pd.Timestamp(fit_end),
        parameters_frozen_at=frozen,
        calibration_effective_at=pd.NaT,
        candidate_evidence=pd.DataFrame(),
    )


def three_bar_fixture():
    member_dates = [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
        "2024-01-09",
    ]
    all_dates = ["2024-01-01", *member_dates]
    daily = _daily_frame(member_dates, [40.0, 70.0, 130.0, 30.0, 30.0, 40.0])
    ix = _ix_frame(all_dates, [1_000.0] * len(all_dates))
    calendar = _calendar(all_dates)
    calibration = _manual_calibration("2024-01-09")
    return daily, ix, calendar, calibration


def test_threshold_freezes_and_bar_amount_reconciles():
    daily, ix, calendar, calibration = three_bar_fixture()
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=2,
        min_market_amount_observations=1,
        min_completed_bars=1,
    )
    tables = DollarBarBuilder(config).transform(
        daily, ix, calendar, calibration, role="CALIBRATION_HISTORY"
    )
    bars = tables.dollar_bars

    assert bars["bar_amount"].tolist() == pytest.approx([110.0, 130.0, 100.0])
    reconciled = tables.bar_daily_membership.groupby(["etf_id", "bar_id"])[
        "etf_amount"
    ].sum()
    expected = bars.set_index(["etf_id", "bar_id"])["bar_amount"]
    assert reconciled.tolist() == pytest.approx(expected.tolist())
    assert bars.iloc[0]["threshold_asof_date"] < bars.iloc[0]["bar_start_date"]
    assert bars.iloc[0]["threshold_amount"] == bars.iloc[0]["frozen_threshold_amount"]
    assert tables.open_bar_checkpoints.empty


def test_one_day_can_close_at_most_one_bar_and_overshoot_is_not_carried():
    dates = ["2024-01-01", "2024-01-02"]
    daily = _daily_frame(["2024-01-02"], [500.0])
    ix = _ix_frame(dates, [1_000.0, 1_000.0])
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=2,
        min_market_amount_observations=1,
        min_completed_bars=1,
    )
    tables = DollarBarBuilder(config).transform(
        daily,
        ix,
        _calendar(dates),
        _manual_calibration("2024-01-02"),
        role="CALIBRATION_HISTORY",
    )

    assert len(tables.dollar_bars) == 1
    assert tables.dollar_bars.iloc[0]["overshoot_amount"] == pytest.approx(400.0)
    assert tables.open_bar_checkpoints.empty


def test_future_append_preserves_finalized_prefix():
    daily, ix, calendar, calibration = three_bar_fixture()
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=2,
        min_market_amount_observations=1,
        min_completed_bars=1,
    )
    prefix_end = pd.Timestamp("2024-01-04")
    prefix = DollarBarBuilder(config).transform(
        daily[daily["date"].le(prefix_end)],
        ix[ix["date"].le(prefix_end)],
        calendar[calendar["date"].le(prefix_end)],
        calibration,
        role="CALIBRATION_HISTORY",
    )
    extended = DollarBarBuilder(config).transform(
        daily, ix, calendar, calibration, role="CALIBRATION_HISTORY"
    )

    pd.testing.assert_frame_equal(
        prefix.dollar_bars,
        extended.dollar_bars.query("bar_end_date <= @prefix_end").reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        prefix.bar_daily_membership,
        extended.bar_daily_membership.query("date <= @prefix_end").reset_index(drop=True),
    )


def test_open_terminal_bar_is_checkpoint_only():
    dates = ["2024-01-01", "2024-01-02"]
    daily = _daily_frame(["2024-01-02"], [40.0])
    ix = _ix_frame(dates, [1_000.0, 1_000.0])
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=2,
        min_market_amount_observations=1,
        min_completed_bars=1,
    )
    tables = DollarBarBuilder(config).transform(
        daily,
        ix,
        _calendar(dates),
        _manual_calibration("2024-01-02"),
        role="CALIBRATION_HISTORY",
    )

    assert tables.dollar_bars.empty
    assert tables.bar_daily_membership.empty
    assert tables.open_bar_checkpoints.iloc[0]["bar_status"] == "OPEN_PROVISIONAL"
    assert tables.open_bar_checkpoints.iloc[0]["accumulated_amount"] == 40.0


def test_q_calibration_is_train_only_and_future_append_invariant():
    dates = pd.bdate_range("2023-12-27", "2024-01-19")
    ix = _ix_frame([str(value.date()) for value in dates], [1_000.0] * len(dates))
    research_dates = [str(value.date()) for value in dates[3:]]
    first_amounts = [80.0 if index % 2 == 0 else 120.0 for index in range(len(research_dates))]
    second_amounts = [70.0 if index % 2 == 0 else 130.0 for index in range(len(research_dates))]
    first = _daily_frame(research_dates, first_amounts)
    second = _daily_frame(
        research_dates, second_amounts, etf_id="low_volatility"
    )
    daily = pd.concat([first, second], ignore_index=True)
    boundaries = AFMLBoundaries("2024-01-01", "2024-01-10", "2024-01-15", "2024-01-19")
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=3,
        min_market_amount_observations=2,
        candidate_quantile_min=0.25,
        candidate_quantile_max=0.75,
        candidate_quantile_count=3,
        min_completed_bars=1,
        max_bar_duration_trading_days=10,
    )
    calibrator = DollarBarCalibrator(config)
    original = calibrator.fit(
        daily, ix, boundaries, ("momentum", "low_volatility")
    )
    future_changed = daily.copy()
    future_changed.loc[future_changed["date"].gt(pd.Timestamp("2024-01-10")), "etf_amount"] = 1e12
    extended = calibrator.fit(
        future_changed, ix, boundaries, ("momentum", "low_volatility")
    )

    assert original.q_star == extended.q_star
    pd.testing.assert_frame_equal(original.candidate_evidence, extended.candidate_evidence)
    assert original.calibration_scope == "TEST_ONLY_SUBSET"


def test_q_calibration_fails_when_no_candidate_meets_bar_count():
    daily, ix, _, _ = three_bar_fixture()
    boundaries = AFMLBoundaries("2024-01-02", "2024-01-04", "2024-01-05", "2024-01-09")
    config = replace(
        AFMLConfig().dollar_bar,
        market_amount_lookback_days=2,
        min_market_amount_observations=1,
        min_completed_bars=99,
    )

    with pytest.raises(DollarBarContractError, match="bar_threshold_not_calibrated"):
        DollarBarCalibrator(config).fit(daily, ix, boundaries, ("momentum",))
