from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, TypeVar

import numpy as np
import pandas as pd
import psutil

from etf_tricks.data_gateway import DataGateway
from etf_tricks.registry import ETF_IDS
from etf_tricks.result import ETFTrickResult

from .config import (
    AFMLBoundaries,
    AFMLConfig,
    AFMLContractError,
    AFMLScopeError,
    config_sha256,
    validate_run_mode,
)
from .dataset import AFMLDataset
from .dollar_bars import DollarBarBuilder, DollarBarCalibrator, DollarBarTables, QCalibration
from .features import AFMLFeatureEngine
from .ffd import FFDSelection, FFDSelector
from .labels import TripleBarrierLabeler
from .pit import PITSourceAdapter
from .structural import StructuralFeatureEngine


T = TypeVar("T")


@dataclass
class _StageRecorder:
    rows: list[dict[str, object]]

    def run(self, stage: str, operation: Callable[[], T]) -> T:
        process = psutil.Process()
        rss_before = process.memory_info().rss
        started = perf_counter()
        result = operation()
        elapsed = perf_counter() - started
        rss_after = process.memory_info().rss
        self.rows.append(
            {
                "diagnostic_id": f"stage-{len(self.rows):04d}",
                "stage": stage,
                "etf_id": pd.NA,
                "status": "COMPLETED",
                "code": "STAGE_COMPLETED",
                "severity": "INFO",
                "elapsed_seconds": elapsed,
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "peak_rss_observed_bytes": max(rss_before, rss_after),
                "row_count": _result_row_count(result),
                "details": pd.NA,
            }
        )
        return result

    def add(
        self,
        stage: str,
        code: str,
        *,
        status: str,
        severity: str,
        etf_id: str | None = None,
        row_count: int = 0,
        details: object = pd.NA,
    ) -> None:
        rss = psutil.Process().memory_info().rss
        self.rows.append(
            {
                "diagnostic_id": f"diagnostic-{len(self.rows):04d}",
                "stage": stage,
                "etf_id": etf_id if etf_id is not None else pd.NA,
                "status": status,
                "code": code,
                "severity": severity,
                "elapsed_seconds": 0.0,
                "rss_before_bytes": rss,
                "rss_after_bytes": rss,
                "peak_rss_observed_bytes": rss,
                "row_count": row_count,
                "details": details,
            }
        )


