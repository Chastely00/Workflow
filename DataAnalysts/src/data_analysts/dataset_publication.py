from __future__ import annotations

import hashlib
import base64
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import (
    ArtifactContract,
    RunScope,
    empty_contract_schema_fingerprint,
)
from data_analysts.artifacts import (
    ArtifactError,
    _columns_from_rows,
    _normalize_parquet_scalar,
    _utc_now,
    atomic_write_text,
    stage_parquet,
    validate_rows,
)
from data_analysts.paths import DataAnalystsContext


@dataclass(frozen=True)
class PublicationResult:
    touched_paths: tuple[str, ...]
    total_row_count: int
    date_range: tuple[str, str] | None
    manifest_path: Path
    manifest: dict[str, Any]
    cleanup_diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _FileBackup:
    target: Path
    backup: Path | None


@dataclass(frozen=True)
class _RestoreFailure:
    record: _FileBackup
    error: Exception


class _SpillKeyIndex:
    """Disk-backed exact-key index used by streaming publication/audit paths."""

    def __init__(self) -> None:
        descriptor, temporary_path = tempfile.mkstemp(prefix="data-analysts-keys-", suffix=".sqlite3")
        os.close(descriptor)
        self._temporary_path = Path(temporary_path)
        self.connection = sqlite3.connect(self._temporary_path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute("CREATE TABLE keys (key TEXT PRIMARY KEY)")
        self.connection.execute("CREATE TABLE sources (source TEXT PRIMARY KEY)")

    @staticmethod
    def encode(key: tuple[Any, ...]) -> str:
        return json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)

    def add_key(self, key: tuple[Any, ...]) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO keys(key) VALUES (?)", (self.encode(key),)
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def contains(self, key: tuple[Any, ...]) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM keys WHERE key = ?", (self.encode(key),)
        ).fetchone() is not None

    def add_source(self, source: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO sources(source) VALUES (?)", (source,)
        )

    def sources(self) -> list[str]:
        return [str(row[0]) for row in self.connection.execute(
            "SELECT source FROM sources ORDER BY source"
        )]

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._temporary_path.unlink(missing_ok=True)

    def flush(self) -> None:
        self.connection.commit()


