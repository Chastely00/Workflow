from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .result import ETFTrickResult


def build_selection_diagnostics(candidate_audit: pd.DataFrame) -> pd.DataFrame:
    required = {"formation_date", "etf_id", "selected"}
    missing = sorted(required.difference(candidate_audit.columns))
    if missing:
        raise ValueError(f"candidate_audit missing diagnostic columns: {missing}")
    if candidate_audit.empty:
        return pd.DataFrame(
            columns=["date", "formation_date", "etf_id", "diagnostic", "candidate_count"]
        )
    frame = candidate_audit.copy()
    frame["formation_date"] = pd.to_datetime(frame["formation_date"], errors="coerce")
    frame["selected"] = frame["selected"].fillna(False).astype(bool)
    counts = (
        frame.groupby(["formation_date", "etf_id"], as_index=False)["selected"]
        .sum()
        .rename(columns={"selected": "candidate_count"})
    )
    counts["diagnostic"] = np.where(
        counts["candidate_count"].eq(0),
        "zero_candidate_carry_forward",
        np.where(counts["candidate_count"].lt(5), "candidate_shortage", ""),
    )
    counts = counts[counts["diagnostic"].ne("")].copy()
    counts.insert(0, "date", counts["formation_date"])
    return counts.reset_index(drop=True)


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
    if "date" in holdings.columns:
        holdings["date"] = pd.to_datetime(holdings["date"], errors="coerce")

    if not {"date", "etf_id"}.issubset(daily.columns):
        hard.append(ValidationIssue("missing_daily_schema", "daily_etf lacks date or etf_id"))
        return _report(
            expected, daily, holdings, result.diagnostics, result.trades, hard, warnings
        )
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

    run_config = result.metadata.get("run_config", {}) if isinstance(result.metadata, dict) else {}
    try:
        initial_capital = float(run_config["initial_capital"])
    except (KeyError, TypeError, ValueError):
        initial_capital = float("nan")
    if not np.isfinite(initial_capital) or initial_capital <= 0:
        hard.append(
            ValidationIssue(
                "broken_nav_reconciliation",
                "metadata.run_config.initial_capital must be finite and positive",
            )
        )
    elif {"nav", "total_assets"}.issubset(daily.columns):
        expected_nav = (
            pd.to_numeric(daily["total_assets"], errors="coerce")
            / initial_capital
            * 100.0
        )
        observed_nav = pd.to_numeric(daily["nav"], errors="coerce")
        if (
            ~np.isfinite(expected_nav)
            | ~np.isfinite(observed_nav)
            | ~np.isclose(observed_nav, expected_nav, rtol=1e-10, atol=1e-8)
        ).any():
            hard.append(
                ValidationIssue(
                    "broken_nav_reconciliation",
                    "NAV does not equal 100 times total assets divided by initial capital",
                )
            )
    else:
        hard.append(
            ValidationIssue(
                "broken_nav_reconciliation", "NAV reconciliation fields are missing"
            )
        )

    if "daily_return" not in daily.columns:
        hard.append(
            ValidationIssue("broken_return_reconciliation", "daily_return is missing")
        )
    else:
        broken_return = False
        for _, group in daily.groupby("etf_id", sort=False):
            ordered = group.sort_values("date", kind="stable")
            observed = pd.to_numeric(ordered["daily_return"], errors="coerce")
            expected_return = pd.to_numeric(ordered["nav"], errors="coerce").pct_change(
                fill_method=None
            )
            if not pd.isna(observed.iloc[0]):
                broken_return = True
                break
            comparable = observed.iloc[1:].notna() & expected_return.iloc[1:].notna()
            if (
                (~comparable).any()
                or not np.isclose(
                    observed.iloc[1:][comparable],
                    expected_return.iloc[1:][comparable],
                    rtol=1e-10,
                    atol=1e-12,
                ).all()
            ):
                broken_return = True
                break
        if broken_return:
            hard.append(
                ValidationIssue(
                    "broken_return_reconciliation",
                    "daily_return does not equal the ETF NAV percentage change",
                )
            )
    if "etf_amount" not in daily.columns:
        hard.append(ValidationIssue("invalid_etf_amount", "daily_etf lacks etf_amount"))
    else:
        etf_amount = pd.to_numeric(daily["etf_amount"], errors="coerce")
        if (~np.isfinite(etf_amount) | etf_amount.lt(0)).any():
            hard.append(
                ValidationIssue(
                    "invalid_etf_amount", "ETF amount must be finite and non-negative"
                )
            )
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

    holding_fields = {
        "date", "etf_id", "ticker", "shares", "raw_close", "market_value", "actual_weight"
    }
    if holding_fields.issubset(holdings.columns):
        if holdings.duplicated(["date", "etf_id", "ticker"]).any():
            hard.append(
                ValidationIssue("broken_holding_reconciliation", "daily_holdings has duplicate keys")
            )
        holding_value = (
            pd.to_numeric(holdings["shares"], errors="coerce")
            * pd.to_numeric(holdings["raw_close"], errors="coerce")
        )
        recorded_value = pd.to_numeric(holdings["market_value"], errors="coerce")
        asset_lookup = daily.drop_duplicates(["date", "etf_id"], keep="last").set_index(
            ["date", "etf_id"]
        )["total_assets"]
        row_assets = pd.Series(
            [asset_lookup.get((row.date, row.etf_id), np.nan) for row in holdings.itertuples()],
            index=holdings.index,
            dtype="float64",
        )
        expected_weight = recorded_value / row_assets
        recorded_weight = pd.to_numeric(holdings["actual_weight"], errors="coerce")
        if (
            ~np.isfinite(holding_value)
            | ~np.isclose(holding_value, recorded_value, rtol=1e-10, atol=1e-6)
            | ~np.isfinite(expected_weight)
            | ~np.isclose(expected_weight, recorded_weight, rtol=1e-10, atol=1e-10)
        ).any():
            hard.append(
                ValidationIssue(
                    "broken_holding_reconciliation",
                    "holding value or actual weight does not reconcile to shares, raw close, and assets",
                )
            )
    else:
        hard.append(
            ValidationIssue(
                "broken_holding_reconciliation", "daily_holdings reconciliation fields are missing"
            )
        )

    trades = result.trades.copy()
    if "date" in trades.columns:
        trades["date"] = pd.to_datetime(trades["date"], errors="coerce")
    trade_fields = {"date", "etf_id", "executed_shares", "raw_close", "notional", "commission", "tax"}
    if not trades.empty and trade_fields.issubset(trades.columns):
        executed = pd.to_numeric(trades["executed_shares"], errors="coerce").abs()
        raw_close = pd.to_numeric(trades["raw_close"], errors="coerce")
        trade_notional = pd.Series(
            np.where(executed.eq(0), 0.0, executed * raw_close), index=trades.index
        )
        recorded_notional = pd.to_numeric(trades["notional"], errors="coerce")
        if (
            ~np.isfinite(trade_notional)
            | ~np.isclose(trade_notional, recorded_notional, rtol=1e-10, atol=1e-6)
        ).any():
            hard.append(
                ValidationIssue(
                    "broken_trade_reconciliation",
                    "trade notional does not reconcile to executed shares and raw close",
                )
            )

    daily_cost_fields = {"date", "etf_id", "commission", "tax", "total_cost"}
    if daily_cost_fields.issubset(daily.columns):
        recorded_commission = pd.to_numeric(daily["commission"], errors="coerce")
        recorded_tax = pd.to_numeric(daily["tax"], errors="coerce")
        recorded_total = pd.to_numeric(daily["total_cost"], errors="coerce")
        if trades.empty:
            grouped_costs = pd.DataFrame(columns=["date", "etf_id", "trade_commission", "trade_tax"])
        elif trade_fields.issubset(trades.columns):
            grouped_costs = (
                trades.groupby(["date", "etf_id"], as_index=False)[["commission", "tax"]]
                .sum()
                .rename(columns={"commission": "trade_commission", "tax": "trade_tax"})
            )
        else:
            grouped_costs = None
        if grouped_costs is None:
            hard.append(ValidationIssue("broken_cost_reconciliation", "trade cost fields are missing"))
        else:
            cost_check = daily[["date", "etf_id"]].merge(
                grouped_costs, on=["date", "etf_id"], how="left"
            ).fillna({"trade_commission": 0.0, "trade_tax": 0.0})
            trade_commission = pd.to_numeric(
                cost_check["trade_commission"], errors="coerce"
            )
            trade_tax = pd.to_numeric(cost_check["trade_tax"], errors="coerce")
            if (
                ~np.isclose(recorded_total, recorded_commission + recorded_tax, rtol=0, atol=1e-8)
                | ~np.isclose(recorded_commission, trade_commission, rtol=0, atol=1e-8)
                | ~np.isclose(recorded_tax, trade_tax, rtol=0, atol=1e-8)
            ).any():
                hard.append(
                    ValidationIssue(
                        "broken_cost_reconciliation",
                        "daily costs do not reconcile to trade commissions and taxes",
                    )
                )
    else:
        hard.append(
            ValidationIssue("broken_cost_reconciliation", "daily cost fields are missing")
        )

    ledger_fields = {
        "date", "etf_id", "ticker", "shares",
        "synthetic_ca_share_delta", "synthetic_ca_cash",
    }
    trade_ledger_fields = {
        "date", "etf_id", "ticker", "executed_shares", "notional", "commission", "tax",
        "synthetic_ca_share_delta", "synthetic_ca_cash",
    }
    if ledger_fields.issubset(holdings.columns) and (
        trades.empty or trade_ledger_fields.issubset(trades.columns)
    ):
        share_broken = False
        cash_broken = False
        holding_groups = {
            (str(etf_id), pd.Timestamp(date)): group
            for (etf_id, date), group in holdings.groupby(
                ["etf_id", "date"], sort=False
            )
        }
        trade_groups = (
            {
                (str(etf_id), pd.Timestamp(date)): group
                for (etf_id, date), group in trades.groupby(
                    ["etf_id", "date"], sort=False
                )
            }
            if not trades.empty
            else {}
        )
        evidence_columns = [
            "etf_id", "date", "ticker", "synthetic_ca_share_delta", "synthetic_ca_cash"
        ]
        evidence = pd.concat(
            [
                holdings[evidence_columns],
                trades[evidence_columns] if not trades.empty else holdings.iloc[:0][evidence_columns],
            ],
            ignore_index=True,
        )
        evidence["ticker"] = evidence["ticker"].astype(str)
        evidence["synthetic_ca_share_delta"] = pd.to_numeric(
            evidence["synthetic_ca_share_delta"], errors="coerce"
        )
        evidence["synthetic_ca_cash"] = pd.to_numeric(
            evidence["synthetic_ca_cash"], errors="coerce"
        )
        evidence_keys = ["etf_id", "date", "ticker"]
        evidence_conflicts = evidence.groupby(evidence_keys, sort=False)[
            ["synthetic_ca_share_delta", "synthetic_ca_cash"]
        ].nunique(dropna=True)
        if evidence_conflicts.gt(1).any(axis=None):
            share_broken = True
        evidence_first = evidence.groupby(evidence_keys, sort=False)[
            ["synthetic_ca_share_delta", "synthetic_ca_cash"]
        ].first()
        evidence_lookup = {
            (str(etf_id), pd.Timestamp(date), str(ticker)): (
                0 if pd.isna(row.synthetic_ca_share_delta) else int(row.synthetic_ca_share_delta),
                0.0 if pd.isna(row.synthetic_ca_cash) else float(row.synthetic_ca_cash),
            )
            for (etf_id, date, ticker), row in evidence_first.iterrows()
        }
        for etf_id, daily_group in daily.groupby("etf_id", sort=False):
            previous_shares: dict[str, int] = {}
            previous_cash = initial_capital
            for daily_row in daily_group.sort_values("date", kind="stable").itertuples():
                date = daily_row.date
                holding_day = holding_groups.get(
                    (str(etf_id), pd.Timestamp(date)), holdings.iloc[:0]
                )
                trade_day = trade_groups.get(
                    (str(etf_id), pd.Timestamp(date)), trades.iloc[:0]
                )
                current_shares = {
                    str(row.ticker): int(row.shares)
                    for row in holding_day.itertuples(index=False)
                }
                executed = (
                    trade_day.groupby("ticker")["executed_shares"].sum().to_dict()
                    if not trade_day.empty
                    else {}
                )
                tickers = set(previous_shares) | set(current_shares) | {
                    str(ticker) for ticker in executed
                }
                ca_cash = 0.0
                for ticker in tickers:
                    share_delta, ticker_ca_cash = evidence_lookup.get(
                        (str(etf_id), pd.Timestamp(date), ticker), (0, 0.0)
                    )
                    ca_cash += ticker_ca_cash
                    expected_shares = (
                        previous_shares.get(ticker, 0)
                        + share_delta
                        + int(executed.get(ticker, 0))
                    )
                    if expected_shares != current_shares.get(ticker, 0):
                        share_broken = True
                        break
                trade_cash_effect = 0.0
                if not trade_day.empty:
                    executed_values = pd.to_numeric(
                        trade_day["executed_shares"], errors="coerce"
                    )
                    notionals = pd.to_numeric(trade_day["notional"], errors="coerce")
                    fees = pd.to_numeric(trade_day["commission"], errors="coerce") + pd.to_numeric(
                        trade_day["tax"], errors="coerce"
                    )
                    trade_cash_effect = float(
                        np.where(executed_values.lt(0), notionals, -notionals).sum()
                        - fees.sum()
                    )
                expected_cash = previous_cash + ca_cash + trade_cash_effect
                if not np.isclose(expected_cash, float(daily_row.cash), rtol=0, atol=1e-6):
                    cash_broken = True
                previous_shares = current_shares
                previous_cash = float(daily_row.cash)
        if share_broken:
            hard.append(
                ValidationIssue(
                    "broken_share_ledger",
                    "daily shares do not reconcile to prior shares, corporate actions, and trades",
                )
            )
        if cash_broken:
            hard.append(
                ValidationIssue(
                    "broken_cash_ledger",
                    "daily cash does not reconcile to prior cash, corporate actions, and trades",
                )
            )
    else:
        hard.append(
            ValidationIssue(
                "broken_share_ledger", "share or trade ledger fields are missing"
            )
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
    if "formation_date" in candidates.columns:
        candidate_formation = pd.to_datetime(
            candidates["formation_date"], errors="coerce"
        )
        candidate_pit_violation = False
        for column in (
            "r18_source_available_date",
            "r103_source_available_date",
            "r103_revision_date",
        ):
            if column not in candidates.columns:
                continue
            available = pd.to_datetime(candidates[column], errors="coerce")
            candidate_pit_violation |= bool(
                (available.notna() & candidate_formation.notna() & available.gt(candidate_formation)).any()
            )
        if candidate_pit_violation:
            hard.append(
                ValidationIssue(
                    "pit_timing_violation",
                    "candidate source availability exceeds formation date",
                )
            )
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

    return _report(
        expected, daily, holdings, result.diagnostics, result.trades, hard, warnings
    )


def _report(
    expected: tuple[str, ...],
    daily: pd.DataFrame,
    holdings: pd.DataFrame,
    diagnostics: pd.DataFrame,
    trades: pd.DataFrame,
    hard: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> ReadinessReport:
    summaries = []
    for etf_id in expected:
        group = daily[daily.get("etf_id", pd.Series(dtype=str)).eq(etf_id)]
        holding_group = holdings[
            holdings.get("etf_id", pd.Series(dtype=str)).eq(etf_id)
        ]
        diagnostic_group = (
            diagnostics[diagnostics["etf_id"].eq(etf_id)]
            if {"etf_id", "diagnostic"}.issubset(diagnostics.columns)
            else pd.DataFrame()
        )
        trade_group = (
            trades[trades["etf_id"].eq(etf_id)]
            if "etf_id" in trades.columns
            else pd.DataFrame()
        )
        if not group.empty and "target_completion_ratio" in group.columns:
            month_end = (
                group.assign(month=pd.to_datetime(group["date"]).dt.to_period("M"))
                .sort_values("date", kind="stable")
                .groupby("month", as_index=False)
                .tail(1)
            )
            incomplete_count = int(
                (pd.to_numeric(month_end["target_completion_ratio"], errors="coerce") < 1).sum()
            )
        else:
            incomplete_count = 0
        summaries.append(
            {
                "etf_id": etf_id,
                "inception_date": pd.NaT if group.empty else pd.to_datetime(group["date"]).min(),
                "rows": len(group),
                "final_nav": np.nan if group.empty or "nav" not in group else group.sort_values("date").iloc[-1]["nav"],
                "maximum_stale_days": 0 if holding_group.empty or "stale_price_days" not in holding_group else pd.to_numeric(holding_group["stale_price_days"], errors="coerce").max(),
                "candidate_shortage_count": 0 if diagnostic_group.empty else int(diagnostic_group["diagnostic"].eq("candidate_shortage").sum()),
                "zero_candidate_carry_count": 0 if diagnostic_group.empty else int(diagnostic_group["diagnostic"].eq("zero_candidate_carry_forward").sum()),
                "incomplete_transition_count": incomplete_count,
                "forced_delisting_count": 0 if trade_group.empty or "is_forced_delist_liquidation" not in trade_group else int(trade_group["is_forced_delist_liquidation"].fillna(False).astype(bool).sum()),
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
