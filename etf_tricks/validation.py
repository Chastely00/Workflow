from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .result import ETFTrickResult


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    etf_id: str | None = None


@dataclass(frozen=True)
class ReadinessReport:
    status: str
    hard_failures: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    per_etf: pd.DataFrame
    目前可用: tuple[str, ...]
    目前缺失限制: tuple[str, ...]


def validate_result(
    result: ETFTrickResult,
    calendar: TradingCalendar,
    expected_etf_ids: Iterable[str],
) -> ReadinessReport:
    expected = tuple(expected_etf_ids)
    hard: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = [
        ValidationIssue(
            "snapshot_industry_classification",
            "security_master industry is a current snapshot, not full PIT history",
        ),
        ValidationIssue(
            "synthetic_corporate_action_model",
            "corporate actions use the approved synthetic total-return model",
        ),
    ]
    daily = result.daily_etf.copy()
    holdings = result.daily_holdings.copy()

    if not {"date", "etf_id"}.issubset(daily.columns):
        hard.append(ValidationIssue("missing_daily_schema", "daily_etf lacks date or etf_id"))
        return _report(expected, daily, holdings, hard, warnings)
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    present = set(daily["etf_id"].astype(str))
    for etf_id in expected:
        if etf_id not in present:
            hard.append(ValidationIssue("missing_etf", f"missing ETF curve: {etf_id}", etf_id))

    if daily.duplicated(["date", "etf_id"]).any():
        hard.append(ValidationIssue("duplicate_daily_key", "daily_etf has duplicate date-etf_id keys"))
    if "nav" not in daily.columns:
        hard.append(ValidationIssue("invalid_nav", "daily_etf lacks nav"))
    else:
        nav = pd.to_numeric(daily["nav"], errors="coerce")
        if (~np.isfinite(nav) | nav.le(0)).any():
            hard.append(ValidationIssue("invalid_nav", "NAV must be finite and positive"))
    if "cash" not in daily.columns or (
        pd.to_numeric(daily.get("cash"), errors="coerce") < 0
    ).any():
        hard.append(ValidationIssue("negative_cash", "cash is missing or negative"))
    if "shares" not in holdings.columns or (
        pd.to_numeric(holdings.get("shares"), errors="coerce") < 0
    ).any():
        hard.append(ValidationIssue("negative_shares", "shares are missing or negative"))

    calendar_days = pd.DatetimeIndex(calendar.days)
    for etf_id in expected:
        etf_days = pd.DatetimeIndex(
            daily.loc[daily["etf_id"].eq(etf_id), "date"].dropna().unique()
        ).sort_values()
        if etf_days.empty:
            continue
        required = calendar_days[(calendar_days >= etf_days.min())]
        missing = required.difference(etf_days)
        if len(missing):
            hard.append(
                ValidationIssue(
                    "missing_post_inception_date",
                    f"{etf_id} missing {len(missing)} calendar dates after inception",
                    etf_id,
                )
            )

    if {"date", "etf_id", "market_value"}.issubset(holdings.columns) and {
        "date", "etf_id", "total_assets", "cash"
    }.issubset(daily.columns):
        values = (
            holdings.groupby(["date", "etf_id"], as_index=False)["market_value"]
            .sum()
            .rename(columns={"market_value": "holdings_market_value"})
        )
        reconciliation = daily.merge(values, on=["date", "etf_id"], how="left")
        reconciliation["holdings_market_value"] = reconciliation["holdings_market_value"].fillna(0.0)
        difference = (
            pd.to_numeric(reconciliation["total_assets"], errors="coerce")
            - pd.to_numeric(reconciliation["cash"], errors="coerce")
            - pd.to_numeric(reconciliation["holdings_market_value"], errors="coerce")
        )
        if (~np.isfinite(difference) | difference.abs().gt(1e-6)).any():
            hard.append(
                ValidationIssue(
                    "broken_asset_reconciliation",
                    "total_assets does not equal cash plus holdings market value",
                )
            )
    else:
        hard.append(
            ValidationIssue("broken_asset_reconciliation", "required reconciliation fields are missing")
        )

    targets = result.monthly_targets
    if {"formation_date", "source_available_date"}.issubset(targets.columns):
        formation = pd.to_datetime(targets["formation_date"], errors="coerce")
        available = pd.to_datetime(targets["source_available_date"], errors="coerce")
        if (available.notna() & formation.notna() & available.gt(formation)).any():
            hard.append(
                ValidationIssue("pit_timing_violation", "source availability exceeds formation date")
            )

    candidates = result.candidate_audit
    if (
        "liquidity_ratio_vs_ix0001_20d" not in candidates.columns
        or candidates.empty
        or not set(expected).issubset(set(candidates.get("etf_id", pd.Series(dtype=str))))
    ):
        hard.append(
            ValidationIssue("missing_ix0001_evidence", "candidate audit lacks IX0001 liquidity evidence")
        )

    if {"target_month", "etf_id", "ticker"}.issubset(targets.columns):
        counts = targets.groupby(["target_month", "etf_id"])["ticker"].nunique()
        for (_, etf_id), count in counts[counts < 5].items():
            warnings.append(
                ValidationIssue("candidate_shortage", f"selected only {count} candidates", str(etf_id))
            )
    if "stale_price_days" in holdings.columns and (
        pd.to_numeric(holdings["stale_price_days"], errors="coerce") > 0
    ).any():
        warnings.append(ValidationIssue("stale_price", "one or more holdings use stale valuation"))
    trades = result.trades
    if "unfilled_shares" in trades.columns and (
        pd.to_numeric(trades["unfilled_shares"], errors="coerce").fillna(0).abs() > 0
    ).any():
        warnings.append(ValidationIssue("backlog", "one or more scheduled orders remain unfilled"))
    if "target_completion_ratio" in daily.columns:
        month_end_rows = (
            daily.assign(month=daily["date"].dt.to_period("M"))
            .sort_values("date", kind="stable")
            .groupby(["etf_id", "month"], as_index=False)
            .tail(1)
        )
        if (pd.to_numeric(month_end_rows["target_completion_ratio"], errors="coerce") < 1).any():
            warnings.append(
                ValidationIssue("incomplete_transition", "month-end target completion is below 100%")
            )
    if "is_forced_delist_liquidation" in trades.columns and trades[
        "is_forced_delist_liquidation"
    ].fillna(False).astype(bool).any():
        warnings.append(ValidationIssue("forced_delisting", "forced delisting liquidation occurred"))
    if "missing_traded_value_count" in daily.columns and (
        pd.to_numeric(daily["missing_traded_value_count"], errors="coerce") > 0
    ).any():
        warnings.append(
            ValidationIssue("missing_traded_amount", "missing stock traded value contributed zero")
        )
    diagnostics = result.diagnostics
    if "diagnostic" in diagnostics.columns and diagnostics["diagnostic"].eq(
        "zero_candidate_carry_forward"
    ).any():
        warnings.append(
            ValidationIssue("zero_candidate_carry_forward", "prior holdings were carried forward")
        )

    return _report(expected, daily, holdings, hard, warnings)


