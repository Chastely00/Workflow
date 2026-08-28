from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from etf_tricks import ETFTrickResult
from etf_tricks.afml import AFMLConfig, AFMLFeatureEngine, FeatureConfig


@dataclass
class FeatureFixture:
    config: AFMLConfig
    bars: pd.DataFrame
    memberships: pd.DataFrame
    ffd: pd.DataFrame
    structural_etf: pd.DataFrame
    structural_ix: pd.DataFrame
    base: ETFTrickResult

    def inputs(self, **updates):
        values = {
            "bars": self.bars,
            "memberships": self.memberships,
            "ffd": self.ffd,
            "structural_etf": self.structural_etf,
            "structural_ix": self.structural_ix,
            "base": self.base,
        }
        values.update(updates)
        return tuple(values[name] for name in values)

    @property
    def features(self):
        return AFMLFeatureEngine(self.config).build(*self.inputs())


@pytest.fixture
def feature_fixture() -> FeatureFixture:
    size = 75
    dates = pd.bdate_range("2024-01-02", periods=size)
    available = dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18)
    bar_id = np.arange(size)
    close = 100.0 * np.exp(np.linspace(0.0, 0.12, size))
    amount = 1_000.0 + 17.0 * bar_id
    bars = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": bar_id,
            "bar_status": "FINALIZED",
            "bar_end_date": dates,
            "close_nav": close,
            "previous_close_nav": np.r_[np.nan, close[:-1]],
            "log_return": np.r_[np.nan, np.diff(np.log(close))],
            "bar_amount": amount,
            "overshoot_ratio": 0.05 + bar_id / 10_000,
            "trading_day_count": 1 + (bar_id % 3),
            "close_path_high_nav": close * 1.01,
            "close_path_low_nav": close * 0.99,
            "etf_market_share": 0.001 + bar_id / 1_000_000,
            "feature_available_at": available,
            "bar_available_at": available,
            "calibration_version": "q-v1",
            "config_version": "bar-v1",
            "source_revision_status": "PIT_REVISION_UNVERIFIED",
            "source_quality_flag": False,
        }
    )
    memberships = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": bar_id,
            "date": dates,
            "nav": close,
            "etf_amount": amount,
            "member_available_at": available,
        }
    )
    ffd = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": bar_id,
            "ffd_level": np.sin(bar_id / 8.0) + bar_id / 100.0,
            "selected_d": 0.37,
            "ffd_width": 8,
            "calibration_version": "ffd-v1",
            "config_version": "ffd-config-v1",
        }
    )
    structural_etf = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": bar_id,
            "feature_available_at": available,
            "sadf": bar_id / 100.0,
            "qadf": bar_id / 110.0,
            "qadf_dispersion": 0.2,
            "cadf": bar_id / 120.0,
            "cadf_dispersion": 0.1,
            "sadf_cadf_z": 1.0,
            "adf_window_count": bar_id + 1,
            "structural_quality_reason": None,
        }
    )
    ix_close = 20_000.0 * np.exp(np.linspace(0.0, 0.08, size))
    structural_ix = pd.DataFrame(
        {
            "date": dates,
            "feature_available_at": available,
            "structural_source_value": np.log(ix_close),
            "sadf": bar_id / 200.0,
            "qadf": bar_id / 210.0,
            "qadf_dispersion": 0.3,
            "cadf": bar_id / 220.0,
            "cadf_dispersion": 0.15,
            "sadf_cadf_z": 0.5,
            "adf_window_count": bar_id + 1,
            "structural_quality_reason": None,
        }
    )
    daily_etf = pd.DataFrame(
        {
            "date": dates,
            "etf_id": "momentum",
            "nav": close,
            "daily_return": np.r_[np.nan, np.diff(close) / close[:-1]],
            "etf_amount": amount,
            "cash_weight": 0.02,
            "invested_weight": 0.98,
            "holdings_count": 2,
            "target_completion_ratio": 1.0,
        }
    )
    daily_holdings = pd.DataFrame(
        [
            {
                "date": date,
                "etf_id": "momentum",
                "ticker": ticker,
                "actual_weight": weight,
            }
            for date in dates
            for ticker, weight in (("1101", 0.60), ("2330", 0.38))
        ]
    )
    monthly_targets = pd.DataFrame(
        [
            {"target_month": month, "etf_id": "momentum", "ticker": ticker, "target_weight": weight}
            for month, holdings in (
                ("2023-12", (("1101", 0.5), ("2330", 0.5))),
                ("2024-01", (("1101", 0.6), ("2330", 0.4))),
                ("2024-02", (("1101", 0.5), ("2603", 0.5))),
            )
            for ticker, weight in holdings
        ]
    )
    base = ETFTrickResult(
        daily_etf=daily_etf,
        daily_holdings=daily_holdings,
        trades=pd.DataFrame(),
        monthly_targets=monthly_targets,
        candidate_audit=pd.DataFrame(),
        diagnostics=pd.DataFrame(),
        metadata={"spec_hash": "feature-fixture"},
    )
    config = AFMLConfig(
        features=FeatureConfig(
            ffd_ma_window=5,
            ffd_vol_windows=(3, 5),
            shape_window=5,
            min_shape_obs=3,
            amount_window=20,
            efficiency_window=5,
            market_vol_windows=(3, 5),
            beta_window=5,
        )
    )
    return FeatureFixture(
        config, bars, memberships, ffd, structural_etf, structural_ix, base
    )


