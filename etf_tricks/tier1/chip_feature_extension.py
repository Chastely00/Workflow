from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Tier1ChipFeatureExtensionConfig:
    window: int = 20

    def __post_init__(self) -> None:
        if self.window <= 1:
            raise ValueError("chip feature window must exceed one session")


class Tier1ChipFeatureExtensionBuilder:
    """Build a read-only, constituent-weighted daily-chip sidecar for Tier 1."""

    def __init__(self, config: Tier1ChipFeatureExtensionConfig | None = None) -> None:
        self.config = config or Tier1ChipFeatureExtensionConfig()

    def build(
        self,
        bars: pd.DataFrame,
        holdings: pd.DataFrame,
        chip: pd.DataFrame,
    ) -> pd.DataFrame:
        bar_frame = self._prepare_bars(bars)
        holding_frame = self._prepare_holdings(holdings)
        chip_frame = self._prepare_chip(chip)
        daily = self._daily_weighted_flow(holding_frame, chip_frame)
        daily = daily.sort_values(["etf_id", "date"], kind="stable")
        grouped = daily.groupby("etf_id", sort=False)["chip_net_flow"]
        daily[f"chip_net_flow_{self.config.window}"] = daily["chip_net_flow"]
        mean = grouped.transform(
            lambda values: values.rolling(self.config.window, min_periods=self.config.window).mean()
        )
        std = grouped.transform(
            lambda values: values.rolling(self.config.window, min_periods=self.config.window).std(ddof=1)
        )
        daily[f"chip_net_flow_z_{self.config.window}"] = (
            (daily["chip_net_flow"] - mean) / std.replace(0.0, np.nan)
        )
        daily["chip_observation_date"] = daily["date"]
        daily["chip_revision_status"] = "PIT_REVISION_UNVERIFIED"
        daily["chip_availability_assumption"] = "AFTER_CLOSE_DATE_ONLY"
        extension = bar_frame.merge(
            daily[
                [
                    "etf_id",
                    "date",
                    f"chip_net_flow_{self.config.window}",
                    f"chip_net_flow_z_{self.config.window}",
                    "chip_observation_date",
                    "chip_source_available_date",
                    "chip_revision_status",
                    "chip_availability_assumption",
                ]
            ],
            left_on=["etf_id", "bar_end_date"],
            right_on=["etf_id", "date"],
            how="left",
            validate="one_to_one",
        )
        source_available_at = (
            extension["chip_source_available_date"]
            .dt.tz_localize("Asia/Taipei")
            .add(pd.Timedelta(hours=18))
            .dt.tz_convert("UTC")
        )
        too_early = source_available_at.notna() & extension["feature_available_at"].lt(
            source_available_at
        )
        if too_early.any():
            raise ValueError("chip feature availability must follow the after-close cutoff")
        return extension[
            [
                "etf_id",
                "bar_id",
                "feature_available_at",
                f"chip_net_flow_{self.config.window}",
                    f"chip_net_flow_z_{self.config.window}",
                    "chip_observation_date",
                    "chip_source_available_date",
                    "chip_revision_status",
                "chip_availability_assumption",
            ]
        ].sort_values(["etf_id", "bar_id"], kind="stable").reset_index(drop=True)

    @staticmethod
    def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
        required = {"etf_id", "bar_id", "bar_status", "bar_end_date", "feature_available_at"}
        if missing := required.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        frame = bars.loc[bars["bar_status"].eq("FINALIZED")].copy()
        if frame.duplicated(["etf_id", "bar_id"]).any():
            raise ValueError("bars has duplicate etf_id-bar_id keys")
        frame["bar_end_date"] = pd.to_datetime(frame["bar_end_date"], errors="coerce").dt.normalize()
        frame["feature_available_at"] = pd.to_datetime(
            frame["feature_available_at"], errors="coerce", utc=True
        ).astype("datetime64[ns, UTC]")
        if frame[["bar_end_date", "feature_available_at"]].isna().any().any():
            raise ValueError("bars requires valid end dates and availability")
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
        if frame[["date", "etf_id", "ticker", "actual_weight"]].isna().any().any() or (frame["ticker"] == "").any():
            raise ValueError("holdings requires valid date, etf_id, ticker, and actual_weight")
        if frame.duplicated(["date", "etf_id", "ticker"]).any():
            raise ValueError("holdings has duplicate date-etf_id-ticker keys")
        return frame

    @staticmethod
    def _prepare_chip(chip: pd.DataFrame) -> pd.DataFrame:
        fields = ("qfii_examt", "fund_examt", "dlrp_examt")
        required = {"date", "ticker", "source_available_date", *fields}
        if missing := required.difference(chip.columns):
            raise ValueError(f"chip missing columns: {sorted(missing)}")
        frame = chip.loc[:, sorted(required)].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame["source_available_date"] = pd.to_datetime(
            frame["source_available_date"], errors="coerce"
        ).dt.normalize()
        frame["ticker"] = frame["ticker"].astype(str).str.strip()
        for field in fields:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if frame[["date", "ticker", "source_available_date"]].isna().any().any() or (frame["ticker"] == "").any():
            raise ValueError("chip requires valid date, ticker, and source_available_date")
        if (frame["source_available_date"] > frame["date"]).any():
            raise ValueError("chip source availability cannot follow its observation date")
        if frame.duplicated(["date", "ticker"]).any():
            raise ValueError("chip has duplicate date-ticker keys")
        frame["chip_component_flow"] = frame.loc[:, fields].sum(axis=1, min_count=len(fields))
        return frame

    @staticmethod
    def _daily_weighted_flow(holdings: pd.DataFrame, chip: pd.DataFrame) -> pd.DataFrame:
        merged = holdings.merge(
            chip[
                [
                    "date",
                    "ticker",
                    "chip_component_flow",
                    "source_available_date",
                ]
            ],
            on=["date", "ticker"],
            how="left",
            validate="many_to_one",
        )
        merged["weighted_chip_flow"] = merged["actual_weight"] * merged["chip_component_flow"]
        summary = merged.groupby(["etf_id", "date"], as_index=False).agg(
            holding_count=("ticker", "size"),
            available_count=("chip_component_flow", "count"),
            chip_net_flow=("weighted_chip_flow", "sum"),
            chip_source_available_date=("source_available_date", "max"),
        )
        summary.loc[
            summary["holding_count"].ne(summary["available_count"]), "chip_net_flow"
        ] = np.nan
        return summary[
            ["etf_id", "date", "chip_net_flow", "chip_source_available_date"]
        ]