def _report(
    expected: tuple[str, ...],
    daily: pd.DataFrame,
    holdings: pd.DataFrame,
    hard: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> ReadinessReport:
    summaries = []
    for etf_id in expected:
        group = daily[daily.get("etf_id", pd.Series(dtype=str)).eq(etf_id)]
        holding_group = holdings[
            holdings.get("etf_id", pd.Series(dtype=str)).eq(etf_id)
        ]
        summaries.append(
            {
                "etf_id": etf_id,
                "inception_date": pd.NaT if group.empty else pd.to_datetime(group["date"]).min(),
                "rows": len(group),
                "final_nav": np.nan if group.empty or "nav" not in group else group.sort_values("date").iloc[-1]["nav"],
                "maximum_stale_days": 0 if holding_group.empty or "stale_price_days" not in holding_group else pd.to_numeric(holding_group["stale_price_days"], errors="coerce").max(),
                "total_cost": np.nan if group.empty or "total_cost" not in group else pd.to_numeric(group["total_cost"], errors="coerce").sum(),
            }
        )
    status = "NOT_READY" if hard else "READY"
    usable = (
        "Daily NAV and ETF amount tables passed all hard readiness gates",
    ) if not hard else ()
    limitations = tuple(issue.message for issue in [*hard, *warnings])
    return ReadinessReport(
        status=status,
        hard_failures=tuple(hard),
        warnings=tuple(warnings),
        per_etf=pd.DataFrame(summaries),
        目前可用=usable,
        目前缺失限制=limitations,
    )