class ETFAFMLLab:
    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway

    @classmethod
    def from_data_analysts(cls, root: str | Path | None = None) -> "ETFAFMLLab":
        resolved = Path(root) if root is not None else Path.cwd() / "DataAnalysts"
        return cls(DataGateway.from_data_analysts(resolved))

    def build_all(
        self,
        base: ETFTrickResult,
        *,
        config: AFMLConfig,
        mode: str,
        train_start: str | pd.Timestamp,
        train_end: str | pd.Timestamp,
        validation_end: str | pd.Timestamp,
        test_end: str | pd.Timestamp,
        etf_ids: tuple[str, ...] | None = None,
        retrain_dates: tuple[str | pd.Timestamp, ...] = (),
        full_history_acceptance: bool = False,
    ) -> AFMLDataset:
        run_mode = validate_run_mode(mode)
        ids = tuple(ETF_IDS if etf_ids is None else dict.fromkeys(etf_ids))
        if not ids:
            raise AFMLContractError("at least one ETF ID is required")
        unknown = sorted(set(ids).difference(ETF_IDS))
        if unknown:
            raise AFMLContractError(f"unknown ETF IDs: {unknown}")
        boundaries = AFMLBoundaries(train_start, train_end, validation_end, test_end)
        full_scope = _is_full_history_scope(ids, boundaries)
        if full_scope and not full_history_acceptance:
            raise AFMLScopeError(
                "13-ETF full-history execution requires full_history_acceptance=True"
            )
        present = set(base.daily_etf["etf_id"].astype(str).unique())
        missing_ids = sorted(set(ids).difference(present))
        if missing_ids:
            raise AFMLContractError(f"upstream result missing requested ETFs: {missing_ids}")
        retrain = _validate_retrain_dates(run_mode, retrain_dates, boundaries)
        recorder = _StageRecorder([])

        inputs = recorder.run(
            "source_adapter",
            lambda: PITSourceAdapter(self.gateway).prepare(
                base,
                boundaries,
                config,
                requested_etf_ids=ids,
            ),
        )
        calibrator = DollarBarCalibrator(config.dollar_bar)
        if run_mode == "research_full_history":
            calibration_ends = (pd.Timestamp(boundaries.test_end),)
        else:
            calibration_ends = (pd.Timestamp(boundaries.train_end), *retrain)
        calibrations = recorder.run(
            "dollar_bar_calibration",
            lambda: tuple(
                calibrator.fit(
                    inputs.daily_etf,
                    inputs.ix0001,
                    boundaries,
                    ids,
                    calibration_end=end,
                )
                for end in calibration_ends
            ),
        )
        bar_tables = recorder.run(
            "dollar_bars",
            lambda: _build_bar_tables(
                inputs, config, calibrations, run_mode, boundaries
            ),
        )

        ffd_outputs = recorder.run(
            "ffd",
            lambda: _build_ffd_tables(
                bar_tables.dollar_bars, calibrations, config, recorder
            ),
        )
        ffd_weights, ffd_search, ffd_series, selections = ffd_outputs

        structural_outputs = recorder.run(
            "structural",
            lambda: _build_structural_tables(
                bar_tables.dollar_bars, inputs.ix0001, config
            ),
        )
        structural_etf, structural_ix, structural_canonical = structural_outputs

        features = recorder.run(
            "features",
            lambda: AFMLFeatureEngine(config).build(
                bar_tables.dollar_bars,
                bar_tables.bar_daily_membership,
                ffd_series,
                structural_etf,
                structural_ix,
                base,
            ),
        )
        split_cutoffs = _split_cutoffs(boundaries)
        label_tables = recorder.run(
            "labels",
            lambda: TripleBarrierLabeler(config.labels).build(
                features,
                bar_tables.dollar_bars,
                bar_tables.bar_daily_membership,
                split_cutoffs,
            ),
        )
        readiness = _build_readiness(
            ids,
            bar_tables,
            selections,
            ffd_series,
            features,
            label_tables.labels,
            inputs.source_identity,
            config,
            run_mode,
        )
        metadata = _build_metadata(
            ids,
            boundaries,
            config,
            run_mode,
            full_scope,
            inputs,
            calibrations,
            selections,
            readiness,
        )
        diagnostics = pd.DataFrame(recorder.rows)
        dataset = AFMLDataset(
            source_capabilities=inputs.source_capabilities,
            dollar_bars=bar_tables.dollar_bars,
            open_bar_checkpoints=bar_tables.open_bar_checkpoints,
            bar_daily_membership=bar_tables.bar_daily_membership,
            ffd_weights=ffd_weights,
            ffd_search=ffd_search,
            ffd_series=ffd_series,
            structural_features=structural_canonical,
            features=features,
            events=label_tables.events,
            labels=label_tables.labels,
            diagnostics=diagnostics,
            metadata=metadata,
            readiness=readiness,
        )
        return dataset


