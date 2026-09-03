from __future__ import annotations

import hashlib
import errno
import json
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifacts import _normalize_parquet_scalar
from data_analysts.filesystem import replace_file
from data_analysts.paths import (
    FORBIDDEN_ARTIFACT_PATH_SEGMENTS,
    DataAnalystsContext,
    PathBoundaryError,
)


_SUPPORTED_NUMPY_DATETIME_UNITS = frozenset(
    {"Y", "M", "W", "D", "h", "m", "s", "ms", "us", "ns"}
)
_NUMPY_DATETIME_MICROSECONDS = {
    "h": 60 * 60 * 1_000_000,
    "m": 60 * 1_000_000,
    "s": 1_000_000,
    "ms": 1_000,
    "us": 1,
}
_EPOCH_DATE = date(1970, 1, 1)
_EPOCH_DATETIME = datetime(1970, 1, 1)
_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
_RESERVED_FORMAL_LOCK_COMPONENTS = ("jobs", "publish.lock")
_RESERVED_FORMAL_STAGING_COMPONENTS = ("jobs", ".publish-staging")
_WINDOWS_ILLEGAL_COMPONENT_CHARACTERS = frozenset('?*|"<>')
_WINDOWS_FORBIDDEN_FORMAL_COMPONENTS = frozenset(
    component.casefold() for component in FORBIDDEN_ARTIFACT_PATH_SEGMENTS
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PublishTransactionError(RuntimeError):
    """Raised when a partition transaction cannot be published safely."""


class PartitionSourceSnapshot(dict[str, str | None]):
    """Lazily capture immutable entry hashes for the physical read closure."""

    def __init__(self, context: DataAnalystsContext) -> None:
        super().__init__()
        self.context = context

    def capture(self, artifact_path: str | Path) -> str | None:
        normalized = _validate_formal_artifact_path(
            self.context, artifact_path, path_kind="source snapshot"
        )
        if normalized in self:
            return self[normalized]
        target = self.context.artifact_path(normalized)
        if not target.exists():
            self[normalized] = None
            return None
        if not target.is_file():
            raise PublishTransactionError(
                f"source snapshot target is not a file: {normalized}"
            )
        before = _file_identity(target)
        content_sha256 = _content_sha256(target)
        after = _file_identity(target)
        if before != after:
            raise PublishTransactionError(
                f"source changed while taking entry snapshot: {normalized}"
            )
        self[normalized] = content_sha256
        return content_sha256


@dataclass(frozen=True)
class PartitionSpec:
    base_path: str
    partition_field: str
    partition_name: str
    key_fields: tuple[str, ...]
    required_columns: tuple[str, ...]
    partition_derivation: Literal["year", "identity"] = "year"
    column_types: Mapping[str, pa.DataType] | None = None


@dataclass(frozen=True)
class StagedPartition:
    artifact_path: str
    staged_path: Path
    backup_path: Path
    row_count: int
    date_range: tuple[str, str] | None
    content_sha256: str
    source_exists: bool | None = None
    source_sha256: str | None = None
    source_row_count: int | None = None
    source_date_range: tuple[str, str] | None = None


@dataclass(frozen=True)
class _CommitEntry:
    artifact_path: str
    staged_path: Path
    backup_path: Path
    target_path: Path
    existed: bool
    kind: str


@dataclass(frozen=True)
class _CleanupFailure:
    message: str
    cause: OSError

    def __str__(self) -> str:
        return self.message


def stage_partition_rows(
    context: DataAnalystsContext,
    rows: Iterable[Mapping[str, Any]],
    spec: PartitionSpec,
    *,
    mode: Literal["replace", "upsert"],
    transaction_root: Path | None = None,
    source_snapshot: Mapping[str, str | None] | None = None,
) -> list[StagedPartition]:
    """Stage deterministic replacements for only the incoming partitions."""
    normalized_base_path = _validate_spec(context, spec, mode)
    incoming_rows = [dict(row) for row in rows]
    if not incoming_rows:
        return []
    incoming_rows = _canonicalize_rows(incoming_rows, spec, source="incoming")
    _reject_duplicate_keys(incoming_rows, spec.key_fields, source="incoming")

    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    for row in incoming_rows:
        partition_value = _partition_value(
            row[spec.partition_field], spec.partition_derivation
        )
        grouped_rows.setdefault(partition_value, []).append(row)

    staging_base = context.store_path("jobs", ".publish-staging")
    if transaction_root is None:
        transaction_root = staging_base / uuid.uuid4().hex
    else:
        transaction_root = transaction_root.resolve()
        try:
            relative_root = transaction_root.relative_to(staging_base.resolve())
        except ValueError as exc:
            raise PublishTransactionError(
                f"transaction root escapes publish staging: {transaction_root}"
            ) from exc
        if len(relative_root.parts) != 1 or not transaction_root.is_dir():
            raise PublishTransactionError(
                f"invalid existing transaction root: {transaction_root}"
            )
    staged_partitions: list[StagedPartition] = []
    try:
        prepared_partitions: list[
            tuple[
                str,
                str,
                list[dict[str, Any]],
                pa.Table,
                bool | None,
                str | None,
                int | None,
                tuple[str, str] | None,
            ]
        ] = []
        partition_artifact_paths = {
            partition_value: _partition_artifact_path(
                context,
                normalized_base_path,
                spec.partition_name,
                partition_value,
            )
            for partition_value in sorted(grouped_rows)
        }
        _validate_publish_target_collisions(
            context, partition_artifact_paths.values()
        )
        for partition_value, artifact_path in partition_artifact_paths.items():
            target_path = context.artifact_path(artifact_path)
            partition_rows = grouped_rows[partition_value]
            source_exists: bool | None = None
            source_sha256: str | None = None
            source_row_count: int | None = None
            source_date_range: tuple[str, str] | None = None
            if source_snapshot is not None:
                if artifact_path not in source_snapshot:
                    raise PublishTransactionError(
                        f"entry source snapshot missing partition: {artifact_path}"
                    )
                source_sha256 = source_snapshot[artifact_path]
                source_exists = source_sha256 is not None
                actual_exists = target_path.is_file()
                if actual_exists != source_exists:
                    raise PublishTransactionError(
                        f"entry source existence changed before staging: {artifact_path}"
                    )
            elif mode == "upsert":
                source_exists = target_path.exists()
            if mode == "upsert" and source_exists:
                if source_sha256 is None:
                    source_sha256 = _content_sha256(target_path)
                elif _content_sha256(target_path) != source_sha256:
                    raise PublishTransactionError(
                        f"entry source changed before staging: {artifact_path}"
                    )
                existing_rows = _read_parquet_rows(target_path)
                source_row_count = len(existing_rows)
                source_date_range = _date_range(existing_rows, spec.partition_field)
                if _content_sha256(target_path) != source_sha256:
                    raise PublishTransactionError(
                        f"upsert source changed while staging: {artifact_path}"
                    )
                existing_rows = _canonicalize_rows(
                    existing_rows, spec, source="existing"
                )
                _reject_duplicate_keys(existing_rows, spec.key_fields, source="existing")
                merged_rows = _merge_rows(existing_rows, partition_rows, spec.key_fields)
                incoming_new_key_count = len(
                    _row_keys(partition_rows, spec.key_fields)
                    - _row_keys(existing_rows, spec.key_fields)
                )
                expected_count = len(existing_rows) + incoming_new_key_count
                if len(merged_rows) != expected_count:
                    raise PublishTransactionError(
                        "row-count conservation failed for "
                        f"{artifact_path}: merged={len(merged_rows)}, "
                        f"expected={expected_count}"
                    )
            else:
                merged_rows = [dict(row) for row in partition_rows]

            merged_rows.sort(key=lambda row: _sort_key(row, spec.key_fields))
            parquet_table = _materialize_parquet_table(
                merged_rows,
                spec,
                artifact_path=artifact_path,
            )
            prepared_partitions.append(
                (
                    partition_value,
                    artifact_path,
                    merged_rows,
                    parquet_table,
                    source_exists,
                    source_sha256,
                    source_row_count,
                    source_date_range,
                )
            )

        for (
            partition_value,
            artifact_path,
            merged_rows,
            parquet_table,
            source_exists,
            source_sha256,
            source_row_count,
            source_date_range,
        ) in prepared_partitions:
            staged_path = transaction_root / "partitions" / PurePosixPath(artifact_path)
            backup_path = transaction_root / "backups" / PurePosixPath(artifact_path)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            _write_parquet(staged_path, parquet_table)
            _validate_staged_partition(staged_path, merged_rows, spec)
            staged_partitions.append(
                StagedPartition(
                    artifact_path=artifact_path,
                    staged_path=staged_path,
                    backup_path=backup_path,
                    row_count=len(merged_rows),
                    date_range=_date_range(merged_rows, spec.partition_field),
                    content_sha256=_content_sha256(staged_path),
                    source_exists=source_exists,
                    source_sha256=source_sha256,
                    source_row_count=source_row_count,
                    source_date_range=source_date_range,
                )
            )
        return staged_partitions
    except Exception as exc:
        cleanup_errors = _cleanup_transaction_roots({transaction_root})
        if cleanup_errors:
            raise PublishTransactionError(
                f"failed to stage partition transaction: {exc}; "
                f"staging cleanup errors: {_render_cleanup_errors(cleanup_errors)}; "
                f"recovery path: {transaction_root}"
            ) from exc
        if isinstance(exc, PublishTransactionError):
            raise
        raise PublishTransactionError(
            f"failed to stage partition transaction: {exc}"
        ) from exc


def snapshot_partition_sources(
    context: DataAnalystsContext,
    artifact_paths: Iterable[str | Path],
) -> PartitionSourceSnapshot:
    """Capture one entry SHA for each bounded formal partition source."""
    normalized = sorted(
        {
            _validate_formal_artifact_path(
                context, path, path_kind="source snapshot"
            )
            for path in artifact_paths
        }
    )
    _validate_publish_target_collisions(context, normalized)
    snapshots = PartitionSourceSnapshot(context)
    for artifact_path in normalized:
        snapshots.capture(artifact_path)
    return snapshots


def capture_partition_source(
    context: DataAnalystsContext,
    source_snapshot: Mapping[str, str | None],
    artifact_path: str | Path,
) -> str | None:
    normalized = _validate_formal_artifact_path(
        context, artifact_path, path_kind="source snapshot"
    )
    if isinstance(source_snapshot, PartitionSourceSnapshot):
        if source_snapshot.context != context:
            raise PublishTransactionError("source snapshot context mismatch")
        return source_snapshot.capture(normalized)
    if normalized not in source_snapshot:
        raise PublishTransactionError(
            f"entry source snapshot missing partition: {normalized}"
        )
    return source_snapshot[normalized]


def commit_publish_transaction(
    context: DataAnalystsContext,
    staged_partitions: Iterable[StagedPartition],
    metadata_payloads: Mapping[str | Path, Mapping[str, Any]],
    *,
    source_preconditions: Mapping[str | Path, str | None] | None = None,
) -> None:
    """Atomically publish staged partitions, then JSON metadata, with rollback."""
    partitions = list(staged_partitions)
    transaction_roots: set[Path] = set()
    transaction_root: Path | None = None
    lock_path = context.store_path("jobs", "publish.lock")
    lock_acquired = False
    attempted: list[_CommitEntry] = []
    primary_error: Exception | None = None
    rollback_errors: list[str] = []
    try:
        transaction_root, transaction_roots = _resolve_transaction_root(
            context, partitions, lock_path
        )
        _validate_partition_formal_paths(context, partitions)
        validated_metadata_payloads = _validate_metadata_targets(
            context, metadata_payloads
        )
        validated_source_preconditions = _validate_source_precondition_targets(
            context, source_preconditions
        )
        publish_artifact_paths = [
            *(partition.artifact_path for partition in partitions),
            *(artifact_path for artifact_path, _ in validated_metadata_payloads),
        ]
        _validate_publish_target_identities(publish_artifact_paths)
        _validate_source_precondition_publish_aliases(
            validated_source_preconditions, publish_artifact_paths
        )
        _validate_physical_publish_target_collisions(
            context,
            (artifact_path for artifact_path, _ in validated_metadata_payloads),
        )
        _validate_physical_publish_target_collisions(
            context,
            (artifact_path for artifact_path, _ in validated_source_preconditions),
        )
        if transaction_root is None:
            transaction_root = (
                context.store_path("jobs", ".publish-staging") / uuid.uuid4().hex
            )
            transaction_root.mkdir(parents=True, exist_ok=False)
            transaction_roots = {transaction_root}
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock_path.open("x", encoding="utf-8") as lock_file:
                lock_file.write(transaction_root.name)
        except FileExistsError as exc:
            raise PublishTransactionError(
                f"publish lock already exists: {lock_path}"
            ) from exc
        lock_acquired = True

        _validate_physical_publish_target_collisions(
            context, publish_artifact_paths
        )
        _validate_physical_publish_target_collisions(
            context,
            (artifact_path for artifact_path, _ in validated_source_preconditions),
        )
        _validate_lock_scoped_source_preconditions(
            context, validated_source_preconditions
        )
        metadata_entries = _stage_metadata(
            context,
            transaction_root,
            validated_metadata_payloads,
        )
        partition_entries = _preflight_partitions(context, transaction_root, partitions)
        entries = [*partition_entries, *metadata_entries]
        _reject_duplicate_targets(entries)
        _validate_source_preconditions(partitions, partition_entries)
        _prepare_backups(entries)
        journal_path = transaction_root / "rollback_journal.json"
        _write_journal(journal_path, entries, attempted, status="prepared")

        for entry in entries:
            attempted.append(entry)
            _write_journal(journal_path, entries, attempted, status="committing")
            entry.target_path.parent.mkdir(parents=True, exist_ok=True)
            replace_file(entry.staged_path, entry.target_path)
        _write_journal(journal_path, entries, attempted, status="committed")
    except Exception as exc:
        primary_error = exc
        rollback_errors = _rollback(attempted)

    if rollback_errors:
        assert transaction_root is not None
        journal_path = transaction_root / "rollback_journal.json"
        raise PublishTransactionError(
            "commit publish transaction failed and rollback was incomplete: "
            f"{'; '.join(rollback_errors)}; recovery root: {transaction_root}; "
            f"journal: {journal_path}; lock retained: {lock_path}"
        ) from primary_error

    cleanup_errors: list[_CleanupFailure] = []
    if lock_acquired:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(
                _CleanupFailure(f"lock cleanup {lock_path}: {exc}", exc)
            )
    cleanup_errors.extend(
        _cleanup_transaction_roots_respecting_lock(transaction_roots, lock_path)
    )

    if primary_error is not None:
        if cleanup_errors:
            raise PublishTransactionError(
                f"failed to commit publish transaction: {primary_error}; "
                f"cleanup errors: {_render_cleanup_errors(cleanup_errors)}"
            ) from primary_error
        if isinstance(primary_error, PublishTransactionError):
            raise primary_error
        raise PublishTransactionError(
            f"failed to commit publish transaction: {primary_error}"
        ) from primary_error
    if cleanup_errors:
        assert transaction_root is not None
        raise PublishTransactionError(
            "publish committed but cleanup failed: "
            + _render_cleanup_errors(cleanup_errors)
            + f"; recovery root: {transaction_root}; "
            + f"journal: {transaction_root / 'rollback_journal.json'}"
        ) from cleanup_errors[0].cause


def _validate_spec(
    context: DataAnalystsContext,
    spec: PartitionSpec,
    mode: str,
) -> str:
    if mode not in {"replace", "upsert"}:
        raise PublishTransactionError(f"unsupported partition mode: {mode}")
    if not spec.partition_field or not spec.partition_name or not spec.key_fields:
        raise PublishTransactionError(
            "partition_field, partition_name, and key_fields are required"
        )
    if not spec.required_columns:
        raise PublishTransactionError("required_columns must not be empty")
    if spec.partition_derivation not in {"year", "identity"}:
        raise PublishTransactionError(
            f"unsupported partition derivation: {spec.partition_derivation}"
        )
    missing_contract_columns = [
        column
        for column in (spec.partition_field, *spec.key_fields)
        if column not in spec.required_columns
    ]
    if missing_contract_columns:
        raise PublishTransactionError(
            "required_columns must include partition and key fields: "
            + ", ".join(missing_contract_columns)
        )
    normalized_base_path = _validate_formal_artifact_path(
        context,
        spec.base_path,
        path_kind="formal",
    ).rstrip("/")
    _validate_windows_formal_component(
        spec.partition_name,
        artifact_path=f"partition name {spec.partition_name!r}",
        path_kind="formal",
    )
    return normalized_base_path


def _normalize_transaction_scalar(value: Any) -> Any:
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return None
        unit, step = np.datetime_data(value.dtype)
        if step != 1 or unit not in _SUPPORTED_NUMPY_DATETIME_UNITS:
            raise PublishTransactionError(
                f"unsupported numpy datetime64 unit: {value.dtype}"
            )
        return _materialize_numpy_datetime(value, unit)
    return _normalize_parquet_scalar(value)


def _materialize_numpy_datetime(value: np.datetime64, unit: str) -> date | datetime:
    ticks = int(value.astype(np.int64))
    try:
        if unit == "Y":
            return date(1970 + ticks, 1, 1)
        if unit == "M":
            year, zero_based_month = divmod((1970 * 12) + ticks, 12)
            return date(year, zero_based_month + 1, 1)
        if unit in {"W", "D"}:
            day_ticks = ticks * 7 if unit == "W" else ticks
            return _EPOCH_DATE + timedelta(days=day_ticks)
        if unit == "ns":
            microseconds, nanoseconds = divmod(ticks, 1_000)
            materialized = _EPOCH_DATETIME + timedelta(microseconds=microseconds)
            if nanoseconds:
                materialized = pa.scalar(value).as_py()
            if not isinstance(materialized, datetime):
                raise TypeError(f"unexpected datetime materialization: {materialized!r}")
            return materialized
        return _EPOCH_DATETIME + timedelta(
            microseconds=ticks * _NUMPY_DATETIME_MICROSECONDS[unit]
        )
    except (OverflowError, TypeError, ValueError) as exc:
        raise PublishTransactionError(
            f"numpy datetime64 value is outside safe datetime range: {value!r}"
        ) from exc


def _canonical_scalar_value(value: Any) -> Any:
    normalized = _normalize_transaction_scalar(value)
    if isinstance(normalized, float) and normalized == 0.0:
        return 0.0
    return normalized


def _canonicalize_rows(
    rows: Iterable[Mapping[str, Any]],
    spec: PartitionSpec,
    *,
    source: str,
) -> list[dict[str, Any]]:
    canonical_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        missing = [column for column in spec.required_columns if column not in row]
        if missing:
            raise PublishTransactionError(
                f"{source} row {index} missing required columns: {', '.join(missing)}"
            )
        try:
            canonical_row = {
                column: _canonical_scalar_value(value)
                for column, value in row.items()
            }
        except PublishTransactionError as exc:
            raise PublishTransactionError(
                f"{source} row {index} has unsupported scalar: {exc}"
            ) from exc
        null_keys = [
            column
            for column in spec.key_fields
            if canonical_row.get(column) is None
        ]
        if null_keys:
            raise PublishTransactionError(
                f"{source} row {index} has null key fields: {', '.join(null_keys)}"
            )
        if canonical_row.get(spec.partition_field) is None:
            raise PublishTransactionError(
                f"{source} row {index} has null partition field: {spec.partition_field}"
            )
        canonical_rows.append(canonical_row)
    return canonical_rows


def _reject_duplicate_keys(
    rows: Iterable[Mapping[str, Any]],
    key_fields: tuple[str, ...],
    *,
    source: str,
) -> None:
    seen: set[tuple[tuple[str, str], ...]] = set()
    for row in rows:
        key = _canonical_key(row, key_fields)
        if key in seen:
            values = tuple(row[field] for field in key_fields)
            raise PublishTransactionError(f"duplicate {source} key: {values!r}")
        seen.add(key)


def _partition_value(
    value: Any,
    derivation: Literal["year", "identity"],
) -> str:
    canonical_value = _canonical_scalar_value(value)
    if derivation == "year":
        derived_value = _date_like_year(canonical_value)
    else:
        derived_value = canonical_value
    text = str(derived_value)
    partition_value = text
    if (
        not partition_value
        or partition_value in {".", ".."}
        or "/" in partition_value
        or "\\" in partition_value
    ):
        raise PublishTransactionError(f"invalid partition value: {text!r}")
    _validate_windows_formal_component(
        partition_value,
        artifact_path=f"partition value {text!r}",
        path_kind="formal",
    )
    return partition_value


def _date_like_year(value: Any) -> int:
    if isinstance(value, (datetime, date)):
        return value.year
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).year
        except ValueError:
            try:
                return datetime.fromisoformat(value).year
            except ValueError as exc:
                raise PublishTransactionError(
                    f"year partition requires a date/datetime-like value: {value!r}"
                ) from exc
    raise PublishTransactionError(
        f"year partition requires a date/datetime-like value: {value!r}"
    )


