from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from data_analysts.metadata import load_data_store_metadata, verify_config_snapshot_hashes
from data_analysts.metadata import load_active_snapshot_runtime_config
from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import (
    FORBIDDEN_ARTIFACT_PATH_SEGMENTS,
    DataAnalystsContext,
    PathBoundaryError,
    is_absolute_artifact_path,
)
from data_analysts.store_audit import audit_store

FORMAL_MANIFEST_REPLACEMENTS = {
    "dividend_policy": "dividend_events",
    "capital_formation": "capital_action_events",
}

RAW_EXPANSION_FAMILY_IDS = {
    "trading_calendar",
    "daily_tradability",
    "daily_chip",
    "monthly_sales",
    "financial_statement_raw",
    "self_reported_numbers_raw",
    "director_supervisor_holdings",
    "board_reelection_statistics",
    "executive_change_events",
    "merger_acquisition_events",
    "private_placement_relation_events",
    "insider_transfer_completed",
    "insider_transfer_declared_not_completed",
    "treasury_stock_events",
    "taiwan_index_futures_near_month",
}


def inspect_artifacts(context: DataAnalystsContext, as_of_date: str | None = None) -> dict[str, Any]:
    artifacts, metrics = load_manifest_artifacts(context)
    try:
        config = (
            load_active_snapshot_runtime_config(context)
            if context.store_path("metadata", "data_store_manifest.json").exists()
            else load_runtime_config(context)
        )
        store_audit = audit_store(context, config.artifact_contracts)
    except (ConfigError, FileNotFoundError, ValueError, json.JSONDecodeError, PathBoundaryError) as exc:
        store_audit = {
            "status": "unavailable",
            "metrics": {},
            "artifacts": {},
            "issues": [{"check": "audit_config", "message": str(exc)}],
        }
    metrics.update(store_audit["metrics"])
    try:
        raw_error, raw_metrics = check_raw_family_diagnostics(context)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raw_error = f"invalid raw family diagnostics: {exc}"
        raw_metrics = {
            "family_count": 0,
            "raw_family_diagnostic_count": 0,
            "pit_parse_failure_count_total": 0,
            "unresolved_duplicate_count_total": 0,
            "diagnostic_error_count": 1,
            "diagnostic_error": str(exc),
        }
    historical_universe = summarize_historical_universe(artifacts, context)
    legacy_status = context.legacy_layout_status()
    return {
        "status": (
            "ready"
            if artifacts and store_audit["status"] == "ready" and raw_error is None
            else "blocked"
        ),
        "project_root": context.project_root.as_posix(),
        "data_store": context.data_store.as_posix(),
        "scope": as_of_date or "all",
        "legacy_layout_detected": any(legacy_status.values()),
        **legacy_status,
        **metrics,
        "artifacts": artifacts,
        "artifact_audit": store_audit,
        "historical_universe": historical_universe,
        "raw_family_diagnostics": {
            "status": "ready" if raw_error is None else "blocked",
            **raw_metrics,
        },
    }


def check_raw_family_diagnostics(context: DataAnalystsContext) -> tuple[str | None, dict[str, Any]]:
    diagnostics_dir = context.store_path("diagnostics", "raw_families")
    if not diagnostics_dir.exists():
        return None, {
            "family_count": 0,
            "raw_family_diagnostic_count": 0,
            "pit_parse_failure_count_total": 0,
            "unresolved_duplicate_count_total": 0,
        }
    totals = {
        "family_count": 0,
        "raw_family_diagnostic_count": 0,
        "pit_parse_failure_count_total": 0,
        "unresolved_duplicate_count_total": 0,
    }
    for path in sorted(diagnostics_dir.glob("*.json")):
        payload = _load_json_object(path)
        totals["family_count"] += 1
        totals["raw_family_diagnostic_count"] += 1
        totals["pit_parse_failure_count_total"] += int(
            payload.get("pit_parse_failure_count") or 0
        )
        totals["unresolved_duplicate_count_total"] += int(
            payload.get("unresolved_duplicate_count") or 0
        )
    if totals["pit_parse_failure_count_total"] != 0:
        return "raw family PIT parse failures are nonzero", totals
    if totals["unresolved_duplicate_count_total"] != 0:
        return "raw family unresolved duplicate count is nonzero", totals
    return None, totals


