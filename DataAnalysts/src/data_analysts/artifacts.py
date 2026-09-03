from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.filesystem import replace_file
from data_analysts.paths import (
    DataAnalystsContext,
    PathBoundaryError,
    is_absolute_artifact_path,
)


class ArtifactError(ValueError):
    """Raised when an artifact cannot be safely published."""


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_ARTIFACT_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


def _portable_artifact_path_identity(path: str | Path) -> str:
    normalized = os.fspath(path).replace("\\", "/")
    portable = PurePosixPath(normalized)
    parts = portable.parts
    if (
        not parts
        or is_absolute_artifact_path(normalized)
        or ".." in parts
        or any(part.endswith((" ", ".")) for part in parts)
    ):
        raise ArtifactError("manifest fingerprint structure invalid")
    return portable.as_posix().casefold()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    if chunk_size <= 0:
        raise ArtifactError("chunk_size must be positive")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactError("artifact fingerprint source missing") from exc
    return digest.hexdigest()


def build_artifact_fingerprints(
    context: DataAnalystsContext,
    artifact_paths: Sequence[str],
) -> list[dict[str, str]]:
    safe_paths: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for artifact_path in artifact_paths:
        if not isinstance(artifact_path, str):
            raise ArtifactError("manifest fingerprint structure invalid")
        try:
            safe_path = context.validate_artifact_path(artifact_path)
        except PathBoundaryError as exc:
            raise ArtifactError(str(exc)) from exc
        path_identity = _portable_artifact_path_identity(safe_path)
        if path_identity in seen:
            raise ArtifactError("manifest fingerprint structure invalid")
        seen.add(path_identity)
        try:
            final_path = context.artifact_path(safe_path)
        except PathBoundaryError as exc:
            raise ArtifactError(str(exc)) from exc
        if not final_path.is_file():
            raise ArtifactError("artifact fingerprint source missing")
        safe_paths.append((safe_path, final_path))

    fingerprints: list[dict[str, str]] = []
    for safe_path, final_path in safe_paths:
        fingerprints.append(
            {"artifact_path": safe_path, "sha256": sha256_file(final_path)}
        )
    return fingerprints


def validate_manifest_fingerprint_structure(
    payload: Mapping[str, Any],
    *,
    allow_legacy: bool = False,
) -> bool:
    schema_version = payload.get("schema_version")
    if allow_legacy and schema_version == "1.0":
        return False
    if not isinstance(schema_version, str) or schema_version != "1.1":
        raise ArtifactError("unsupported artifact manifest schema")
    paths = payload.get("artifact_paths")
    entries = payload.get("artifact_fingerprints")
    if (
        not isinstance(paths, list)
        or not isinstance(entries, list)
        or len(paths) != len(entries)
    ):
        raise ArtifactError("manifest fingerprint structure invalid")
    seen: set[str] = set()
    for artifact_path, entry in zip(paths, entries, strict=True):
        if (
            not isinstance(artifact_path, str)
            or not isinstance(entry, dict)
            or entry.get("artifact_path") != artifact_path
            or not isinstance(entry.get("sha256"), str)
            or SHA256_PATTERN.fullmatch(entry["sha256"]) is None
        ):
            raise ArtifactError("manifest fingerprint structure invalid")
        path_identity = _portable_artifact_path_identity(artifact_path)
        if path_identity in seen:
            raise ArtifactError("manifest fingerprint structure invalid")
        seen.add(path_identity)
    return True


