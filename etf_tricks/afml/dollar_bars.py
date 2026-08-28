from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

import numpy as np
import pandas as pd

from etf_tricks.registry import ETF_IDS

from .config import (
    AFMLBoundaries,
    AFMLContractError,
    DollarBarConfig,
    config_sha256,
)


BarRole = Literal["CALIBRATION_HISTORY", "LIVE_ELIGIBLE"]


class DollarBarContractError(AFMLContractError):
    def __init__(self, message: str, *, evidence: pd.DataFrame | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class QCalibration:
    q_star: float
    calibration_version: str
    calibration_scope: str
    etf_ids: tuple[str, ...]
    fit_start: pd.Timestamp
    fit_end: pd.Timestamp
    parameters_frozen_at: pd.Timestamp
    calibration_effective_at: pd.Timestamp | pd.NaT
    candidate_evidence: pd.DataFrame


@dataclass(frozen=True)
class DollarBarTables:
    dollar_bars: pd.DataFrame
    bar_daily_membership: pd.DataFrame
    open_bar_checkpoints: pd.DataFrame
    calibration_evidence: pd.DataFrame


_BAR_COLUMNS = (
    "etf_id",
    "bar_id",
    "bar_status",
    "bar_role",
    "bar_start_date",
    "bar_end_date",
    "threshold_asof_date",
    "threshold_mode",
    "market_amount_baseline",
    "market_fraction_q",
    "threshold_amount",
    "frozen_threshold_amount",
    "bar_amount",
    "overshoot_amount",
    "overshoot_ratio",
    "trading_day_count",
    "close_path_open_nav",
    "close_path_high_nav",
    "close_path_low_nav",
    "close_nav",
    "previous_close_nav",
    "log_return",
    "ix0001_amount_sum",
    "etf_market_share",
    "source_observation_max_date",
    "calibration_fit_end",
    "parameters_frozen_at",
    "calibration_effective_at",
    "bar_available_at",
    "feature_available_at",
    "live_eligible",
    "crosses_split_boundary",
    "source_quality_flag",
    "source_revision_status",
    "calibration_version",
    "config_version",
)

_MEMBERSHIP_COLUMNS = (
    "etf_id",
    "bar_id",
    "date",
    "observation_date",
    "nav",
    "etf_amount",
    "ix0001_traded_value",
    "source_available_at",
    "ix0001_source_available_at",
    "member_available_at",
    "ingested_at",
    "source_revision_id",
    "ix0001_ingested_at",
    "ix0001_source_revision_id",
    "source_manifest_hash",
    "ix0001_source_manifest_hash",
    "source_revision_status",
    "source_quality_flag",
    "calibration_version",
)

_CHECKPOINT_COLUMNS = (
    "etf_id",
    "bar_id",
    "bar_status",
    "bar_role",
    "bar_start_date",
    "last_observation_date",
    "threshold_asof_date",
    "market_amount_baseline",
    "market_fraction_q",
    "threshold_amount",
    "accumulated_amount",
    "ix0001_amount_sum",
    "trading_day_count",
    "close_path_open_nav",
    "close_path_high_nav",
    "close_path_low_nav",
    "close_nav",
    "member_dates",
    "member_amounts",
    "member_navs",
    "calibration_version",
    "config_version",
)


class DollarBarCalibrator:
    def __init__(self, config: DollarBarConfig) -> None:
        self.config = config

    def fit(
        self,
        daily_etf: pd.DataFrame,
        ix0001: pd.DataFrame,
        boundaries: AFMLBoundaries,
        etf_ids: tuple[str, ...],
        *,
        calibration_end: pd.Timestamp | str | None = None,
    ) -> QCalibration:
        ids = tuple(dict.fromkeys(str(value) for value in etf_ids))
        if not ids:
            raise DollarBarContractError("q calibration requires at least one ETF ID")
        prepared = _prepare_daily_market(
            daily_etf,
            ix0001,
            self.config,
            etf_ids=ids,
        )
        fit_end = (
            pd.Timestamp(calibration_end).normalize()
            if calibration_end is not None
            else pd.Timestamp(boundaries.train_end)
        )
        if fit_end < pd.Timestamp(boundaries.train_start) or fit_end > pd.Timestamp(
            boundaries.test_end
        ):
            raise DollarBarContractError(
                "calibration_end must be within train_start and test_end"
            )
        train = prepared[
            prepared["date"].between(
                pd.Timestamp(boundaries.train_start), fit_end
            )
        ].copy()
        ratios = train["etf_amount"] / train["market_amount_baseline"]
        valid_ratios = ratios[np.isfinite(ratios) & ratios.gt(0)].to_numpy(dtype=float)
        if valid_ratios.size == 0:
            raise DollarBarContractError("bar_threshold_not_calibrated: no valid training ratios")
        levels = np.linspace(
            self.config.candidate_quantile_min,
            self.config.candidate_quantile_max,
            self.config.candidate_quantile_count,
        )
        candidates = np.unique(
            np.quantile(
                valid_ratios,
                levels,
                method=self.config.candidate_quantile_method,
            )
        )
        evidence_rows: list[dict[str, object]] = []
        for candidate_index, q_value in enumerate(candidates):
            for etf_id in ids:
                summary = _candidate_summary(
                    train[train["etf_id"].eq(etf_id)],
                    float(q_value),
                )
                passes_count = summary["completed_bars"] >= self.config.min_completed_bars
                max_duration = summary["max_completed_bar_duration"]
                passes_duration = (
                    np.isfinite(max_duration)
                    and max_duration <= self.config.max_bar_duration_trading_days
                )
                evidence_rows.append(
                    {
                        "candidate_index": candidate_index,
                        "quantile_level_rule_min": self.config.candidate_quantile_min,
                        "quantile_level_rule_max": self.config.candidate_quantile_max,
                        "quantile_level_rule_count": self.config.candidate_quantile_count,
                        "quantile_method": self.config.candidate_quantile_method,
                        "q_candidate": float(q_value),
                        "etf_id": etf_id,
                        **summary,
                        "passes_bar_count": bool(passes_count),
                        "passes_duration": bool(passes_duration),
                        "passed": bool(passes_count and passes_duration),
                    }
                )
        evidence = pd.DataFrame(evidence_rows).sort_values(
            ["q_candidate", "etf_id"], kind="mergesort"
        ).reset_index(drop=True)
        passing = (
            evidence.groupby("q_candidate", sort=True)["passed"]
            .agg(lambda values: bool(values.all()) and len(values) == len(ids))
        )
        passing_q = passing[passing].index.to_numpy(dtype=float)
        if passing_q.size == 0:
            raise DollarBarContractError(
                "bar_threshold_not_calibrated: no common q satisfies bar gates",
                evidence=evidence,
            )
        q_star = float(passing_q.max())
        fit_start = pd.Timestamp(boundaries.train_start)
        parameters_frozen_at = _date_end(fit_end)
        future_dates = prepared.loc[prepared["date"].gt(fit_end), "date"].sort_values()
        calibration_effective_at: pd.Timestamp | pd.NaT
        if future_dates.empty:
            calibration_effective_at = pd.NaT
        else:
            calibration_effective_at = pd.Timestamp(
                future_dates.iloc[0], tz="Asia/Taipei"
            )
        calibration_scope = (
            "PRODUCTION_ALL_13" if set(ids) == set(ETF_IDS) else "TEST_ONLY_SUBSET"
        )
        version_payload = {
            "config_sha256": config_sha256(self.config),
            "q_star": q_star,
            "fit_start": fit_start.date().isoformat(),
            "fit_end": fit_end.date().isoformat(),
            "etf_ids": ids,
            "scope": calibration_scope,
        }
        calibration_version = hashlib.sha256(
            json.dumps(version_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return QCalibration(
            q_star=q_star,
            calibration_version=calibration_version,
            calibration_scope=calibration_scope,
            etf_ids=ids,
            fit_start=fit_start,
            fit_end=fit_end,
            parameters_frozen_at=parameters_frozen_at,
            calibration_effective_at=calibration_effective_at,
            candidate_evidence=evidence,
        )


class DollarBarBuilder:
    def __init__(self, config: DollarBarConfig) -> None:
        self.config = config

    def transform(
        self,
        daily_etf: pd.DataFrame,
        ix0001: pd.DataFrame,
        calendar: pd.DataFrame,
        calibration: QCalibration | tuple[QCalibration, ...],
        role: BarRole,
        *,
        split_boundaries: tuple[pd.Timestamp, ...] = (),
    ) -> DollarBarTables:
        if role not in {"CALIBRATION_HISTORY", "LIVE_ELIGIBLE"}:
            raise DollarBarContractError(f"unsupported bar role: {role}")
        calibrations = (
            (calibration,) if isinstance(calibration, QCalibration) else calibration
        )
        if not calibrations:
            raise DollarBarContractError("at least one calibration is required")
        if role == "CALIBRATION_HISTORY" and len(calibrations) != 1:
            raise DollarBarContractError(
                "calibration history requires exactly one calibration"
            )
        etf_ids = calibrations[0].etf_ids
        if any(item.etf_ids != etf_ids for item in calibrations):
            raise DollarBarContractError("all calibration versions must share ETF IDs")
        calibrations = tuple(
            sorted(calibrations, key=lambda item: item.fit_end)
        )
        valid_dates = _validated_calendar_dates(calendar)
        prepared = _prepare_daily_market(
            daily_etf,
            ix0001,
            self.config,
            etf_ids=etf_ids,
        )
        if not prepared["date"].isin(valid_dates).all():
            raise DollarBarContractError("daily ETF rows include non-TWSE trading dates")
        if role == "CALIBRATION_HISTORY":
            prepared = prepared[prepared["date"].le(calibrations[0].fit_end)].copy()
        else:
            effective_values = [
                _naive_date(item.calibration_effective_at)
                for item in calibrations
                if pd.notna(item.calibration_effective_at)
            ]
            if not effective_values:
                prepared = prepared.iloc[0:0].copy()
            else:
                prepared = prepared[
                    prepared["date"].ge(min(effective_values))
                ].copy()

        bars: list[dict[str, object]] = []
        memberships: list[dict[str, object]] = []
        checkpoints: list[dict[str, object]] = []
        config_version = config_sha256(self.config)
        for etf_id in etf_ids:
            rows = prepared[prepared["etf_id"].eq(etf_id)].sort_values(
                "date", kind="mergesort"
            )
            bar_id = 1
            current: list[dict[str, object]] = []
            threshold = np.nan
            baseline = np.nan
            threshold_asof_date = pd.NaT
            previous_close = np.nan
            active_calibration: QCalibration | None = None
            for row in rows.to_dict("records"):
                if not current:
                    active_calibration = _active_calibration(
                        calibrations, pd.Timestamp(row["date"]), role
                    )
                    if active_calibration is None:
                        continue
                    baseline = float(row["market_amount_baseline"])
                    if not np.isfinite(baseline) or baseline <= 0:
                        continue
                    threshold = (
                        float(self.config.fixed_nominal_threshold)
                        if self.config.threshold_mode == "fixed_nominal"
                        else active_calibration.q_star * baseline
                    )
                    threshold_asof_date = pd.Timestamp(row["threshold_asof_date"])
                member_available_at = max(
                    pd.Timestamp(row["source_available_at"]),
                    pd.Timestamp(row["ix0001_source_available_at"]),
                )
                current.append(
                    {
                        "etf_id": etf_id,
                        "bar_id": bar_id,
                        "date": pd.Timestamp(row["date"]),
                        "observation_date": pd.Timestamp(
                            row.get("observation_date", row["date"])
                        ),
                        "nav": float(row["nav"]),
                        "etf_amount": float(row["etf_amount"]),
                        "ix0001_traded_value": float(row["ix0001_traded_value"]),
                        "source_available_at": row["source_available_at"],
                        "ix0001_source_available_at": row[
                            "ix0001_source_available_at"
                        ],
                        "member_available_at": member_available_at,
                        "ingested_at": row.get("ingested_at", pd.NaT),
                        "source_revision_id": row.get(
                            "source_revision_id", pd.NA
                        ),
                        "ix0001_ingested_at": row.get(
                            "ix0001_ingested_at", pd.NaT
                        ),
                        "ix0001_source_revision_id": row.get(
                            "ix0001_source_revision_id", pd.NA
                        ),
                        "source_manifest_hash": row.get("source_manifest_hash", pd.NA),
                        "ix0001_source_manifest_hash": row.get(
                            "ix0001_source_manifest_hash", pd.NA
                        ),
                        "source_revision_status": _combined_revision_status(
                            row.get("source_revision_status"),
                            row.get("ix0001_source_revision_status"),
                        ),
                        "source_quality_flag": bool(
                            row.get("has_data_quality_flag", False)
                            or row.get("missing_traded_value_count", 0)
                        ),
                        "calibration_version": active_calibration.calibration_version,
                    }
                )
                accumulated = float(sum(float(value["etf_amount"]) for value in current))
                if accumulated < threshold:
                    continue
                ix_sum = float(
                    sum(float(value["ix0001_traded_value"]) for value in current)
                )
                navs = np.asarray([float(value["nav"]) for value in current], dtype=float)
                start_date = pd.Timestamp(current[0]["date"])
                end_date = pd.Timestamp(current[-1]["date"])
                close_nav = float(navs[-1])
                bar_available_at = max(
                    pd.Timestamp(value["member_available_at"]) for value in current
                )
                revisions = [str(value["source_revision_status"]) for value in current]
                source_revision_status = (
                    "PIT_REVISION_VERIFIED"
                    if all(value == "PIT_REVISION_VERIFIED" for value in revisions)
                    else "PIT_REVISION_UNVERIFIED"
                )
                overshoot = accumulated - threshold
                bars.append(
                    {
                        "etf_id": etf_id,
                        "bar_id": bar_id,
                        "bar_status": "FINALIZED",
                        "bar_role": role,
                        "bar_start_date": start_date,
                        "bar_end_date": end_date,
                        "threshold_asof_date": threshold_asof_date,
                        "threshold_mode": self.config.threshold_mode,
                        "market_amount_baseline": baseline,
                        "market_fraction_q": active_calibration.q_star,
                        "threshold_amount": threshold,
                        "frozen_threshold_amount": threshold,
                        "bar_amount": accumulated,
                        "overshoot_amount": overshoot,
                        "overshoot_ratio": overshoot / threshold,
                        "trading_day_count": len(current),
                        "close_path_open_nav": float(navs[0]),
                        "close_path_high_nav": float(navs.max()),
                        "close_path_low_nav": float(navs.min()),
                        "close_nav": close_nav,
                        "previous_close_nav": previous_close,
                        "log_return": (
                            np.log(close_nav / previous_close)
                            if np.isfinite(previous_close) and previous_close > 0
                            else np.nan
                        ),
                        "ix0001_amount_sum": ix_sum,
                        "etf_market_share": accumulated / ix_sum if ix_sum > 0 else np.nan,
                        "source_observation_max_date": end_date,
                        "calibration_fit_end": active_calibration.fit_end,
                        "parameters_frozen_at": active_calibration.parameters_frozen_at,
                        "calibration_effective_at": active_calibration.calibration_effective_at,
                        "bar_available_at": bar_available_at,
                        "feature_available_at": bar_available_at,
                        "live_eligible": role == "LIVE_ELIGIBLE",
                        "crosses_split_boundary": any(
                            start_date <= pd.Timestamp(boundary).normalize() < end_date
                            for boundary in split_boundaries
                        ),
                        "source_quality_flag": any(
                            bool(value["source_quality_flag"]) for value in current
                        ),
                        "source_revision_status": source_revision_status,
                        "calibration_version": active_calibration.calibration_version,
                        "config_version": config_version,
                    }
                )
                memberships.extend(current)
                previous_close = close_nav
                current = []
                active_calibration = None
                bar_id += 1
            if current:
                if active_calibration is None:
                    raise DollarBarContractError("open bar lacks an active calibration")
                navs = np.asarray([float(value["nav"]) for value in current], dtype=float)
                checkpoints.append(
                    {
                        "etf_id": etf_id,
                        "bar_id": bar_id,
                        "bar_status": "OPEN_PROVISIONAL",
                        "bar_role": role,
                        "bar_start_date": current[0]["date"],
                        "last_observation_date": current[-1]["date"],
                        "threshold_asof_date": threshold_asof_date,
                        "market_amount_baseline": baseline,
                        "market_fraction_q": active_calibration.q_star,
                        "threshold_amount": threshold,
                        "accumulated_amount": sum(
                            float(value["etf_amount"]) for value in current
                        ),
                        "ix0001_amount_sum": sum(
                            float(value["ix0001_traded_value"]) for value in current
                        ),
                        "trading_day_count": len(current),
                        "close_path_open_nav": float(navs[0]),
                        "close_path_high_nav": float(navs.max()),
                        "close_path_low_nav": float(navs.min()),
                        "close_nav": float(navs[-1]),
                        "member_dates": tuple(value["date"] for value in current),
                        "member_amounts": tuple(value["etf_amount"] for value in current),
                        "member_navs": tuple(value["nav"] for value in current),
                        "calibration_version": active_calibration.calibration_version,
                        "config_version": config_version,
                    }
                )

        bar_frame = pd.DataFrame(bars, columns=_BAR_COLUMNS)
        member_frame = pd.DataFrame(memberships, columns=_MEMBERSHIP_COLUMNS)
        checkpoint_frame = pd.DataFrame(checkpoints, columns=_CHECKPOINT_COLUMNS)
        if not bar_frame.empty:
            bar_frame = bar_frame.sort_values(
                ["etf_id", "bar_id"], kind="mergesort"
            ).reset_index(drop=True)
        if not member_frame.empty:
            member_frame = member_frame.sort_values(
                ["etf_id", "bar_id", "date"], kind="mergesort"
            ).reset_index(drop=True)
        if not checkpoint_frame.empty:
            checkpoint_frame = checkpoint_frame.sort_values(
                ["etf_id", "bar_id"], kind="mergesort"
            ).reset_index(drop=True)
        return DollarBarTables(
            dollar_bars=bar_frame,
            bar_daily_membership=member_frame,
            open_bar_checkpoints=checkpoint_frame,
            calibration_evidence=pd.concat(
                [item.candidate_evidence for item in calibrations], ignore_index=True
            ),
        )


def _naive_date(value: pd.Timestamp | pd.NaT) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _active_calibration(
    calibrations: tuple[QCalibration, ...],
    observation_date: pd.Timestamp,
    role: BarRole,
) -> QCalibration | None:
    if role == "CALIBRATION_HISTORY":
        return calibrations[0]
    eligible = [
        item
        for item in calibrations
        if pd.notna(item.calibration_effective_at)
        and _naive_date(item.calibration_effective_at) <= observation_date.normalize()
    ]
    return eligible[-1] if eligible else None


def _prepare_daily_market(
    daily_etf: pd.DataFrame,
    ix0001: pd.DataFrame,
    config: DollarBarConfig,
    *,
    etf_ids: tuple[str, ...],
) -> pd.DataFrame:
    daily_required = {"date", "etf_id", "nav", "etf_amount"}
    ix_required = {"date", "traded_value"}
    missing_daily = sorted(daily_required.difference(daily_etf.columns))
    missing_ix = sorted(ix_required.difference(ix0001.columns))
    if missing_daily:
        raise DollarBarContractError(f"daily_etf missing columns: {missing_daily}")
    if missing_ix:
        raise DollarBarContractError(f"IX0001 missing columns: {missing_ix}")
    daily = daily_etf[daily_etf["etf_id"].astype(str).isin(etf_ids)].copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["etf_id"] = daily["etf_id"].astype(str)
    if daily[["date", "etf_id"]].isna().any().any():
        raise DollarBarContractError("daily_etf contains invalid date or ETF ID")
    if daily.duplicated(["date", "etf_id"]).any():
        raise DollarBarContractError("daily_etf contains duplicate date-etf_id keys")
    missing_ids = sorted(set(etf_ids).difference(daily["etf_id"].unique()))
    if missing_ids:
        raise DollarBarContractError(f"daily_etf missing requested ETFs: {missing_ids}")
    daily["nav"] = pd.to_numeric(daily["nav"], errors="coerce")
    daily["etf_amount"] = pd.to_numeric(daily["etf_amount"], errors="coerce")
    if (~np.isfinite(daily["nav"]) | daily["nav"].le(0)).any():
        raise DollarBarContractError("daily_etf nav must be finite and positive")
    if (~np.isfinite(daily["etf_amount"]) | daily["etf_amount"].lt(0)).any():
        raise DollarBarContractError("daily_etf amount must be finite and non-negative")

    ix = ix0001.copy()
    ix["date"] = pd.to_datetime(ix["date"], errors="coerce")
    ix["traded_value"] = pd.to_numeric(ix["traded_value"], errors="coerce")
    if ix["date"].isna().any() or ix.duplicated("date").any():
        raise DollarBarContractError("IX0001 contains invalid or duplicate dates")
    ix = ix.sort_values("date", kind="mergesort").reset_index(drop=True)
    positive_amount = ix["traded_value"].where(
        np.isfinite(ix["traded_value"]) & ix["traded_value"].gt(0)
    )
    ix["market_amount_baseline"] = positive_amount.shift(1).rolling(
        config.market_amount_lookback_days,
        min_periods=config.min_market_amount_observations,
    ).median()
    ix["threshold_asof_date"] = ix["date"].shift(1)
    ix = ix.rename(
        columns={
            "traded_value": "ix0001_traded_value",
            "source_available_at": "ix0001_source_available_at",
            "ingested_at": "ix0001_ingested_at",
            "source_revision_id": "ix0001_source_revision_id",
            "source_manifest_hash": "ix0001_source_manifest_hash",
            "source_revision_status": "ix0001_source_revision_status",
        }
    )
    if "ix0001_source_available_at" not in ix:
        ix["ix0001_source_available_at"] = _date_end_series(ix["date"])
    if "ix0001_source_manifest_hash" not in ix:
        ix["ix0001_source_manifest_hash"] = pd.NA
    if "ix0001_ingested_at" not in ix:
        ix["ix0001_ingested_at"] = pd.NaT
    if "ix0001_source_revision_id" not in ix:
        ix["ix0001_source_revision_id"] = pd.NA
    if "ix0001_source_revision_status" not in ix:
        ix["ix0001_source_revision_status"] = "PIT_REVISION_UNVERIFIED"
    if "source_available_at" not in daily:
        daily["source_available_at"] = _date_end_series(daily["date"])
    merged = daily.merge(
        ix.loc[
            :,
            [
                "date",
                "ix0001_traded_value",
                "market_amount_baseline",
                "threshold_asof_date",
                "ix0001_source_available_at",
                "ix0001_ingested_at",
                "ix0001_source_revision_id",
                "ix0001_source_manifest_hash",
                "ix0001_source_revision_status",
            ],
        ],
        on="date",
        how="left",
        sort=False,
        validate="many_to_one",
    )
    missing_current_ix = ~np.isfinite(merged["ix0001_traded_value"]) | merged[
        "ix0001_traded_value"
    ].le(0)
    if missing_current_ix.any():
        raise DollarBarContractError("daily ETF rows lack positive same-day IX0001 amount")
    return merged.sort_values(["etf_id", "date"], kind="mergesort").reset_index(
        drop=True
    )


def _candidate_summary(frame: pd.DataFrame, q_value: float) -> dict[str, object]:
    completed_durations: list[int] = []
    accumulated = 0.0
    threshold = np.nan
    duration = 0
    eligible_days = 0
    for row in frame.sort_values("date", kind="mergesort").to_dict("records"):
        if duration == 0:
            baseline = float(row["market_amount_baseline"])
            if not np.isfinite(baseline) or baseline <= 0:
                continue
            threshold = q_value * baseline
        eligible_days += 1
        accumulated += float(row["etf_amount"])
        duration += 1
        if accumulated >= threshold:
            completed_durations.append(duration)
            accumulated = 0.0
            duration = 0
            threshold = np.nan
    return {
        "eligible_training_days": eligible_days,
        "completed_bars": len(completed_durations),
        "max_completed_bar_duration": (
            max(completed_durations) if completed_durations else np.nan
        ),
        "open_terminal_duration": duration,
        "open_terminal_amount": accumulated,
    }


def _validated_calendar_dates(calendar: pd.DataFrame) -> pd.DatetimeIndex:
    required = {"date", "market", "is_trading_day"}
    missing = sorted(required.difference(calendar.columns))
    if missing:
        raise DollarBarContractError(f"trading calendar missing columns: {missing}")
    normalized = calendar.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    twse = normalized[
        normalized["market"].eq("TWSE") & normalized["is_trading_day"].eq(True)
    ]
    if twse["date"].isna().any() or twse.duplicated("date").any():
        raise DollarBarContractError("trading calendar has invalid or duplicate TWSE dates")
    return pd.DatetimeIndex(twse["date"].sort_values())


def _combined_revision_status(*values: object) -> str:
    return (
        "PIT_REVISION_VERIFIED"
        if all(str(value) == "PIT_REVISION_VERIFIED" for value in values)
        else "PIT_REVISION_UNVERIFIED"
    )


def _date_end(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Taipei")
    else:
        timestamp = timestamp.tz_convert("Asia/Taipei")
    return timestamp.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)


def _date_end_series(values: pd.Series) -> pd.Series:
    index = pd.DatetimeIndex(pd.to_datetime(values))
    if index.tz is None:
        index = index.tz_localize("Asia/Taipei")
    else:
        index = index.tz_convert("Asia/Taipei")
    return pd.Series(
        index.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1),
        index=values.index,
    )