def _partition_artifact_path(
    context: DataAnalystsContext,
    base_path: str,
    partition_name: str,
    partition_value: str,
) -> str:
    candidate = PurePosixPath(
        base_path,
        f"{partition_name}={partition_value}",
        "part.parquet",
    ).as_posix()
    return _validate_formal_artifact_path(
        context,
        candidate,
        path_kind="formal",
    )


def _merge_rows(
    existing_rows: Iterable[Mapping[str, Any]],
    incoming_rows: Iterable[Mapping[str, Any]],
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows_by_key = {
        _canonical_key(row, key_fields): dict(row)
        for row in existing_rows
    }
    for row in incoming_rows:
        rows_by_key[_canonical_key(row, key_fields)] = dict(row)
    return list(rows_by_key.values())


def _row_keys(
    rows: Iterable[Mapping[str, Any]],
    key_fields: tuple[str, ...],
) -> set[tuple[tuple[str, str], ...]]:
    return {_canonical_key(row, key_fields) for row in rows}


def _canonical_key(
    row: Mapping[str, Any],
    key_fields: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(_canonical_scalar(row[field]) for field in key_fields)


def _canonical_scalar(value: Any) -> tuple[str, str]:
    normalized = _canonical_scalar_value(value)
    if isinstance(normalized, datetime):
        return ("datetime.datetime", normalized.isoformat())
    if isinstance(normalized, date):
        return ("datetime.date", normalized.isoformat())
    value_type = type(normalized)
    return (
        f"{value_type.__module__}.{value_type.__qualname__}",
        str(normalized),
    )


def _sort_key(
    row: Mapping[str, Any],
    key_fields: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    return _canonical_key(row, key_fields)


def _materialize_parquet_table(
    rows: list[dict[str, Any]],
    spec: PartitionSpec,
    *,
    artifact_path: str,
) -> pa.Table:
    columns = list(spec.required_columns)
    known_columns = set(columns)
    columns.extend(
        sorted(
            {
                column
                for row in rows
                for column in row
                if column not in known_columns
            }
        )
    )
    try:
        data: dict[str, Any] = {}
        for column in columns:
            values = [row.get(column) for row in rows]
            canonical_type = (spec.column_types or {}).get(column)
            if canonical_type is not None:
                data[column] = pa.array(values, type=canonical_type)
            elif any(_has_sub_microsecond_precision(value) for value in values):
                data[column] = pa.array(values, type=pa.timestamp("ns"))
            else:
                data[column] = values
        return pa.table(data)
    except Exception as exc:
        raise PublishTransactionError(
            f"failed Arrow materialization for {artifact_path}: {exc}"
        ) from exc


def _write_parquet(path: Path, table: pa.Table) -> None:
    pq.write_table(table, path)


def _has_sub_microsecond_precision(value: Any) -> bool:
    return isinstance(value, datetime) and bool(getattr(value, "nanosecond", 0))


def _validate_staged_partition(
    path: Path,
    expected_rows: list[dict[str, Any]],
    spec: PartitionSpec,
) -> None:
    parquet_file = pq.ParquetFile(path)
    try:
        actual_row_count = parquet_file.metadata.num_rows
        staged_rows = parquet_file.read().to_pylist()
    finally:
        parquet_file.close()
    if actual_row_count != len(expected_rows):
        raise PublishTransactionError(
            f"staged row count mismatch: {path}; "
            f"expected={len(expected_rows)}, actual={actual_row_count}"
        )
    _canonicalize_rows(staged_rows, spec, source="staged")
    _reject_duplicate_keys(staged_rows, spec.key_fields, source="staged")
    if staged_rows != sorted(staged_rows, key=lambda row: _sort_key(row, spec.key_fields)):
        raise PublishTransactionError(f"staged rows are not deterministically sorted: {path}")


def _date_range(
    rows: Iterable[Mapping[str, Any]],
    partition_field: str,
) -> tuple[str, str] | None:
    values = [str(row[partition_field]) for row in rows]
    return (min(values), max(values)) if values else None


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat_result = path.stat()
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    parquet_file = pq.ParquetFile(path)
    try:
        return parquet_file.read().to_pylist()
    finally:
        parquet_file.close()


def _resolve_transaction_root(
    context: DataAnalystsContext,
    partitions: list[StagedPartition],
    lock_path: Path,
) -> tuple[Path | None, set[Path]]:
    staging_base = context.store_path("jobs", ".publish-staging")
    roots: set[Path] = set()
    try:
        for partition in partitions:
            root = _find_transaction_root(partition.staged_path, staging_base)
            roots.add(root)
            expected_staged = root / "partitions" / PurePosixPath(partition.artifact_path)
            expected_backup = root / "backups" / PurePosixPath(partition.artifact_path)
            if (
                partition.staged_path.resolve() != expected_staged.resolve()
                or partition.backup_path.resolve() != expected_backup.resolve()
            ):
                raise PublishTransactionError(
                    "staged partition paths do not match artifact path: "
                    f"{partition.artifact_path}"
                )
    except PublishTransactionError:
        _cleanup_transaction_roots_respecting_lock(roots, lock_path)
        raise
    if len(roots) > 1:
        _cleanup_transaction_roots_respecting_lock(roots, lock_path)
        raise PublishTransactionError("staged partitions belong to multiple transactions")
    if roots:
        return next(iter(roots)), roots
    return None, set()


def _find_transaction_root(path: Path, staging_base: Path) -> Path:
    resolved_path = path.resolve()
    resolved_base = staging_base.resolve()
    try:
        relative = resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise PublishTransactionError(f"staged path escapes publish staging: {path}") from exc
    if len(relative.parts) < 3:
        raise PublishTransactionError(f"invalid staged partition path: {path}")
    return resolved_base / relative.parts[0]


def _stage_metadata(
    context: DataAnalystsContext,
    transaction_root: Path,
    metadata_payloads: Iterable[tuple[str, Mapping[str, Any]]],
) -> list[_CommitEntry]:
    entries: list[_CommitEntry] = []
    try:
        for artifact_path, payload in metadata_payloads:
            staged_path = transaction_root / "metadata" / PurePosixPath(artifact_path)
            backup_path = transaction_root / "backups" / PurePosixPath(artifact_path)
            target_path = context.artifact_path(artifact_path)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            staged_path.write_text(serialized + "\n", encoding="utf-8")
            entries.append(
                _CommitEntry(
                    artifact_path=artifact_path,
                    staged_path=staged_path,
                    backup_path=backup_path,
                    target_path=target_path,
                    existed=target_path.exists(),
                    kind="metadata",
                )
            )
        return sorted(entries, key=lambda entry: entry.artifact_path)
    except PublishTransactionError:
        raise
    except Exception as exc:
        raise PublishTransactionError(f"failed to stage metadata: {exc}") from exc


def _validate_metadata_targets(
    context: DataAnalystsContext,
    metadata_payloads: Mapping[str | Path, Mapping[str, Any]],
) -> list[tuple[str, Mapping[str, Any]]]:
    validated: list[tuple[str, Mapping[str, Any]]] = []
    for raw_path, payload in metadata_payloads.items():
        artifact_path = _validate_formal_artifact_path(
            context,
            raw_path,
            path_kind="metadata",
        )
        validated.append((artifact_path, payload))
    return validated


def _validate_source_precondition_targets(
    context: DataAnalystsContext,
    source_preconditions: Mapping[str | Path, str | None] | None,
) -> list[tuple[str, str | None]]:
    if source_preconditions is None:
        return []
    if not isinstance(source_preconditions, Mapping):
        raise PublishTransactionError("source preconditions must be a mapping")
    validated: list[tuple[str, str | None]] = []
    for raw_path, expected_sha256 in source_preconditions.items():
        artifact_path = _validate_formal_artifact_path(
            context,
            raw_path,
            path_kind="source precondition",
        )
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or _SHA256_PATTERN.fullmatch(expected_sha256) is None
        ):
            raise PublishTransactionError(
                f"invalid source precondition hash: {artifact_path}"
            )
        validated.append((artifact_path, expected_sha256))
    _validate_publish_target_identities(path for path, _ in validated)
    return sorted(validated)


def _validate_lock_scoped_source_preconditions(
    context: DataAnalystsContext,
    source_preconditions: list[tuple[str, str | None]],
) -> None:
    for artifact_path, expected_sha256 in source_preconditions:
        target = context.artifact_path(artifact_path)
        try:
            target_stat = target.stat()
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise PublishTransactionError(
                f"source precondition unreadable: {artifact_path}: {exc}"
            ) from exc
        if expected_sha256 is None:
            if target_stat is not None:
                raise PublishTransactionError(
                    f"source precondition expected absence: {artifact_path}"
                )
            continue
        if target_stat is None:
            raise PublishTransactionError(
                f"source precondition missing: {artifact_path}"
            )
        if not stat.S_ISREG(target_stat.st_mode):
            raise PublishTransactionError(
                f"source precondition unreadable: {artifact_path}"
            )
        try:
            actual_sha256 = _content_sha256(target)
        except OSError as exc:
            raise PublishTransactionError(
                f"source precondition unreadable: {artifact_path}: {exc}"
            ) from exc
        if actual_sha256 != expected_sha256:
            raise PublishTransactionError(
                f"source precondition hash mismatch: {artifact_path}; "
                f"expected_sha256={expected_sha256}, actual_sha256={actual_sha256}"
            )


def _validate_source_precondition_publish_aliases(
    source_preconditions: list[tuple[str, str | None]],
    publish_artifact_paths: list[str],
) -> None:
    publish_by_identity = {
        _formal_target_identity(artifact_path): artifact_path
        for artifact_path in publish_artifact_paths
    }
    for artifact_path, _ in source_preconditions:
        publish_path = publish_by_identity.get(_formal_target_identity(artifact_path))
        if publish_path is not None and publish_path != artifact_path:
            raise PublishTransactionError(
                "case-insensitive publish target collision: "
                f"{artifact_path} conflicts with {publish_path}"
            )


def _validate_formal_artifact_path(
    context: DataAnalystsContext,
    raw_path: str | Path,
    *,
    path_kind: str,
) -> str:
    raw_parts = PurePosixPath(str(raw_path).replace("\\", "/")).parts
    control_parts = tuple(
        component.casefold().split(":", 1)[0].rstrip(". ")
        for component in raw_parts
    )
    if (
        control_parts[:2] == _RESERVED_FORMAL_LOCK_COMPONENTS
        or control_parts[:2] == _RESERVED_FORMAL_STAGING_COMPONENTS
    ):
        raise PublishTransactionError(
            f"reserved {path_kind} artifact path: {raw_path}"
        )
    try:
        artifact_path = context.validate_artifact_path(raw_path)
    except PathBoundaryError as exc:
        raise PublishTransactionError(
            f"invalid {path_kind} artifact path: {exc}"
        ) from exc
    parts = _canonicalize_windows_formal_components(
        artifact_path,
        path_kind=path_kind,
    )
    forbidden = _WINDOWS_FORBIDDEN_FORMAL_COMPONENTS.intersection(parts)
    if forbidden:
        raise PublishTransactionError(
            f"invalid {path_kind} artifact path: {artifact_path}; "
            f"forbidden segments: {sorted(forbidden)}"
        )
    if (
        parts[:2] == _RESERVED_FORMAL_LOCK_COMPONENTS
        or parts[:2] == _RESERVED_FORMAL_STAGING_COMPONENTS
    ):
        raise PublishTransactionError(
            f"reserved {path_kind} artifact path: {artifact_path}"
        )
    return artifact_path


def _canonicalize_windows_formal_components(
    artifact_path: str,
    *,
    path_kind: str,
) -> tuple[str, ...]:
    canonical_parts: list[str] = []
    for component in PurePosixPath(artifact_path).parts:
        canonical_parts.append(
            _validate_windows_formal_component(
                component,
                artifact_path=artifact_path,
                path_kind=path_kind,
            )
        )
    return tuple(canonical_parts)


def _validate_windows_formal_component(
    component: str,
    *,
    artifact_path: str,
    path_kind: str,
) -> str:
    folded = component.casefold()
    device_stem = folded.split(".", 1)[0].rstrip(" ")
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or ":" in component
        or any(character in _WINDOWS_ILLEGAL_COMPONENT_CHARACTERS for character in component)
        or any(ord(character) < 32 or ord(character) == 127 for character in component)
        or component.endswith((".", " "))
        or device_stem in _WINDOWS_DEVICE_NAMES
    ):
        raise PublishTransactionError(
            f"reserved {path_kind} artifact path: {artifact_path}; "
            f"invalid Windows component: {component!r}"
        )
    return folded


def _formal_target_identity(artifact_path: str) -> tuple[str, ...]:
    return tuple(component.casefold() for component in PurePosixPath(artifact_path).parts)


def _validate_publish_target_collisions(
    context: DataAnalystsContext,
    artifact_paths: Iterable[str],
) -> None:
    paths = list(artifact_paths)
    _validate_publish_target_identities(paths)
    _validate_physical_publish_target_collisions(context, paths)


def _validate_publish_target_identities(artifact_paths: Iterable[str]) -> None:
    seen: dict[tuple[str, ...], str] = {}
    for artifact_path in artifact_paths:
        identity = _formal_target_identity(artifact_path)
        previous = seen.get(identity)
        if previous is not None:
            if previous != artifact_path:
                raise PublishTransactionError(
                    "case-insensitive publish target collision: "
                    f"{previous} conflicts with {artifact_path}"
                )
            raise PublishTransactionError(f"duplicate publish target: {artifact_path}")
        seen[identity] = artifact_path


def _validate_physical_publish_target_collisions(
    context: DataAnalystsContext,
    artifact_paths: Iterable[str],
) -> None:
    directory_indexes: dict[Path, dict[str, list[Path]]] = {}
    for artifact_path in artifact_paths:
        _reject_existing_case_alias(
            context,
            artifact_path,
            directory_indexes=directory_indexes,
        )


def _reject_existing_case_alias(
    context: DataAnalystsContext,
    artifact_path: str,
    *,
    directory_indexes: dict[Path, dict[str, list[Path]]],
) -> None:
    current = context.data_store
    if not current.is_dir():
        return
    actual_parts: list[str] = []
    for requested_component in PurePosixPath(artifact_path).parts:
        index = directory_indexes.get(current)
        if index is None:
            index = {}
            for child in current.iterdir():
                index.setdefault(child.name.casefold(), []).append(child)
            directory_indexes[current] = index
        matches = index.get(requested_component.casefold(), [])
        if not matches:
            return
        if len(matches) != 1:
            raise PublishTransactionError(
                "case-insensitive publish target collision: "
                f"multiple physical components match {artifact_path}"
            )
        matched = matches[0]
        actual_parts.append(matched.name)
        if matched.name != requested_component:
            actual_path = PurePosixPath(*actual_parts).as_posix()
            raise PublishTransactionError(
                "case-insensitive publish target collision: "
                f"{artifact_path} conflicts with existing {actual_path}"
            )
        current = matched


def _validate_partition_formal_paths(
    context: DataAnalystsContext,
    partitions: Iterable[StagedPartition],
) -> None:
    for partition in partitions:
        _validate_formal_artifact_path(
            context,
            partition.artifact_path,
            path_kind="formal",
        )


def _preflight_partitions(
    context: DataAnalystsContext,
    transaction_root: Path,
    partitions: list[StagedPartition],
) -> list[_CommitEntry]:
    entries: list[_CommitEntry] = []
    for partition in sorted(partitions, key=lambda item: item.artifact_path):
        artifact_path = _validate_formal_artifact_path(
            context,
            partition.artifact_path,
            path_kind="formal",
        )
        if not partition.staged_path.is_file():
            raise PublishTransactionError(
                f"staged partition does not exist: {partition.staged_path}"
            )
        if _content_sha256(partition.staged_path) != partition.content_sha256:
            raise PublishTransactionError(
                f"staged partition content hash changed: {partition.artifact_path}"
            )
        parquet_file = pq.ParquetFile(partition.staged_path)
        try:
            actual_row_count = parquet_file.metadata.num_rows
        finally:
            parquet_file.close()
        if actual_row_count != partition.row_count:
            raise PublishTransactionError(
                f"staged partition row count changed: {partition.artifact_path}"
            )
        target_path = context.artifact_path(artifact_path)
        entries.append(
            _CommitEntry(
                artifact_path=artifact_path,
                staged_path=partition.staged_path,
                backup_path=partition.backup_path,
                target_path=target_path,
                existed=target_path.exists(),
                kind="partition",
            )
        )
    return entries


def _reject_duplicate_targets(entries: list[_CommitEntry]) -> None:
    seen: dict[tuple[str, ...], str] = {}
    for entry in entries:
        identity = _formal_target_identity(entry.artifact_path)
        previous = seen.get(identity)
        if previous is not None:
            if previous != entry.artifact_path:
                raise PublishTransactionError(
                    "case-insensitive publish target collision: "
                    f"{previous} conflicts with {entry.artifact_path}"
                )
            raise PublishTransactionError(f"duplicate publish target: {entry.artifact_path}")
        seen[identity] = entry.artifact_path


def _validate_source_preconditions(
    partitions: list[StagedPartition],
    entries: list[_CommitEntry],
) -> None:
    entries_by_artifact = {entry.artifact_path: entry for entry in entries}
    for partition in partitions:
        if partition.source_exists is None:
            continue
        entry = entries_by_artifact[partition.artifact_path]
        actual_exists = entry.target_path.is_file()
        if actual_exists != partition.source_exists:
            raise PublishTransactionError(
                f"stale upsert source existence: {partition.artifact_path}; "
                f"staged_exists={partition.source_exists}, current_exists={actual_exists}"
            )
        if actual_exists:
            actual_sha256 = _content_sha256(entry.target_path)
            if actual_sha256 != partition.source_sha256:
                raise PublishTransactionError(
                    f"stale upsert source hash: {partition.artifact_path}; "
                    f"staged_sha256={partition.source_sha256}, "
                    f"current_sha256={actual_sha256}"
                )


def _prepare_backups(entries: list[_CommitEntry]) -> None:
    for entry in entries:
        if not entry.existed:
            continue
        if not entry.target_path.is_file():
            raise PublishTransactionError(
                f"publish target is not a file: {entry.target_path}"
            )
        entry.backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.target_path, entry.backup_path)


