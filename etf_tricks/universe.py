from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .models import ETFSpec


class UniverseContractError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionResult:
    etf_id: str
    formation_date: pd.Timestamp
    liquidity_threshold: float
    candidates: pd.DataFrame
    targets: pd.DataFrame
    carry_forward: bool


class UniverseEngine:
    def __init__(self, calendar: TradingCalendar) -> None:
        self.calendar = calendar

    def select(
        self,
        spec: ETFSpec,
        formation_date: str | pd.Timestamp,
        features: pd.DataFrame,
        security_master: pd.DataFrame,
        ix0001: pd.DataFrame,
    ) -> SelectionResult:
        formation = pd.Timestamp(formation_date)
        feature_frame = self._validate_features(features, formation, spec)
        master = self._validate_master(security_master)
        denominator = self._ix0001_sum20(ix0001, formation)

        audit = feature_frame.merge(
            master,
            on="ticker",
            how="left",
            validate="one_to_one",
        )
        audit.insert(0, "etf_id", spec.etf_id)
        audit["formation_date"] = formation
        audit["signal_value"] = pd.to_numeric(
            audit[spec.signal_name], errors="coerce"
        )
        audit["liquidity_ratio_vs_ix0001_20d"] = (
            pd.to_numeric(audit["stock_traded_value_sum20"], errors="coerce")
            / denominator
        )
        audit["exclusion_reason"] = ""

        self._apply_base_eligibility(audit, formation)
        self._apply_industry_eligibility(audit, spec)
        self._exclude(
            audit,
            ~np.isfinite(audit["signal_value"]),
            "invalid_signal",
        )
        self._exclude(
            audit,
            pd.to_numeric(
                audit["stock_traded_value_observation_count"], errors="coerce"
            ).ne(20),
            "incomplete_stock_liquidity_window",
        )
        self._exclude(
            audit,
            ~np.isfinite(audit["liquidity_ratio_vs_ix0001_20d"])
            | audit["liquidity_ratio_vs_ix0001_20d"].lt(0),
            "invalid_stock_liquidity_value",
        )

        pre_liquidity = audit["exclusion_reason"].eq("")
        preferred_count = int(
            (
                pre_liquidity
                & audit["liquidity_ratio_vs_ix0001_20d"].ge(
                    spec.liquidity_policy.preferred_ratio
                )
            ).sum()
        )
        threshold = spec.liquidity_policy.preferred_ratio
        if spec.liquidity_policy.adaptive and preferred_count < spec.min_candidates:
            threshold = spec.liquidity_policy.floor_ratio

        audit["liquidity_threshold"] = threshold
        self._exclude(
            audit,
            audit["liquidity_ratio_vs_ix0001_20d"].lt(threshold),
            "below_liquidity_threshold",
        )
        audit["eligible"] = audit["exclusion_reason"].eq("")
        audit["selected"] = False
        audit["target_weight"] = np.nan

        eligible = audit[audit["eligible"]].copy()
        ascending = spec.direction == "ascending"
        eligible = eligible.sort_values(
            ["signal_value", "adv20", "market_cap", "ticker"],
            ascending=[ascending, False, False, True],
            kind="stable",
        ).head(spec.max_candidates)

        if not eligible.empty:
            if spec.weighting == "market_cap":
                market_caps = pd.to_numeric(eligible["market_cap"], errors="coerce")
                if (~np.isfinite(market_caps) | market_caps.le(0)).any():
                    raise UniverseContractError(
                        "market_cap targets require finite positive market_cap"
                    )
                weights = market_caps / market_caps.sum()
            else:
                weights = pd.Series(1.0 / len(eligible), index=eligible.index)
            eligible["target_weight"] = weights
            audit.loc[eligible.index, "selected"] = True
            audit.loc[eligible.index, "target_weight"] = weights

        target_columns = [
            "etf_id",
            "formation_date",
            "ticker",
            "stock_name",
            "signal_value",
            "target_weight",
            "close",
            "adv20",
            "market_cap",
            "liquidity_ratio_vs_ix0001_20d",
            "liquidity_threshold",
        ]
        targets = eligible.loc[:, target_columns].reset_index(drop=True)
        audit = audit.sort_values("ticker", kind="stable").reset_index(drop=True)
        return SelectionResult(
            etf_id=spec.etf_id,
            formation_date=formation,
            liquidity_threshold=float(threshold),
            candidates=audit,
            targets=targets,
            carry_forward=targets.empty,
        )

    @staticmethod
    def _validate_features(
        features: pd.DataFrame, formation: pd.Timestamp, spec: ETFSpec
    ) -> pd.DataFrame:
        required = {
            "formation_date",
            "ticker",
            "close",
            "adv20",
            "stock_traded_value_sum20",
            "stock_traded_value_observation_count",
            "market_cap",
            spec.signal_name,
        }
        missing = sorted(required.difference(features.columns))
        if missing:
            raise UniverseContractError(f"features missing columns: {missing}")
        frame = features.copy()
        frame["formation_date"] = pd.to_datetime(frame["formation_date"], errors="coerce")
        if not frame["formation_date"].eq(formation).all():
            raise UniverseContractError("features contain rows from another formation_date")
        frame["ticker"] = frame["ticker"].astype(str)
        if frame["ticker"].duplicated().any():
            raise UniverseContractError("features contain duplicate tickers")
        return frame

    @staticmethod
    def _validate_master(security_master: pd.DataFrame) -> pd.DataFrame:
        required = {"ticker", "stock_name", "list_date", "delist_date", "main_industry"}
        missing = sorted(required.difference(security_master.columns))
        if missing:
            raise UniverseContractError(f"security_master missing columns: {missing}")
        master = security_master.loc[:, sorted(required)].copy()
        master["ticker"] = master["ticker"].astype(str)
        if master["ticker"].duplicated().any():
            raise UniverseContractError("security_master contains duplicate tickers")
        master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
        master["delist_date"] = pd.to_datetime(master["delist_date"], errors="coerce")
        return master

    def _ix0001_sum20(
        self, ix0001: pd.DataFrame, formation: pd.Timestamp
    ) -> float:
        if not {"date", "amt"}.issubset(ix0001.columns):
            raise UniverseContractError("IX0001 requires date and amt columns")
        frame = ix0001.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        if "ticker" in frame.columns:
            frame = frame[frame["ticker"].astype(str).eq("IX0001")]
        if frame["date"].duplicated().any():
            raise UniverseContractError("IX0001 contains duplicate dates")

        calendar_days = pd.DatetimeIndex(self.calendar.days)
        locations = np.flatnonzero(calendar_days == formation)
        if len(locations) != 1:
            raise UniverseContractError("formation date is not a unique TWSE trading day")
        position = int(locations[0])
        expected = calendar_days[max(0, position - 19) : position + 1]
        aligned = frame.set_index("date").reindex(expected)
        amounts = pd.to_numeric(aligned["amt"], errors="coerce")
        if len(expected) != 20 or amounts.notna().sum() != 20:
            raise UniverseContractError("IX0001 requires an aligned complete 20-day window")
        if (~np.isfinite(amounts) | amounts.lt(0)).any():
            raise UniverseContractError("IX0001 20-day amounts must be finite and non-negative")
        total = float(amounts.sum())
        if total <= 0:
            raise UniverseContractError("IX0001 20-day amount sum must be positive")
        return total

    @classmethod
    def _apply_base_eligibility(
        cls, audit: pd.DataFrame, formation: pd.Timestamp
    ) -> None:
        cls._exclude(
            audit,
            ~audit["ticker"].str.fullmatch(r"[1-9][0-9]{3}"),
            "invalid_common_stock_ticker",
        )
        cls._exclude(audit, audit["list_date"].isna(), "missing_security_master")
        cls._exclude(audit, audit["list_date"].gt(formation), "not_yet_listed")
        cls._exclude(
            audit,
            audit["delist_date"].notna() & audit["delist_date"].le(formation),
            "delisted_on_or_before_formation",
        )
        close = pd.to_numeric(audit["close"], errors="coerce")
        cls._exclude(
            audit,
            ~np.isfinite(close) | close.le(0),
            "missing_positive_raw_close",
        )

    @classmethod
    def _apply_industry_eligibility(cls, audit: pd.DataFrame, spec: ETFSpec) -> None:
        if spec.industry_include:
            cls._exclude(
                audit,
                ~audit["main_industry"].isin(spec.industry_include),
                "not_included_industry",
            )
        if spec.industry_exclude:
            cls._exclude(
                audit,
                audit["main_industry"].isin(spec.industry_exclude),
                "excluded_industry",
            )

    @staticmethod
    def _exclude(audit: pd.DataFrame, mask: pd.Series, reason: str) -> None:
        available = audit["exclusion_reason"].eq("")
        audit.loc[available & mask.fillna(False), "exclusion_reason"] = reason
