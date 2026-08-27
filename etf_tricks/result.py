from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .registry import ETF_IDS


def _sequential_float_sum(values: pd.Series) -> float:
    total = 0.0
    for value in values:
        total += float(value)
    return total


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

    if daily.empty:
        daily["etf_amount"] = pd.Series(dtype="float64")
        daily["missing_traded_value_count"] = pd.Series(dtype="int64")
    else:
        daily["_result_row"] = np.arange(len(daily), dtype=np.int64)
        pairs = daily.loc[:, ["_result_row", "date", "etf_id"]].sort_values(
            ["etf_id", "date"], kind="stable"
        )
        pairs["holding_date"] = pairs.groupby("etf_id", sort=False)["date"].shift()
        previous_holdings = holdings.loc[
            :, ["date", "etf_id", "ticker", "actual_weight"]
        ].rename(columns={"date": "holding_date"})
        aligned = pairs.merge(
            previous_holdings,
            on=["holding_date", "etf_id"],
            how="left",
            sort=False,
            validate="one_to_many",
        )
        aligned = aligned.merge(
            amounts.loc[:, ["date", "ticker", "traded_value"]],
            on=["date", "ticker"],
            how="left",
            sort=False,
            validate="many_to_one",
        )

        has_holding = aligned["ticker"].notna()
        weights = pd.to_numeric(aligned["actual_weight"], errors="coerce")
        invalid_weight = has_holding & (~np.isfinite(weights) | weights.lt(0))
        if invalid_weight.any():
            raise ValueError(
                "daily_holdings actual_weight must be finite and non-negative"
            )
        traded_values = pd.to_numeric(aligned["traded_value"], errors="coerce")
        missing_amount = has_holding & (
            ~np.isfinite(traded_values) | traded_values.lt(0)
        )
        aligned["_missing_amount"] = missing_amount.astype("int64")
        aligned["_amount_contribution"] = np.where(
            has_holding & ~missing_amount,
            traded_values * weights,
            0.0,
        )

        grouped = aligned.groupby("_result_row", sort=False)
        amount_by_row = grouped["_amount_contribution"].agg(
            _sequential_float_sum
        )
        missing_by_row = grouped["_missing_amount"].sum()
        daily["etf_amount"] = daily["_result_row"].map(amount_by_row)
        daily["missing_traded_value_count"] = (
            daily["_result_row"].map(missing_by_row).astype("int64")
        )
        daily = daily.drop(columns="_result_row")
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

    def write(self, output_dir: str | Path) -> dict[str, Any]:
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        tables = {
            "daily_etf": self.daily_etf,
            "daily_holdings": self.daily_holdings,
            "trades": self.trades,
            "monthly_targets": self.monthly_targets,
            "candidate_audit": self.candidate_audit,
            "diagnostics": self.diagnostics,
        }
        table_manifest: dict[str, dict[str, Any]] = {}
        for name, frame in tables.items():
            final_path = output / f"{name}.parquet"
            temporary_path = output / f".{name}.tmp.parquet"
            frame.to_parquet(temporary_path, index=False)
            temporary_path.replace(final_path)
            table_manifest[name] = {
                "path": final_path.name,
                "rows": len(frame),
                "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
            }
        manifest = {
            "format_version": 1,
            "metadata": self.metadata,
            "tables": table_manifest,
        }
        manifest_path = output / "result_manifest.json"
        temporary_manifest = output / ".result_manifest.tmp.json"
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        temporary_manifest.replace(manifest_path)
        return manifest

    @classmethod
    def read(cls, output_dir: str | Path) -> "ETFTrickResult":
        output = Path(output_dir).resolve()
        manifest_path = output / "result_manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"missing ETF Trick result manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != 1:
            raise ValueError("unsupported ETF Trick result format version")
        frames: dict[str, pd.DataFrame] = {}
        for name in (
            "daily_etf",
            "daily_holdings",
            "trades",
            "monthly_targets",
            "candidate_audit",
            "diagnostics",
        ):
            entry = manifest.get("tables", {}).get(name)
            if not isinstance(entry, dict) or "path" not in entry:
                raise ValueError(f"result manifest missing table: {name}")
            path = (output / str(entry["path"])).resolve()
            if not path.is_relative_to(output) or not path.is_file():
                raise ValueError(f"invalid or missing result table path: {name}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry.get("sha256"):
                raise ValueError(f"result table hash mismatch: {name}")
            frame = pd.read_parquet(path)
            if len(frame) != entry.get("rows"):
                raise ValueError(f"result table row-count mismatch: {name}")
            frames[name] = frame
        return cls(
            daily_etf=frames["daily_etf"],
            daily_holdings=frames["daily_holdings"],
            trades=frames["trades"],
            monthly_targets=frames["monthly_targets"],
            candidate_audit=frames["candidate_audit"],
            diagnostics=frames["diagnostics"],
            metadata=manifest.get("metadata", {}),
        )

    def _wide(self, value: str) -> pd.DataFrame:
        wide = self.daily_etf.pivot(index="date", columns="etf_id", values=value)
        columns = [etf_id for etf_id in ETF_IDS if etf_id in wide.columns]
        result = wide.reindex(columns=columns).sort_index()
        result.columns.name = None
        return result
