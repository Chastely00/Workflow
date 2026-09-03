from __future__ import annotations

from dataclasses import replace
import json

import pandas as pd
import pytest

from etf_tricks.registry import ETF_IDS
from etf_tricks.result import ETFTrickResult, _validate_lifecycle_payload, attach_etf_amount
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
    lifecycle = {
        "state_row_count": 0,
        "lifecycle_active_row_count": 0,
        "lifecycle_inactive_row_count": 0,
        "lifecycle_conflict_count": 0,
        "identity_conflict_count": 0,
        "formation_state_counts": {},
        "formation_exclusion_reason_counts": {},
    }
    diagnostics = pd.DataFrame(
        {
            "diagnostic": ["market_state_lifecycle_evidence"],
            "lifecycle_evidence_json": [
                json.dumps(
                    lifecycle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ],
        }
    )
    manifest_hashes = {
        artifact_id: "a" * 64
        for artifact_id in (
            "trading_calendar",
            "daily_price_volume",
            "daily_chip",
            "monthly_sales",
            "financial_statement_raw",
            "security_master",
            "daily_market_state",
        )
    }
    return ETFTrickResult(
        daily_etf=_daily() if daily is None else daily,
        daily_holdings=empty,
        trades=empty,
        monthly_targets=empty,
        candidate_audit=empty,
        diagnostics=diagnostics,
        metadata={
            "run_config": {
                "start_date": "2025-01-02",
                "end_date": "2025-01-03",
                "initial_capital": "10000000",
            },
            "spec_hash": "b" * 64,
            "manifest_hashes": manifest_hashes,
            "market_state_identity": {
                "artifact_id": "daily_market_state",
                "manifest_sha256": "a" * 64,
                "active_version": "market-state-v3",
                "classification_policy_version": "daily_market_state_v3",
                "state_lattice_policy_version": "daily_market_state_lattice_v5",
                "market_identity_policy_version": "daily_market_identity_v3",
                "dependency_certification_fingerprint": "b" * 64,
            },
            "market_state_config": {
                "scan_start_date": "2025-01-02",
                "scan_end_date": "2025-01-03",
                "formation_admission": "TRADING_ONLY",
                "execution_admission": "SAME_SESSION_TRADING_AND_EXCHANGE_TRADABLE",
                "amount_source": "PRIOR_SESSION_HOLDINGS_AUTHORITATIVE_TRADED_VALUE",
            },
            "lifecycle_diagnostics": lifecycle,
        },
    )


def _state(market: pd.DataFrame) -> pd.DataFrame:
    frame = market.rename(
        columns={"traded_value": "authoritative_traded_value"}
    ).copy()
    frame["market_state"] = "TRADING"
    frame["amount_state"] = "OBSERVED"
    frame["amount_zero_authorized"] = False
    frame["exchange_tradable"] = True
    return frame


def _master(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"ticker": tickers, "delist_date": pd.NaT})


def test_lifecycle_payload_retains_explicit_missing_lifecycle_conflicts():
    payload = {
        "state_row_count": 1,
        "lifecycle_active_row_count": 0,
        "lifecycle_inactive_row_count": 1,
        "lifecycle_conflict_count": 1,
        "identity_conflict_count": 0,
        "formation_state_counts": {},
        "formation_exclusion_reason_counts": {},
    }

    assert _validate_lifecycle_payload(payload) == payload


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

    calculated = attach_etf_amount(daily, holdings, _state(market), _master(["1101", "1102"]))
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

    calculated = attach_etf_amount(daily, holdings, _state(market), _master(["1101"]))
    assert calculated.iloc[1]["etf_amount"] == 0.0
    assert calculated.iloc[1]["missing_traded_value_count"] == 1
    assert bool(calculated.iloc[1]["has_data_quality_flag"]) is True


