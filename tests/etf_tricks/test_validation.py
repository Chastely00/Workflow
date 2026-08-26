from __future__ import annotations

import pandas as pd
import pytest

from etf_tricks.calendar import TradingCalendar
from etf_tricks.result import ETFTrickResult
from etf_tricks.validation import build_selection_diagnostics, validate_result


DATES = pd.to_datetime(["2025-01-02", "2025-01-03"])


def _calendar() -> TradingCalendar:
    return TradingCalendar(
        pd.DataFrame({"date": DATES, "market": "TWSE", "is_trading_day": True})
    )


def _valid_result() -> ETFTrickResult:
    daily = pd.DataFrame(
        {
            "date": DATES,
            "etf_id": "momentum",
            "nav": [100.0, 100.0],
            "daily_return": [float("nan"), 0.0],
            "etf_amount": [0.0, 1_000.0],
            "total_assets": [1_000.0, 1_000.0],
            "cash": [100.0, 100.0],
            "target_completion_ratio": [0.5, 1.0],
            "total_cost": [0.0, 0.0],
            "missing_traded_value_count": [0, 0],
        }
    )
    holdings = pd.DataFrame(
        {
            "date": DATES,
            "etf_id": "momentum",
            "ticker": "1101",
            "shares": [9, 9],
            "market_value": [900.0, 900.0],
            "stale_price_days": [0, 0],
        }
    )
    targets = pd.DataFrame(
        {
            "formation_date": pd.Timestamp("2024-12-31"),
            "target_month": pd.Period("2025-01", freq="M"),
            "etf_id": "momentum",
            "ticker": ["1101", "1102", "1103", "1104", "1105"],
            "source_available_date": pd.Timestamp("2024-12-30"),
        }
    )
    candidates = pd.DataFrame(
        {
            "formation_date": pd.Timestamp("2024-12-31"),
            "etf_id": "momentum",
            "ticker": ["1101"],
            "liquidity_ratio_vs_ix0001_20d": [0.002],
            "r18_source_available_date": [pd.Timestamp("2024-12-30")],
        }
    )
    return ETFTrickResult(
        daily_etf=daily,
        daily_holdings=holdings,
        trades=pd.DataFrame(),
        monthly_targets=targets,
        candidate_audit=candidates,
        diagnostics=pd.DataFrame(),
        metadata={},
    )


def _codes(report) -> set[str]:
    return {issue.code for issue in report.hard_failures}


def test_valid_result_is_ready_and_has_explicit_availability_sections():
    report = validate_result(_valid_result(), _calendar(), ["momentum"])
    assert report.status == "READY"
    assert report.目前可用
    assert isinstance(report.目前缺失限制, tuple)
    assert report.per_etf.iloc[0]["rows"] == 2
    assert report.per_etf.iloc[0]["inception_date"] == DATES[0]
    assert report.per_etf.iloc[0]["candidate_shortage_count"] == 0


def test_missing_etf_and_post_inception_calendar_date_are_hard_failures():
    missing_etf = validate_result(_valid_result(), _calendar(), ["momentum", "roe"])
    assert "missing_etf" in _codes(missing_etf)

    result = _valid_result()
    result.daily_etf = result.daily_etf.iloc[:1].copy()
    missing_date = validate_result(result, _calendar(), ["momentum"])
    assert "missing_post_inception_date" in _codes(missing_date)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda r: setattr(r, "daily_etf", pd.concat([r.daily_etf, r.daily_etf.iloc[[0]]])), "duplicate_daily_key"),
        (lambda r: setattr(r, "daily_etf", r.daily_etf.assign(nav=[100.0, float("nan")])), "invalid_nav"),
        (lambda r: setattr(r, "daily_etf", r.daily_etf.assign(etf_amount=[0.0, float("nan")])), "invalid_etf_amount"),
        (lambda r: setattr(r, "daily_etf", r.daily_etf.assign(cash=[100.0, -1.0])), "negative_cash"),
        (lambda r: setattr(r, "daily_holdings", r.daily_holdings.assign(shares=[9, -1])), "negative_shares"),
        (lambda r: setattr(r, "daily_etf", r.daily_etf.assign(total_assets=[1_000.0, 999.0])), "broken_asset_reconciliation"),
        (lambda r: setattr(r, "monthly_targets", r.monthly_targets.assign(source_available_date=pd.Timestamp("2025-01-01"))), "pit_timing_violation"),
        (lambda r: setattr(r, "candidate_audit", r.candidate_audit.assign(r18_source_available_date=pd.Timestamp("2025-01-01"))), "pit_timing_violation"),
        (lambda r: setattr(r, "candidate_audit", r.candidate_audit.drop(columns="liquidity_ratio_vs_ix0001_20d")), "missing_ix0001_evidence"),
    ],
)
def test_each_accounting_and_pit_gate_fails_closed(mutation, expected: str):
    result = _valid_result()
    mutation(result)
    report = validate_result(result, _calendar(), ["momentum"])
    assert report.status == "NOT_READY"
    assert expected in _codes(report)


def test_operational_limitations_remain_warnings_not_hidden_failures():
    result = _valid_result()
    result.monthly_targets = result.monthly_targets.iloc[:3].copy()
    result.daily_holdings.loc[1, "stale_price_days"] = 2
    result.daily_etf.loc[1, "target_completion_ratio"] = 0.8
    result.daily_etf.loc[1, "missing_traded_value_count"] = 1
    result.trades = pd.DataFrame(
        {
            "date": [DATES[1]],
            "etf_id": ["momentum"],
            "ticker": ["1101"],
            "unfilled_shares": [2],
            "is_forced_delist_liquidation": [True],
        }
    )
    result.diagnostics = pd.DataFrame(
        {"diagnostic": ["zero_candidate_carry_forward"]}
    )

    report = validate_result(result, _calendar(), ["momentum"])
    warning_codes = {issue.code for issue in report.warnings}
    assert report.status == "READY"
    assert {
        "candidate_shortage",
        "stale_price",
        "backlog",
        "incomplete_transition",
        "forced_delisting",
        "missing_traded_amount",
        "zero_candidate_carry_forward",
        "snapshot_industry_classification",
        "synthetic_corporate_action_model",
    }.issubset(warning_codes)


def test_selection_diagnostics_persist_shortage_and_zero_candidate_carry():
    audit = pd.DataFrame(
        {
            "formation_date": [pd.Timestamp("2025-01-31")] * 3
            + [pd.Timestamp("2025-02-27")] * 2,
            "etf_id": "momentum",
            "ticker": ["1101", "1102", "1103", "1101", "1102"],
            "selected": [True, True, True, False, False],
        }
    )
    diagnostics = build_selection_diagnostics(audit)
    assert diagnostics["diagnostic"].tolist() == [
        "candidate_shortage",
        "zero_candidate_carry_forward",
    ]
    assert diagnostics["candidate_count"].tolist() == [3, 0]
