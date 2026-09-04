from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Tier1RoeFeatureExtensionConfig:
    statement_no: str = "TTM"
    merged: str = "Y"
    currency: str = "NTD"


class Tier1RoeFeatureExtensionBuilder:
    """Build a PIT-safe constituent-weighted R103/ROE feature sidecar."""

    _AFTER_CLOSE = pd.Timedelta(hours=18)

    def __init__(self, config: Tier1RoeFeatureExtensionConfig | None = None) -> None:
        self.config = config or Tier1RoeFeatureExtensionConfig()

    def build(
        self,
        bars: pd.DataFrame,
        holdings: pd.DataFrame,
        roe: pd.DataFrame,
    ) -> pd.DataFrame:
        bar_frame = self._prepare_bars(bars)
        holding_frame = self._prepare_holdings(holdings)
        roe_frame = self._prepare_roe(roe)
        bar_holdings = bar_frame[["etf_id", "bar_id", "bar_end_date", "feature_available_at"]].merge(
            holding_frame,
            left_on=["etf_id", "bar_end_date"],
            right_on=["etf_id", "date"],
            how="left",
            validate="one_to_many",
        )
        matched = self._asof_roe(bar_holdings, roe_frame)
        summary = self._weighted_summary(matched)
        result = bar_frame.merge(
            summary,
            on=["etf_id", "bar_id"],
            how="left",
            validate="one_to_one",
        )
        result["roe_revision_status"] = "PIT_REVISION_UNVERIFIED"
        result["roe_availability_assumption"] = "AFTER_CLOSE_DATE_ONLY"
        return result[
            [
                "etf_id", "bar_id", "feature_available_at",
                "roe_weighted_r103", "roe_coverage_count", "roe_holding_count",
                "roe_source_available_date", "roe_revision_status",
                "roe_availability_assumption",
            ]
        ].sort_values(["etf_id", "bar_id"], kind="stable").reset_index(drop=True)

    @staticmethod
    def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
        required = {"etf_id", "bar_id", "bar_status", "bar_end_date", "feature_available_at"}
        if missing := required.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        frame = bars.loc[bars["bar_status"].eq("FINALIZED"), sorted(required)].copy()
        frame["bar_end_date"] = pd.to_datetime(frame["bar_end_date"], errors="coerce").dt.normalize()
        frame["feature_available_at"] = pd.to_datetime(frame["feature_available_at"], errors="coerce", utc=True).astype("datetime64[ns, UTC]")
        if frame.duplicated(["etf_id", "bar_id"]).any() or frame[["bar_end_date", "feature_available_at"]].isna().any().any():
            raise ValueError("bars requires unique finalized keys and valid PIT times")
        return frame

    @staticmethod
    def _prepare_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "etf_id", "ticker", "actual_weight"}
        if missing := required.difference(holdings.columns):
            raise ValueError(f"holdings missing columns: {sorted(missing)}")
        frame = holdings.loc[:, sorted(required)].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame["ticker"] = frame["ticker"].astype(str).str.strip()
        frame["actual_weight"] = pd.to_numeric(frame["actual_weight"], errors="coerce")
        if frame[["date", "etf_id", "ticker", "actual_weight"]].isna().any().any() or (frame["ticker"] == "").any() or frame.duplicated(["date", "etf_id", "ticker"]).any():
            raise ValueError("holdings requires valid unique date-etf-ticker rows")
        return frame

    def _prepare_roe(self, roe: pd.DataFrame) -> pd.DataFrame:
        required = {"ticker", "no", "merg", "curr", "source_available_date", "revision_date", "r103", "r103_conflict"}
        if missing := required.difference(roe.columns):
            raise ValueError(f"roe snapshot missing columns: {sorted(missing)}")
        frame = roe.loc[:, sorted(required)].copy()
        frame["ticker"] = frame["ticker"].astype(str).str.strip()
        for column in ("source_available_date", "revision_date"):
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()
        frame["r103"] = pd.to_numeric(frame["r103"], errors="coerce")
        frame["r103_conflict"] = frame["r103_conflict"].astype(bool)
        if frame[["ticker", "source_available_date", "revision_date"]].isna().any().any() or (frame["ticker"] == "").any():
            raise ValueError("roe snapshot requires valid ticker and PIT dates")
        # A date-only announcement becomes usable at the conservative same-day
        # after-close cutoff.  Conflicting source identities never enter as-of.
        frame = frame.loc[
            frame["no"].eq(self.config.statement_no)
            & frame["merg"].eq(self.config.merged)
            & frame["curr"].eq(self.config.currency)
            & ~frame["r103_conflict"]
            & frame["r103"].notna()
        ].copy()
        frame["roe_available_at"] = (
            frame["source_available_date"].dt.tz_localize("Asia/Taipei")
            .add(self._AFTER_CLOSE).dt.tz_convert("UTC")
        ).astype("datetime64[ns, UTC]")
        return frame.sort_values(["ticker", "roe_available_at", "revision_date"], kind="stable")

    @staticmethod
    def _asof_roe(holdings: pd.DataFrame, roe: pd.DataFrame) -> pd.DataFrame:
        frame = holdings.copy()
        frame["_holding_key"] = np.arange(len(frame))
        pieces: list[pd.DataFrame] = []
        for ticker, holding_group in frame.groupby("ticker", sort=False):
            candidates = roe.loc[roe["ticker"].eq(ticker)]
            if candidates.empty:
                pieces.append(
                    holding_group.assign(
                        r103=np.nan,
                        source_available_date=pd.NaT,
                    )
                )
                continue
            left = holding_group.sort_values("feature_available_at", kind="stable")
            right = candidates.sort_values("roe_available_at", kind="stable")
            pieces.append(
                pd.merge_asof(
                    left,
                    right[["roe_available_at", "r103", "source_available_date"]],
                    left_on="feature_available_at",
                    right_on="roe_available_at",
                    direction="backward",
                    allow_exact_matches=True,
                )
            )
        return pd.concat(pieces, ignore_index=True).sort_values("_holding_key", kind="stable")

    @staticmethod
    def _weighted_summary(matched: pd.DataFrame) -> pd.DataFrame:
        matched["weighted_r103"] = matched["actual_weight"] * matched["r103"]
        summary = matched.groupby(["etf_id", "bar_id"], as_index=False).agg(
            roe_holding_count=("ticker", "size"),
            roe_coverage_count=("r103", "count"),
            roe_weighted_r103=("weighted_r103", "sum"),
            roe_source_available_date=("source_available_date", "max"),
        )
        summary.loc[summary["roe_holding_count"].ne(summary["roe_coverage_count"]), "roe_weighted_r103"] = np.nan
        return summary
