from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any

import pyarrow.parquet as pq

from data_analysts.artifacts import ArtifactError, validate_manifest_fingerprint_structure
from data_analysts.adjusted_ohlc import ADJUSTMENT_POLICY_ID, empty_violation_counts
from data_analysts.adjusted_ohlc_evidence import (
    AdjustedOhlcEvidenceError,
    validate_ready_formal_evidence,
)
from data_analysts.config import ConfigError, RuntimeConfig, load_runtime_config
from data_analysts.diagnostics import write_diagnostic
from data_analysts.inspect import (
    check_raw_family_diagnostics,
    collect_formal_metrics,
    load_manifests,
)
from data_analysts.metadata import (
    load_active_snapshot_runtime_config,
    verify_config_snapshot_hashes,
)
from data_analysts.paths import DataAnalystsContext, PathBoundaryError
from data_analysts.pit import PitError, normalize_date
from data_analysts.store_audit import audit_store, coverage_regressions
from data_analysts.artifact_contracts import RunScope, expected_contract_outputs


LEAKAGE_TOKENS = ("future", "forward", "next", "realized", "outcome", "label_return")


class _SpillKeys:
    def __init__(self) -> None:
        self.connection = sqlite3.connect("")
        self.connection.execute("CREATE TABLE keys (kind TEXT, value TEXT, PRIMARY KEY(kind, value))")
        self.connection.execute("CREATE TABLE counts (kind TEXT, value TEXT, count INTEGER, PRIMARY KEY(kind, value))")

    def add(self, kind: str, key: tuple[Any, ...]) -> bool:
        value = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
        try:
            self.connection.execute("INSERT INTO keys VALUES (?, ?)", (kind, value))
            return True
        except sqlite3.IntegrityError:
            return False

    def increment(self, kind: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO counts VALUES (?, ?, 1) ON CONFLICT(kind, value) DO UPDATE SET count=count+1",
            (kind, value),
        )

    def counts(self, kind: str):
        return self.connection.execute(
            "SELECT value, count FROM counts WHERE kind=? ORDER BY value", (kind,)
        )

    def close(self) -> None:
        self.connection.close()


def _iter_parquet_rows(path, columns=None):
    parquet = pq.ParquetFile(path)
    try:
        for batch in parquet.iter_batches(columns=columns, batch_size=65536):
            yield from batch.to_pylist()
    finally:
        parquet.close()