def test_market_state_amount_uses_prior_weights_and_lifecycle_states():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame([
        {"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.6},
        {"date": dates[0], "etf_id": "momentum", "ticker": "1102", "actual_weight": 0.4},
    ])
    state = pd.DataFrame([
        {"date": dates[1], "ticker": "1101", "market_state": "TRADING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": 100.0, "exchange_tradable": True},
        {"date": dates[1], "ticker": "1102", "market_state": "HALTED", "amount_state": "ZERO_AUTHORIZED", "amount_zero_authorized": True, "authoritative_traded_value": 0.0, "exchange_tradable": False},
    ])
    security_master = pd.DataFrame({"ticker": ["1101", "1102"], "delist_date": [pd.NaT, pd.NaT]})

    calculated = attach_etf_amount(daily, holdings, state, security_master)

    assert calculated["etf_amount"].tolist() == [0.0, 60.0]
    assert calculated["status_zero_authorized_count"].tolist() == [0, 1]
    assert calculated["missing_traded_value_count"].tolist() == [0, 0]
    assert calculated["status_missing_count"].tolist() == [0, 0]
    assert calculated["amount_quality_state"].tolist() == ["READY", "READY"]


def test_etf_amount_aligns_tables_without_iterating_daily_rows(monkeypatch):
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
            {
                "date": dates[0],
                "etf_id": "momentum",
                "ticker": "1101",
                "actual_weight": 0.75,
            }
        ]
    )
    market = pd.DataFrame(
        [
            {"date": dates[1], "ticker": "1101", "traded_value": 2_000.0},
        ]
    )

    def reject_row_iteration(*args, **kwargs):
        raise AssertionError("ETF amount must use relational alignment, not row iteration")

    monkeypatch.setattr(pd.DataFrame, "iterrows", reject_row_iteration)
    monkeypatch.setattr(pd.DataFrame, "itertuples", reject_row_iteration)

    calculated = attach_etf_amount(daily, holdings, _state(market), _master(["1101"]))

    assert calculated["etf_amount"].tolist() == [0.0, 1_500.0]
    assert calculated["missing_traded_value_count"].tolist() == [0, 0]


