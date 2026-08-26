from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .registry import ETF_IDS


def attach_etf_amount(
    daily_etf: pd.DataFrame,
    daily_holdings: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    daily = daily_etf.copy()
    required_daily = {"date", "etf_id"}
    required_holdings = {"date", "etf_id", "ticker", "actual_weight"}
    required_market = {"date", "ticker", "traded_value"}
    for name, frame, required in (
        ("daily_etf", daily, required_daily),
        ("daily_holdings", daily_holdings, required_holdings),
        ("market", market, required_market),
    ):
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"{name} missing columns: {missing}")

    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    holdings = daily_holdings.copy()
    holdings["date"] = pd.to_datetime(holdings["date"], errors="coerce")
    holdings["ticker"] = holdings["ticker"].astype(str)
    amounts = market.copy()
    amounts["date"] = pd.to_datetime(amounts["date"], errors="coerce")
    amounts["ticker"] = amounts["ticker"].astype(str)
    if daily.duplicated(["date", "etf_id"]).any():
        raise ValueError("daily_etf contains duplicate date-etf_id keys")
    if holdings.duplicated(["date", "etf_id", "ticker"]).any():
        raise ValueError("daily_holdings contains duplicate keys")
    if amounts.duplicated(["date", "ticker"]).any():
        raise ValueError("market contains duplicate date-ticker keys")

    amount_lookup = amounts.set_index(["date", "ticker"])["traded_value"]
    holdings_lookup = {
        key: group for key, group in holdings.groupby(["date", "etf_id"], sort=False)
    }
    output_amount: dict[int, float] = {}
    missing_counts: dict[int, int] = {}
    for etf_id, group in daily.groupby("etf_id", sort=False):
        ordered = group.sort_values("date", kind="stable")
        previous_date: pd.Timestamp | None = None
        for index, row in ordered.iterrows():
            total = 0.0
            missing_count = 0
            if previous_date is not None:
                previous = holdings_lookup.get((previous_date, etf_id), pd.DataFrame())
                for holding in previous.itertuples(index=False):
                    weight = float(holding.actual_weight)
                    if not np.isfinite(weight) or weight < 0:
                        raise ValueError("daily_holdings actual_weight must be finite and non-negative")
                    try:
                        value = float(amount_lookup.loc[(row.date, str(holding.ticker))])
                    except KeyError:
                        value = float("nan")
                    if not np.isfinite(value) or value < 0:
                        missing_count += 1
                    else:
                        total += value * weight
            output_amount[index] = total
            missing_counts[index] = missing_count
            previous_date = row.date

    daily["etf_amount"] = pd.Series(output_amount)
    daily["missing_traded_value_count"] = pd.Series(missing_counts, dtype="int64")
    if "has_data_quality_flag" not in daily.columns:
        daily["has_data_quality_flag"] = False
    daily["has_data_quality_flag"] = (
        daily["has_data_quality_flag"].fillna(False).astype(bool)
        | daily["missing_traded_value_count"].gt(0)
    )
    return daily.sort_values(["date", "etf_id"], kind="stable").reset_index(drop=True)


@dataclass
class ETFTrickResult:
    daily_etf: pd.DataFrame
    daily_holdings: pd.DataFrame
    trades: pd.DataFrame
    monthly_targets: pd.DataFrame
    candidate_audit: pd.DataFrame
    diagnostics: pd.DataFrame
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        required = {"date", "etf_id", "nav", "daily_return", "etf_amount"}
        missing = sorted(required.difference(self.daily_etf.columns))
        if missing:
            raise ValueError(f"daily_etf missing columns: {missing}")
        frame = self.daily_etf.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["etf_id"] = frame["etf_id"].astype(str)
        if frame[["date", "etf_id"]].isna().any().any():
            raise ValueError("daily_etf contains invalid date or etf_id")
        if frame.duplicated(["date", "etf_id"]).any():
            raise ValueError("daily_etf contains duplicate date-etf_id keys")
        nav = pd.to_numeric(frame["nav"], errors="coerce")
        if (~np.isfinite(nav) | nav.le(0)).any():
            raise ValueError("daily_etf nav must be finite and positive")
        frame["nav"] = nav
        self.daily_etf = frame.sort_values(["date", "etf_id"], kind="stable").reset_index(drop=True)

    @property
    def daily(self) -> pd.DataFrame:
        return self.daily_etf

    @property
    def holdings(self) -> pd.DataFrame:
        return self.daily_holdings

    @property
    def targets(self) -> pd.DataFrame:
        return self.monthly_targets

    @property
    def candidates(self) -> pd.DataFrame:
        return self.candidate_audit

    @property
    def nav(self) -> pd.DataFrame:
        return self._wide("nav")

    @property
    def returns(self) -> pd.DataFrame:
        return self._wide("daily_return")

    @property
    def amount(self) -> pd.DataFrame:
        return self._wide("etf_amount")

    def for_ffd(self, etf_id: str) -> pd.DataFrame:
        if etf_id not in ETF_IDS:
            raise KeyError(f"unknown ETF ID: {etf_id}")
        columns = ["date", "etf_id", "nav", "daily_return", "etf_amount"]
        return (
            self.daily_etf[self.daily_etf["etf_id"].eq(etf_id)]
            .loc[:, columns]
            .sort_values("date", kind="stable")
            .reset_index(drop=True)
        )

    def _wide(self, value: str) -> pd.DataFrame:
        wide = self.daily_etf.pivot(index="date", columns="etf_id", values=value)
        columns = [etf_id for etf_id in ETF_IDS if etf_id in wide.columns]
        result = wide.reindex(columns=columns).sort_index()
        result.columns.name = None
        return result