def _build_bar_tables(
    inputs,
    config: AFMLConfig,
    calibrations: tuple[QCalibration, ...],
    mode: str,
    boundaries: AFMLBoundaries,
) -> DollarBarTables:
    builder = DollarBarBuilder(config.dollar_bar)
    split_boundaries = (
        pd.Timestamp(boundaries.train_end),
        pd.Timestamp(boundaries.validation_end),
    )
    history = builder.transform(
        inputs.daily_etf,
        inputs.ix0001,
        inputs.trading_calendar,
        calibrations[0],
        "CALIBRATION_HISTORY",
        split_boundaries=split_boundaries,
    )
    if mode == "research_full_history":
        return history
    live = builder.transform(
        inputs.daily_etf,
        inputs.ix0001,
        inputs.trading_calendar,
        calibrations,
        "LIVE_ELIGIBLE",
        split_boundaries=split_boundaries,
    )
    live_bars = live.dollar_bars.copy()
    live_members = live.bar_daily_membership.copy()
    live_checkpoints = live.open_bar_checkpoints.copy()
    for etf_id in calibrations[0].etf_ids:
        history_etf = history.dollar_bars[history.dollar_bars["etf_id"].eq(etf_id)]
        offset = int(history_etf["bar_id"].max()) if not history_etf.empty else 0
        for frame in (live_bars, live_members, live_checkpoints):
            mask = frame["etf_id"].eq(etf_id) if not frame.empty else pd.Series(False)
            if not frame.empty:
                frame.loc[mask, "bar_id"] = frame.loc[mask, "bar_id"].astype(int) + offset
        live_etf = live_bars[live_bars["etf_id"].eq(etf_id)].sort_values("bar_id")
        if not history_etf.empty and not live_etf.empty:
            first_index = live_etf.index[0]
            previous_close = float(history_etf.sort_values("bar_id").iloc[-1]["close_nav"])
            close = float(live_bars.at[first_index, "close_nav"])
            live_bars.at[first_index, "previous_close_nav"] = previous_close
            live_bars.at[first_index, "log_return"] = np.log(close / previous_close)
    bars = pd.concat([history.dollar_bars, live_bars], ignore_index=True)
    members = pd.concat(
        [history.bar_daily_membership, live_members], ignore_index=True
    )
    return DollarBarTables(
        dollar_bars=bars.sort_values(["etf_id", "bar_id"], kind="stable").reset_index(
            drop=True
        ),
        bar_daily_membership=members.sort_values(
            ["etf_id", "bar_id", "date"], kind="stable"
        ).reset_index(drop=True),
        open_bar_checkpoints=live_checkpoints.sort_values(
            ["etf_id", "bar_id"], kind="stable"
        ).reset_index(drop=True),
        calibration_evidence=pd.concat(
            [item.candidate_evidence for item in calibrations], ignore_index=True
        ),
    )


def _build_ffd_tables(
    bars: pd.DataFrame,
    calibrations: tuple[QCalibration, ...],
    config: AFMLConfig,
    recorder: _StageRecorder,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, list[FFDSelection]]]:
    weight_rows: list[dict[str, object]] = []
    search_frames: list[pd.DataFrame] = []
    series_frames: list[pd.DataFrame] = []
    selections: dict[str, list[FFDSelection]] = {}
    selector = FFDSelector(config.ffd)
    for etf_id, etf_bars in bars.groupby("etf_id", sort=False):
        ordered = etf_bars.sort_values("bar_id", kind="stable").reset_index(drop=True)
        log_nav = pd.Series(
            np.log(ordered["close_nav"].to_numpy(dtype=float)),
            index=ordered["bar_id"].to_numpy(),
        )
        selections[str(etf_id)] = []
        for calibration in calibrations:
            fit_mask = ordered["bar_end_date"].le(calibration.fit_end)
            fit_values = log_nav.loc[ordered.loc[fit_mask, "bar_id"].to_numpy()]
            selection_version = f"{calibration.calibration_version}:ffd"
            selection = selector.fit(fit_values, selection_version)
            selections[str(etf_id)].append(selection)
            evidence = selection.search_evidence.copy()
            evidence.insert(0, "etf_id", str(etf_id))
            evidence.insert(1, "q_calibration_version", calibration.calibration_version)
            evidence.insert(2, "search_order", np.arange(len(evidence), dtype=int))
            search_frames.append(evidence)
            recorder.add(
                "ffd",
                "FFD_SELECTION",
                status=selection.status,
                severity="INFO" if selection.status == "stationarity_reached" else "ERROR",
                etf_id=str(etf_id),
                row_count=len(fit_values),
                details=f"d={selection.d}; width={selection.width}",
            )
            if selection.status != "stationarity_reached":
                continue
            weight_rows.extend(
                {
                    "etf_id": str(etf_id),
                    "calibration_version": selection.calibration_version,
                    "q_calibration_version": calibration.calibration_version,
                    "weight_lag": lag,
                    "weight": float(weight),
                    "selected_d": selection.d,
                    "ffd_width": selection.width,
                    "config_version": selection.config_version,
                }
                for lag, weight in enumerate(selection.weights)
            )
            transformed = selector.transform(log_nav, selection).reset_index(
                names="bar_id"
            )
            transformed.insert(0, "etf_id", str(etf_id))
            target_version = calibration.calibration_version
            version_by_bar = ordered.set_index("bar_id")["calibration_version"]
            transformed["q_calibration_version"] = transformed["bar_id"].map(
                version_by_bar
            )
            transformed = transformed[
                transformed["q_calibration_version"].eq(target_version)
            ]
            transformed = transformed.merge(
                ordered[["bar_id", "feature_available_at"]],
                on="bar_id",
                how="left",
                validate="one_to_one",
            )
            series_frames.append(transformed)
    weights = pd.DataFrame(weight_rows)
    if weights.empty:
        weights = pd.DataFrame(
            columns=[
                "etf_id",
                "calibration_version",
                "q_calibration_version",
                "weight_lag",
                "weight",
                "selected_d",
                "ffd_width",
                "config_version",
            ]
        )
    search = pd.concat(search_frames, ignore_index=True) if search_frames else pd.DataFrame()
    series = pd.concat(series_frames, ignore_index=True) if series_frames else pd.DataFrame(
        columns=[
            "etf_id",
            "bar_id",
            "ffd_level",
            "selected_d",
            "ffd_width",
            "calibration_version",
            "config_version",
            "q_calibration_version",
            "feature_available_at",
        ]
    )
    if not series.empty and series.duplicated(["etf_id", "bar_id"]).any():
        raise AFMLContractError("FFD version segments produced duplicate bar keys")
    return weights, search, series, selections