def test_bar_amount_is_raw_and_ratio_excludes_current_bar(feature_fixture):
    features = feature_fixture.features
    row = features.query("bar_id == 21").iloc[0]
    history = feature_fixture.bars.query("1 <= bar_id <= 20")["bar_amount"]

    assert row["bar_amount"] == feature_fixture.bars.query("bar_id == 21").iloc[0][
        "bar_amount"
    ]
    assert row["amount_ratio_20"] == pytest.approx(row["bar_amount"] / history.mean())


def test_future_market_row_does_not_enter_backward_asof_join(feature_fixture):
    future_ix = feature_fixture.structural_ix.assign(
        feature_available_at=lambda x: x["feature_available_at"]
        + pd.Timedelta(days=400)
    )

    result = AFMLFeatureEngine(feature_fixture.config).build(
        *feature_fixture.inputs(structural_ix=future_ix)
    )

    assert result["ix_sadf"].isna().all()
    assert result["ix_alignment_reason"].eq("NO_PRIOR_MARKET_OBSERVATION").all()


def test_unavailable_microstructure_names_are_absent(feature_fixture):
    assert not {"vpin", "kyle_lambda", "atr", "adx", "vix"}.intersection(
        feature_fixture.features.columns
    )


def test_portfolio_state_and_ix_are_pit_aligned(feature_fixture):
    result = feature_fixture.features
    row = result.iloc[-1]

    assert row["portfolio_hhi"] == pytest.approx(0.60**2 + 0.38**2)
    assert row["cash_weight"] == 0.02
    assert row["invested_weight"] == 0.98
    assert row["holdings_count"] == 2
    assert row["target_completion_ratio"] == 1.0
    assert row["ix_feature_available_at"] <= row["feature_available_at"]
    assert row["ix_staleness_trading_days"] == 0


def test_feature_prefix_is_unchanged_by_future_append(feature_fixture):
    prefix_size = 50
    inputs = feature_fixture.inputs()
    prefix_inputs = (
        inputs[0].iloc[:prefix_size],
        inputs[1].iloc[:prefix_size],
        inputs[2].iloc[:prefix_size],
        inputs[3].iloc[:prefix_size],
        inputs[4].iloc[:prefix_size],
        ETFTrickResult(
            daily_etf=feature_fixture.base.daily_etf.iloc[:prefix_size],
            daily_holdings=feature_fixture.base.daily_holdings[
                feature_fixture.base.daily_holdings["date"].le(
                    feature_fixture.bars.iloc[prefix_size - 1]["bar_end_date"]
                )
            ],
            trades=pd.DataFrame(),
            monthly_targets=feature_fixture.base.monthly_targets,
            candidate_audit=pd.DataFrame(),
            diagnostics=pd.DataFrame(),
            metadata=feature_fixture.base.metadata,
        ),
    )

    prefix = AFMLFeatureEngine(feature_fixture.config).build(*prefix_inputs)
    extended = feature_fixture.features.iloc[:prefix_size]

    pd.testing.assert_frame_equal(
        prefix.reset_index(drop=True), extended.reset_index(drop=True)
    )


def test_feature_keys_and_input_contract_fail_closed(feature_fixture):
    duplicate = pd.concat(
        [feature_fixture.bars, feature_fixture.bars.iloc[[0]]], ignore_index=True
    )
    with pytest.raises(ValueError, match="duplicate"):
        AFMLFeatureEngine(feature_fixture.config).build(
            *feature_fixture.inputs(bars=duplicate)
        )