def test_observed_amount_preserves_prior_holding_sequential_sum_order():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame(
        [
            {"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.5},
            {"date": dates[0], "etf_id": "momentum", "ticker": "1102", "actual_weight": 0.25},
            {"date": dates[0], "etf_id": "momentum", "ticker": "1103", "actual_weight": 0.25},
        ]
    )
    state = _state(
        pd.DataFrame(
            [
                {"date": dates[1], "ticker": "1101", "traded_value": 2e16},
                {"date": dates[1], "ticker": "1102", "traded_value": 4.0},
                {"date": dates[1], "ticker": "1103", "traded_value": 4.0},
            ]
        )
    )

    calculated = attach_etf_amount(
        daily, holdings, state, _master(["1101", "1102", "1103"])
    )

    expected = 0.0
    for value in (1e16, 1.0, 1.0):
        expected += value
    assert calculated.iloc[1]["etf_amount"] == expected


def test_halted_observed_amount_contributes_even_though_not_executable():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame(
        [{"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.8}]
    )
    state = pd.DataFrame(
        [{
            "date": dates[1], "ticker": "1101", "market_state": "HALTED",
            "amount_state": "OBSERVED", "amount_zero_authorized": False,
            "authoritative_traded_value": 125.0, "exchange_tradable": False,
        }]
    )

    calculated = attach_etf_amount(daily, holdings, state, _master(["1101"]))

    assert calculated["etf_amount"].tolist() == [0.0, 100.0]
    assert calculated["status_missing_count"].tolist() == [0, 0]
    assert calculated["status_zero_authorized_count"].tolist() == [0, 0]


def test_missing_state_counts_both_missing_audits_and_blocks_amount_quality():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame(
        [{"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.8}]
    )
    state = pd.DataFrame(
        [{
            "date": dates[1], "ticker": "1101", "market_state": "MISSING",
            "amount_state": "MISSING", "amount_zero_authorized": False,
            "authoritative_traded_value": None, "exchange_tradable": None,
        }]
    )

    calculated = attach_etf_amount(daily, holdings, state, _master(["1101"]))

    assert calculated["etf_amount"].tolist() == [0.0, 0.0]
    assert calculated["missing_traded_value_count"].tolist() == [0, 1]
    assert calculated["status_missing_count"].tolist() == [0, 1]
    assert calculated["status_zero_authorized_count"].tolist() == [0, 0]
    assert calculated["amount_quality_state"].tolist() == ["READY", "MISSING"]


def test_delisted_prior_holding_is_excluded_before_state_join_without_renormalizing():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame(
        [
            {"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 0.6},
            {"date": dates[0], "etf_id": "momentum", "ticker": "1102", "actual_weight": 0.4},
        ]
    )
    state = _state(
        pd.DataFrame(
            [{"date": dates[1], "ticker": "1102", "traded_value": 100.0}]
        )
    )
    master = pd.DataFrame(
        {"ticker": ["1101", "1102"], "delist_date": [dates[1], pd.NaT]}
    )

    calculated = attach_etf_amount(daily, holdings, state, master)

    assert calculated["etf_amount"].tolist() == [0.0, 40.0]
    assert calculated["status_missing_count"].tolist() == [0, 0]
    assert calculated["status_zero_authorized_count"].tolist() == [0, 0]


@pytest.mark.parametrize(
    "updates",
    [
        {"market_state": "TRADING", "amount_state": "ZERO_AUTHORIZED", "amount_zero_authorized": True, "authoritative_traded_value": 0.0, "exchange_tradable": True},
        {"market_state": "HALTED", "amount_state": "MISSING", "amount_zero_authorized": False, "authoritative_traded_value": None, "exchange_tradable": False},
        {"market_state": "MISSING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": 1.0, "exchange_tradable": None},
        {"market_state": "TRADING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": 1.0, "exchange_tradable": False},
        {"market_state": "HALTED", "amount_state": "ZERO_AUTHORIZED", "amount_zero_authorized": "true", "authoritative_traded_value": 0.0, "exchange_tradable": False},
        {"market_state": "TRADING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": "1.0", "exchange_tradable": True},
        {"market_state": "TRADING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": float("inf"), "exchange_tradable": True},
        {"market_state": "TRADING", "amount_state": "OBSERVED", "amount_zero_authorized": False, "authoritative_traded_value": True, "exchange_tradable": True},
    ],
)
def test_market_state_cross_field_and_dtype_violations_fail_closed(updates):
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    state = pd.DataFrame([{"date": dates[1], "ticker": "1101", **updates}])

    with pytest.raises(ValueError, match="cross-field|dtype"):
        attach_etf_amount(
            pd.DataFrame({"date": dates, "etf_id": "momentum"}),
            pd.DataFrame([{"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 1.0}]),
            state,
            _master(["1101"]),
        )


def test_market_state_and_security_master_duplicate_keys_fail_closed():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
    daily = pd.DataFrame({"date": dates, "etf_id": "momentum"})
    holdings = pd.DataFrame([{"date": dates[0], "etf_id": "momentum", "ticker": "1101", "actual_weight": 1.0}])
    state = _state(pd.DataFrame([{"date": dates[1], "ticker": "1101", "traded_value": 1.0}]))

    with pytest.raises(ValueError, match="duplicate"):
        attach_etf_amount(daily, holdings, pd.concat([state, state]), _master(["1101"]))
    with pytest.raises(ValueError, match="duplicate"):
        attach_etf_amount(daily, holdings, state, pd.concat([_master(["1101"]), _master(["1101"])]))


def test_notebook_facade_binds_one_explicit_data_analysts_root(tmp_path):
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    assert lab.gateway.data_analysts_root == tmp_path.resolve()


def test_result_artifacts_round_trip_with_hashes_and_row_counts(tmp_path):
    result = _result()
    handle = result.write(tmp_path / "run")
    assert len(handle.manifest_sha256) == 64
    assert "result_manifest_sha256" not in handle.manifest
    assert set(handle["tables"]) == {
        "daily_etf",
        "daily_holdings",
        "trades",
        "monthly_targets",
        "candidate_audit",
        "diagnostics",
    }
    assert handle["tables"]["daily_etf"]["rows"] == 26
    assert len(handle["tables"]["daily_etf"]["sha256"]) == 64
    restored = ETFTrickResult.read(
        tmp_path / "run",
        expected_handle=handle,
    )
    pd.testing.assert_frame_equal(restored.daily_etf, result.daily_etf)
    assert restored.metadata["spec_hash"] == "b" * 64
    assert restored.result_manifest_sha256 == handle.manifest_sha256
    with pytest.raises(TypeError):
        ETFTrickResult.read(tmp_path / "run")


def test_result_artifacts_accept_certified_generation_rotation_and_bind_source_identity(
    tmp_path, monkeypatch,
):
    result = _result()
    result.metadata["market_state_identity"].update(
        {
            "active_version": "market-state-v4",
            "state_lattice_policy_version": "daily_market_state_lattice_v6",
            "market_identity_policy_version": "daily_market_identity_v4",
            "dependency_certification_fingerprint": "c" * 64,
        }
    )

    handle = result.write(tmp_path / "rotated")
    assert len(handle.market_state_identity_sha256) == 64
    restored = ETFTrickResult.read(tmp_path / "rotated", expected_handle=handle)
    assert restored.metadata["market_state_identity"] == result.metadata[
        "market_state_identity"
    ]

    wrong_source = replace(handle, market_state_identity_sha256="d" * 64)
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda *args, **kwargs: pytest.fail(
            "wrong source identity must fail before table reads"
        ),
    )
    with pytest.raises(ValueError, match="market-state identity authority mismatch"):
        ETFTrickResult.read(tmp_path / "rotated", expected_handle=wrong_source)