def verify_runtime(
    context: DataAnalystsContext,
    as_of_date: str | None = None,
    *,
    pre_publication_audit: dict[str, Any] | None = None,
    run_scope: RunScope | None = None,
    audit_contract_keys: set[str] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    manifests = load_manifests(context)
    metrics = _verification_metrics(context, manifests)
    metadata_exists = context.store_path("metadata", "data_store_manifest.json").exists()

    if metadata_exists:
        metadata_error, metadata_actions = _metadata_gate(context, metrics)
        if metadata_error is not None:
            result = _blocked(
                "metadata",
                metadata_error,
                metadata_actions,
                as_of_date,
                checks,
                metrics,
            )
            _write_verification_result(context, result)
            return result
        try:
            config = load_active_snapshot_runtime_config(context)
        except (ConfigError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            result = _blocked(
                "metadata",
                f"invalid active config snapshot: {exc}",
                ["run a DataAnalysts refresh before verify"],
                as_of_date,
                checks,
                metrics,
            )
            _write_verification_result(context, result)
            return result
    else:
        try:
            config = load_runtime_config(context)
        except ConfigError as exc:
            result = _blocked("config", str(exc), ["fix configs/*.json"], as_of_date, checks)
            _write_verification_result(context, result)
            return result

        metadata_error, metadata_actions = _metadata_gate(context, metrics)
        if metadata_error is not None:
            result = _blocked(
                "metadata",
                metadata_error,
                metadata_actions,
                as_of_date,
                checks,
                metrics,
            )
            _write_verification_result(context, result)
            return result

    pit_foundation = _pit_foundation_metrics(config)
    write_diagnostic(context, "pit_foundation/source_catalog", pit_foundation)
    if _pit_foundation_blocked(pit_foundation):
        result = _blocked(
            "pit_foundation",
            "PIT foundation source catalog checks failed",
            ["fix PIT source catalog fields and forbidden source usage"],
            as_of_date,
            checks,
            metrics,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    raw_error, raw_metrics = check_raw_family_diagnostics(context)
    checks.append(
        {
            "check": "raw_family_diagnostics",
            "status": "ready" if raw_error is None else "blocked",
            **raw_metrics,
        }
    )
    if raw_error:
        result = _blocked(
            "raw_family_diagnostics",
            raw_error,
            ["fix raw family diagnostics and rebuild affected families"],
            as_of_date,
            checks,
            metrics,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    if metrics["manifest_count"] == 0:
        result = _blocked(
            "manifests",
            "missing data_store/manifests",
            ["run a DataAnalysts refresh before verify"],
            as_of_date,
            checks,
            metrics,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    checks.append(
        {
            "check": "formal_data_store_metrics",
            "status": "ready" if _path_metrics_clear(metrics) else "blocked",
            **metrics,
        }
    )
    if not _path_metrics_clear(metrics):
        result = _blocked(
            "manifest_paths",
            _path_error_message(metrics),
            ["fix manifest artifact_paths"],
            as_of_date,
            checks,
            metrics,
        )
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    # Validate manifest-local contracts first so the blocked step identifies
    # the semantic defect rather than a downstream inventory symptom.
    for manifest in manifests:
        path_error = _check_manifest_paths(context, manifest)
        if path_error:
            result = _blocked(
                "manifest_paths",
                path_error,
                ["fix manifest artifact_paths"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result
        try:
            validate_manifest_fingerprint_structure(manifest, allow_legacy=True)
        except ArtifactError as exc:
            result = _blocked(
                "manifest_fingerprints",
                str(exc),
                ["repair or republish the affected manifest"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result
        selected_pit_error = _check_selected_pit_manifest(context, manifest)
        if selected_pit_error:
            result = _blocked(
                "selected_pit_artifacts",
                selected_pit_error,
                ["rebuild selected PIT artifacts from PIT-safe source rows"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result
        security_panel_error = _check_security_panel_manifest(context, manifest)
        if security_panel_error:
            blocked_step = (
                "security_panel_history"
                if manifest.get("artifact_id") == "security_panel_history"
                else "security_panel"
            )
            result = _blocked(
                blocked_step,
                security_panel_error,
                ["rebuild security panel"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result
        universe_error = _check_universe_manifest(
            context, manifest, config.universe_specs
        )
        if universe_error:
            blocked_step = (
                "historical_universe"
                if _is_historical_universe_manifest(manifest)
                else "universe"
            )
            result = _blocked(
                blocked_step,
                universe_error,
                ["rebuild universe membership"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

    store_audit = audit_store(
        context, config.artifact_contracts, contract_keys=audit_contract_keys
    )
    metrics.update(store_audit["metrics"])
    checks.append(
        {
            "check": "artifact_inventory",
            "status": store_audit["status"],
            "metrics": store_audit["metrics"],
            "issues": store_audit["issues"],
        }
    )
    if store_audit["status"] != "ready":
        first_issue = store_audit["issues"][0]
        result = _blocked(
            "artifact_inventory",
            first_issue["message"],
            ["repair the exact artifact inventory and republish its manifest"],
            as_of_date,
            checks,
            metrics,
        )
        result["artifact_audit"] = store_audit
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    if pre_publication_audit is not None:
        attestation_error, attested_scope = _validate_run_attestation(
            context, config, pre_publication_audit, run_scope
        )
        if attestation_error is not None:
            result = _blocked(
                "run_attestation",
                attestation_error,
                ["rerun publication through the matching DataAnalysts CLI transaction"],
                as_of_date,
                checks,
                metrics,
            )
            result["artifact_audit"] = store_audit
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result
    else:
        attested_scope = None
    regressions = coverage_regressions(
        pre_publication_audit,
        store_audit,
        run_scope=attested_scope if attested_scope is not None else run_scope,
    )
    metrics["coverage_regression_count"] = len(regressions)
    checks.append(
        {
            "check": "artifact_coverage",
            "status": "ready" if not regressions else "blocked",
            "regressions": regressions,
        }
    )
    if regressions:
        first = regressions[0]
        result = _blocked(
            "artifact_coverage",
            f"{first['artifact_id']} coverage regression: {first['message']}",
            ["restore the last verified coverage before publishing ready state"],
            as_of_date,
            checks,
            metrics,
        )
        result["artifact_audit"] = store_audit
        result["pit_foundation"] = pit_foundation
        _write_verification_result(context, result)
        return result

    for manifest in manifests:
        path_error = _check_manifest_paths(context, manifest)
        if path_error:
            result = _blocked(
                "manifest_paths",
                path_error,
                ["fix manifest artifact_paths"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

        try:
            validate_manifest_fingerprint_structure(manifest, allow_legacy=True)
        except ArtifactError as exc:
            result = _blocked(
                "manifest_fingerprints",
                str(exc),
                ["repair or republish the affected manifest"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

        adjusted_error, adjusted_check = _check_adjusted_ohlc_evidence(
            context, manifest, config.artifact_contracts
        )
        if adjusted_check is not None:
            checks.append(adjusted_check)
        if adjusted_error:
            result = _blocked(
                "adjusted_ohlc",
                adjusted_error,
                [
                    "run certify-adjusted-ohlc --mode full",
                    "publish a ready adjusted OHLC candidate",
                ],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

        selected_pit_error = _check_selected_pit_manifest(context, manifest)
        if selected_pit_error:
            result = _blocked(
                "selected_pit_artifacts",
                selected_pit_error,
                ["rebuild selected PIT artifacts from PIT-safe source rows"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

        security_panel_error = _check_security_panel_manifest(context, manifest)
        if security_panel_error:
            blocked_step = (
                "security_panel_history"
                if manifest.get("artifact_id") == "security_panel_history"
                else "security_panel"
            )
            result = _blocked(
                blocked_step,
                security_panel_error,
                ["rebuild security panel"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

        universe_error = _check_universe_manifest(context, manifest, config.universe_specs)
        if universe_error:
            blocked_step = (
                "historical_universe" if _is_historical_universe_manifest(manifest) else "universe"
            )
            result = _blocked(
                blocked_step,
                universe_error,
                ["rebuild universe membership"],
                as_of_date,
                checks,
                metrics,
            )
            result["pit_foundation"] = pit_foundation
            _write_verification_result(context, result)
            return result

    result = {
        "status": "ready",
        "checked_at": _utc_now(),
        "scope": as_of_date or "all",
        "metrics": dict(metrics),
        "path_metrics": dict(metrics),
        "checks": checks,
        "pit_foundation": pit_foundation,
        "artifact_audit": store_audit,
    }
    if pre_publication_audit is not None:
        consume_error = _consume_run_attestation(
            context, config, pre_publication_audit, run_scope
        )
        if consume_error is not None:
            blocked = _blocked(
                "run_attestation", consume_error,
                ["rerun the publication transaction"],
                as_of_date, checks, metrics,
            )
            _write_verification_result(context, blocked)
            return blocked
        pipeline_state = json.loads(
            context.store_path("jobs", "pipeline_result.json").read_text(encoding="utf-8")
        )
        result["run_id"] = pipeline_state.get("run_id")
        result["run_attestation"] = pipeline_state.get("run_attestation")
    _write_verification_result(context, result)
    return result


def _validate_run_attestation(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    pre_publication_audit: dict[str, Any],
    expected_scope: RunScope | None,
) -> tuple[str | None, RunScope | None]:
    try:
        pipeline = json.loads(context.store_path("jobs", "pipeline_result.json").read_text(encoding="utf-8"))
        current = json.loads(context.store_path("jobs", "current_run.json").read_text(encoding="utf-8"))
        metadata = json.loads(context.store_path("metadata", "data_store_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"missing or invalid persisted run attestation: {exc}", None
    attestation = pipeline.get("run_attestation")
    current_attestation = current.get("run_attestation")
    if (
        not isinstance(attestation, dict)
        or attestation.get("status") != "verifying"
        or current_attestation != attestation
        or pipeline.get("status") != "verifying"
        or pipeline.get("phase") != "verify"
        or current.get("status") != "verifying"
        or current.get("phase") != "verify"
    ):
        return "persisted run state is not one matching verifying attestation", None
    run_id = attestation.get("run_id")
    if not isinstance(run_id, str) or pipeline.get("run_id") != run_id or current.get("run_id") != run_id:
        return "stale or mismatched run_id in persisted run state", None
    scope = attestation.get("run_scope")
    if scope not in {"full_history", "bounded_backfill", "daily"}:
        return "persisted run attestation has invalid scope", None
    if expected_scope is not None and scope != expected_scope:
        return "caller run_scope does not match persisted run attestation", None
    selected = attestation.get("selected_families")
    enabled = attestation.get("enabled_families")
    pipeline_families = pipeline.get("families")
    current_families = current.get("selected_families")
    if (
        not isinstance(selected, list)
        or not all(isinstance(family, str) for family in selected)
        or not isinstance(enabled, list)
        or not all(isinstance(family, str) for family in enabled)
        or not isinstance(pipeline_families, list)
        or not isinstance(current_families, list)
        or selected != sorted(set(selected))
        or selected != sorted(pipeline_families)
        or selected != sorted(current_families)
        or enabled != sorted(config.family_ids)
        or any(family not in enabled for family in selected)
    ):
        return "family sets do not match persisted run intent", None
    expected_matrix = expected_contract_outputs(
        config.artifact_contracts, selected
    )
    expected_matrix_payload = {
        family_id: list(keys)
        for family_id, keys in sorted(expected_matrix.items())
    }
    expected_keys = sorted(
        {key for keys in expected_matrix.values() for key in keys}
    )
    attested_expected = attestation.get("expected_contract_keys")
    attested_changed = attestation.get("changed_contract_keys")
    if (
        attestation.get("expected_outputs_by_family")
        != expected_matrix_payload
        or attested_expected != expected_keys
        or not isinstance(attested_changed, list)
        or attested_changed != sorted(set(attested_changed))
        or any(key not in config.artifact_contracts for key in attested_changed)
        or (scope == "full_history" and attested_changed != expected_keys)
    ):
        return "expected and changed outputs do not match registry run intent", None
    if metadata.get("config_hashes") != attestation.get("config_hashes"):
        return "active config hashes do not match persisted run attestation", None
    try:
        metadata_bytes = context.store_path("metadata", "data_store_manifest.json").read_bytes()
    except OSError as exc:
        return f"cannot re-read attested metadata: {exc}", None
    if hashlib.sha256(metadata_bytes).hexdigest() != attestation.get("metadata_sha256"):
        return "metadata changed after run attestation", None
    baseline_hash = hashlib.sha256(
        json.dumps(pre_publication_audit, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if baseline_hash != attestation.get("pre_publication_audit_sha256"):
        return "pre-publication audit does not match persisted run intent", None
    identities = attestation.get("manifest_identities")
    try:
        current_identities = _current_manifest_identities(context)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"cannot read exact formal manifest identity set: {exc}", None
    if (
        not isinstance(identities, list)
        or (config.artifact_contracts and not identities)
        or identities != current_identities
    ):
        return "formal manifest identity set does not match persisted run attestation", None
    return None, scope


def _current_manifest_identities(context: DataAnalystsContext) -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    root = context.store_path("manifests")
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        identities.append({
            "path": path.relative_to(context.data_store).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "artifact_id": payload.get("artifact_id"),
            "contract_key": payload.get("contract_key", payload.get("artifact_id")),
            "variant": payload.get("variant", "default"),
        })
    return identities


def _consume_run_attestation(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    pre_publication_audit: dict[str, Any],
    expected_scope: RunScope | None,
) -> str | None:
    try:
        owner_run_id = json.loads(
            context.store_path("jobs", "pipeline_result.json").read_text(encoding="utf-8")
        ).get("run_id", "unknown")
    except Exception:
        owner_run_id = "unknown"
    with _attestation_lock(context, str(owner_run_id)):
        error, _ = _validate_run_attestation(
            context, config, pre_publication_audit, expected_scope
        )
        if error is not None:
            return error
        pipeline_path = context.store_path("jobs", "pipeline_result.json")
        current_path = context.store_path("jobs", "current_run.json")
        pipeline_before = pipeline_path.read_bytes()
        current_before = current_path.read_bytes()
        pipeline = json.loads(pipeline_before.decode("utf-8"))
        current = json.loads(current_before.decode("utf-8"))
        verified = {**pipeline["run_attestation"], "status": "verified"}
        pipeline.update({
            "status": "ready", "phase": "complete", "run_attestation": verified,
        })
        current.update({
            "status": "ready", "phase": "complete", "current_family": None,
            "run_attestation": verified,
        })
        recovery_path = context.store_path(
            "jobs", f"attestation_consume_recovery_{verified['run_id']}.json"
        )
        recovery = {
            "run_id": verified["run_id"],
            "status": "prepared",
            "pipeline_before": pipeline_before.decode("utf-8"),
            "current_before": current_before.decode("utf-8"),
        }
        _atomic_write_bytes(
            recovery_path, json.dumps(recovery, indent=2, sort_keys=True).encode("utf-8")
        )
        try:
            from data_analysts.artifacts import atomic_write_text
            atomic_write_text(current_path, json.dumps(current, indent=2, sort_keys=True))
            atomic_write_text(pipeline_path, json.dumps(pipeline, indent=2, sort_keys=True))
        except Exception as exc:
            restore_errors = []
            for path, before in (
                (current_path, current_before), (pipeline_path, pipeline_before)
            ):
                try:
                    _atomic_write_bytes(path, before)
                except Exception as restore_exc:
                    restore_errors.append(f"{path.name}: {restore_exc}")
            if not restore_errors:
                recovery_path.unlink(missing_ok=True)
            recovery_note = (
                f"; recovery={recovery_path}; restore_errors={restore_errors}"
                if restore_errors else ""
            )
            return f"failed to atomically consume run attestation: {exc}{recovery_note}"
        recovery_path.unlink(missing_ok=True)
        return None


@contextmanager
def _attestation_lock(context: DataAnalystsContext, owner_run_id: str):
    path = context.store_path("jobs", "run_attestation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        deadline = time.monotonic() + 30.0
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out acquiring run attestation lock")
                    time.sleep(0.05)
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps({
                    "owner_pid": os.getpid(), "run_id": owner_run_id,
                    "acquired_at": _utc_now(),
                }).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out acquiring run attestation lock")
                    time.sleep(0.05)
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps({
                    "owner_pid": os.getpid(), "run_id": owner_run_id,
                    "acquired_at": _utc_now(),
                }).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_bytes(path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staging.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _pit_foundation_metrics(config: RuntimeConfig) -> dict[str, object]:
    catalog = config.source_catalog
    registry = config.pit_registry
    sources = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    forbidden = [item for item in catalog.get("forbidden_sources", []) if isinstance(item, dict)]
    missing_pit = [item.get("family_id") for item in sources if not item.get("pit_field")]
    missing_key = [item.get("family_id") for item in sources if not item.get("logical_key")]
    return {
        "forbidden_source_count": len(forbidden),
        "approved_source_count": len(sources),
        "pit_registry_family_count": len(registry.get("families", {})),
        "missing_pit_field_count": len(missing_pit),
        "missing_logical_key_count": len(missing_key),
        "missing_pit_field_families": missing_pit,
        "missing_logical_key_families": missing_key,
    }


def _pit_foundation_blocked(metrics: dict[str, object]) -> bool:
    return (
        metrics["missing_pit_field_count"] != 0
        or metrics["missing_logical_key_count"] != 0
    )


def _blocked(
    blocked_step: str,
    message: str,
    next_actions: list[str],
    as_of_date: str | None,
    checks: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "status": "blocked",
        "checked_at": _utc_now(),
        "scope": as_of_date or "all",
        "blocked_step": blocked_step,
        "message": message,
        "next_actions": next_actions,
        "checks": checks,
    }
    if metrics is not None:
        result["metrics"] = dict(metrics)
        result["path_metrics"] = dict(metrics)
    return result


def _write_verification_result(context: DataAnalystsContext, result: dict[str, Any]) -> None:
    result_path = context.store_path("jobs", "verification_result.json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def _check_manifest_paths(context: DataAnalystsContext, manifest: dict[str, Any]) -> str | None:
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list) or not artifact_paths:
        return f"manifest {manifest.get('artifact_id')} has no artifact_paths"
    for artifact_path in artifact_paths:
        if not isinstance(artifact_path, str):
            return f"manifest {manifest.get('artifact_id')} has non-string artifact_path"
        try:
            resolved = context.artifact_path(artifact_path)
        except PathBoundaryError:
            return f"manifest {manifest.get('artifact_id')} artifact path resolves outside DataAnalysts root"
        if not resolved.exists():
            return f"manifest {manifest.get('artifact_id')} artifact path does not exist: {artifact_path}"
    return None


def _check_adjusted_ohlc_evidence(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
    contracts: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    if manifest.get("artifact_id") != "daily_price_volume":
        return None, None

    check: dict[str, Any] = {
        "check": "adjusted_ohlc",
        "status": "blocked",
        "artifact_id": "daily_price_volume",
        "adjustment_policy_id": ADJUSTMENT_POLICY_ID,
        "formal_evidence_pointer": "diagnostics/adjusted_ohlc_verification.json",
        "formal_evidence_sha256": None,
        "verified_partition_count": 0,
        "violation_totals": empty_violation_counts(),
    }

    def blocked(message: str) -> tuple[str, dict[str, Any]]:
        check["message"] = message
        return message, check

    if manifest.get("adjustment_policy_id") != ADJUSTMENT_POLICY_ID:
        return blocked("daily_price_volume manifest has no approved adjustment policy")

    evidence_path = context.store_path(
        "diagnostics", "adjusted_ohlc_verification.json"
    )
    if not evidence_path.exists():
        return blocked("missing formal adjusted OHLC evidence")
    try:
        evidence_bytes = evidence_path.read_bytes()
        check["formal_evidence_sha256"] = sha256(evidence_bytes).hexdigest()
        evidence = json.loads(evidence_bytes)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return blocked(f"invalid formal adjusted OHLC evidence: {exc}")
    if not isinstance(evidence, dict):
        return blocked("formal adjusted OHLC evidence must be a JSON object")
    try:
        ordered_paths, _ = validate_ready_formal_evidence(
            context, manifest, evidence, contracts
        )
    except AdjustedOhlcEvidenceError as exc:
        return blocked(f"invalid formal adjusted OHLC evidence: {exc}")
    violation_totals = evidence["violation_totals"]

    check.update(
        {
            "status": "ready",
            "verified_partition_count": len(ordered_paths),
            "violation_totals": dict(violation_totals),
        }
    )
    return None, check


def _verification_metrics(
    context: DataAnalystsContext,
    manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = dict(collect_formal_metrics(context, manifests))
    metrics.update(context.legacy_layout_status())
    return metrics


def _metadata_gate(
    context: DataAnalystsContext,
    metrics: dict[str, Any],
) -> tuple[str | None, list[str]]:
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    if not manifest_path.exists():
        return (
            "missing data_store/metadata/data_store_manifest.json",
            ["run a DataAnalysts refresh before verify"],
        )
    try:
        snapshot_metrics = verify_config_snapshot_hashes(context)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"invalid data_store metadata: {exc}",
            ["run a DataAnalysts refresh before verify"],
        )

    metrics["config_snapshot_file_count"] = snapshot_metrics["config_snapshot_file_count"]
    metrics["config_snapshot_hash_mismatch_count"] = snapshot_metrics[
        "config_snapshot_hash_mismatch_count"
    ]
    if snapshot_metrics["config_snapshot_missing_count"] != 0:
        return (
            "missing required config snapshot file",
            ["run the formalized pipeline to republish metadata", "inspect data_store/metadata/config_snapshot"],
        )
    if snapshot_metrics["config_snapshot_hash_mismatch_count"] != 0:
        return (
            "config snapshot hash mismatch",
            ["run the formalized pipeline to republish metadata", "inspect data_store/metadata/config_snapshot"],
        )
    return None, []


def _path_metrics_clear(metrics: dict[str, Any]) -> bool:
    return (
        metrics["absolute_artifact_path_count"] == 0
        and metrics["artifact_path_escape_count"] == 0
        and metrics["forbidden_path_segment_count"] == 0
        and metrics["required_manifest_missing_count"] == 0
    )


def _path_error_message(metrics: dict[str, Any]) -> str:
    if metrics["absolute_artifact_path_count"] != 0:
        return "manifest artifact_paths include absolute paths"
    if metrics["artifact_path_escape_count"] != 0:
        return "manifest artifact_paths escape data_store"
    if metrics["forbidden_path_segment_count"] != 0:
        return "manifest artifact_paths include forbidden path segments"
    if metrics["required_manifest_missing_count"] != 0:
        return "missing required manifest"
    return "manifest artifact_paths failed validation"


def _check_security_panel_manifest(context: DataAnalystsContext, manifest: dict[str, Any]) -> str | None:
    spill = _SpillKeys()
    try:
        return _check_security_panel_manifest_with_spill(context, manifest, spill)
    finally:
        spill.close()


def _check_security_panel_manifest_with_spill(
    context: DataAnalystsContext, manifest: dict[str, Any], spill: _SpillKeys
) -> str | None:
    if manifest.get("artifact_id") not in {"security_panel", "security_panel_history"}:
        return None
    columns = manifest.get("columns") or []
    leakage_columns = [
        column
        for column in columns
        if isinstance(column, str) and any(token in column.lower() for token in LEAKAGE_TOKENS)
    ]
    if leakage_columns:
        return f"security panel contains leakage columns: {', '.join(leakage_columns)}"
    if manifest.get("artifact_id") != "security_panel_history":
        return None
    required = {"as_of_date", "effective_date", "ticker"}
    for artifact_path in manifest.get("artifact_paths", []):
        for row in _iter_parquet_rows(context.artifact_path(artifact_path)):
            missing = sorted(field for field in required if row.get(field) in {None, ""})
            if missing:
                return (
                    "security_panel_history missing required fields: "
                    f"{', '.join(missing)}"
                )
            as_of_date = str(row["as_of_date"])
            effective_date = str(row["effective_date"])
            if effective_date <= as_of_date:
                return "security_panel_history has effective_date <= as_of_date"
            panel_key = (row.get("as_of_date"), row.get("ticker"))
            if not spill.add("panel", panel_key):
                return "duplicate security_panel_history as_of_date ticker key"
    return None


def _check_selected_pit_manifest(context: DataAnalystsContext, manifest: dict[str, Any]) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if artifact_id not in {"financial_statement_pit_selected", "self_reported_numbers_pit_selected"}:
        return None
    required_columns = {"source_available_date", "decision_date"}
    for artifact_path in manifest.get("artifact_paths", []):
        path = context.artifact_path(artifact_path)
        parquet_file = pq.ParquetFile(path)
        try:
            columns = set(parquet_file.schema.names)
        finally:
            parquet_file.close()
        missing = sorted(required_columns - columns)
        if missing:
            return f"selected PIT artifact {artifact_id} missing required columns: {', '.join(missing)}"
        for index, row in enumerate(
            _iter_parquet_rows(path, columns=sorted(required_columns))
        ):
            try:
                source_available_date = normalize_date(row.get("source_available_date"))
                decision_date = normalize_date(row.get("decision_date"))
            except PitError as exc:
                return f"selected PIT artifact {artifact_id} has invalid PIT date at row {index}: {exc}"
            if source_available_date is None:
                return f"selected PIT artifact {artifact_id} has blank source_available_date at row {index}"
            if decision_date is None:
                return f"selected PIT artifact {artifact_id} has blank decision_date at row {index}"
            if source_available_date > decision_date:
                return (
                    f"selected PIT artifact {artifact_id} has source_available_date > decision_date "
                    f"at row {index}: {source_available_date} > {decision_date}"
                )
    return None


def _check_universe_manifest(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
    universe_specs: dict[str, Any],
) -> str | None:
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("universe_"):
        return None
    if _is_historical_universe_manifest(manifest):
        return _check_historical_universe_manifest(context, manifest, universe_specs)
    spill = _SpillKeys()
    try:
        for artifact_path in manifest.get("artifact_paths", []):
            for row in _iter_parquet_rows(context.artifact_path(artifact_path)):
                membership_key = (row.get("as_of_date"), row.get("universe_id"), row.get("ticker"))
                if not spill.add("membership", membership_key):
                    return "duplicate membership key in universe artifact"
                rank_key = (row.get("as_of_date"), row.get("universe_id"), row.get("rank"))
                if not spill.add("rank", rank_key):
                    return "duplicate rank in universe artifact"
        return None
    finally:
        spill.close()


def _is_historical_universe_manifest(manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("partitioning") == ["as_of_year"]
        or manifest.get("pit_policy") == "effective_next_trading_day_membership"
    )


def _check_historical_universe_manifest(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
    universe_specs: dict[str, Any],
) -> str | None:
    spill = _SpillKeys()
    try:
        return _check_historical_universe_manifest_with_spill(
            context, manifest, universe_specs, spill
        )
    finally:
        spill.close()


def _check_historical_universe_manifest_with_spill(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
    universe_specs: dict[str, Any],
    spill: _SpillKeys,
) -> str | None:
    artifact_id = str(manifest["artifact_id"])
    universe_id = artifact_id.removeprefix("universe_")
    limit = _universe_top_n_limit(universe_specs, universe_id)
    small_file_daily_partition_count = 0
    required = {"as_of_date", "effective_date", "universe_id", "ticker", "rank"}
    for artifact_path in manifest.get("artifact_paths", []):
        artifact_path_text = str(artifact_path)
        if "membership_by_date" in artifact_path_text and "as_of_date=" in artifact_path_text:
            small_file_daily_partition_count += 1
            continue
        for row in _iter_parquet_rows(context.artifact_path(artifact_path)):
            missing = sorted(field for field in required if row.get(field) in {None, ""})
            if missing:
                return (
                    f"historical universe {artifact_id} missing required fields: "
                    f"{', '.join(missing)}"
                )
            as_of_date = str(row["as_of_date"])
            effective_date = str(row["effective_date"])
            if effective_date <= as_of_date:
                return f"historical universe {artifact_id} has effective_date <= as_of_date"
            membership_key = (row.get("effective_date"), row.get("universe_id"), row.get("ticker"))
            if not spill.add("membership", membership_key):
                return "duplicate historical universe effective membership key"
            rank_key = (row.get("effective_date"), row.get("universe_id"), row.get("rank"))
            if not spill.add("rank", rank_key):
                return "duplicate historical universe effective rank"
            spill.increment("effective", effective_date)
    if small_file_daily_partition_count > 0:
        return f"historical universe {artifact_id} has small_file_daily_partition_count > 0"
    if isinstance(limit, int):
        for effective_date, row_count in spill.counts("effective"):
            if row_count > limit:
                return (
                    f"historical universe {artifact_id} row_count per effective_date exceeds top-n limit: "
                    f"{effective_date} has {row_count} rows > {limit}"
                )
        diagnostics_error = _check_historical_universe_diagnostics(context, manifest, limit)
        if diagnostics_error:
            return diagnostics_error
    return None


def _universe_top_n_limit(universe_specs: dict[str, Any], universe_id: str) -> int | None:
    for spec in universe_specs.get("universes", []):
        if isinstance(spec, dict) and spec.get("universe_id") == universe_id:
            limit = spec.get("limit")
            return limit if isinstance(limit, int) else None
    return None


def _check_historical_universe_diagnostics(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
    limit: int,
) -> str | None:
    diagnostics_path = _historical_universe_diagnostics_path(context, manifest)
    if diagnostics_path is None or not diagnostics_path.exists():
        return f"historical universe {manifest.get('artifact_id')} missing diagnostics"
    rows = iter(_iter_parquet_rows(diagnostics_path))
    row = next(rows, None)
    extra = next(rows, None)
    if row is None or extra is not None:
        if row is None:
            return f"historical universe {manifest.get('artifact_id')} has empty diagnostics"
        return (
            f"historical universe {manifest.get('artifact_id')} must have exactly one diagnostics row"
        )
    required_counters = {
        "as_of_date_count",
        "candidate_count",
        "included_count",
        "excluded_count",
        "top_n_limit",
        "max_included_count",
        "duplicate_universe_effective_ticker_count",
        "duplicate_universe_effective_rank_count",
        "top_n_underfilled_date_count",
    }
    missing_counters = sorted(counter for counter in required_counters if counter not in row)
    if missing_counters:
        return (
            f"historical universe {manifest.get('artifact_id')} missing required diagnostics counters: "
            f"{', '.join(missing_counters)}"
        )
    invalid_counter_types = sorted(
        counter for counter in required_counters if type(row.get(counter)) is not int
    )
    if invalid_counter_types:
        return (
            f"historical universe {manifest.get('artifact_id')} has invalid diagnostics counter type: "
            f"{', '.join(invalid_counter_types)}"
        )
    duplicate_membership_count = row.get("duplicate_universe_effective_ticker_count")
    duplicate_rank_count = row.get("duplicate_universe_effective_rank_count")
    if (
        isinstance(duplicate_membership_count, int)
        and duplicate_membership_count > 0
    ) or (
        isinstance(duplicate_rank_count, int)
        and duplicate_rank_count > 0
    ):
        return (
            f"historical universe {manifest.get('artifact_id')} has duplicate diagnostics counters: "
            f"membership={duplicate_membership_count}, rank={duplicate_rank_count}"
        )
    underfilled_date_count = row.get("top_n_underfilled_date_count")
    if isinstance(underfilled_date_count, int) and underfilled_date_count > 0:
        return (
            f"historical universe {manifest.get('artifact_id')} has underfilled top-n dates: "
            f"{underfilled_date_count}"
        )
    max_included_count = row.get("max_included_count")
    if isinstance(max_included_count, int) and max_included_count > limit:
        return (
            f"historical universe {manifest.get('artifact_id')} diagnostics max_included_count "
            f"exceeds top_n_limit: {max_included_count} > {limit}"
        )
    candidate_count = row.get("candidate_count")
    as_of_date_count = row.get("as_of_date_count")
    if (
        isinstance(candidate_count, int)
        and isinstance(max_included_count, int)
        and isinstance(as_of_date_count, int)
        and as_of_date_count == 1
        and candidate_count >= limit
        and max_included_count != limit
    ):
        return (
            f"historical universe {manifest.get('artifact_id')} included_count must equal top_n_limit "
            f"when eligible_count >= limit"
        )
    return None


def _historical_universe_diagnostics_path(
    context: DataAnalystsContext,
    manifest: dict[str, Any],
):
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list):
        return None
    for artifact_path in artifact_paths:
        if not isinstance(artifact_path, str):
            continue
        marker = "/membership_by_year/"
        normalized = artifact_path.replace("\\", "/")
        if marker not in normalized:
            continue
        universe_root = normalized.split(marker, 1)[0]
        return context.artifact_path(f"{universe_root}/diagnostics/diagnostics.parquet")
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
