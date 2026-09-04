from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import (
    ArtifactContract,
    empty_contract_schema_fingerprint,
)
from data_analysts.artifact_contracts import RunScope
from data_analysts.paths import DataAnalystsContext, PathBoundaryError


_AUDIT_BATCH_SIZE = 4096


class _BoundedEvidenceState:
    def __init__(self) -> None:
        descriptor, temporary_path = tempfile.mkstemp(prefix="data-analysts-audit-", suffix=".sqlite3")
        os.close(descriptor)
        self._temporary_path = Path(temporary_path)
        self.connection = sqlite3.connect(self._temporary_path)
        # Exact duplicate evidence can contain millions of keys.  It is a
        # disposable audit index, so bound SQLite's page cache and commit each
        # streamed parquet batch rather than retaining one giant transaction.
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute(
            "CREATE TABLE logical_keys (key TEXT PRIMARY KEY, first_path TEXT NOT NULL)"
        )
        self.date_min: str | None = None
        self.date_max: str | None = None
        self.availability_min: str | None = None
        self.availability_max: str | None = None
        self.cutoff_max: tuple[datetime, str] | None = None

    def add_key(self, key: tuple[Any, ...], path: str) -> str | None:
        encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
        try:
            self.connection.execute(
                "INSERT INTO logical_keys(key, first_path) VALUES (?, ?)",
                (encoded, path),
            )
            return None
        except sqlite3.IntegrityError:
            row = self.connection.execute(
                "SELECT first_path FROM logical_keys WHERE key = ?", (encoded,)
            ).fetchone()
            return str(row[0]) if row else "<unknown>"

    def add_date(self, value: str) -> None:
        self.date_min = value if self.date_min is None else min(self.date_min, value)
        self.date_max = value if self.date_max is None else max(self.date_max, value)

    def add_availability(self, value: str) -> None:
        self.availability_min = (
            value if self.availability_min is None else min(self.availability_min, value)
        )
        self.availability_max = (
            value if self.availability_max is None else max(self.availability_max, value)
        )

    def add_cutoff(self, parsed: datetime, text: str) -> None:
        candidate = (parsed, text)
        if self.cutoff_max is None or parsed > self.cutoff_max[0]:
            self.cutoff_max = candidate

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._temporary_path.unlink(missing_ok=True)

    def flush(self) -> None:
        self.connection.commit()


