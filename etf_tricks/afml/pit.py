from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from etf_tricks.data_gateway import DataContractError, DataGateway
from etf_tricks.result import ETFTrickResult

from .capabilities import SourceCapabilityAuditor
from .config import AFMLBoundaries, AFMLConfig, AFMLContractError


class PITContractError(AFMLContractError):
    """Raised when AFML source identity or knowledge-time rules are violated."""


@dataclass(frozen=True)
class PITDailyInputs:
    daily_etf: pd.DataFrame
    ix0001: pd.DataFrame
    trading_calendar: pd.DataFrame
    source_capabilities: pd.DataFrame
    source_identity: dict[str, Any]


class PITSourceAdapter:
    _DAILY_REQUIRED = (
        "date",
        "etf_id",
        "nav",
        "daily_return",
        "etf_amount",
        "missing_traded_value_count",
        "has_data_quality_flag",
        "cash_weight",
        "invested_weight",
        "holdings_count",
        "target_completion_ratio",
    )

    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway

    def prepare(
        self,
        base: ETFTrickResult,
        boundaries: AFMLBoundaries,
        config: AFMLConfig,
        *,
        requested_etf_ids: tuple[str, ...] | None = None,
    ) -> PITDailyInputs:
        expected_hashes, current_manifests = self._verify_source_identity(base)
        revision_status = self._revision_status(current_manifests)
        calendar, calendar_coverage_declared = self._prepare_calendar(
            boundaries, current_manifests["trading_calendar"]
        )
        expected_sessions = _requested_sessions(calendar, boundaries)
        requested_ids = tuple(
            dict.fromkeys(
                requested_etf_ids
                if requested_etf_ids is not None
                else base.daily_etf["etf_id"].dropna().astype(str)
            )
        )
        if not requested_ids:
            raise PITContractError("no requested ETF IDs for AFML source preparation")
        daily = self._prepare_daily_etf(
            base,
            boundaries,
            config,
            expected_hashes,
            revision_status,
            requested_ids,
            expected_sessions,
        )
        ix0001 = self._prepare_ix0001(
            boundaries, expected_hashes, revision_status, expected_sessions
        )
        capabilities = SourceCapabilityAuditor(self.gateway).audit(ix0001=ix0001)
        source_identity = {
            "upstream_spec_hash": base.metadata.get("spec_hash"),
            "upstream_run_config": base.metadata.get("run_config"),
            "manifest_hashes": dict(sorted(expected_hashes.items())),
            "daily_etf_sha256": _hash_frame(daily.loc[:, self._DAILY_REQUIRED]),
            "source_revision_status": revision_status,
            "coverage": {
                "requested_etf_ids": list(requested_ids),
                "expected_session_count": len(expected_sessions),
                "first_session": expected_sessions.min().date().isoformat(),
                "last_session": expected_sessions.max().date().isoformat(),
                "daily_etf_complete": True,
                "ix0001_complete": True,
                "trading_calendar_complete": True,
                "trading_calendar_manifest_coverage_declared": (
                    calendar_coverage_declared
                ),
            },
        }
        return PITDailyInputs(
            daily_etf=daily,
            ix0001=ix0001,
            trading_calendar=calendar,
            source_capabilities=capabilities,
            source_identity=source_identity,
        )

    def _verify_source_identity(
        self, base: ETFTrickResult
    ) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
        if not isinstance(base, ETFTrickResult):
            raise PITContractError("base must be an ETFTrickResult")
        if not isinstance(base.metadata, dict):
            raise PITContractError("upstream result metadata is missing")
        raw_hashes = base.metadata.get("manifest_hashes")
        if not isinstance(raw_hashes, dict):
            raise PITContractError("upstream result lacks manifest_hashes")
        missing_required = sorted(
            {"daily_price_volume", "trading_calendar"}.difference(raw_hashes)
        )
        if missing_required:
            raise PITContractError(
                f"upstream result manifest_hashes missing: {missing_required}"
            )
        expected_hashes = {str(key): str(value) for key, value in raw_hashes.items()}
        current_manifests: dict[str, dict[str, object]] = {}
        for artifact_id, expected in sorted(expected_hashes.items()):
            try:
                manifest = self.gateway.load_manifest(artifact_id)
            except DataContractError as exc:
                raise PITContractError(
                    f"cannot verify upstream artifact {artifact_id}: {exc}"
                ) from exc
            current = _upstream_manifest_sha256(manifest)
            if current != expected:
                raise PITContractError(
                    f"source identity mismatch for {artifact_id}: "
                    f"upstream={expected} current={current}"
                )
            current_manifests[artifact_id] = manifest
        if not base.metadata.get("spec_hash"):
            raise PITContractError("upstream result lacks spec_hash")
        return expected_hashes, current_manifests

    def _prepare_daily_etf(
        self,
        base: ETFTrickResult,
        boundaries: AFMLBoundaries,
        config: AFMLConfig,
        manifest_hashes: dict[str, str],
        revision_status: str,
        requested_etf_ids: tuple[str, ...],
        expected_sessions: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        missing = sorted(set(self._DAILY_REQUIRED).difference(base.daily_etf.columns))
        if missing:
            raise PITContractError(f"daily_etf missing required columns: {missing}")
        daily = base.daily_etf.copy()
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
        if daily["date"].isna().any():
            raise PITContractError("daily_etf contains invalid dates")
        daily = daily[
            daily["date"].between(
                pd.Timestamp(boundaries.train_start),
                pd.Timestamp(boundaries.test_end),
            )
        ].copy()
        daily["etf_id"] = daily["etf_id"].astype(str)
        daily = daily[daily["etf_id"].isin(requested_etf_ids)].copy()
        if daily.duplicated(["date", "etf_id"]).any():
            raise PITContractError("daily_etf contains duplicate date-etf_id keys")
        _validate_panel_session_coverage(
            daily,
            expected_sessions,
            requested_etf_ids,
        )

        nav = pd.to_numeric(daily["nav"], errors="coerce")
        amount = pd.to_numeric(daily["etf_amount"], errors="coerce")
        if (~np.isfinite(nav) | nav.le(0)).any():
            raise PITContractError("daily_etf nav must be finite and positive")
        if (~np.isfinite(amount) | amount.lt(0)).any():
            raise PITContractError("daily_etf etf_amount must be finite and non-negative")
        missing_amount = pd.to_numeric(
            daily["missing_traded_value_count"], errors="coerce"
        )
        quality_flag = daily["has_data_quality_flag"].fillna(True).astype(bool)
        if config.dollar_bar.quality_policy == "fail" and (
            quality_flag | missing_amount.ne(0) | missing_amount.isna()
        ).any():
            bad = int((quality_flag | missing_amount.ne(0) | missing_amount.isna()).sum())
            raise PITContractError(
                f"daily_etf amount quality policy failed for {bad} rows"
            )

        daily["nav"] = nav
        daily["etf_amount"] = amount
        daily["observation_date"] = daily["date"]
        daily["source_available_at"] = _after_close(daily["date"])
        daily["availability_assumption"] = "AFTER_CLOSE_DATE_ONLY"
        daily["ingested_at"] = pd.NaT
        daily["source_revision_id"] = pd.NA
        daily["source_manifest_hash"] = manifest_hashes["daily_price_volume"]
        daily["source_revision_status"] = revision_status
        return daily.sort_values(["date", "etf_id"], kind="mergesort").reset_index(
            drop=True
        )

    def _prepare_ix0001(
        self,
        boundaries: AFMLBoundaries,
        manifest_hashes: dict[str, str],
        revision_status: str,
        expected_sessions: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        try:
            ix = self.gateway.scan_artifact(
                "daily_price_volume",
                columns=("date", "ticker", "close", "traded_value"),
                filters=(("ticker", "==", "IX0001"),),
                start=boundaries.train_start,
                end=boundaries.test_end,
            )
        except DataContractError as exc:
            raise PITContractError(f"cannot prepare IX0001: {exc}") from exc
        ix = ix[
            ix["date"].between(
                pd.Timestamp(boundaries.train_start),
                pd.Timestamp(boundaries.test_end),
            )
        ].copy()
        ix["date"] = pd.to_datetime(ix["date"], errors="coerce")
        if ix["date"].isna().any() or ix.duplicated("date").any():
            raise PITContractError("IX0001 contains invalid or duplicate dates")
        _validate_series_session_coverage(ix, expected_sessions, "IX0001")
        close = pd.to_numeric(ix["close"], errors="coerce")
        amount = pd.to_numeric(ix["traded_value"], errors="coerce")
        if (~np.isfinite(close) | close.le(0)).any():
            raise PITContractError("IX0001 close must be finite and positive")
        if (~np.isfinite(amount) | amount.le(0)).any():
            raise PITContractError("IX0001 traded_value must be finite and positive")
        ix["close"] = close
        ix["traded_value"] = amount
        ix["observation_date"] = ix["date"]
        ix["source_available_at"] = _after_close(ix["date"])
        ix["availability_assumption"] = "AFTER_CLOSE_DATE_ONLY"
        ix["ingested_at"] = pd.NaT
        ix["source_revision_id"] = pd.NA
        ix["source_manifest_hash"] = manifest_hashes["daily_price_volume"]
        ix["source_revision_status"] = revision_status
        return ix.sort_values("date", kind="mergesort").reset_index(drop=True)

    def _prepare_calendar(
        self,
        boundaries: AFMLBoundaries,
        manifest: dict[str, object],
    ) -> tuple[pd.DataFrame, bool]:
        raw_range = manifest.get("date_range") or manifest.get(
            "availability_date_range"
        )
        coverage_declared = isinstance(raw_range, list) and len(raw_range) == 2
        scan_end = pd.Timestamp(boundaries.test_end) + pd.Timedelta(days=31)
        scan_bounds = (
            {
                "start": boundaries.train_start,
                "end": min(pd.Timestamp(raw_range[1]), scan_end),
            }
            if coverage_declared
            else {}
        )
        try:
            calendar = self.gateway.scan_artifact(
                "trading_calendar",
                columns=("date", "market", "is_trading_day"),
                filters=(("market", "==", "TWSE"), ("is_trading_day", "==", True)),
                **scan_bounds,
            )
        except DataContractError as exc:
            raise PITContractError(f"cannot prepare trading_calendar: {exc}") from exc
        if calendar.empty:
            raise PITContractError("trading_calendar has no TWSE trading sessions")
        if calendar["date"].isna().any() or calendar.duplicated("date").any():
            raise PITContractError("trading_calendar contains invalid or duplicate TWSE dates")
        calendar["date"] = pd.to_datetime(calendar["date"], errors="coerce")
        calendar = calendar[
            calendar["date"].between(
                pd.Timestamp(boundaries.train_start), scan_end
            )
        ].copy()
        requested = calendar[calendar["date"].le(pd.Timestamp(boundaries.test_end))]
        if (
            requested.empty
            or requested["date"].max() < pd.Timestamp(boundaries.test_end)
        ):
            raise PITContractError(
                "trading_calendar does not cover the requested test_end boundary"
            )
        return (
            calendar.sort_values("date", kind="mergesort").reset_index(drop=True),
            coverage_declared,
        )

    @staticmethod
    def _revision_status(manifests: dict[str, dict[str, object]]) -> str:
        verified_policies = {"append_only_vintages", "versioned", "immutable"}
        verified = all(
            manifest.get("availability_field")
            and str(manifest["availability_field"])
            in {str(value) for value in manifest.get("columns", ())}
            and manifest.get("revision_policy") in verified_policies
            for manifest in manifests.values()
        )
        return "PIT_REVISION_VERIFIED" if verified else "PIT_REVISION_UNVERIFIED"


def next_execution_session(
    calendar: pd.DataFrame,
    feature_available_at: pd.Timestamp | str,
    decision_cutoff: str,
) -> pd.Timestamp:
    if decision_cutoff != "after_close":
        raise PITContractError(
            f"unsupported decision_cutoff {decision_cutoff!r}; expected 'after_close'"
        )
    required = {"date", "market", "is_trading_day"}
    missing = sorted(required.difference(calendar.columns))
    if missing:
        raise PITContractError(f"trading_calendar missing columns: {missing}")
    available = pd.Timestamp(feature_available_at)
    if available.tzinfo is None:
        raise PITContractError("feature_available_at must be timezone-aware")
    available_date = available.tz_convert("Asia/Taipei").tz_localize(None).normalize()
    twse = calendar[
        calendar["market"].eq("TWSE") & calendar["is_trading_day"].eq(True)
    ].copy()
    twse["date"] = pd.to_datetime(twse["date"], errors="coerce")
    if twse["date"].isna().any() or twse.duplicated("date").any():
        raise PITContractError("trading_calendar contains invalid or duplicate TWSE dates")
    future = twse.loc[twse["date"].gt(available_date), "date"].sort_values()
    if future.empty:
        raise PITContractError(
            f"no TWSE execution session after {available_date.date()}"
        )
    return pd.Timestamp(future.iloc[0]).normalize()


def _after_close(values: pd.Series) -> pd.Series:
    localized = pd.DatetimeIndex(pd.to_datetime(values)).tz_localize("Asia/Taipei")
    end_of_day = localized.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return pd.Series(end_of_day, index=values.index)


def _upstream_manifest_sha256(manifest: dict[str, object]) -> str:
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _hash_frame(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].astype(str)
    payload = normalized.to_json(
        orient="records", date_format="iso", date_unit="ns", double_precision=15
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _requested_sessions(
    calendar: pd.DataFrame, boundaries: AFMLBoundaries
) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["date"], errors="coerce"))
    requested = dates[
        (dates >= pd.Timestamp(boundaries.train_start))
        & (dates <= pd.Timestamp(boundaries.test_end))
    ].sort_values()
    if requested.empty:
        raise PITContractError("trading_calendar has no sessions in requested AFML window")
    return requested


def _validate_panel_session_coverage(
    frame: pd.DataFrame,
    expected_sessions: pd.DatetimeIndex,
    requested_etf_ids: tuple[str, ...],
) -> None:
    expected = set(expected_sessions)
    for etf_id in requested_etf_ids:
        observed = set(
            pd.DatetimeIndex(frame.loc[frame["etf_id"].eq(etf_id), "date"])
        )
        missing = sorted(expected.difference(observed))
        extra = sorted(observed.difference(expected))
        if missing or extra:
            details = ",".join(value.date().isoformat() for value in missing[:5])
            raise PITContractError(
                f"daily_etf coverage failed for {etf_id}; "
                f"missing_sessions={len(missing)} [{details}] extra_sessions={len(extra)}"
            )


def _validate_series_session_coverage(
    frame: pd.DataFrame, expected_sessions: pd.DatetimeIndex, source_name: str
) -> None:
    expected = set(expected_sessions)
    observed = set(pd.DatetimeIndex(frame["date"]))
    missing = sorted(expected.difference(observed))
    extra = sorted(observed.difference(expected))
    if missing or extra:
        details = ",".join(value.date().isoformat() for value in missing[:5])
        raise PITContractError(
            f"{source_name} coverage failed; missing_sessions={len(missing)} "
            f"[{details}] extra_sessions={len(extra)}"
        )