def archive_superseded_paths(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    *,
    expected_manifest_sha256: str,
    confirm_no_legacy_readers: bool,
) -> dict[str, Any]:
    """Archive an exact superseded ledger after callers quiesce legacy readers."""
    if not confirm_no_legacy_readers:
        raise ArtifactError("archive requires confirm_no_legacy_readers")
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    before = manifest_path.read_bytes()
    actual_hash = hashlib.sha256(before).hexdigest()
    if actual_hash != expected_manifest_sha256:
        raise ArtifactError("superseded archive manifest hash changed")
    manifest = json.loads(before.decode("utf-8"))
    entries = manifest.get("superseded_paths")
    if not isinstance(entries, list) or not entries:
        raise ArtifactError("manifest has no superseded paths to archive")

    archive_id = uuid.uuid4().hex
    archive_root = context.store_path("archives", "superseded", archive_id)
    mappings: list[tuple[Path, Path, dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("state") != "retained":
            raise ArtifactError("invalid superseded archive evidence")
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise ArtifactError("invalid superseded archive path")
        source = context.artifact_path(relative)
        if (
            not source.is_file()
            or source.stat().st_size != entry.get("size")
            or _sha256_file(source) != entry.get("sha256")
        ):
            raise ArtifactError(f"superseded archive evidence changed: {relative}")
        destination = archive_root / "payload" / PurePosixPath(relative)
        if destination.exists():
            raise ArtifactError(f"archive destination already exists: {destination}")
        mappings.append((source, destination, entry))
    updated = dict(manifest)
    updated.pop("superseded_paths", None)
    updated_bytes = json.dumps(updated, indent=2, sort_keys=True).encode("utf-8")
    updated_hash = hashlib.sha256(updated_bytes).hexdigest()
    intent = {
        "archive_id": archive_id,
        "contract_key": contract.contract_key,
        "status": "intent",
        "manifest_path": _relative_store_path(context, manifest_path),
        "manifest_sha256_before": actual_hash,
        "manifest_before_b64": base64.b64encode(before).decode("ascii"),
        "manifest_sha256_after": updated_hash,
        "manifest_after_b64": base64.b64encode(updated_bytes).decode("ascii"),
        "mappings": [
            {
                "source": _relative_store_path(context, source),
                "destination": _relative_store_path(context, destination),
                "sha256": entry["sha256"], "size": entry["size"],
            }
            for source, destination, entry in mappings
        ],
        "unrestored": [],
    }
    intent_locations = _persist_archive_recovery(context, archive_root, intent)
    if not intent_locations:
        raise ArtifactError("cannot persist archive recovery intent; no payload moved")
    moved: list[tuple[Path, Path, dict[str, Any]]] = []
    try:
        for source, destination, entry in mappings:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            moved.append((source, destination, entry))

        receipt_relative = (
            f"archives/superseded/{archive_id}/receipt.json"
        )
        receipt = {
            "archive_id": archive_id,
            "contract_key": contract.contract_key,
            "manifest_sha256_before": actual_hash,
            "manifest_sha256_after": updated_hash,
            "entries": entries,
            "status": "complete",
            "receipt_path": receipt_relative,
        }
        atomic_write_text(manifest_path, updated_bytes.decode("utf-8"))
        committed = {
            **intent,
            "status": "committed",
            "mappings": [
                {**mapping, "state": "archived"} for mapping in intent["mappings"]
            ],
            "receipt_path": receipt_relative,
        }
        committed_locations = _persist_archive_recovery(
            context, archive_root, committed
        )
        missing_terminal_locations = sorted(
            set(intent_locations) - set(committed_locations)
        )
        if missing_terminal_locations:
            raise ArtifactError(
                "cannot persist archive committed state to every prepared sink: "
                f"{missing_terminal_locations}"
            )
        receipt_path = context.store_path(*PurePosixPath(receipt_relative).parts)
        try:
            _durable_atomic_write(
                receipt_path,
                json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
            )
        except Exception:
            receipt_path = receipt_path.with_name("receipt.fallback.json")
            receipt_relative = _relative_store_path(context, receipt_path)
            receipt["receipt_path"] = receipt_relative
            try:
                _durable_atomic_write(
                    receipt_path,
                    json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
                )
            except Exception:
                receipt["status"] = "committed_without_receipt"
                receipt["receipt_path"] = committed_locations[0]
        return receipt
    except Exception as archive_error:
        manifest_restore_error: str | None = None
        if not manifest_path.is_file() or manifest_path.read_bytes() != before:
            try:
                staging_manifest = manifest_path.with_name(
                    f".{manifest_path.name}.{uuid.uuid4().hex}.restore"
                )
                staging_manifest.write_bytes(before)
                os.replace(staging_manifest, manifest_path)
            except OSError as exc:
                manifest_restore_error = str(exc)
        unrestored: list[dict[str, Any]] = []
        for source, destination, entry in reversed(moved):
            if destination.exists():
                try:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, source)
                except OSError as exc:
                    unrestored.append({
                        "source": _relative_store_path(context, source),
                        "destination": _relative_store_path(context, destination),
                        "sha256": entry["sha256"],
                        "size": entry["size"],
                        "error": str(exc),
                        "manual_step": f"restore {destination} to {source} after verifying sha256",
                    })
        if unrestored or manifest_restore_error is not None:
            recovery = {
                "archive_id": archive_id,
                "contract_key": contract.contract_key,
                "status": "manual_recovery_required",
                "operation_error": str(archive_error),
                "manifest_restore_error": manifest_restore_error,
                "unrestored": unrestored,
                "manifest_before_b64": intent["manifest_before_b64"],
                "mappings": intent["mappings"],
            }
            locations = _persist_archive_recovery(context, archive_root, recovery)
            raise ArtifactError(
                f"archive failed; recovery required; receipts={locations}; "
                f"unrestored={len(unrestored)}"
            ) from archive_error
        _persist_archive_recovery(
            context, archive_root, {**intent, "status": "rolled_back"}
        )
        raise


def _persist_archive_recovery(
    context: DataAnalystsContext,
    archive_root: Path,
    recovery: dict[str, Any],
) -> list[str]:
    payload = json.dumps(recovery, indent=2, sort_keys=True)
    archive_receipt = archive_root / "recovery.json"
    jobs_receipt = context.store_path(
        "jobs", f"archive_recovery_{recovery['archive_id']}.json"
    )
    locations: list[str] = []
    for primary in (archive_receipt, jobs_receipt):
        try:
            _durable_atomic_write(primary, payload.encode("utf-8"))
            locations.append(_relative_store_path(context, primary))
            continue
        except Exception:
            try:
                fallback = primary.with_name(f"{primary.stem}.fallback.json")
                _durable_atomic_write(fallback, payload.encode("utf-8"))
                locations.append(_relative_store_path(context, fallback))
            except Exception:
                continue
    return locations


def _durable_atomic_write(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staging.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, target)
    finally:
        if staging.exists():
            staging.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_dataset(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
    run_scope: RunScope,
    *,
    snapshot_value: str | None = None,
    write_schema: pa.Schema | None = None,
) -> PublicationResult:
    """Publish a dataset according to its transaction and partition contract."""
    if run_scope not in {"full_history", "bounded_backfill", "daily"}:
        raise ArtifactError(f"unsupported run scope: {run_scope}")
    if contract.contract_key != contract.artifact_id:
        migrate_legacy_variant_manifests(context, [contract])
    if not rows and not contract.allow_empty:
        raise ArtifactError(
            f"{contract.artifact_id} does not allow empty {run_scope} publication"
        )
    if not rows:
        return _publish_empty_inventory(context, contract)
    if write_schema is None and run_scope == "full_history":
        write_schema = _full_history_write_schema(contract, rows)
    if contract.publication_mode == "full_replace":
        if run_scope != "full_history":
            raise ArtifactError(
                f"{contract.artifact_id} full_replace requires full_history scope"
            )
        return _publish_full_replace(context, contract, rows)
    if contract.publication_mode == "snapshot_by_value":
        return _publish_partition_versioned(
            context,
            contract,
            rows,
            run_scope,
            snapshot_value=snapshot_value,
            write_schema=write_schema,
        )
    return _publish_partition_versioned(
        context, contract, rows, run_scope, write_schema=write_schema
    )


def migrate_legacy_variant_manifests(
    context: DataAnalystsContext,
    contracts: Iterable[ArtifactContract],
) -> list[str]:
    """Atomically migrate uniquely identifiable legacy shared-artifact manifests."""
    candidates_by_artifact: dict[str, list[ArtifactContract]] = {}
    for contract in contracts:
        if contract.contract_key != contract.artifact_id:
            candidates_by_artifact.setdefault(contract.artifact_id, []).append(contract)

    migrated: list[str] = []
    for artifact_id, candidates in sorted(candidates_by_artifact.items()):
        legacy = context.store_path("manifests", f"{artifact_id}.json")
        if not legacy.exists():
            continue
        try:
            legacy_bytes = legacy.read_bytes()
            payload = json.loads(legacy_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError(
                f"{artifact_id} legacy manifest migration cannot read JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("artifact_id") != artifact_id:
            raise ArtifactError(
                f"{artifact_id} legacy manifest migration artifact_id mismatch"
            )
        paths = payload.get("artifact_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) and path for path in paths)
        ):
            raise ArtifactError(
                f"{artifact_id} legacy manifest migration has invalid artifact_paths"
            )
        path_matches = [
            contract
            for contract in candidates
            if payload.get("partitioning")
            == ([contract.partition_name] if contract.partition_name else ["single_file"])
            and all(_legacy_path_matches_contract(contract, path) for path in paths)
        ]
        if not path_matches:
            raise ArtifactError(
                f"{artifact_id} legacy manifest has mixed or no contract match"
            )
        if len(path_matches) != 1:
            raise ArtifactError(
                f"{artifact_id} legacy manifest has ambiguous contract match: "
                f"{[contract.contract_key for contract in path_matches]}"
            )
        contract = path_matches[0]
        target = context.store_path("manifests", contract.manifest_file_name)
        if target.exists():
            raise ArtifactError(
                f"{artifact_id} legacy migration target already exists: {target}"
            )
        listed_files = [context.artifact_path(path) for path in paths]
        if not all(path.is_file() for path in listed_files):
            missing = [str(path) for path in listed_files if not path.is_file()]
            raise ArtifactError(
                f"{artifact_id} legacy migration manifest-listed parquet missing: {missing}"
            )
        inventory = (
            listed_files
            if all("/versions/" in path.replace("\\", "/") for path in paths)
            else _inventory(context, contract)
        )
        inventory_relative = sorted(_relative_store_path(context, path) for path in inventory)
        if inventory_relative != sorted(paths):
            raise ArtifactError(
                f"{artifact_id} legacy migration inventory differs from manifest-listed paths"
            )
        rebuilt = _manifest_from_files(
            contract,
            [(path, relative) for path, relative in zip(listed_files, paths)],
        )
        evidence_fields = (
            "artifact_id", "schema_version", "layer", "source_families",
            "source_collections", "row_count", "date_range",
            "availability_date_range", "columns", "schema_fingerprint",
            "partitioning", "artifact_paths", "pit_policy", "data_cutoff_at",
            "duplicate_count", "omitted_row_count", "status",
        )
        mismatches = [
            field for field in evidence_fields
            if payload.get(field) != rebuilt.get(field)
        ]
        if mismatches:
            raise ArtifactError(
                f"{artifact_id} legacy migration evidence mismatch: {mismatches}"
            )
        migrated_payload = {
            **payload,
            "contract_key": contract.contract_key,
            "variant": contract.variant,
        }
        try:
            atomic_write_text(
                target, json.dumps(migrated_payload, indent=2, sort_keys=True)
            )
            legacy.unlink()
        except OSError as exc:
            try:
                if target.exists():
                    target.unlink()
            except OSError as cleanup_exc:
                raise ArtifactError(
                    f"{artifact_id} legacy manifest migration failed; original={legacy}; "
                    f"partial_target={target}; cleanup_error={cleanup_exc}; error={exc}"
                ) from exc
            if not legacy.exists():
                try:
                    legacy.write_bytes(legacy_bytes)
                except OSError as restore_exc:
                    raise ArtifactError(
                        f"{artifact_id} legacy manifest migration failed and original "
                        f"restore failed: original={legacy}; error={restore_exc}"
                    ) from exc
            raise ArtifactError(
                f"{artifact_id} legacy manifest migration failed; original preserved: {exc}"
            ) from exc
        migrated.append(contract.contract_key)
    return migrated


def _legacy_path_matches_contract(
    contract: ArtifactContract, relative_path: str
) -> bool:
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    base = PurePosixPath(contract.base_path).parts
    if parts[: len(base)] != base:
        return False
    remainder = parts[len(base) :]
    if contract.publication_mode == "full_replace":
        return _is_full_replace_version_path(contract, relative_path)
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


def _publish_partition_versioned(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
    run_scope: RunScope,
    *,
    snapshot_value: str | None = None,
    write_schema: pa.Schema | None = None,
) -> PublicationResult:
    """Publish one immutable complete partition inventory and switch its manifest."""
    if rows:
        _validate_unique_rows_spill(contract, rows, "incoming batch")
    incoming_by_partition: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = _partition_value(contract, row)
        incoming_by_partition.setdefault(value, []).append(row)
    if (
        contract.publication_mode == "snapshot_by_value"
        and not incoming_by_partition
        and snapshot_value is not None
    ):
        incoming_by_partition[snapshot_value] = []
    if contract.publication_mode == "snapshot_by_value" and len(incoming_by_partition) != 1:
        raise ArtifactError(
            f"{contract.artifact_id} snapshot publication requires exactly one snapshot"
        )
    for value, partition_rows in incoming_by_partition.items():
        validate_rows(contract, partition_rows, partition_value=value)

    active_inventory = _partition_active_inventory(context, contract)
    _validate_inventory_paths(context, contract, active_inventory)
    active_by_partition = {
        _partition_from_relative_path(
            contract, _relative_store_path(context, path)
        ): path
        for path in active_inventory
    }
    empty_schema: pa.Schema | None = write_schema
    if active_by_partition:
        schema_source = pq.ParquetFile(
            next(iter(active_by_partition.values()))
        )
        try:
            empty_schema = schema_source.schema_arrow
        finally:
            schema_source.close()
    version = uuid.uuid4().hex
    version_root = context.artifact_path(f"{contract.base_path}/versions/{version}")
    retain_distinct_snapshots = contract.publication_mode == "snapshot_by_value"
    output_values = (
        set(active_by_partition) | set(incoming_by_partition)
        if run_scope != "full_history" or retain_distinct_snapshots
        else set(incoming_by_partition)
    )
    files: list[tuple[Path, str]] = []
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    old_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
    old_manifest_payload = (
        json.loads(old_manifest.decode("utf-8")) if old_manifest is not None else {}
    )
    legacy_active = [
        path
        for path in active_inventory
        if "/versions/" not in _relative_store_path(context, path)
    ]
    retained_legacy_root: Path | None = None
    committed = False
    try:
        if legacy_active:
            retained_version = f"legacy-{uuid.uuid4().hex}"
            retained_legacy_root = context.artifact_path(
                f"{contract.base_path}/versions/{retained_version}"
            )
            for source in legacy_active:
                value = _partition_from_relative_path(
                    contract, _relative_store_path(context, source)
                )
                retained = context.artifact_path(
                    contract.path_for_partition(value, version=retained_version)
                )
                retained.parent.mkdir(parents=True, exist_ok=True)
                _copy_immutable_partition(source, retained)
        for value in sorted(output_values):
            relative = contract.path_for_partition(value, version=version)
            target = context.artifact_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = active_by_partition.get(value)
            incoming = incoming_by_partition.get(value)
            if incoming is None:
                assert existing is not None
                _copy_immutable_partition(existing, target)
            else:
                _write_partition_stream(
                    contract,
                    target,
                    value,
                    incoming or [],
                    existing
                    if run_scope != "full_history" and not retain_distinct_snapshots
                    else None,
                    empty_schema=empty_schema,
                    write_schema=write_schema,
                )
            files.append((target, relative))

        manifest = (
            _manifest_from_files(contract, files)
            if files
            else _empty_manifest(contract)
        )
        manifest["active_version"] = version
        superseded = list(old_manifest_payload.get("superseded_paths", []))
        if legacy_active:
            assert retained_legacy_root is not None
            retained_version = retained_legacy_root.name
            for source in legacy_active:
                value = _partition_from_relative_path(
                    contract, _relative_store_path(context, source)
                )
                retained_relative = contract.path_for_partition(
                    value, version=retained_version
                )
                superseded.append(
                    _superseded_path_evidence(
                        context, source, retained_relative=retained_relative
                    )
                )
        if superseded:
            manifest["superseded_paths"] = superseded
        validate_staged_dataset(contract, files, manifest)
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
        committed = True
    finally:
        if not committed and version_root.exists():
            shutil.rmtree(version_root, ignore_errors=True)
        if not committed and retained_legacy_root is not None and retained_legacy_root.exists():
            shutil.rmtree(retained_legacy_root, ignore_errors=True)

    return PublicationResult(
        touched_paths=tuple(relative for _, relative in files),
        total_row_count=manifest["row_count"],
        date_range=tuple(manifest["date_range"]) if manifest["date_range"] else None,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def _copy_immutable_partition(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _superseded_path_evidence(
    context: DataAnalystsContext,
    source: Path,
    *,
    retained_relative: str,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": _relative_store_path(context, source),
        "size": source.stat().st_size,
        "sha256": digest.hexdigest(),
        "retained_path": retained_relative,
        "state": "retained",
    }


def _write_partition_stream(
    contract: ArtifactContract,
    target: Path,
    partition_value: str,
    incoming_rows: list[dict[str, Any]],
    existing_path: Path | None,
    *,
    empty_schema: pa.Schema | None = None,
    write_schema: pa.Schema | None = None,
) -> None:
    incoming_keys = _SpillKeyIndex()
    for index, row in enumerate(incoming_rows):
        key = _key(contract, row)
        if not incoming_keys.add_key(key):
            incoming_keys.close()
            raise ArtifactError(
                f"{contract.artifact_id} duplicate logical key in incoming batch "
                f"row {index}: {key!r}"
            )
    incoming_keys.flush()
    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None

    def write_rows(batch_rows: list[dict[str, Any]]) -> None:
        nonlocal writer, schema
        if not batch_rows:
            return
        validate_rows(contract, batch_rows, partition_value=partition_value)
        table = _table_from_rows(contract, batch_rows, write_schema=write_schema)
        if schema is None:
            schema = table.schema
            writer = pq.ParquetWriter(target, schema)
        elif not table.schema.equals(schema, check_metadata=False):
            raise ArtifactError(
                f"{contract.artifact_id} schema mismatch for partition {partition_value}"
            )
        assert writer is not None
        writer.write_table(table)

    try:
        if existing_path is not None:
            parquet = pq.ParquetFile(existing_path)
            try:
                for batch in parquet.iter_batches(batch_size=65536):
                    retained = [
                        row
                        for row in batch.to_pylist()
                        if not incoming_keys.contains(_key(contract, row))
                    ]
                    write_rows(retained)
            finally:
                parquet.close()
        incoming_rows.sort(key=lambda row: _sort_key(contract, row))
        for offset in range(0, len(incoming_rows), 65536):
            write_rows(incoming_rows[offset : offset + 65536])
    finally:
        if writer is not None:
            writer.close()
        incoming_keys.close()
    if schema is None:
        if contract.allow_empty:
            table = (
                pa.Table.from_batches([], schema=empty_schema)
                if empty_schema is not None
                else _table_from_rows(contract, [])
            )
            pq.write_table(table, target)
        else:
            raise ArtifactError(
                f"{contract.artifact_id} produced an empty partition {partition_value}"
            )


def _publish_full_replace(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
) -> PublicationResult:
    _unique_rows(contract, rows, "incoming full dataset")
    validate_rows(contract, rows)
    table = _table_from_rows(contract, rows)
    version = uuid.uuid4().hex
    staging_root = context.store_path(".staging", contract.artifact_id, version)
    staged_dataset = staging_root / "dataset"
    staged_file = staged_dataset / contract.file_name
    relative_path = contract.path_for_version(version)
    final_file = context.artifact_path(relative_path)
    final_directory = final_file.parent
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    prior_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    legacy_relative = f"{contract.base_path}/{contract.file_name}"
    legacy_flat = context.artifact_path(legacy_relative)
    migrate_flat = (
        prior_manifest.get("artifact_paths") == [legacy_relative]
        and legacy_flat.is_file()
    )
    retained_legacy_root: Path | None = None
    staged_manifest = staging_root / "manifest.json"
    committed = False
    cleanup_diagnostics: tuple[str, ...] = ()
    try:
        staged_dataset.mkdir(parents=True)
        pq.write_table(table, staged_file)
        manifest = _manifest_from_files(
            contract,
            [(staged_file, relative_path)],
        )
        manifest["active_version"] = version
        superseded = list(prior_manifest.get("superseded_paths", []))
        if migrate_flat:
            retained_version = f"legacy-{uuid.uuid4().hex}"
            retained_relative = contract.path_for_version(retained_version)
            retained = context.artifact_path(retained_relative)
            retained_legacy_root = retained.parent
            retained.parent.mkdir(parents=True, exist_ok=True)
            _copy_immutable_partition(legacy_flat, retained)
            superseded.append(
                _superseded_path_evidence(
                    context, legacy_flat, retained_relative=retained_relative
                )
            )
        if superseded:
            manifest["superseded_paths"] = superseded
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        validate_staged_dataset(
            contract,
            [(staged_file, relative_path)],
            json.loads(staged_manifest.read_text(encoding="utf-8")),
        )
        final_directory.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_dataset, final_directory)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_manifest, manifest_path)
        committed = True
    finally:
        if staging_root.exists():
            try:
                shutil.rmtree(staging_root)
            except OSError as exc:
                state = "publication committed" if committed else "publication failed"
                cleanup_diagnostics = (
                    f"{contract.artifact_id} {state}; staging cleanup failed: "
                    f"path={staging_root}, error={exc}",
                )
        if not committed and retained_legacy_root is not None:
            shutil.rmtree(retained_legacy_root, ignore_errors=True)

    return PublicationResult(
        touched_paths=(relative_path,),
        total_row_count=manifest["row_count"],
        date_range=tuple(manifest["date_range"]) if manifest["date_range"] else None,
        manifest_path=manifest_path,
        manifest=manifest,
        cleanup_diagnostics=cleanup_diagnostics,
    )


def merge_partition_rows(
    contract: ArtifactContract,
    existing_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = _unique_rows(contract, existing_rows, "existing")
    incoming = _unique_rows(contract, incoming_rows, "incoming")
    existing.update(incoming)
    return sorted(existing.values(), key=lambda row: _sort_key(contract, row))


def reconstruct_manifest(
    context: DataAnalystsContext,
    contract: ArtifactContract,
) -> dict[str, Any]:
    inventory = _inventory(context, contract)
    _validate_inventory_paths(context, contract, inventory)
    manifest = (
        _manifest_from_files(
            contract,
            [(path, _relative_store_path(context, path)) for path in inventory],
        )
        if inventory
        else _empty_manifest(contract)
    )
    target = context.store_path("manifests", contract.manifest_file_name)
    atomic_write_text(target, json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def validate_staged_dataset(
    contract: ArtifactContract,
    files: list[tuple[Path, str]],
    manifest: dict[str, Any],
) -> None:
    """Validate parquet evidence and its staged manifest before visibility changes."""
    rebuilt = _manifest_from_files(contract, files) if files else _empty_manifest(contract)
    comparable_fields = (
        "artifact_id",
        "contract_key",
        "variant",
        "row_count",
        "date_range",
        "availability_date_range",
        "columns",
        "schema_fingerprint",
        "partitioning",
        "artifact_paths",
        "data_cutoff_at",
        "status",
    )
    mismatches = [
        field
        for field in comparable_fields
        if rebuilt.get(field) != manifest.get(field)
    ]
    if mismatches:
        raise ArtifactError(
            f"{contract.artifact_id} staged dataset manifest mismatch: "
            f"{', '.join(mismatches)}"
        )


def _manifest_from_files(
    contract: ArtifactContract,
    files: list[tuple[Path, str]],
) -> dict[str, Any]:
    if not files:
        raise ArtifactError(f"{contract.artifact_id} has no parquet inventory")
    common_schema: pa.Schema | None = None
    seen = _SpillKeyIndex()
    row_count = 0
    date_min: str | None = None
    date_max: str | None = None
    availability_min: str | None = None
    availability_max: str | None = None
    cutoff_max: tuple[datetime, str] | None = None
    try:
        for path, relative_path in files:
            parquet = pq.ParquetFile(path)
            try:
                schema = parquet.schema_arrow
                missing = sorted(set(contract.required_columns) - set(schema.names))
                if missing:
                    raise ArtifactError(
                        f"{contract.artifact_id} schema missing required columns: "
                        f"{', '.join(missing)}"
                    )
                if common_schema is None:
                    common_schema = schema
                elif not schema.equals(common_schema, check_metadata=False):
                    raise ArtifactError(
                        f"{contract.artifact_id} schema mismatch: {relative_path}"
                    )
                partition_value = (
                    _partition_from_relative_path(contract, relative_path)
                    if contract.partition_name is not None
                    else None
                )
                bounded_columns = list(dict.fromkeys(
                    column for column in (
                        *contract.required_columns,
                        *contract.logical_key,
                        contract.date_field,
                        contract.availability_field,
                        contract.partition_field,
                        "source_collection",
                        "data_cutoff_at",
                    ) if column is not None and column in schema.names
                ))
                for batch in parquet.iter_batches(
                    columns=bounded_columns, batch_size=65536
                ):
                    rows = batch.to_pylist()
                    validate_rows(contract, rows, partition_value=partition_value)
                    row_count += len(rows)
                    for row in rows:
                        logical_key = _key(contract, row)
                        if not seen.add_key(logical_key):
                            raise ArtifactError(
                                f"{contract.artifact_id} duplicate logical key "
                                f"across partitions: {logical_key!r}"
                            )
                        if contract.date_field:
                            value = _date_text(
                                row.get(contract.date_field), contract.date_field
                            )
                            date_min = (
                                value if date_min is None else min(date_min, value)
                            )
                            date_max = (
                                value if date_max is None else max(date_max, value)
                            )
                        if contract.availability_field:
                            value = _date_text(
                                row.get(contract.availability_field),
                                contract.availability_field,
                            )
                            availability_min = (
                                value
                                if availability_min is None
                                else min(availability_min, value)
                            )
                            availability_max = (
                                value
                                if availability_max is None
                                else max(availability_max, value)
                            )
                        collection = row.get("source_collection")
                        if collection is not None and str(collection).strip():
                            seen.add_source(str(collection).strip())
                        cutoff = row.get("data_cutoff_at")
                        text = (
                            cutoff.isoformat()
                            if isinstance(cutoff, datetime)
                            else str(cutoff)
                        )
                        parsed = (
                            cutoff
                            if isinstance(cutoff, datetime)
                            else datetime.fromisoformat(text.replace("Z", "+00:00"))
                        )
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        candidate = (parsed.astimezone(timezone.utc), text)
                        if cutoff_max is None or candidate[0] > cutoff_max[0]:
                            cutoff_max = candidate
                    seen.flush()
            finally:
                parquet.close()

        assert common_schema is not None
        source_collections = seen.sources()
    finally:
        seen.close()

    relative_paths = sorted(relative_path for _, relative_path in files)
    return {
        "artifact_id": contract.artifact_id,
        "contract_key": contract.contract_key,
        "variant": contract.variant,
        "schema_version": "1.0",
        "layer": contract.layer,
        "source_families": list(contract.source_families),
        "source_collections": source_collections,
        "row_count": row_count,
        "date_range": [date_min, date_max] if date_min is not None else None,
        "availability_date_range": [availability_min, availability_max] if availability_min is not None else None,
        "columns": list(common_schema.names),
        "schema_fingerprint": _schema_fingerprint(common_schema),
        "partitioning": [contract.partition_name] if contract.partition_name else ["single_file"],
        "artifact_paths": relative_paths,
        "pit_policy": contract.pit_policy,
        "data_cutoff_at": cutoff_max[1] if cutoff_max is not None else None,
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": _utc_now(),
    }


def _empty_manifest(
    contract: ArtifactContract,
    previous_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not contract.allow_empty:
        raise ArtifactError(f"{contract.artifact_id} does not allow empty inventory")
    previous_manifest = previous_manifest or {}
    previous_columns = previous_manifest.get("columns")
    compatible_previous = (
        isinstance(previous_columns, list)
        and all(isinstance(column, str) for column in previous_columns)
        and set(contract.required_columns).issubset(previous_columns)
        and isinstance(previous_manifest.get("schema_fingerprint"), str)
    )
    columns = (
        list(previous_columns)
        if compatible_previous
        else list(contract.required_columns)
    )
    schema_fingerprint = (
        previous_manifest["schema_fingerprint"]
        if compatible_previous
        else empty_contract_schema_fingerprint(contract)
    )
    return {
        "artifact_id": contract.artifact_id,
        "contract_key": contract.contract_key,
        "variant": contract.variant,
        "schema_version": "1.0",
        "layer": contract.layer,
        "source_families": list(contract.source_families),
        "source_collections": [],
        "row_count": 0,
        "date_range": None,
        "availability_date_range": None,
        "columns": columns,
        "schema_fingerprint": schema_fingerprint,
        "partitioning": [contract.partition_name] if contract.partition_name else ["single_file"],
        "artifact_paths": [],
        "pit_policy": contract.pit_policy,
        "data_cutoff_at": None,
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": _utc_now(),
    }


def _publish_empty_inventory(
    context: DataAnalystsContext,
    contract: ArtifactContract,
) -> PublicationResult:
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    previous_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else None
    )
    manifest = _empty_manifest(contract, previous_manifest)
    manifest["active_version"] = uuid.uuid4().hex
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return PublicationResult(
        touched_paths=(),
        total_row_count=0,
        date_range=None,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def _inventory(context: DataAnalystsContext, contract: ArtifactContract) -> list[Path]:
    if contract.publication_mode == "full_replace":
        return _full_replace_active_inventory(context, contract)
    if contract.publication_mode in {"partition_upsert", "snapshot_by_value"}:
        return _partition_active_inventory(context, contract)
    base = context.artifact_path(contract.base_path)
    if not base.exists():
        return []
    return sorted(base.glob(contract.inventory_glob().removeprefix(f"{contract.base_path}/")))


def _partition_active_inventory(
    context: DataAnalystsContext, contract: ArtifactContract
) -> list[Path]:
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    if not manifest_path.exists():
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
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"{contract.artifact_id} cannot read active manifest: {exc}"
        ) from exc
    paths = manifest.get("artifact_paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ArtifactError(
            f"{contract.artifact_id} partition manifest has invalid artifact_paths"
        )
    if not paths and not contract.allow_empty:
        raise ArtifactError(
            f"{contract.artifact_id} partition manifest has empty artifact_paths"
        )
    active = [context.artifact_path(path) for path in paths]
    missing = [path for path in active if not path.is_file()]
    if missing:
        raise ArtifactError(
            f"{contract.artifact_id} active partition is missing: {missing}"
        )
    return active


def _full_replace_active_inventory(
    context: DataAnalystsContext, contract: ArtifactContract
) -> list[Path]:
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"{contract.artifact_id} cannot read active manifest: {exc}"
        ) from exc
    if manifest.get("artifact_id") != contract.artifact_id:
        raise ArtifactError(
            f"{contract.artifact_id} active manifest artifact_id mismatch"
        )
    artifact_paths = manifest.get("artifact_paths")
    if not isinstance(artifact_paths, list) or len(artifact_paths) != 1:
        raise ArtifactError(
            f"{contract.artifact_id} full_replace manifest must list one active path"
        )
    relative_path = artifact_paths[0]
    if not isinstance(relative_path, str) or not _is_full_replace_version_path(
        contract, relative_path
    ):
        raise ArtifactError(
            f"{contract.artifact_id} invalid active version path: {relative_path!r}"
        )
    return [context.artifact_path(relative_path)]


def _is_full_replace_version_path(
    contract: ArtifactContract, relative_path: str
) -> bool:
    path_parts = PurePosixPath(relative_path).parts
    base_parts = PurePosixPath(contract.base_path).parts
    remainder = path_parts[len(base_parts) :]
    return (
        path_parts[: len(base_parts)] == base_parts
        and len(remainder) == 3
        and remainder[0] == "versions"
        and bool(remainder[1])
        and remainder[1] not in {".", ".."}
        and remainder[2] == contract.file_name
    )


def _validate_inventory_paths(
    context: DataAnalystsContext,
    contract: ArtifactContract,
    inventory: list[Path],
) -> None:
    base = context.artifact_path(contract.base_path)
    if not base.exists():
        return
    if contract.publication_mode == "full_replace":
        allowed_versions = {
            path.resolve()
            for path in base.glob(
                contract.inventory_glob().removeprefix(f"{contract.base_path}/")
            )
        }
        invalid_active = sorted(
            path for path in inventory if path.resolve() not in allowed_versions
        )
        if invalid_active:
            paths = ", ".join(str(path) for path in invalid_active)
            raise ArtifactError(
                f"{contract.artifact_id} invalid active version path: {paths}"
            )
        orphans = sorted(
            path
            for path in base.rglob("*.parquet")
            if path.resolve() not in allowed_versions
        )
        if orphans:
            paths = ", ".join(_relative_store_path(context, path) for path in orphans)
            raise ArtifactError(f"{contract.artifact_id} orphan parquet: {paths}")
        return
    if contract.publication_mode in {"partition_upsert", "snapshot_by_value"}:
        allowed_versioned = {
            path.resolve()
            for path in base.glob(
                contract.inventory_glob().removeprefix(f"{contract.base_path}/")
            )
            if path.is_file()
        }
        allowed_legacy = {
            path.resolve()
            for path in base.glob(
                contract.legacy_inventory_glob().removeprefix(
                    f"{contract.base_path}/"
                )
            )
            if path.is_file()
        }
        active_is_versioned = any(
            path.resolve() in allowed_versioned for path in inventory
        )
        invalid_active = [
            path
            for path in inventory
            if path.resolve() not in allowed_versioned | allowed_legacy
        ]
        if invalid_active:
            raise ArtifactError(
                f"{contract.artifact_id} invalid active inventory: {invalid_active}"
            )
        orphans = [
            path
            for path in base.rglob("*.parquet")
            if path.resolve() not in allowed_versioned
            and (active_is_versioned or path.resolve() not in allowed_legacy)
        ]
        if orphans:
            raise ArtifactError(
                f"{contract.artifact_id} orphan parquet: "
                f"{[_relative_store_path(context, path) for path in orphans]}"
            )
        return
    expected = {path.resolve() for path in inventory}
    orphans = sorted(
        path for path in base.rglob("*.parquet") if path.resolve() not in expected
    )
    if orphans:
        paths = ", ".join(_relative_store_path(context, path) for path in orphans)
        raise ArtifactError(f"{contract.artifact_id} orphan parquet: {paths}")


def _common_inventory_schema(
    inventory: Iterable[Path], contract: ArtifactContract
) -> pa.Schema | None:
    common: pa.Schema | None = None
    for path in inventory:
        schema = pq.read_schema(path)
        missing = sorted(set(contract.required_columns) - set(schema.names))
        if missing:
            raise ArtifactError(
                f"{contract.artifact_id} schema missing required columns: {', '.join(missing)}"
            )
        if common is None:
            common = schema
        elif not schema.equals(common, check_metadata=False):
            raise ArtifactError(f"{contract.artifact_id} schema mismatch: {path}")
    return common


def _table_from_rows(
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
    *,
    write_schema: pa.Schema | None = None,
) -> pa.Table:
    columns = _columns_from_rows(rows)
    ordered = (
        list(write_schema.names)
        if write_schema is not None
        else list(dict.fromkeys([*contract.required_columns, *columns]))
    )
    try:
        return pa.table(
            {
                column: [_normalize_parquet_scalar(row.get(column)) for row in rows]
                for column in ordered
            },
            schema=write_schema,
        )
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise ArtifactError(f"{contract.artifact_id} schema mismatch: {exc}") from exc


def _full_history_write_schema(
    contract: ArtifactContract, rows: list[dict[str, Any]]
) -> pa.Schema:
    """Infer one permissive schema over all rows before partitioned publication.

    Mongo source documents can introduce optional fields in only some years.
    A bounded batch union avoids materializing one giant Arrow table while
    guaranteeing that every full-history partition has the same schema.
    """
    column_names = list(
        dict.fromkeys([*contract.required_columns, *_columns_from_rows(rows)])
    )
    schema = pa.schema([pa.field(column, pa.null()) for column in column_names])
    for offset in range(0, len(rows), 65536):
        batch = rows[offset : offset + 65536]
        batch_schema = pa.table(
            {
                column: [
                    _normalize_parquet_scalar(row.get(column)) for row in batch
                ]
                for column in column_names
            }
        ).schema
        try:
            schema = pa.unify_schemas([schema, batch_schema], promote_options="permissive")
        except pa.ArrowException as exc:
            raise ArtifactError(
                f"{contract.artifact_id} cannot unify full-history schema: {exc}"
            ) from exc
    return schema


def _unique_rows(
    contract: ArtifactContract,
    rows: Iterable[dict[str, Any]],
    label: str,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = _key(contract, row)
        if key in unique:
            raise ArtifactError(
                f"{contract.artifact_id} duplicate logical key in {label} row {index}: {key!r}"
            )
        unique[key] = row
    return unique


def _validate_unique_rows_spill(
    contract: ArtifactContract,
    rows: Iterable[dict[str, Any]],
    label: str,
) -> None:
    index = _SpillKeyIndex()
    try:
        for row_index, row in enumerate(rows):
            key = _key(contract, row)
            if not index.add_key(key):
                raise ArtifactError(
                    f"{contract.artifact_id} duplicate logical key in {label} "
                    f"row {row_index}: {key!r}"
                )
            if row_index and row_index % 65536 == 0:
                index.flush()
        index.flush()
    finally:
        index.close()


def _key(contract: ArtifactContract, row: dict[str, Any]) -> tuple[Any, ...]:
    values: list[Any] = []
    for field in contract.logical_key:
        if field not in row:
            raise ArtifactError(
                f"{contract.artifact_id} logical key {contract.logical_key} "
                f"missing field: {field}"
            )
        values.append(_comparable(row[field]))
    key = tuple(values)
    try:
        hash(key)
    except TypeError as exc:
        raise ArtifactError(
            f"{contract.artifact_id} has unhashable logical key "
            f"{contract.logical_key}: {key!r}"
        ) from exc
    return key


def _sort_key(contract: ArtifactContract, row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(_comparable(row[field]) for field in contract.logical_key)


def _comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _partition_value(contract: ArtifactContract, row: dict[str, Any]) -> str:
    if contract.partition_field is None or contract.partition_name is None:
        raise ArtifactError(f"{contract.artifact_id} requires partition fields")
    value = _date_text(row.get(contract.partition_field), contract.partition_field)
    if contract.partition_name == "year" or contract.partition_name.endswith("_year"):
        return value[:4]
    return value


def _partition_from_path(contract: ArtifactContract, path: Path) -> str:
    if contract.partition_name is None:
        raise ArtifactError(f"{contract.artifact_id} requires a partition name")
    prefix = f"{contract.partition_name}="
    parent = path.parent.name
    if not parent.startswith(prefix) or not parent[len(prefix) :]:
        raise ArtifactError(f"{contract.artifact_id} invalid inventory path: {path}")
    return parent[len(prefix) :]


def _partition_from_relative_path(
    contract: ArtifactContract, relative_path: str
) -> str:
    if contract.partition_name is None:
        raise ArtifactError(f"{contract.artifact_id} requires a partition name")
    parts = PurePosixPath(relative_path).parts
    prefix = f"{contract.partition_name}="
    matches = [part[len(prefix) :] for part in parts if part.startswith(prefix)]
    if len(matches) != 1 or not matches[0]:
        raise ArtifactError(
            f"{contract.artifact_id} invalid inventory path: {relative_path}"
        )
    return matches[0]


def _range(rows: list[dict[str, Any]], field: str | None) -> list[str] | None:
    if field is None or not rows:
        return None
    values = sorted(_date_text(row.get(field), field) for row in rows)
    return [values[0], values[-1]]


def _date_text(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError as exc:
            raise ArtifactError(f"invalid ISO date in {field}: {value!r}") from exc
    raise ArtifactError(f"invalid ISO date in {field}: {value!r}")


def _max_cutoff(rows: list[dict[str, Any]]) -> str:
    cutoffs: list[tuple[datetime, str]] = []
    for row in rows:
        value = row.get("data_cutoff_at")
        if isinstance(value, datetime):
            parsed = value
            text = value.isoformat()
        else:
            text = str(value)
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        cutoffs.append((parsed.astimezone(timezone.utc), text))
    return max(cutoffs, key=lambda item: item[0])[1]


def _schema_fingerprint(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def _source_collections(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(row["source_collection"]).strip()
            for row in rows
            if row.get("source_collection") is not None
            and str(row["source_collection"]).strip()
        }
    )


def _backup_files(targets: Iterable[Path]) -> list[_FileBackup]:
    backups: list[_FileBackup] = []
    try:
        for target in targets:
            if target.exists():
                backup = target.with_name(
                    f".{target.name}.{uuid.uuid4().hex}.publication-backup"
                )
                backups.append(_FileBackup(target=target, backup=backup))
                shutil.copyfile(target, backup)
            else:
                backups.append(_FileBackup(target=target, backup=None))
        return backups
    except Exception:
        _discard_backups(backups)
        raise


def _restore_files(backups: list[_FileBackup]) -> list[_RestoreFailure]:
    failures: list[_RestoreFailure] = []
    for record in reversed(backups):
        try:
            if record.backup is None:
                if record.target.exists():
                    record.target.unlink()
            else:
                os.replace(record.backup, record.target)
        except Exception as exc:
            failures.append(_RestoreFailure(record=record, error=exc))
    return failures


def _discard_backups(
    backups: Iterable[_FileBackup],
    *,
    preserve: set[Path] | None = None,
    committed: bool = False,
    artifact_id: str = "publication",
) -> tuple[str, ...]:
    preserved = preserve or set()
    diagnostics: list[str] = []
    for record in backups:
        if (
            record.backup is not None
            and record.backup not in preserved
            and record.backup.exists()
        ):
            try:
                record.backup.unlink()
            except OSError as exc:
                state = "publication committed" if committed else "publication failed"
                diagnostics.append(
                    f"{artifact_id} {state}; backup cleanup failed: "
                    f"recovery={record.backup}, error={exc}"
                )
    return tuple(diagnostics)


def _relative_store_path(context: DataAnalystsContext, path: Path) -> str:
    relative = path.resolve().relative_to(context.data_store.resolve())
    return PurePosixPath(*relative.parts).as_posix()