def audit_store(
    context: DataAnalystsContext,
    contracts: dict[str, ArtifactContract],
    *,
    contract_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Derive active artifact evidence from contract-bounded parquet inventories.

    Supplying ``contract_keys`` creates an explicit changed-contract audit: it
    deeply validates only those contracts and their exact manifest identities.
    The default remains a complete store audit for periodic health checks.
    """
    if contract_keys is not None:
        unknown = sorted(set(contract_keys).difference(contracts))
        if unknown:
            raise ValueError(f"audit scope has unknown contract keys: {unknown}")
        selected_contracts = {
            key: contracts[key] for key in sorted(contract_keys)
        }
        selected_manifest_paths = {
            context.store_path("manifests", contract.manifest_file_name).resolve()
            for contract in selected_contracts.values()
        }
    else:
        selected_contracts = contracts
        selected_manifest_paths = None
    manifests = _load_manifest_objects(context)
    artifacts: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    metrics = {
        "audited_artifact_count": 0,
        "parquet_file_count": 0,
        "orphan_partition_count": 0,
        "missing_partition_count": 0,
        "missing_manifest_count": 0,
        "manifest_mismatch_count": 0,
        "wrong_partition_count": 0,
        "duplicate_logical_key_count": 0,
        "malformed_cutoff_count": 0,
        "unavailable_cutoff_count": 0,
        "parquet_read_error_count": 0,
        "configured_field_error_count": 0,
        "superseded_retained_count": 0,
        "superseded_path_mismatch_count": 0,
        "artifact_issue_count": 0,
    }

    by_artifact_id: dict[str, list[ArtifactContract]] = {}
    for contract in selected_contracts.values():
        by_artifact_id.setdefault(contract.artifact_id, []).append(contract)

    for manifest_path, manifest in manifests:
        if (
            selected_manifest_paths is not None
            and manifest_path.resolve() not in selected_manifest_paths
        ):
            continue
        artifact_id = manifest.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            _issue(
                issues,
                metrics,
                "manifest_contract",
                "<unknown>",
                _relative_store_path(context, manifest_path),
                "manifest has no artifact_id",
            )
            continue
        candidates = by_artifact_id.get(artifact_id, [])
        declared_contract_key = manifest.get("contract_key")
        contract = None
        if isinstance(declared_contract_key, str):
            declared = selected_contracts.get(declared_contract_key)
            if (
                declared is not None
                and declared.artifact_id == artifact_id
                and manifest.get("variant") == declared.variant
                and (
                    _select_contract(manifest, [declared]) is declared
                    or (
                        declared.allow_empty
                        and manifest.get("artifact_paths") == []
                        and manifest.get("row_count") == 0
                    )
                )
            ):
                contract = declared
        else:
            contract = _select_contract(manifest, candidates)
            if contract is not None and contract.contract_key != contract.artifact_id:
                _issue(
                    issues,
                    metrics,
                    "manifest_contract",
                    artifact_id,
                    _relative_store_path(context, manifest_path),
                    f"{artifact_id} legacy ambiguous manifest cannot identify variant; "
                    f"rebuild as {contract.manifest_file_name}",
                )
                continue
        if contract is None:
            _issue(
                issues,
                metrics,
                "manifest_contract",
                artifact_id,
                _relative_store_path(context, manifest_path),
                f"{artifact_id} manifest paths do not match one artifact contract variant",
            )
            continue
        expected_manifest_path = context.store_path(
            "manifests", contract.manifest_file_name
        )
        if manifest_path.resolve() != expected_manifest_path.resolve():
            _issue(
                issues,
                metrics,
                "manifest_contract",
                artifact_id,
                _relative_store_path(context, manifest_path),
                f"{artifact_id} manifest identity path mismatch: expected "
                f"manifests/{contract.manifest_file_name}",
            )
            continue
        if contract.contract_key in artifacts:
            _issue(
                issues,
                metrics,
                "manifest_contract",
                artifact_id,
                _relative_store_path(context, manifest_path),
                f"{artifact_id} duplicate manifest identity for {contract.contract_key}",
            )
            continue
        evidence = _audit_artifact(context, contract, manifest, issues, metrics)
        artifacts[contract.contract_key] = evidence

    for contract_key, contract in selected_contracts.items():
        if contract_key in artifacts:
            continue
        inventory = _inventory(context, contract, [])
        retained = (
            _retained_versions(context, contract)
            if contract.publication_mode in {
                "full_replace", "partition_upsert", "snapshot_by_value"
            }
            else []
        )
        missing_counted = False
        if retained:
            metrics["missing_manifest_count"] += 1
            missing_counted = True
            _issue(
                issues, metrics, "missing_manifest", contract.artifact_id, None,
                f"{contract.artifact_id} retained versions exist without an active manifest",
            )
            if not inventory:
                inventory = retained
        if not inventory:
            continue
        if not missing_counted:
            metrics["missing_manifest_count"] += 1
        inventory_paths = [_relative_store_path(context, path) for path in inventory]
        for relative in inventory_paths:
            metrics["orphan_partition_count"] += 1
            _issue(
                issues,
                metrics,
                "orphan_partition",
                contract.artifact_id,
                relative,
                f"{contract.artifact_id} orphan parquet has no active manifest: {relative}",
            )
        evidence = _derive_evidence(context, contract, inventory, issues, metrics)
        evidence.update(
            {
                "artifact_id": contract.artifact_id,
                "contract_key": contract.contract_key,
                "variant": contract.variant,
                "publication_mode": contract.publication_mode,
                "manifest_paths": [],
                "inventory_paths": inventory_paths,
            }
        )
        artifacts[contract_key] = evidence
        metrics["parquet_file_count"] += len(inventory)

    # Full-replace contracts retain valid versions for rollback, but any parquet
    # in their owned directory outside that layout is a legacy/orphan surface.
    for contract in selected_contracts.values():
        if contract.publication_mode not in {
            "full_replace", "partition_upsert", "snapshot_by_value"
        }:
            continue
        for path in _owned_layout_orphans(context, contract):
            relative = _relative_store_path(context, path)
            if any(issue.get("path") == relative for issue in issues):
                continue
            metrics["orphan_partition_count"] += 1
            _issue(
                issues, metrics, "orphan_partition", contract.artifact_id, relative,
                f"{contract.artifact_id} legacy parquet is outside retained versions layout: {relative}",
            )

    metrics["audited_artifact_count"] = len(artifacts)
    metrics["artifact_issue_count"] = len(issues)
    return {
        "status": "ready" if not issues else "blocked",
        "checked_at": _utc_now(),
        "metrics": metrics,
        "artifacts": artifacts,
        "issues": issues,
        "backup_evidence": _backup_evidence(context, selected_contracts),
    }


def coverage_regressions(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    run_scope: RunScope | None,
) -> list[dict[str, Any]]:
    """Compare cumulative artifact coverage only when both audits are trustworthy."""
    if not before or before.get("status") != "ready" or after.get("status") != "ready":
        return []
    if run_scope == "full_history":
        return []
    regressions: list[dict[str, Any]] = []
    before_artifacts = before.get("artifacts") or {}
    after_artifacts = after.get("artifacts") or {}
    for contract_key, prior in before_artifacts.items():
        if not isinstance(prior, dict) or prior.get("publication_mode") == "full_replace":
            continue
        current = after_artifacts.get(contract_key)
        if not isinstance(current, dict):
            regressions.append(
                _regression(prior, contract_key, "active artifact variant disappeared")
            )
            continue
        prior_partitions = set(prior.get("partition_values") or [])
        current_partitions = set(current.get("partition_values") or [])
        lost = sorted(prior_partitions - current_partitions)
        if lost:
            regressions.append(
                _regression(prior, contract_key, f"partition coverage regressed: missing {lost}")
            )
        if _integer(current.get("row_count")) < _integer(prior.get("row_count")):
            regressions.append(
                _regression(
                    prior,
                    contract_key,
                    f"row coverage regressed: {_integer(prior.get('row_count'))} -> "
                    f"{_integer(current.get('row_count'))}",
                )
            )
        prior_range = prior.get("date_range")
        current_range = current.get("date_range")
        if _range_regressed(prior_range, current_range):
            regressions.append(
                _regression(prior, contract_key, f"date coverage regressed: {prior_range} -> {current_range}")
            )
    return regressions


def _audit_artifact(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    manifest: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> dict[str, Any]:
    artifact_id = contract.artifact_id
    _audit_superseded_paths(context, contract, manifest, issues, metrics)
    _audit_active_partition_version(contract, manifest, issues, metrics)
    listed = [path for path in manifest.get("artifact_paths", []) if isinstance(path, str)]
    if not listed and contract.allow_empty and manifest.get("row_count") == 0:
        manifest_columns = manifest.get("columns")
        compatible_columns = (
            isinstance(manifest_columns, list)
            and all(isinstance(column, str) for column in manifest_columns)
            and set(contract.required_columns).issubset(manifest_columns)
        )
        evidence = {
            "row_count": 0,
            "date_range": None,
            "availability_date_range": None,
            "columns": (
                manifest_columns
                if compatible_columns
                else list(contract.required_columns)
            ),
            "schema_fingerprint": (
                manifest.get("schema_fingerprint")
                if compatible_columns
                and isinstance(manifest.get("schema_fingerprint"), str)
                else empty_contract_schema_fingerprint(contract)
            ),
            "partitioning": [contract.partition_name] if contract.partition_name else ["single_file"],
            "partition_values": [],
            "data_cutoff_at": None,
            "artifact_id": contract.artifact_id,
            "contract_key": contract.contract_key,
            "variant": contract.variant,
            "publication_mode": contract.publication_mode,
            "manifest_paths": [],
            "inventory_paths": [],
        }
        _compare_manifest(contract, manifest, evidence, issues, metrics)
        return evidence
    inventory = _inventory(context, contract, listed)
    inventory_relative = [_relative_store_path(context, path) for path in inventory]
    listed_set = set(listed)
    inventory_set = set(inventory_relative)

    for path in sorted(inventory_set - listed_set):
        metrics["orphan_partition_count"] += 1
        _issue(
            issues, metrics, "orphan_partition", artifact_id, path,
            f"{artifact_id} orphan parquet partition is not manifest-listed: {path}",
        )
    for path in sorted(listed_set - inventory_set):
        metrics["missing_partition_count"] += 1
        _issue(
            issues, metrics, "missing_partition", artifact_id, path,
            f"{artifact_id} manifest-listed parquet partition is missing: {path}",
        )

    active_paths = [context.artifact_path(path) for path in listed if path in inventory_set]
    metrics["parquet_file_count"] += len(active_paths)
    evidence = _derive_evidence(context, contract, active_paths, issues, metrics)
    evidence.update(
        {
            "artifact_id": artifact_id,
            "contract_key": contract.contract_key,
            "variant": contract.variant,
            "publication_mode": contract.publication_mode,
            "manifest_paths": listed,
            "inventory_paths": inventory_relative,
        }
    )
    _compare_manifest(contract, manifest, evidence, issues, metrics)
    return evidence


def _derive_evidence(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    paths: list[Path],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> dict[str, Any]:
    state = _BoundedEvidenceState()
    try:
        return _derive_evidence_with_state(
            context, contract, paths, issues, metrics, state
        )
    finally:
        state.close()


def _derive_evidence_with_state(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    paths: list[Path],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
    state: _BoundedEvidenceState,
) -> dict[str, Any]:
    common_schema: pa.Schema | None = None
    row_count = 0
    partitions: set[str] = set()

    for path in paths:
        relative = _relative_store_path(context, path)
        try:
            parquet = pq.ParquetFile(path)
        except Exception as exc:
            metrics["parquet_read_error_count"] += 1
            _issue(
                issues,
                metrics,
                "parquet_read",
                contract.artifact_id,
                relative,
                f"{contract.artifact_id} cannot read parquet metadata at {relative}: {exc}",
            )
            continue
        try:
            schema = parquet.schema_arrow
            missing = sorted(set(contract.required_columns) - set(schema.names))
            if missing:
                _issue(
                    issues, metrics, "schema", contract.artifact_id, relative,
                    f"{contract.artifact_id} schema missing required columns at {relative}: {', '.join(missing)}",
                )
            if common_schema is None:
                common_schema = schema
            elif not schema.equals(common_schema, check_metadata=False):
                _issue(
                    issues, metrics, "schema", contract.artifact_id, relative,
                    f"{contract.artifact_id} schema mismatch across parquet files: {relative}",
                )
            row_count += parquet.metadata.num_rows
            partition_value = _partition_from_path(contract, relative)
            if partition_value is not None:
                partitions.add(partition_value)
            bounded = list(
                dict.fromkeys(
                    field
                    for field in (
                        *contract.logical_key,
                        contract.date_field,
                        contract.availability_field,
                        contract.partition_field,
                        "data_cutoff_at",
                    )
                    if field is not None and field in schema.names
                )
            )
            row_offset = 0
            for batch in parquet.iter_batches(
                columns=bounded, batch_size=_AUDIT_BATCH_SIZE
            ):
                rows = batch.to_pylist()
                for batch_index, row in enumerate(rows):
                    index = row_offset + batch_index
                    _audit_row(
                        row, index, relative, contract, partition_value, state,
                        issues, metrics,
                    )
                row_offset += len(rows)
                state.flush()
        finally:
            parquet.close()

    evidence = {
        "row_count": row_count,
        "date_range": (
            [state.date_min, state.date_max] if state.date_min is not None else None
        ),
        "availability_date_range": (
            [state.availability_min, state.availability_max]
            if state.availability_min is not None
            else None
        ),
        "columns": list(common_schema.names) if common_schema is not None else [],
        "schema_fingerprint": _schema_fingerprint(common_schema) if common_schema is not None else None,
        "partitioning": [contract.partition_name] if contract.partition_name else ["single_file"],
        "partition_values": sorted(partitions),
        "data_cutoff_at": state.cutoff_max[1] if state.cutoff_max else None,
    }
    return evidence


def _audit_row(
    row: dict[str, Any],
    index: int,
    relative: str,
    contract: ArtifactContract,
    partition_value: str | None,
    state: _BoundedEvidenceState,
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> None:
            row_date = _date_value(row.get(contract.date_field)) if contract.date_field else None
            availability = (
                _date_value(row.get(contract.availability_field))
                if contract.availability_field
                else None
            )
            if contract.date_field and row_date is None:
                _configured_field_issue(
                    issues, metrics, contract, relative, index,
                    "date_field", contract.date_field, row.get(contract.date_field),
                )
            if contract.availability_field and availability is None:
                _configured_field_issue(
                    issues, metrics, contract, relative, index,
                    "availability_field", contract.availability_field,
                    row.get(contract.availability_field),
                )
            if row_date is not None:
                state.add_date(row_date)
            if availability is not None:
                state.add_availability(availability)
            if contract.partition_field and partition_value is not None:
                actual_partition = _row_partition(contract, row.get(contract.partition_field))
                if actual_partition is None:
                    _configured_field_issue(
                        issues, metrics, contract, relative, index,
                        "partition_field", contract.partition_field,
                        row.get(contract.partition_field),
                    )
                if actual_partition != partition_value:
                    metrics["wrong_partition_count"] += 1
                    _issue(
                        issues, metrics, "wrong_partition", contract.artifact_id, relative,
                        f"{contract.artifact_id} wrong partition membership at {relative} row {index}: "
                        f"{actual_partition!r} != {partition_value!r}",
                    )
            logical_values: list[Any] = []
            logical_key_valid = True
            for field in contract.logical_key:
                value = row.get(field)
                if not _valid_logical_value(value):
                    logical_key_valid = False
                    _configured_field_issue(
                        issues, metrics, contract, relative, index,
                        "logical_key", field, value,
                    )
                else:
                    logical_values.append(_key_value(value))
            if logical_key_valid:
                key = tuple(logical_values)
                first_path = state.add_key(key, relative)
                if first_path is not None:
                    metrics["duplicate_logical_key_count"] += 1
                    _issue(
                        issues, metrics, "duplicate_logical_key", contract.artifact_id, relative,
                        f"{contract.artifact_id} duplicate logical key across files: {key!r}; "
                        f"first={first_path}, duplicate={relative}",
                    )
            if "data_cutoff_at" in row:
                parsed = _parse_cutoff(row.get("data_cutoff_at"))
                if parsed is None:
                    metrics["malformed_cutoff_count"] += 1
                    _issue(
                        issues, metrics, "malformed_cutoff", contract.artifact_id, relative,
                        f"{contract.artifact_id} malformed data_cutoff_at at {relative} row {index}: "
                        f"{row.get('data_cutoff_at')!r}",
                    )
                else:
                    text = str(row["data_cutoff_at"])
                    state.add_cutoff(parsed, text)
                    if (
                        availability
                        and _cutoff_must_cover_availability(contract)
                        and parsed.date() < date.fromisoformat(availability)
                    ):
                        metrics["unavailable_cutoff_count"] += 1
                        _issue(
                            issues, metrics, "unavailable_cutoff", contract.artifact_id, relative,
                            f"{contract.artifact_id} data_cutoff_at predates availability at "
                            f"{relative} row {index}: {text} < {availability}",
                        )

def _compare_manifest(
    contract: ArtifactContract,
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> None:
    fields = (
        "row_count",
        "date_range",
        "availability_date_range",
        "columns",
        "schema_fingerprint",
        "partitioning",
        "data_cutoff_at",
    )
    for field in fields:
        if manifest.get(field) != evidence.get(field):
            metrics["manifest_mismatch_count"] += 1
            _issue(
                issues, metrics, "manifest_mismatch", contract.artifact_id, None,
                f"{contract.artifact_id} {field} mismatch: manifest={manifest.get(field)!r}, "
                f"parquet={evidence.get(field)!r}",
            )
    if manifest.get("status") != "ready":
        metrics["manifest_mismatch_count"] += 1
        _issue(
            issues, metrics, "manifest_mismatch", contract.artifact_id, None,
            f"{contract.artifact_id} manifest status is not ready: {manifest.get('status')!r}",
        )


def _inventory(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    listed_paths: list[str],
) -> list[Path]:
    if contract.publication_mode == "full_replace":
        # Only the manifest-selected version is active. Retained versions are rollback evidence.
        return sorted(
            context.artifact_path(path)
            for path in listed_paths
            if _path_matches_contract(contract, path) and context.artifact_path(path).is_file()
        )
    if contract.publication_mode in {"partition_upsert", "snapshot_by_value"}:
        listed_existing = [
            context.artifact_path(path)
            for path in listed_paths
            if _path_matches_contract(contract, path)
            and context.artifact_path(path).is_file()
        ]
        if any("/versions/" in path.replace("\\", "/") for path in listed_paths):
            return sorted(listed_existing)
        base = context.artifact_path(contract.base_path)
        if not base.exists():
            return []
        return sorted(
            path
            for path in base.glob(
                contract.legacy_inventory_glob().removeprefix(
                    f"{contract.base_path}/"
                )
            )
            if path.is_file()
        )
    base = context.artifact_path(contract.base_path)
    if not base.exists():
        return []
    pattern = contract.inventory_glob().removeprefix(f"{contract.base_path}/")
    return sorted(path for path in base.glob(pattern) if path.is_file())


def _owned_layout_orphans(
    context: DataAnalystsContext, contract: ArtifactContract
) -> list[Path]:
    base = context.artifact_path(contract.base_path)
    if not base.exists():
        return []
    versioned_files = {
        path.resolve()
        for path in base.glob(
            contract.inventory_glob().removeprefix(f"{contract.base_path}/")
        )
        if path.is_file()
    }
    allowed = set(versioned_files)
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        listed = manifest.get("artifact_paths")
        if isinstance(listed, list):
            listed_existing = {
                context.artifact_path(path).resolve()
                for path in listed
                if isinstance(path, str)
                and _path_matches_contract(contract, path)
                and context.artifact_path(path).is_file()
            }
            active_version_dirs = {
                path.parents[1].resolve()
                for path in listed_existing
                if path.parents[1].parent.name == "versions"
            }
            # Retained immutable versions are legal, but every parquet in the
            # active version must be explicitly named by the manifest.
            allowed = {
                path
                for path in versioned_files
                if path.parents[1].resolve() not in active_version_dirs
            }
            allowed.update(listed_existing)
            superseded = manifest.get("superseded_paths")
            if isinstance(superseded, list):
                allowed.update(
                    context.artifact_path(entry["path"]).resolve()
                    for entry in superseded
                    if isinstance(entry, dict)
                    and isinstance(entry.get("path"), str)
                    and context.artifact_path(entry["path"]).is_file()
                )
    return sorted(
        path for path in base.rglob("*.parquet") if path.resolve() not in allowed
    )


def _audit_active_partition_version(
    contract: ArtifactContract,
    manifest: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> None:
    if contract.publication_mode not in {
        "partition_upsert", "snapshot_by_value", "full_replace"
    }:
        return
    listed = manifest.get("artifact_paths")
    active = manifest.get("active_version")
    invalid = not isinstance(active, str)
    if isinstance(active, str):
        try:
            contract._validate_version(active)
        except Exception:
            invalid = True
    versions: set[str] = set()
    if isinstance(listed, list):
        for raw in listed:
            if not isinstance(raw, str) or "/./" in raw or "/../" in raw or "\\" in raw:
                invalid = True
                continue
            parts = PurePosixPath(raw).parts
            base = PurePosixPath(contract.base_path).parts
            remainder = parts[len(base):] if parts[:len(base)] == base else ()
            expected_length = 3 if contract.publication_mode == "full_replace" else 4
            if len(remainder) != expected_length or remainder[0] != "versions":
                invalid = True
                continue
            versions.add(remainder[1])
    expected_versions = (
        set()
        if listed == [] and contract.allow_empty and manifest.get("row_count") == 0
        else ({active} if isinstance(active, str) else set())
    )
    if versions != expected_versions:
        invalid = True
    if invalid:
        _issue(
            issues, metrics, "active_version", contract.artifact_id,
            contract.manifest_file_name,
            f"{contract.artifact_id} partition manifest must name one normalized active_version",
        )


def _audit_superseded_paths(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    manifest: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
) -> None:
    entries = manifest.get("superseded_paths", [])
    if not isinstance(entries, list):
        entries = [entries]
    active = set(manifest.get("artifact_paths", []))
    for entry in entries:
        valid = isinstance(entry, dict) and entry.get("state") == "retained"
        relative = entry.get("path") if isinstance(entry, dict) else None
        retained = entry.get("retained_path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not isinstance(retained, str):
            valid = False
        elif (
            relative in active
            or "/versions/" in relative.replace("\\", "/")
            or not (
                _path_matches_contract(contract, relative)
                or (
                    contract.publication_mode == "full_replace"
                    and relative == f"{contract.base_path}/{contract.file_name}"
                )
            )
        ):
            valid = False
        else:
            source = context.artifact_path(relative)
            retained_path = context.artifact_path(retained)
            expected_size = entry.get("size")
            expected_hash = entry.get("sha256")
            valid = (
                valid and source.is_file() and retained_path.is_file()
                and source.stat().st_size == expected_size
                and retained_path.stat().st_size == expected_size
                and _file_sha256(source) == expected_hash
                and _file_sha256(retained_path) == expected_hash
            )
        if valid:
            metrics["superseded_retained_count"] += 1
        else:
            metrics["superseded_path_mismatch_count"] += 1
            _issue(
                issues, metrics, "superseded_path", contract.artifact_id,
                str(relative),
                f"{contract.artifact_id} has invalid superseded path evidence: {relative!r}",
            )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _retained_versions(
    context: DataAnalystsContext, contract: ArtifactContract
) -> list[Path]:
    base = context.artifact_path(contract.base_path)
    if not base.exists():
        return []
    return sorted(
        path
        for path in base.glob(
            contract.inventory_glob().removeprefix(f"{contract.base_path}/")
        )
        if path.is_file()
    )


def _backup_evidence(
    context: DataAnalystsContext, contracts: dict[str, ArtifactContract]
) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    manifests = context.store_path("manifests")
    if manifests.exists():
        candidates.extend(sorted(path for path in manifests.glob("*.json") if path.is_file()))
    candidates.extend(
        context.store_path("manifests", contract.manifest_file_name)
        for contract in contracts.values()
    )
    for relative in (
        ("metadata", "data_store_manifest.json"),
        ("jobs", "current_run.json"),
        ("jobs", "pipeline_result.json"),
        ("jobs", "daily_state.json"),
    ):
        path = context.store_path(*relative)
        candidates.append(path)
    evidence: list[dict[str, Any]] = []
    for path in dict.fromkeys(candidates):
        exists = path.is_file()
        digest = hashlib.sha256() if exists else None
        if exists:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        evidence.append(
            {
                "relative_path": _relative_store_path(context, path),
                "absolute_path": str(path.resolve()),
                "sha256": digest.hexdigest() if digest is not None else None,
                "exists": exists,
                "size_bytes": path.stat().st_size if exists else None,
            }
        )
    return sorted(evidence, key=lambda item: item["relative_path"])


def _select_contract(
    manifest: dict[str, Any], candidates: list[ArtifactContract]
) -> ArtifactContract | None:
    paths = manifest.get("artifact_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        return None
    matching = [
        contract
        for contract in candidates
        if all(_path_matches_contract(contract, path) for path in paths)
        and manifest.get("partitioning")
        == ([contract.partition_name] if contract.partition_name else ["single_file"])
    ]
    return matching[0] if len(matching) == 1 else None


def _path_matches_contract(
    contract: ArtifactContract,
    relative: str,
) -> bool:
    parts = PurePosixPath(relative.replace("\\", "/")).parts
    base = PurePosixPath(contract.base_path).parts
    if parts[: len(base)] != base:
        return False
    remainder = parts[len(base) :]
    if contract.publication_mode == "full_replace":
        return (
            len(remainder) == 3
            and remainder[0] == "versions"
            and remainder[1] not in {"", ".", ".."}
            and remainder[2] == contract.file_name
        )
    prefix = f"{contract.partition_name}="
    if contract.publication_mode in {"partition_upsert", "snapshot_by_value"} and len(remainder) == 4:
        return (
            remainder[0] == "versions"
            and remainder[1] not in {"", ".", ".."}
            and remainder[2].startswith(prefix)
            and bool(remainder[2][len(prefix) :])
            and remainder[3] == contract.file_name
        )
    return (
        len(remainder) == 2
        and remainder[0].startswith(prefix)
        and bool(remainder[0][len(prefix) :])
        and remainder[1] == contract.file_name
    )


def _partition_from_path(contract: ArtifactContract, relative: str) -> str | None:
    if contract.partition_name is None:
        return None
    prefix = f"{contract.partition_name}="
    values = [part[len(prefix) :] for part in PurePosixPath(relative).parts if part.startswith(prefix)]
    return values[0] if len(values) == 1 and values[0] else None


def _row_partition(contract: ArtifactContract, value: Any) -> str | None:
    normalized = _date_value(value)
    if normalized is None:
        return None
    if contract.partition_name == "year" or str(contract.partition_name).endswith("_year"):
        return normalized[:4]
    return normalized


def _date_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None
    return None


def _parse_cutoff(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed == datetime(1970, 1, 1, tzinfo=timezone.utc):
        return None
    return parsed


def _cutoff_must_cover_availability(contract: ArtifactContract) -> bool:
    next_trading_day_policies = {
        "effective_next_trading_day_panel",
        "effective_next_trading_day_membership",
    }
    return contract.pit_policy not in next_trading_day_policies


def _key_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _valid_logical_value(value: Any) -> bool:
    if value is None or isinstance(value, (list, dict, set, tuple)):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (str, int, bool, date, datetime))


def _configured_field_issue(
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
    contract: ArtifactContract,
    relative: str,
    index: int,
    role: str,
    field: str,
    value: Any,
) -> None:
    metrics["configured_field_error_count"] += 1
    field_label = (
        f"{role} field {field}" if role == "logical_key" else f"{role} {field}"
    )
    _issue(
        issues,
        metrics,
        "configured_field",
        contract.artifact_id,
        relative,
        f"{contract.artifact_id} invalid {field_label} at {relative} "
        f"row {index}: {value!r}",
    )


def _range(values: list[str]) -> list[str] | None:
    return [min(values), max(values)] if values else None


def _schema_fingerprint(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def _load_manifest_objects(
    context: DataAnalystsContext,
) -> list[tuple[Path, dict[str, Any]]]:
    manifests_dir = context.store_path("manifests")
    if not manifests_dir.exists():
        return []
    output = []
    for path in sorted(manifests_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"manifest must be a JSON object: {path}")
        output.append((path, payload))
    return output


def _issue(
    issues: list[dict[str, Any]],
    metrics: dict[str, int],
    check: str,
    artifact_id: str,
    path: str | None,
    message: str,
) -> None:
    issue = {"check": check, "artifact_id": artifact_id, "message": message}
    if path is not None:
        issue["path"] = path
    issues.append(issue)


def _relative_store_path(context: DataAnalystsContext, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(context.data_store.resolve())
    except ValueError as exc:
        raise PathBoundaryError(f"artifact path escapes data_store: {path}") from exc
    return PurePosixPath(*relative.parts).as_posix()


def _integer(value: Any) -> int:
    return value if type(value) is int else 0


def _range_regressed(before: Any, after: Any) -> bool:
    if not isinstance(before, list) or len(before) != 2:
        return False
    if not isinstance(after, list) or len(after) != 2:
        return True
    return after[0] > before[0] or after[1] < before[1]


def _regression(prior: dict[str, Any], contract_key: str, message: str) -> dict[str, Any]:
    return {
        "artifact_id": prior.get("artifact_id"),
        "contract_key": contract_key,
        "message": message,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