def repair_manifest_fingerprints(
    context: DataAnalystsContext,
    artifact_ids: Sequence[str],
) -> tuple[Path, ...]:
    if isinstance(artifact_ids, str):
        raise ArtifactError("invalid artifact id")
    ids = tuple(artifact_ids)
    if not ids:
        raise ArtifactError("artifact ids must be unique and non-empty")

    for artifact_id in ids:
        if (
            not isinstance(artifact_id, str)
            or SAFE_ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None
        ):
            raise ArtifactError("invalid artifact id")

    seen: set[str] = set()
    for artifact_id in ids:
        if artifact_id in seen:
            raise ArtifactError("artifact ids must be unique and non-empty")
        seen.add(artifact_id)

    replacements: list[tuple[Path, str]] = []
    results: list[Path] = []
    for artifact_id in ids:
        manifest_path = context.store_path("manifests", f"{artifact_id}.json")
        try:
            source_bytes = manifest_path.read_bytes()
            payload = json.loads(source_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError("artifact manifest unavailable") from exc
        if not isinstance(payload, dict) or payload.get("artifact_id") != artifact_id:
            raise ArtifactError("artifact manifest identity mismatch")
        paths = payload.get("artifact_paths")
        if not isinstance(paths, list) or not paths:
            raise ArtifactError("manifest fingerprint structure invalid")
        computed = build_artifact_fingerprints(context, paths)
        if payload.get("schema_version") == "1.0":
            replacement = dict(payload)
            replacement["schema_version"] = "1.1"
            replacement["artifact_fingerprints"] = computed
            validate_manifest_fingerprint_structure(replacement)
            replacements.append(
                (manifest_path, json.dumps(replacement, indent=2, sort_keys=True))
            )
        else:
            validate_manifest_fingerprint_structure(payload)
            if payload["artifact_fingerprints"] != computed:
                raise ArtifactError("artifact fingerprint mismatch")
        results.append(manifest_path)

    publisher = ArtifactPublisher(context)
    for manifest_path, serialized in replacements:
        publisher._atomic_write_text(manifest_path, serialized)
    return tuple(results)


_MANIFEST_RESERVED_FIELDS = frozenset(
    {
        "artifact_id",
        "schema_version",
        "layer",
        "source_families",
        "source_collections",
        "row_count",
        "date_range",
        "availability_date_range",
        "columns",
        "partitioning",
        "artifact_paths",
        "artifact_fingerprints",
        "pit_policy",
        "data_cutoff_at",
        "duplicate_count",
        "omitted_row_count",
        "status",
        "created_at",
    }
)


def build_manifest_payload(
    context: DataAnalystsContext,
    *,
    artifact_id: str,
    layer: str,
    source_families: list[str],
    source_collections: list[str],
    columns: list[str],
    artifact_paths: list[str],
    row_count: int,
    date_range: list[str] | None,
    availability_date_range: list[str] | None,
    partitioning: list[str],
    pit_policy: str,
    data_cutoff_at: str,
    duplicate_count: int,
    omitted_row_count: int,
    status: str,
    created_at: str | None = None,
    extension_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a validated manifest without writing to the data store."""
    if extension_fields is None:
        extension_fields = {}
    elif not isinstance(extension_fields, Mapping):
        raise ArtifactError("extension_fields must be a mapping")
    reserved = _MANIFEST_RESERVED_FIELDS.intersection(extension_fields)
    if reserved:
        raise ArtifactError(
            f"extension_fields contains reserved manifest field: {sorted(reserved)[0]}"
        )
    _require_json_object_keys(extension_fields)
    try:
        safe_extensions = json.loads(
            json.dumps(extension_fields, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError("extension_fields must be JSON-compatible") from exc
    if not isinstance(safe_extensions, dict):
        raise ArtifactError("extension_fields must be a JSON-compatible mapping")

    try:
        safe_artifact_paths = [
            context.validate_artifact_path(path) for path in artifact_paths
        ]
    except PathBoundaryError as exc:
        raise ArtifactError(str(exc)) from exc
    manifest = {
        "artifact_id": artifact_id,
        "schema_version": "1.0",
        "layer": layer,
        "source_families": list(source_families),
        "source_collections": list(source_collections),
        "row_count": row_count,
        "date_range": None if date_range is None else list(date_range),
        "availability_date_range": (
            None
            if availability_date_range is None
            else list(availability_date_range)
        ),
        "columns": list(columns),
        "partitioning": list(partitioning),
        "artifact_paths": safe_artifact_paths,
        "pit_policy": pit_policy,
        "data_cutoff_at": data_cutoff_at,
        "duplicate_count": duplicate_count,
        "omitted_row_count": omitted_row_count,
        "status": status,
        "created_at": data_cutoff_at if created_at is None else created_at,
    }
    manifest.update(safe_extensions)
    return manifest


def _require_json_object_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ArtifactError("extension_fields must be JSON-compatible")
        for item in value.values():
            _require_json_object_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_json_object_keys(item)


def validate_rows(
    contract: ArtifactContract,
    rows: list[dict[str, Any]],
    partition_value: str | None = None,
) -> None:
    """Fail closed when rows do not satisfy their artifact contract."""
    if contract.partition_name is not None and not _has_value(partition_value):
        raise ArtifactError(
            f"{contract.artifact_id} requires partition value for {contract.partition_name}"
        )

    seen_keys: set[tuple[Any, ...]] = set()
    date_fields = tuple(
        dict.fromkeys(
            field
            for field in (contract.date_field, contract.availability_field)
            if field is not None
        )
    )
    for index, row in enumerate(rows):
        parsed_dates: dict[str, date] = {}
        if contract.partition_field and not _has_value(row.get(contract.partition_field)):
            raise ArtifactError(
                f"{contract.artifact_id} row {index} missing partition field: "
                f"{contract.partition_field}"
            )

        missing = [column for column in contract.required_columns if column not in row]
        if missing:
            raise ArtifactError(
                f"{contract.artifact_id} row {index} missing required columns: "
                f"{', '.join(missing)}"
            )

        missing_keys = [
            field for field in contract.logical_key if not _has_value(row.get(field))
        ]
        if missing_keys:
            raise ArtifactError(
                f"{contract.artifact_id} row {index} missing logical key fields: "
                f"{', '.join(missing_keys)}"
            )

        for field in date_fields:
            if not _has_value(row.get(field)):
                raise ArtifactError(
                    f"{contract.artifact_id} row {index} missing date field: {field}"
                )
            parsed_dates[field] = _iso_date(
                row[field], contract.artifact_id, index, field
            )

        cutoff = row.get("data_cutoff_at")
        if not _is_real_cutoff(cutoff):
            raise ArtifactError(
                f"{contract.artifact_id} row {index} requires real ISO data_cutoff_at"
            )

        logical_key = tuple(
            parsed_dates.get(field, row[field]) for field in contract.logical_key
        )
        try:
            duplicate = logical_key in seen_keys
        except TypeError as exc:
            raise ArtifactError(
                f"{contract.artifact_id} row {index} has unhashable logical key"
            ) from exc
        if duplicate:
            raise ArtifactError(
                f"{contract.artifact_id} row {index} has duplicate logical key: "
                f"{logical_key!r}"
            )
        seen_keys.add(logical_key)

        if contract.partition_field:
            partition_date = parsed_dates.get(contract.partition_field) or _iso_date(
                row[contract.partition_field],
                contract.artifact_id,
                index,
                contract.partition_field,
            )
            row_partition = (
                str(partition_date.year)
                if _is_year_partition(contract.partition_name)
                else partition_date.isoformat()
            )
            if row_partition != str(partition_value):
                raise ArtifactError(
                    f"{contract.artifact_id} row {index} belongs to partition "
                    f"{row_partition}, not {partition_value}"
                )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if _is_pandas_missing_scalar(value):
        return False
    try:
        if math.isnan(value):
            return False
    except (TypeError, ValueError):
        pass
    try:
        return not bool(value != value)
    except (TypeError, ValueError):
        return True


def _iso_date(value: Any, artifact_id: str, index: int, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            if len(text) == 10:
                return date.fromisoformat(text)
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ArtifactError(
                f"{artifact_id} row {index} has invalid ISO date in {field}: {value!r}"
            ) from exc
    raise ArtifactError(
        f"{artifact_id} row {index} has invalid ISO date in {field}: {value!r}"
    )


def _is_year_partition(partition_name: str | None) -> bool:
    return partition_name == "year" or bool(
        partition_name and partition_name.endswith("_year")
    )


def _is_real_cutoff(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if "T" not in text and " " not in text:
            return False
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False

    if parsed.tzinfo is None:
        return parsed != datetime(1970, 1, 1)
    return parsed.astimezone(timezone.utc) != datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )


@dataclass(frozen=True)
class ArtifactPublisher:
    context: DataAnalystsContext

    def publish_manifest(
        self,
        *,
        artifact_id: str,
        layer: str,
        source_families: list[str],
        source_collections: list[str],
        columns: list[str],
        artifact_paths: list[str],
        row_count: int,
        date_range: list[str] | None,
        availability_date_range: list[str] | None,
        partitioning: list[str],
        pit_policy: str,
        data_cutoff_at: str,
        duplicate_count: int,
        omitted_row_count: int,
        status: str,
        extension_fields: Mapping[str, Any] | None = None,
    ) -> Path:
        artifact_fingerprints = build_artifact_fingerprints(
            self.context,
            artifact_paths,
        )
        manifest = build_manifest_payload(
            self.context,
            artifact_id=artifact_id,
            layer=layer,
            source_families=source_families,
            source_collections=source_collections,
            columns=columns,
            artifact_paths=artifact_paths,
            row_count=row_count,
            date_range=date_range,
            availability_date_range=availability_date_range,
            partitioning=partitioning,
            pit_policy=pit_policy,
            data_cutoff_at=data_cutoff_at,
            duplicate_count=duplicate_count,
            omitted_row_count=omitted_row_count,
            status=status,
            created_at=_utc_now(),
            extension_fields=extension_fields,
        )
        manifest["schema_version"] = "1.1"
        manifest["artifact_paths"] = [
            item["artifact_path"] for item in artifact_fingerprints
        ]
        manifest["artifact_fingerprints"] = artifact_fingerprints
        validate_manifest_fingerprint_structure(manifest)
        target = self.context.store_path("manifests", f"{artifact_id}.json")
        self._atomic_write_text(target, json.dumps(manifest, indent=2, sort_keys=True))
        return target

    def publish_parquet(
        self,
        path: str | Path,
        *,
        rows: list[dict[str, Any]],
        required_columns: list[str],
    ) -> Path:
        target = self._resolve_artifact_target(path)
        columns = _columns_from_rows(rows)
        missing = [column for column in required_columns if column not in columns]
        if missing:
            raise ArtifactError(f"missing required columns: {', '.join(missing)}")

        ordered_columns = list(dict.fromkeys([*required_columns, *columns]))
        data = {
            column: [_normalize_parquet_scalar(row.get(column)) for row in rows]
            for column in ordered_columns
        }
        table = pa.table(data)
        atomic_write_parquet(target, table)
        return target

    def _validate_relative_artifact_path(self, path: str) -> str:
        try:
            return self.context.validate_artifact_path(path)
        except PathBoundaryError as exc:
            raise ArtifactError(str(exc)) from exc

    def _resolve_artifact_target(self, path: str | Path) -> Path:
        try:
            return self.context.artifact_path(path)
        except PathBoundaryError as exc:
            raise ArtifactError(str(exc)) from exc

    def _atomic_write_text(self, target: Path, payload: str) -> None:
        atomic_write_text(target, payload)


def stage_parquet(
    target: Path,
    table: pa.Table,
    *,
    validate: Callable[[pa.Table], None] | None = None,
) -> Path:
    """Write and read back a sibling staging file without replacing the target."""
    staging = target.with_name(f".{target.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        pq.write_table(table, staging)
        # ParquetFile avoids Hive partition-column injection from ``year=...``
        # parent directories; validation must inspect only the file payload.
        parquet = pq.ParquetFile(staging)
        try:
            readback = parquet.read()
        finally:
            parquet.close()
        if validate is not None:
            validate(readback)
        return staging
    except Exception as exc:
        if staging.exists():
            staging.unlink()
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError(f"cannot stage parquet {target}: {exc}") from exc


def atomic_write_parquet(
    target: Path,
    table: pa.Table,
    *,
    validate: Callable[[pa.Table], None] | None = None,
) -> None:
    staging = stage_parquet(target, table, validate=validate)
    try:
        replace_file(staging, target)
    finally:
        if staging.exists():
            staging.unlink()


def atomic_write_text(target: Path, payload: str) -> None:
    staging = target.with_name(f".{target.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.write_text(payload, encoding="utf-8")
        replace_file(staging, target)
    finally:
        if staging.exists():
            staging.unlink()


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for column in row:
            if column not in seen:
                seen.add(column)
                columns.append(column)
    return columns


def _normalize_parquet_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, str, bool, int, datetime, date)):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, float):
        return None if math.isnan(value) else value

    item = getattr(value, "item", None)
    if callable(item):
        try:
            item_value = item()
        except (TypeError, ValueError):
            item_value = value
        if item_value is not value:
            return _normalize_parquet_scalar(item_value)

    if _is_pandas_missing_scalar(value):
        return None
    return value


def _is_pandas_missing_scalar(value: Any) -> bool:
    value_type = type(value)
    if not value_type.__module__.startswith("pandas."):
        return False
    return value_type.__name__ in {"NAType", "NaTType"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