def _build_structural_tables(
    bars: pd.DataFrame, ix0001: pd.DataFrame, config: AFMLConfig
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    engine = StructuralFeatureEngine(config.structural)
    etf_input = bars[["etf_id", "bar_id", "close_nav", "feature_available_at"]].copy()
    etf_input["log_close_nav"] = np.log(etf_input["close_nav"])
    etf = engine.transform(etf_input, "log_close_nav", "feature_available_at")
    ix_input = ix0001[["date", "close", "source_available_at"]].copy()
    ix_input["log_close"] = np.log(ix_input["close"].to_numpy(dtype=float))
    ix = engine.transform(ix_input, "log_close", "source_available_at")
    etf_canonical = etf.copy()
    etf_canonical.insert(0, "entity_type", "ETF_TRICK")
    etf_canonical.insert(1, "entity_id", etf_canonical["etf_id"].astype(str))
    etf_canonical.insert(
        2, "observation_id", etf_canonical["bar_id"].astype(str)
    )
    ix_canonical = ix.copy()
    ix_canonical.insert(0, "entity_type", "MARKET_INDEX")
    ix_canonical.insert(1, "entity_id", "IX0001")
    ix_canonical.insert(
        2,
        "observation_id",
        pd.to_datetime(ix_canonical["date"]).dt.strftime("%Y-%m-%d"),
    )
    canonical = pd.concat([etf_canonical, ix_canonical], ignore_index=True, sort=False)
    return etf, ix, canonical


def _split_cutoffs(boundaries: AFMLBoundaries) -> dict[str, dict[str, object]]:
    train_end = pd.Timestamp(boundaries.train_end)
    validation_end = pd.Timestamp(boundaries.validation_end)
    test_end = pd.Timestamp(boundaries.test_end)
    return {
        "train": {
            "observation_start": pd.Timestamp(boundaries.train_start),
            "observation_end": train_end,
            "decision_cutoff": _date_end_taipei(train_end),
        },
        "validation": {
            "observation_start": train_end + pd.Timedelta(days=1),
            "observation_end": validation_end,
            "decision_cutoff": _date_end_taipei(validation_end),
        },
        "test": {
            "observation_start": validation_end + pd.Timedelta(days=1),
            "observation_end": test_end,
            "decision_cutoff": _date_end_taipei(test_end),
        },
    }


def _build_readiness(
    ids: tuple[str, ...],
    bars: DollarBarTables,
    selections: dict[str, list[FFDSelection]],
    ffd_series: pd.DataFrame,
    features: pd.DataFrame,
    labels: pd.DataFrame,
    source_identity: dict[str, Any],
    config: AFMLConfig,
    mode: str,
) -> dict[str, Any]:
    revision_status = str(source_identity["source_revision_status"])
    coverage = source_identity.get("coverage", {})
    coverage_ready = bool(coverage) and all(
        bool(coverage.get(name))
        for name in (
            "daily_etf_complete",
            "ix0001_complete",
            "trading_calendar_complete",
        )
    )
    etf_rows: dict[str, dict[str, object]] = {}
    core_ready = coverage_ready
    for etf_id in ids:
        etf_bars = bars.dollar_bars[bars.dollar_bars["etf_id"].eq(etf_id)]
        bar_count = len(etf_bars)
        calibration_bar_count = int(
            etf_bars["bar_role"].eq("CALIBRATION_HISTORY").sum()
        )
        ffd_ready = bool(selections.get(etf_id)) and all(
            item.status == "stationarity_reached" for item in selections[etf_id]
        )
        ffd_rows = ffd_series[ffd_series["etf_id"].eq(etf_id)]
        feature_rows = features[features["etf_id"].eq(etf_id)]
        resolved = labels[
            labels["etf_id"].eq(etf_id) & labels["label"].notna()
        ]
        quality_failures = int(
            etf_bars.get(
                "source_quality_flag", pd.Series(False, index=etf_bars.index)
            )
            .fillna(True)
            .astype(bool)
            .sum()
        )
        bar_count_ready = calibration_bar_count >= config.dollar_bar.min_completed_bars
        ffd_coverage_ready = len(ffd_rows) >= config.ffd.min_adf_observations
        feature_coverage_ready = len(feature_rows) == bar_count
        ready = (
            coverage_ready
            and bar_count_ready
            and ffd_ready
            and ffd_coverage_ready
            and feature_coverage_ready
            and not resolved.empty
            and quality_failures == 0
        )
        core_ready &= ready
        etf_rows[etf_id] = {
            "bar_count": bar_count,
            "calibration_bar_count": calibration_bar_count,
            "required_calibration_bar_count": config.dollar_bar.min_completed_bars,
            "bar_count_ready": bar_count_ready,
            "ffd_ready": ffd_ready,
            "ffd_series_row_count": len(ffd_rows),
            "required_ffd_row_count": config.ffd.min_adf_observations,
            "ffd_coverage_ready": ffd_coverage_ready,
            "feature_row_count": len(feature_rows),
            "feature_coverage_ready": feature_coverage_ready,
            "resolved_label_count": len(resolved),
            "source_quality_failure_count": quality_failures,
            "source_coverage_ready": coverage_ready,
            "core_ready": ready,
        }
    limitations = [
        "VPIN_UNAVAILABLE_SOURCE_GRAIN",
        "KYLE_LAMBDA_UNAVAILABLE_SOURCE_GRAIN",
        "ATR_UNAVAILABLE_SOURCE_GRAIN",
        "ADX_UNAVAILABLE_SOURCE_GRAIN",
        "VIX_UNAVAILABLE_SOURCE_GRAIN",
    ]
    if revision_status != "PIT_REVISION_VERIFIED":
        limitations.append("PIT_REVISION_UNVERIFIED")
    if not coverage.get("trading_calendar_manifest_coverage_declared", False):
        limitations.append("TRADING_CALENDAR_MANIFEST_COVERAGE_UNDECLARED")
    if mode == "research_full_history":
        status = "CORE_DESCRIPTIVE_ONLY" if core_ready else "NOT_READY"
    elif (
        core_ready
        and revision_status == "PIT_REVISION_VERIFIED"
        and coverage.get("trading_calendar_manifest_coverage_declared", False)
    ):
        status = "CORE_READY"
    elif core_ready:
        status = "CORE_READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS"
    else:
        status = "NOT_READY"
    return {
        "status": status,
        "core_ready": core_ready,
        "finalized": False,
        "coverage": coverage,
        "source_revision_status": revision_status,
        "limitations": limitations,
        "etfs": etf_rows,
    }


def _build_metadata(
    ids: tuple[str, ...],
    boundaries: AFMLBoundaries,
    config: AFMLConfig,
    mode: str,
    full_scope: bool,
    inputs,
    calibrations: tuple[QCalibration, ...],
    selections: dict[str, list[FFDSelection]],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    cutoffs = _split_cutoffs(boundaries)
    return {
        "schema_version": AFMLDataset.SCHEMA_VERSION,
        "config_sha256": config_sha256(config),
        "mode": mode,
        "scope": "FULL_HISTORY_ACCEPTANCE" if full_scope else "BOUNDED_TEST",
        "readiness_scope": (
            "DESCRIPTIVE_ONLY" if mode == "research_full_history" else "ML_ELIGIBLE"
        ),
        "readiness_status": readiness["status"],
        "readiness_finalized": False,
        "etf_ids": list(ids),
        "train_start": boundaries.train_start.isoformat(),
        "train_end": boundaries.train_end.isoformat(),
        "validation_end": boundaries.validation_end.isoformat(),
        "test_end": boundaries.test_end.isoformat(),
        "train_decision_cutoff": cutoffs["train"]["decision_cutoff"].isoformat(),
        "validation_decision_cutoff": cutoffs["validation"][
            "decision_cutoff"
        ].isoformat(),
        "test_decision_cutoff": cutoffs["test"]["decision_cutoff"].isoformat(),
        "trading_sessions": pd.to_datetime(inputs.trading_calendar["date"])
        .dt.strftime("%Y-%m-%d")
        .tolist(),
        "calibration_scope": (
            "full_history_descriptive"
            if mode == "research_full_history"
            else "train_only"
        ),
        "calibrations": [
            {
                "q_star": item.q_star,
                "calibration_version": item.calibration_version,
                "fit_start": item.fit_start.isoformat(),
                "fit_end": item.fit_end.isoformat(),
                "parameters_frozen_at": item.parameters_frozen_at.isoformat(),
                "calibration_effective_at": (
                    item.calibration_effective_at.isoformat()
                    if pd.notna(item.calibration_effective_at)
                    else None
                ),
            }
            for item in calibrations
        ],
        "ffd_selections": {
            etf_id: [
                {
                    "d": selection.d,
                    "width": selection.width,
                    "status": selection.status,
                    "calibration_version": selection.calibration_version,
                }
                for selection in values
            ]
            for etf_id, values in selections.items()
        },
        "source_identity": inputs.source_identity,
    }


def _validate_retrain_dates(
    mode: str,
    values: tuple[str | pd.Timestamp, ...],
    boundaries: AFMLBoundaries,
) -> tuple[pd.Timestamp, ...]:
    parsed = tuple(sorted({pd.Timestamp(value).normalize() for value in values}))
    if mode != "walk_forward" and parsed:
        raise AFMLContractError("retrain_dates are only valid in walk_forward mode")
    for value in parsed:
        if not pd.Timestamp(boundaries.train_end) < value < pd.Timestamp(
            boundaries.test_end
        ):
            raise AFMLContractError("walk-forward retrain date must be after train_end and before test_end")
    return parsed


def _is_full_history_scope(
    ids: tuple[str, ...], boundaries: AFMLBoundaries
) -> bool:
    duration = pd.Timestamp(boundaries.test_end) - pd.Timestamp(boundaries.train_start)
    return set(ids) == set(ETF_IDS) and duration >= pd.Timedelta(days=15 * 365)


def _date_end_taipei(value: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize("Asia/Taipei") + pd.Timedelta(
        days=1
    ) - pd.Timedelta(nanoseconds=1)


def _result_row_count(value: object) -> int:
    if isinstance(value, pd.DataFrame):
        return len(value)
    if isinstance(value, DollarBarTables):
        return len(value.dollar_bars)
    if isinstance(value, tuple):
        return sum(_result_row_count(item) for item in value)
    if hasattr(value, "labels") and isinstance(value.labels, pd.DataFrame):
        return len(value.labels)
    if hasattr(value, "daily_etf") and isinstance(value.daily_etf, pd.DataFrame):
        return len(value.daily_etf)
    return 0