def load_manifest_artifacts(
    context: DataAnalystsContext,
) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    manifest_payloads = load_manifests(context)
    artifacts = [
        {
            "artifact_id": manifest.get("artifact_id"),
            "contract_key": manifest.get("contract_key", manifest.get("artifact_id")),
            "variant": manifest.get("variant", "default"),
            "status": manifest.get("status"),
            "row_count": manifest.get("row_count"),
            "date_range": manifest.get("date_range"),
            "availability_date_range": manifest.get("availability_date_range"),
            "partitioning": manifest.get("partitioning"),
            "pit_policy": manifest.get("pit_policy"),
            "artifact_paths": manifest.get("artifact_paths", []),
        }
        for manifest in manifest_payloads
    ]
    return artifacts, collect_formal_metrics(context, manifest_payloads)


def load_manifests(context: DataAnalystsContext) -> list[dict[str, Any]]:
    manifests_dir = context.store_path("manifests")
    if not manifests_dir.exists():
        return []
    return [
        _load_json_object(manifest_path)
        for manifest_path in sorted(manifests_dir.glob("*.json"))
    ]


def collect_formal_metrics(
    context: DataAnalystsContext,
    manifest_payloads: list[dict[str, Any]],
    *,
    enabled_family_ids: set[str] | None = None,
) -> dict[str, int | bool]:
    metrics: dict[str, int | bool] = {
        "artifact_path_count": 0,
        "absolute_artifact_path_count": 0,
        "artifact_path_escape_count": 0,
        "forbidden_path_segment_count": 0,
        "manifest_count": len(manifest_payloads),
        "required_manifest_missing_count": 0,
        "zero_row_required_family_count": 0,
        "config_snapshot_file_count": 0,
        "config_snapshot_hash_mismatch_count": 0,
    }
    for manifest in manifest_payloads:
        artifact_paths = manifest.get("artifact_paths")
        if not isinstance(artifact_paths, list):
            continue
        for artifact_path in artifact_paths:
            if not isinstance(artifact_path, str):
                continue
            metrics["artifact_path_count"] += 1
            _classify_artifact_path(metrics, artifact_path)
    if enabled_family_ids is None:
        enabled_family_ids = _enabled_family_ids_from_snapshot(context)
    required_manifest_missing_count, zero_row_required_family_count = _count_missing_required_manifests(
        context,
        manifest_payloads,
        enabled_family_ids,
    )
    metrics["required_manifest_missing_count"] = required_manifest_missing_count
    metrics["zero_row_required_family_count"] = zero_row_required_family_count

    try:
        snapshot_metrics = verify_config_snapshot_hashes(context)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        snapshot_metrics = None

    if snapshot_metrics is not None:
        metrics["config_snapshot_file_count"] = snapshot_metrics["config_snapshot_file_count"]
        metrics["config_snapshot_hash_mismatch_count"] = snapshot_metrics[
            "config_snapshot_hash_mismatch_count"
        ]
    return metrics