def _write_journal(
    path: Path,
    entries: list[_CommitEntry],
    attempted: list[_CommitEntry],
    *,
    status: str,
) -> None:
    attempted_paths = {entry.artifact_path for entry in attempted}
    payload = {
        "status": status,
        "entries": [
            {
                "artifact_path": entry.artifact_path,
                "backup_path": entry.backup_path.relative_to(path.parent).as_posix(),
                "existed": entry.existed,
                "kind": entry.kind,
                "replacement_attempted": entry.artifact_path in attempted_paths,
            }
            for entry in entries
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rollback(attempted: list[_CommitEntry]) -> list[str]:
    errors: list[str] = []
    for entry in reversed(attempted):
        try:
            if entry.existed:
                if not entry.backup_path.is_file():
                    raise FileNotFoundError(f"missing backup {entry.backup_path}")
                entry.target_path.parent.mkdir(parents=True, exist_ok=True)
                replace_file(entry.backup_path, entry.target_path)
            else:
                entry.target_path.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{entry.artifact_path}: {exc}")
    return errors


def _cleanup_transaction_root(transaction_root: Path) -> None:
    if transaction_root.exists():
        shutil.rmtree(transaction_root)
    staging_base = transaction_root.parent
    try:
        staging_base.rmdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise


def _cleanup_transaction_roots(
    transaction_roots: Iterable[Path],
) -> list[_CleanupFailure]:
    errors: list[_CleanupFailure] = []
    for root in transaction_roots:
        try:
            _cleanup_transaction_root(root)
        except OSError as exc:
            errors.append(_CleanupFailure(f"staging cleanup {root}: {exc}", exc))
    return errors


def _cleanup_transaction_roots_respecting_lock(
    transaction_roots: Iterable[Path], lock_path: Path
) -> list[_CleanupFailure]:
    roots = set(transaction_roots)
    if not lock_path.exists():
        return _cleanup_transaction_roots(roots)
    try:
        lock_transaction_id = lock_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return [
            _CleanupFailure(
                "staging cleanup skipped because publish lock could not be read "
                f"{lock_path}: {exc}",
                exc,
            )
        ]
    return _cleanup_transaction_roots(
        root for root in roots if root.name != lock_transaction_id
    )


def _render_cleanup_errors(errors: Iterable[_CleanupFailure]) -> str:
    return "; ".join(str(error) for error in errors)
