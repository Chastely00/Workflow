from __future__ import annotations

import pandas as pd
import pytest

from etf_tricks.registry import ETF_IDS
from etf_tricks.result import ETFTrickResult, attach_etf_amount
from etf_tricks.lab import ETFTrickLab


def _daily() -> pd.DataFrame:
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    return pd.DataFrame(
        [
            {
                "date": date,
                "etf_id": etf_id,
                "nav": 100.0 + day,
                "daily_return": float("nan") if day == 0 else 0.01,
                "etf_amount": float(day * 1_000),
            }
            for etf_id in ETF_IDS
            for day, date in enumerate(dates)
        ]
    )


def _result(daily: pd.DataFrame | None = None) -> ETFTrickResult:
    empty = pd.DataFrame()
    return ETFTrickResult(
        daily_etf=_daily() if daily is None else daily,
        daily_holdings=empty,
        trades=empty,
        monthly_targets=empty,
        candidate_audit=empty,
        diagnostics=empty,
        metadata={"spec_hash": "abc", "manifest_hashes": {"daily_price_volume": "x"}},
    )


def test_notebook_views_have_exactly_13_stably_ordered_columns():
    result = _result()
    assert result.nav.columns.tolist() == list(ETF_IDS)
    assert result.returns.columns.tolist() == list(ETF_IDS)
    assert result.amount.columns.tolist() == list(ETF_IDS)
    assert result.nav.index.is_monotonic_increasing


def test_for_ffd_is_thin_unique_and_does_not_compute_ffd():
    result = _result()
    frame = result.for_ffd("momentum")
    assert frame.columns.tolist() == [
        "date",
        "etf_id",
        "nav",
        "daily_return",
        "etf_amount",
    ]
    assert frame["etf_id"].unique().tolist() == ["momentum"]
    assert frame["date"].is_monotonic_increasing
    with pytest.raises(KeyError, match="unknown ETF"):
        result.for_ffd("not_an_etf")


def test_duplicate_or_nonpositive_nav_fails_closed():
    duplicate = pd.concat([_daily(), _daily().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        _result(duplicate)
    invalid = _daily()
    invalid.loc[0, "nav"] = 0.0
    with pytest.raises(ValueError, match="nav"):
        _result(invalid)


def test_etf_amount_uses_previous_close_actual_weights_not_current_weights():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame(
        {
            "date": dates,
            "etf_id": "momentum",
            "nav": [100.0, 101.0],
            "daily_return": [float("nan"), 0.01],
            "has_data_quality_flag": False,
        }
    )
    holdings = pd.DataFrame(
        [
            {"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.6},
            {"date": dates[0], "etf_id": "momentum", "ticker": "1102", "actual_weight": 0.3},
            {"date": dates[1], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.1},
            {"date": dates[1], "etf_id": "momentum", "ticker": "1102", "actual_weight": 0.8},
        ]
    )
    market = pd.DataFrame(
        [
            {"date": dates[0], "ticker": "1101", "traded_value": 900.0},
            {"date": dates[0], "ticker": "1102", "traded_value": 900.0},
            {"date": dates[1], "ticker": "1101", "traded_value": 1_000.0},
            {"date": dates[1], "ticker": "1102", "traded_value": 2_000.0},
        ]
    )

    calculated = attach_etf_amount(daily, holdings, market)
    assert calculated["etf_amount"].tolist() == pytest.approx([0.0, 1_200.0])
    assert calculated["missing_traded_value_count"].tolist() == [0, 0]
    assert calculated["has_data_quality_flag"].tolist() == [False, False]


def test_missing_stock_amount_contributes_zero_and_sets_quality_flag():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame(
        {
            "date": dates,
            "etf_id": "momentum",
            "nav": [100.0, 100.0],
            "daily_return": [float("nan"), 0.0],
            "has_data_quality_flag": False,
        }
    )
    holdings = pd.DataFrame(
        [{"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.8}]
    )
    market = pd.DataFrame(
        [{"date": dates[0], "ticker": "1101", "traded_value": 100.0}]
    )

    calculated = attach_etf_amount(daily, holdings, market)
    assert calculated.iloc[1]["etf_amount"] == 0.0
    assert calculated.iloc[1]["missing_traded_value_count"] == 1
    assert bool(calculated.iloc[1]["has_data_quality_flag"]) is True


def test_notebook_facade_binds_one_explicit_data_analysts_root(tmp_path):
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    assert lab.gateway.data_analysts_root == tmp_path.resolve()


def test_result_artifacts_round_trip_with_hashes_and_row_counts(tmp_path):
    result = _result()
    manifest = result.write(tmp_path / "run")
    assert set(manifest["tables"]) == {
        "daily_etf",
        "daily_holdings",
        "trades",
        "monthly_targets",
        "candidate_audit",
        "diagnostics",
    }
    assert manifest["tables"]["daily_etf"]["rows"] == 26
    assert len(manifest["tables"]["daily_etf"]["sha256"]) == 64
    restored = ETFTrickResult.read(tmp_path / "run")
    pd.testing.assert_frame_equal(restored.daily_etf, result.daily_etf)
    assert restored.metadata["spec_hash"] == "abc"