def _load_json_object(path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"JSON file is missing: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _classify_artifact_path(metrics: dict[str, int | bool], artifact_path: str) -> None:
    if is_absolute_artifact_path(artifact_path):
        metrics["absolute_artifact_path_count"] += 1
        return

    normalized = artifact_path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        metrics["artifact_path_escape_count"] += 1
        return
    if FORBIDDEN_ARTIFACT_PATH_SEGMENTS.intersection(parts):
        metrics["forbidden_path_segment_count"] += 1


def _count_missing_required_manifests(
    context: DataAnalystsContext,
    manifest_payloads: list[dict[str, Any]],
    enabled_family_ids: set[str],
) -> tuple[int, int]:
    manifest_ids = {
        artifact_id
        for manifest in manifest_payloads
        if isinstance((artifact_id := manifest.get("artifact_id")), str)
    }
    missing_count = 0
    for family_id in enabled_family_ids:
        required_artifact_id = FORMAL_MANIFEST_REPLACEMENTS.get(family_id, family_id)
        if required_artifact_id in manifest_ids:
            continue
        missing_count += 1
    # A zero-row diagnostic is not an active data contract. Empty-capable
    # artifacts must publish an explicit zero-row manifest; otherwise the
    # required artifact is missing and verification fails closed.
    return missing_count, 0


def _enabled_family_ids_from_snapshot(context: DataAnalystsContext) -> set[str]:
    try:
        metadata = load_data_store_metadata(context)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return set()

    snapshot_path = metadata.get("config_snapshot_path")
    if not isinstance(snapshot_path, str):
        return set()
    try:
        snapshot_dir = context.store_path(*PurePosixPath(snapshot_path.replace("\\", "/")).parts)
    except PathBoundaryError:
        return set()
    source_family_profiles_path = snapshot_dir / "source_family_profiles.json"
    if not source_family_profiles_path.exists():
        return set()
    try:
        payload = _load_json_object(source_family_profiles_path)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return set()
    families = payload.get("families")
    if not isinstance(families, list):
        return set()
    return {
        family_id
        for family in families
        if isinstance(family, dict)
        and family.get("enabled", True) is not False
        and isinstance((family_id := family.get("family_id")), str)
        and family_id
    }


def _load_raw_family_diagnostics_by_family(context: DataAnalystsContext) -> dict[str, dict[str, Any]]:
    diagnostics_dir = context.store_path("diagnostics", "raw_families")
    if not diagnostics_dir.exists():
        return {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for path in sorted(diagnostics_dir.glob("*.json")):
        try:
            diagnostics[path.stem] = _load_json_object(path)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            continue
    return diagnostics


def _diagnostic_proves_zero_rows(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        _is_explicit_zero_int(payload.get("source_row_count"))
        and _is_explicit_zero_int(payload.get("pit_parse_failure_count"))
        and _is_explicit_zero_int(payload.get("unresolved_duplicate_count"))
    )


def _is_explicit_zero_int(value: Any) -> bool:
    return type(value) is int and value == 0


def summarize_historical_universe(
    artifacts: list[dict[str, Any]], context: DataAnalystsContext
) -> dict[str, Any]:
    historical_manifests = [
        artifact
        for artifact in artifacts
        if _is_historical_universe_manifest(artifact)
    ]
    file_count = 0
    universe_ids: set[str] = set()
    date_values: list[str] = []
    small_file_daily_partition_count = 0

    for artifact in historical_manifests:
        artifact_id = artifact.get("artifact_id")
        if isinstance(artifact_id, str):
            universe_ids.add(artifact_id.removeprefix("universe_"))
        artifact_paths = artifact.get("artifact_paths")
        if isinstance(artifact_paths, list):
            file_count += len(artifact_paths)
            for artifact_path in artifact_paths:
                if not isinstance(artifact_path, str):
                    continue
                normalized = artifact_path.replace("\\", "/")
                if "membership_by_date/as_of_date=" in normalized:
                    small_file_daily_partition_count += 1
        date_range = artifact.get("date_range")
        if (
            isinstance(date_range, list)
            and len(date_range) == 2
            and all(isinstance(value, str) and value for value in date_range)
        ):
            date_values.extend(date_range)

    diagnostics_dir = context.store_path("diagnostics", "historical_universe")
    diagnostic_file_count = _diagnostic_file_count(diagnostics_dir)
    status = "blocked"
    if historical_manifests and small_file_daily_partition_count == 0:
        status = "ready"

    return {
        "status": status,
        "historical_universe_file_count": file_count,
        "historical_universe_count": len(universe_ids),
        "historical_universe_date_min": min(date_values) if date_values else None,
        "historical_universe_date_max": max(date_values) if date_values else None,
        "small_file_daily_partition_count": small_file_daily_partition_count,
        "diagnostic_file_count": diagnostic_file_count,
    }


def _is_historical_universe_manifest(artifact: dict[str, Any]) -> bool:
    artifact_id = artifact.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return False
    return artifact.get("partitioning") == ["as_of_year"]


def _diagnostic_file_count(diagnostics_dir: Path) -> int:
    if not diagnostics_dir.exists():
        return 0
    return sum(1 for path in diagnostics_dir.rglob("*.json") if path.is_file())
