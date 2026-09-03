"""Publication adapter for the ETF consumer's stricter DMS manifest contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifacts import atomic_write_text
from data_analysts.config import RuntimeConfig
from data_analysts.dataset_publication import PublicationResult
from data_analysts.paths import DataAnalystsContext


_DMS_SCHEMA = pa.schema(
    [
        pa.field("date", pa.string()),
        pa.field("ticker", pa.string()),
        pa.field("price_row_present", pa.bool_()),
        pa.field("attr_row_present", pa.bool_()),
        pa.field("full_delivery", pa.bool_()),
        pa.field("observation_date", pa.string()),
        pa.field("source_available_date", pa.string()),
        pa.field("availability_precision", pa.string()),
        pa.field("earliest_execution_session", pa.string()),
        pa.field("security_master_manifest_sha256", pa.string()),
        pa.field("calendar_manifest_sha256", pa.string()),
        pa.field("price_manifest_sha256", pa.string()),
        pa.field("tradability_manifest_sha256", pa.string()),
        pa.field("classification_policy_version", pa.string()),
        pa.field("data_cutoff_at", pa.string()),
        *[pa.field(name, pa.string()) for name in (
            "atten_fg", "disp_fg", "full_fg", "limit_fg", "limo_fg",
            "sbadt_fg", "ssadt_fg", "susp_fg", "market", "market_state",
            "state_reason", "amount_state",
        )],
        pa.field("authoritative_traded_value", pa.float64()),
        pa.field("amount_zero_authorized", pa.bool_()),
        pa.field("exchange_tradable", pa.bool_()),
        *[pa.field(name, pa.string()) for name in (
            "instrument_kind", "identity_source", "security_master_market",
            "lifecycle_list_date", "lifecycle_delist_date",
            "lifecycle_interval_start", "lifecycle_interval_end_exclusive",
        )],
        pa.field("lifecycle_active", pa.bool_()),
        pa.field("lifecycle_conflict", pa.bool_()),
        pa.field("identity_conflict", pa.bool_()),
        pa.field("lifecycle_pit_status", pa.string()),
        pa.field("revision_pit_status", pa.string()),
    ]
)

def publish_daily_market_state(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    rows: list[dict[str, Any]],
    *,
    build_start: str,
    build_end: str,
    certified_source_start: str,
) -> PublicationResult:
    """Publish immutable partitions, then atomically add DMS authority evidence."""
    return publish_daily_market_state_partitions(
        context,
        config,
        ((None, rows),),
        build_start=build_start,
        build_end=build_end,
        certified_source_start=certified_source_start,
    )


def publish_daily_market_state_partitions(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    partitions: Iterable[tuple[str | None, list[dict[str, Any]]]],
    *,
    build_start: str,
    build_end: str,
    certified_source_start: str,
) -> PublicationResult:
    """Publish bounded DMS partitions without retaining every year in memory."""
    del config
    dependencies = _dependency_evidence(context)
    result = _publish_arrow_partition_stream(context, partitions, build_start, build_end)
    manifest = dict(result.manifest)
    inventory = [_inventory_item(context, path) for path in manifest["artifact_paths"]]
    schema_fingerprint = inventory[0]["schema_fingerprint"]
    if any(item["schema_fingerprint"] != schema_fingerprint for item in inventory):
        raise ValueError("daily_market_state partitions have incompatible physical schemas")
    manifest.update({
        "logical_key": ["date", "ticker"],
        "partition_inventory": inventory,
        "schema_fingerprint": schema_fingerprint,
        "dependency_manifest_sha256_by_contract": {
            key: value["sha256"] for key, value in dependencies.items()
        },
        "dependency_versions": {
            key: value["version"] for key, value in dependencies.items()
        },
        "dependency_certification_fingerprint": _sha256_json(dependencies),
        "build_start": build_start,
        "build_end": build_end,
        "certified_source_start": certified_source_start,
        "classification_policy_version": "daily_market_state_v3",
        "state_lattice_policy_version": "daily_market_state_lattice_v5",
        "market_identity_policy_version": "daily_market_identity_v3",
    })
    manifest_path = context.store_path("manifests", "daily_market_state.json")
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return PublicationResult(
        result.touched_paths, result.total_row_count, result.date_range, manifest_path, manifest,
        result.cleanup_diagnostics,
    )


def _publish_arrow_partitions(
    context: DataAnalystsContext, rows: list[dict[str, Any]], build_start: str, build_end: str
) -> PublicationResult:
    return _publish_arrow_partition_stream(
        context, ((None, rows),), build_start, build_end
    )


def _publish_arrow_partition_stream(
    context: DataAnalystsContext,
    partitions: Iterable[tuple[str | None, list[dict[str, Any]]]],
    build_start: str,
    build_end: str,
) -> PublicationResult:
    version = uuid.uuid4().hex
    paths: list[str] = []
    inventory: list[dict[str, Any]] = []
    total_row_count = 0
    first_key: tuple[str, str] | None = None
    last_key: tuple[str, str] | None = None
    data_cutoff_at: str | None = None
    for declared_year, partition_rows in partitions:
        if not partition_rows:
            continue
        keys = [(str(row["date"]), str(row["ticker"])) for row in partition_rows]
        if (
            keys != sorted(keys)
            or any(left == right for left, right in zip(keys, keys[1:]))
            or (last_key is not None and keys[0] <= last_key)
        ):
            raise ValueError("daily_market_state rows must be globally sorted and unique")
        years = {key[0][:4] for key in keys}
        if len(years) != 1:
            raise ValueError("daily_market_state stream partition must contain one calendar year")
        year = years.pop()
        if declared_year is not None and declared_year != year:
            raise ValueError("daily_market_state stream partition year does not match row dates")
        partition_cutoff = str(partition_rows[0]["data_cutoff_at"])
        if any(str(row["data_cutoff_at"]) != partition_cutoff for row in partition_rows):
            raise ValueError("daily_market_state partition has inconsistent data_cutoff_at")
        if data_cutoff_at is None:
            data_cutoff_at = partition_cutoff
        elif partition_cutoff != data_cutoff_at:
            raise ValueError("daily_market_state partitions have inconsistent data_cutoff_at")
        relative = f"canonical/derived/daily_market_state/versions/{version}/year={year}/part.parquet"
        target = context.artifact_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.staging")
        table = pa.Table.from_pylist(partition_rows, schema=_DMS_SCHEMA)
        pq.write_table(table, staging, compression="zstd")
        os.replace(staging, target)
        paths.append(relative)
        inventory.append(_inventory_item(context, relative))
        total_row_count += len(partition_rows)
        first_key = keys[0] if first_key is None else first_key
        last_key = keys[-1]
    if not paths or first_key is None or data_cutoff_at is None:
        raise ValueError("daily_market_state bounded publication cannot be empty")
    schema_fingerprints = {item["schema_fingerprint"] for item in inventory}
    if len(schema_fingerprints) != 1:
        raise ValueError("daily_market_state yearly parquet schemas differ")
    manifest = {
        "artifact_id": "daily_market_state", "contract_key": "daily_market_state",
        "variant": "default", "schema_version": "1.0", "layer": "derived",
        "source_families": ["security_master", "trading_calendar", "daily_price_volume", "daily_tradability"],
        "source_collections": [], "row_count": total_row_count,
        "date_range": [build_start, build_end], "availability_date_range": [build_start, build_end],
        "columns": list(_DMS_SCHEMA.names),
        "schema_fingerprint": schema_fingerprints.pop(), "partitioning": ["year"],
        "artifact_paths": paths, "pit_policy": "after_close_next_session",
        "data_cutoff_at": data_cutoff_at, "duplicate_count": 0,
        "omitted_row_count": 0, "status": "ready", "active_version": version,
        "created_at": data_cutoff_at, "partition_inventory": inventory,
    }
    return PublicationResult(tuple(paths), total_row_count, (build_start, build_end),
                             context.store_path("manifests", "daily_market_state.json"), manifest)


def _dependency_evidence(context: DataAnalystsContext) -> dict[str, dict[str, str]]:
    expected = ("security_master", "trading_calendar", "daily_price_volume", "daily_tradability")
    result: dict[str, dict[str, str]] = {}
    for artifact_id in expected:
        path = context.store_path("manifests", f"{artifact_id}.json")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise ValueError(f"dependency manifest is not ready: {artifact_id}")
        version = payload.get("active_version")
        result[artifact_id] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "version": str(version) if isinstance(version, str) and version else "legacy-manifest",
        }
    return result


def _inventory_item(context: DataAnalystsContext, relative: str) -> dict[str, Any]:
    path = context.artifact_path(relative)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parquet = pq.ParquetFile(path)
    try:
        row_count = parquet.metadata.num_rows
        schema_fingerprint = hashlib.sha256(
            parquet.schema_arrow.serialize().to_pybytes()
        ).hexdigest()
    finally:
        parquet.close()
    return {
        "path": relative, "content_sha256": digest, "size": path.stat().st_size,
        "row_count": row_count, "schema_fingerprint": schema_fingerprint,
    }


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
