from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Tier1FeatureExtensionConfig:
    bar_return_window: int = 14
    ir0001_windows: tuple[int, ...] = (20, 60)
    annualization_days: int = 252

    def __post_init__(self) -> None:
        if self.bar_return_window <= 0 or self.annualization_days <= 0:
            raise ValueError("feature windows and annualization_days must be positive")
        if not self.ir0001_windows or any(window <= 0 for window in self.ir0001_windows):
            raise ValueError("ir0001_windows must contain positive values")


class Tier1FeatureExtensionBuilder:
    """Create a read-only, availability-aware feature sidecar for Tier 1."""

    def __init__(self, config: Tier1FeatureExtensionConfig | None = None) -> None:
        self.config = config or Tier1FeatureExtensionConfig()

    def build(self, bars: pd.DataFrame, ir0001: pd.DataFrame) -> pd.DataFrame:
        required_bars = {
            "etf_id",
            "bar_id",
            "bar_status",
            "bar_end_date",
            "close_nav",
            "feature_available_at",
        }
        required_ir0001 = {"date", "close"}
        if missing := required_bars.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        if missing := required_ir0001.difference(ir0001.columns):
            raise ValueError(f"ir0001 missing columns: {sorted(missing)}")

        bar_frame = bars.loc[bars["bar_status"].eq("FINALIZED")].copy()
        if bar_frame.duplicated(["etf_id", "bar_id"]).any():
            raise ValueError("bars has duplicate etf_id-bar_id keys")
        bar_frame["bar_end_date"] = pd.to_datetime(
            bar_frame["bar_end_date"], errors="coerce"
        ).dt.normalize()
        bar_frame["feature_available_at"] = pd.to_datetime(
            bar_frame["feature_available_at"], errors="coerce", utc=True
        ).astype("datetime64[ns, UTC]")
        bar_frame["close_nav"] = pd.to_numeric(bar_frame["close_nav"], errors="coerce")
        if bar_frame[["bar_end_date", "feature_available_at", "close_nav"]].isna().any().any():
            raise ValueError("bars requires valid date, availability, and close_nav")
        if bar_frame["close_nav"].le(0).any():
            raise ValueError("bars close_nav must be positive")
        bar_frame = bar_frame.sort_values(["etf_id", "bar_id"], kind="stable")
        bar_frame["_bar_return"] = bar_frame.groupby("etf_id", sort=False)[
            "close_nav"
        ].transform(lambda values: np.log(values).diff())
        bar_frame[f"bar_log_return_std_{self.config.bar_return_window}"] = (
            bar_frame.groupby("etf_id", sort=False)["_bar_return"].transform(
                lambda values: values.rolling(
                    self.config.bar_return_window,
                    min_periods=self.config.bar_return_window,
                ).std(ddof=1)
            )
        )

        ir_frame = self._prepare_ir0001(ir0001)
        extension = pd.merge_asof(
            bar_frame.sort_values("feature_available_at", kind="stable"),
            ir_frame.sort_values("ir0001_available_at", kind="stable"),
            left_on="feature_available_at",
            right_on="ir0001_available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        keep = [
            "etf_id",
            "bar_id",
            "feature_available_at",
            f"bar_log_return_std_{self.config.bar_return_window}",
            "ir0001_observation_date",
            "ir0001_available_at",
            "ir0001_revision_status",
            "ir0001_availability_assumption",
            *[f"ir0001_realized_vol_{window}" for window in self.config.ir0001_windows],
        ]
        return extension.loc[:, keep].sort_values(["etf_id", "bar_id"], kind="stable").reset_index(drop=True)

    def _prepare_ir0001(self, ir0001: pd.DataFrame) -> pd.DataFrame:
        frame = ir0001.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        if frame.duplicated("date").any():
            raise ValueError("ir0001 has duplicate dates")
        if frame[["date", "close"]].isna().any().any() or frame["close"].le(0).any():
            raise ValueError("ir0001 requires valid dates and positive closes")
        if "available_at" in frame:
            frame["ir0001_available_at"] = pd.to_datetime(
                frame["available_at"], errors="coerce", utc=True
            ).astype("datetime64[ns, UTC]")
            frame["ir0001_availability_assumption"] = "SOURCE_DECLARED"
        else:
            local_after_close = frame["date"].dt.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18)
            frame["ir0001_available_at"] = local_after_close.dt.tz_convert("UTC")
            frame["ir0001_availability_assumption"] = "AFTER_CLOSE_DATE_ONLY"
        frame["ir0001_available_at"] = frame["ir0001_available_at"].astype(
            "datetime64[ns, UTC]"
        )
        if frame["ir0001_available_at"].isna().any():
            raise ValueError("ir0001 requires valid availability timestamps")
        frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
        returns = np.log(frame["close"]).diff()
        for window in self.config.ir0001_windows:
            frame[f"ir0001_realized_vol_{window}"] = (
                returns.rolling(window, min_periods=window).std(ddof=1)
                * np.sqrt(self.config.annualization_days)
            )
        frame["ir0001_observation_date"] = frame["date"]
        frame["ir0001_revision_status"] = "PIT_REVISION_UNVERIFIED"
        return frame
